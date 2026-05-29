"""GAIA benchmark PoC 脚本

Task ID   : T-0-T3-GAIA
关联 FR   : FR-G02, AC2-1, FR-E03, PoC-H1
PoC 假设  : PoC-H1（HF GAIA gated dataset 访问成功）
实测维度  :
  - load_dataset("gaia-benchmark/GAIA", split="validation") 可用
  - Level 2 task 数量 >= 5
  - 取 1 个 Level 2 task，记录字段结构和耗时
期望输出  : JSON 结果到 stdout，含 level2_count + sample_task + poc_h1_pass

运行方式  ::

    cd <project_root>
    export HF_TOKEN=hf_...   # 可选，gated dataset 可能需要
    python .specify/features/103d-octobench/poc/poc_gaia.py

注意：
  - 需要 HuggingFace datasets 已安装：uv add datasets
  - GAIA 是 gated dataset，需要 HF 账号申请访问权限
  - 若 PoC-H1 失败，会提示激活 gaia_fallback_tasks.yaml 降级方案

"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any

logger = logging.getLogger("poc_gaia")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _try_import_datasets() -> tuple[Any, str | None]:
    """懒导入 datasets，失败返回 (None, error_message)。"""
    try:
        import datasets  # type: ignore

        return datasets, None
    except ImportError as e:
        return None, str(e)
    except Exception as e:
        return None, f"意外错误: {e}"


def _safe_serialize(val: Any, max_chars: int = 300) -> Any:
    """安全序列化：截断过长字符串，保留基础类型。"""
    if isinstance(val, (str,)):
        return val[:max_chars] + ("..." if len(val) > max_chars else "")
    if isinstance(val, (int, float, bool, type(None))):
        return val
    try:
        json.dumps(val)
        return val
    except (TypeError, ValueError):
        return f"<{type(val).__name__}>"


def main() -> None:
    parser = argparse.ArgumentParser(description="PoC T-0-T3-GAIA: GAIA Level 2 访问验证")
    parser.add_argument("--split", default="validation", help="HF dataset split（默认 validation）")
    parser.add_argument("--timeout", type=int, default=60, help="加载超时秒数（默认 60）")
    args = parser.parse_args()

    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if hf_token:
        logger.info("HF_TOKEN 已设置（%d chars）", len(hf_token))
    else:
        logger.warning("HF_TOKEN 未设置；gated dataset 访问可能失败")

    datasets, import_err = _try_import_datasets()
    if datasets is None:
        result = {
            "status": "IMPORT_ERROR",
            "poc_h1": "FAIL",
            "error": import_err,
            "hint": "安装 datasets: uv add datasets",
            "fallback_action": "激活 T-B-3 gaia_fallback_tasks.yaml",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    t_start = time.perf_counter()
    try:
        logger.info("加载 GAIA dataset（split=%s）...", args.split)
        # 注意：trust_remote_code 在 datasets>=2.20 已废弃，不传
        ds = datasets.load_dataset(
            "gaia-benchmark/GAIA",
            split=args.split,
            token=hf_token,
        )
        load_ms = (time.perf_counter() - t_start) * 1000
        logger.info("加载完成，共 %d 条，耗时 %.0f ms", len(ds), load_ms)
    except Exception as e:
        total_ms = (time.perf_counter() - t_start) * 1000
        logger.error("GAIA 加载失败: %s", e)
        result = {
            "status": "LOAD_ERROR",
            "poc_h1": "FAIL",
            "error": str(e),
            "total_ms": round(total_ms, 1),
            "fallback_action": (
                "PoC-H1 不成立 → 激活 T-B-3 降级方案：\n"
                "  在 benchmarks/tiers/tier2/gaia_fallback_tasks.yaml 手工构造 5 个 [GAIA-FALLBACK] 样本\n"
                "  参考来源：arxiv 2311.12983 附录（GAIA Level 2 公开样本）"
            ),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    # Level 2 过滤
    try:
        level2_tasks = [row for row in ds if row.get("Level") == 2]
    except Exception:
        level2_tasks = [row for row in ds if str(row.get("level", "")).strip() == "2"]

    poc_h1_pass = len(level2_tasks) >= 5
    logger.info("Level 2 task 数: %d（PoC-H1 %s）", len(level2_tasks), "PASS" if poc_h1_pass else "FAIL")

    # 取第一个 Level 2 task 的字段
    sample_task: dict | None = None
    if level2_tasks:
        raw = level2_tasks[0]
        sample_task = {}
        for k in (raw.keys() if hasattr(raw, "keys") else []):
            try:
                sample_task[k] = _safe_serialize(raw[k])
            except Exception:
                sample_task[k] = "<unserializable>"

    total_ms = (time.perf_counter() - t_start) * 1000

    result = {
        "status": "PASS" if poc_h1_pass else "FAIL_POC_H1",
        "task": "T-0-T3-GAIA",
        "poc_h1_level2_count_gte_5": poc_h1_pass,
        "total_tasks_in_split": len(ds),
        "level2_count": len(level2_tasks),
        "load_ms": round(load_ms, 1),
        "total_ms": round(total_ms, 1),
        "sample_task_fields": list(sample_task.keys()) if sample_task else [],
        "sample_task_preview": sample_task,
        "exec_note": (
            "[TBD-MANUAL-RUN] 完整 task 执行（OctoAgent 实际回答 Level 2 question）"
            "需要 ANTHROPIC_API_KEY + OctoHarness bootstrap；本 PoC 仅验证数据集访问路径。"
        ),
    }

    if not poc_h1_pass:
        result["fallback_action"] = (
            f"Level 2 task 数 = {len(level2_tasks)} < 5，建议激活 T-B-3 降级方案：\n"
            "  benchmarks/tiers/tier2/gaia_fallback_tasks.yaml（手工构造 5 个 GAIA-FALLBACK 样本）"
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if poc_h1_pass else 1)


if __name__ == "__main__":
    main()
