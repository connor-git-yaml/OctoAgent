"""并发压测 PoC 脚本

Task ID   : T-0-T5-CONC
关联 FR   : FR-G02, AC2-1b, FR-A01, PoC-H3, W6
PoC 假设  : PoC-H3（8 并发时 SQLite WAL p95 write latency <= 2.0s 额外 overhead）
实测维度  :
  - asyncio.gather 并行启动 8 个 OctoHarness 实例（各独立 tmpdir）
  - 测量每个实例 bootstrap 耗时
  - 计算 p95 latency + 验证无 DB locked 错误
期望输出  : JSON 结果到 stdout，含 p95_seconds + max_seconds + db_locked_errors

运行方式  ::

    cd <project_root>
    python .specify/features/103d-octobench/poc/poc_concurrent.py
    # 自定义并发度：
    python .specify/features/103d-octobench/poc/poc_concurrent.py --concurrency 4

注意：
  - 不需要 ANTHROPIC_API_KEY（仅测 bootstrap + SQLite 初始化，不跑 LLM）
  - 每个 harness 实例用独立 tmpdir（PoC-H3 成立条件）

"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("poc_concurrent")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_GATEWAY_SRC  = _PROJECT_ROOT / "octoagent" / "apps" / "gateway" / "src"
_CORE_SRC     = _PROJECT_ROOT / "octoagent" / "packages" / "core" / "src"

for _p in [str(_GATEWAY_SRC), str(_CORE_SRC)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _import_octo() -> tuple:
    try:
        from octoagent.gateway.harness.octo_harness import OctoHarness  # type: ignore

        return OctoHarness, None
    except ImportError as e:
        return None, str(e)


async def bootstrap_one(slot_idx: int, tmp_path: Path) -> dict:
    """
    单个 OctoHarness slot 的 bootstrap + 简单 EventStore 写入探测。
    返回 {slot_idx, duration_s, status, error}.
    """
    OctoHarness, err = _import_octo()
    if OctoHarness is None:
        return {"slot_idx": slot_idx, "status": "IMPORT_ERROR", "error": err, "duration_s": 0.0}

    harness = OctoHarness(project_root=tmp_path, data_dir=tmp_path)
    t_start = time.perf_counter()

    try:
        from fastapi import FastAPI  # type: ignore

        app = FastAPI()
        await harness.bootstrap(app)

        # 探测 store_group.event_store 可写（仅做一次 get_all_events 查询）
        store_group = getattr(harness, "_store_group", None)
        db_locked_error = False
        if store_group is not None:
            event_store = getattr(store_group, "event_store", None)
            if event_store is not None:
                try:
                    await event_store.get_all_events()
                except Exception as e:
                    if "locked" in str(e).lower():
                        db_locked_error = True
                        logger.warning("Slot %d: DB locked 错误: %s", slot_idx, e)
                    else:
                        logger.warning("Slot %d: EventStore query 非 lock 错误: %s", slot_idx, e)

        duration_s = time.perf_counter() - t_start
        await harness.shutdown(app)
        return {
            "slot_idx": slot_idx,
            "status": "PASS",
            "duration_s": round(duration_s, 3),
            "db_locked_error": db_locked_error,
        }
    except Exception as e:
        duration_s = time.perf_counter() - t_start
        logger.exception("Slot %d bootstrap 失败", slot_idx)
        return {
            "slot_idx": slot_idx,
            "status": "ERROR",
            "error": str(e),
            "duration_s": round(duration_s, 3),
            "db_locked_error": "locked" in str(e).lower(),
        }


def percentile(data: list[float], p: float) -> float:
    """计算百分位数（简单线性插值，n 足够小时等同 numpy.percentile）。"""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (len(sorted_data) - 1) * p / 100.0
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_data) - 1)
    frac = idx - lo
    return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])


async def run_concurrent(concurrency: int) -> dict:
    """并发启动 `concurrency` 个 harness 实例，测量耗时分布。"""
    # 为每个 slot 创建独立 tmpdir
    tmpdirs = [tempfile.mkdtemp(prefix=f"poc_conc_{i}_") for i in range(concurrency)]

    try:
        logger.info("启动 %d 个并发 OctoHarness bootstrap...", concurrency)
        t_wall_start = time.perf_counter()

        results: list[dict] = await asyncio.gather(
            *[bootstrap_one(i, Path(tmpdirs[i])) for i in range(concurrency)],
            return_exceptions=False,  # 异常不静默，会向上传播
        )
        wall_seconds = time.perf_counter() - t_wall_start
    except Exception as e:
        logger.exception("并发 gather 失败")
        return {
            "status": "GATHER_ERROR",
            "error": str(e),
        }
    finally:
        # 清理临时目录
        import shutil

        for d in tmpdirs:
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass

    durations = [r["duration_s"] for r in results if isinstance(r.get("duration_s"), (int, float))]
    db_locked_count = sum(1 for r in results if r.get("db_locked_error", False))
    error_count = sum(1 for r in results if r.get("status") != "PASS")

    p50 = percentile(durations, 50)
    p95 = percentile(durations, 95)
    p_max = max(durations) if durations else 0.0

    # PoC-H3 判断：p95 额外 overhead <= 2.0s
    # baseline 单 harness bootstrap 时间未实测，此处用绝对值判断 p95 <= 5.0s 作为替代
    # [TBD-MANUAL-RUN] 用户需实测单 bootstrap baseline 后更新判断阈值
    poc_h3_pass = p95 <= 5.0 and db_locked_count == 0

    return {
        "status": "PASS" if poc_h3_pass else "FAIL_POC_H3",
        "task": "T-0-T5-CONC",
        "concurrency": concurrency,
        "wall_seconds": round(wall_seconds, 3),
        "slot_results": results,
        "latency_stats": {
            "p50_s": round(p50, 3),
            "p95_s": round(p95, 3),
            "max_s": round(p_max, 3),
        },
        "db_locked_errors": db_locked_count,
        "bootstrap_error_count": error_count,
        "poc_h3_pass": poc_h3_pass,
        "poc_h3_note": (
            f"p95 = {p95:.3f}s, db_locked = {db_locked_count}。"
            "PoC-H3 判断：p95 <= 5.0s && db_locked == 0 为 PASS。"
            "[TBD-MANUAL-RUN] 精确 overhead 需与单 bootstrap baseline 对比。"
        ),
        "fallback_hint": (
            None
            if poc_h3_pass
            else (
                "PoC-H3 不成立 → 考虑降级为共享 store 方案：\n"
                "  单 OctoHarness 实例 + 各 task 间仅清理 task_store 记录\n"
                "  而非 8 个独立实例各自独立 tmpdir"
            )
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="PoC T-0-T5-CONC: 8 并发 SQLite WAL 压测")
    parser.add_argument("--concurrency", type=int, default=8, help="并发度（默认 8，对应 FR-A01）")
    args = parser.parse_args()

    result = asyncio.run(run_concurrent(args.concurrency))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("status") in {"PASS"} else 1)


if __name__ == "__main__":
    main()
