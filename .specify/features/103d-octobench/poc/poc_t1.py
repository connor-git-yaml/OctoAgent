"""PoC 单 Tier 1 task 手工脚本

Task ID   : T-0-T1
关联 FR   : FR-G02, AC2-1, FR-D02
PoC 假设  : OctoHarness DI 可独立实例化、EventStore 可 query
实测维度  :
  - OctoHarness bootstrap 成功（独立 tmpdir，不触碰 ~/.octoagent）
  - 执行 1 个基础工具调用 task，记录 wall clock 耗时
  - EventStore query MEMORY_ENTRY_ADDED 事件是否存在
期望输出  : JSON 结果到 stdout，含 duration_seconds + event_found + detail

运行方式  ::

    export ANTHROPIC_API_KEY=sk-ant-...
    cd <project_root>
    python .specify/features/103d-octobench/poc/poc_t1.py
    # 或自定义 prompt：
    python .specify/features/103d-octobench/poc/poc_t1.py --prompt "记住：代号是 OCTOBENCH"

注意：需要真实 ANTHROPIC_API_KEY，否则脚本会返回 LLM_UNAVAILABLE 状态。

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

logger = logging.getLogger("poc_t1")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# 导入路径（基于 grep 实测：from octoagent.gateway.harness.octo_harness import OctoHarness）
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[5]  # .../OctoAgent/.claude/worktrees/F103d-octobench
_GATEWAY_SRC = _PROJECT_ROOT / "octoagent" / "apps" / "gateway" / "src"
_CORE_SRC    = _PROJECT_ROOT / "octoagent" / "packages" / "core" / "src"

for _p in [str(_GATEWAY_SRC), str(_CORE_SRC)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _import_octo() -> tuple:
    """懒导入 OctoHarness + EventType，失败时返回 (None, None) + 结构化错误信息。"""
    try:
        from octoagent.gateway.harness.octo_harness import OctoHarness  # type: ignore
        from octoagent.core.models.enums import EventType  # type: ignore

        return OctoHarness, EventType
    except ImportError as e:
        return None, str(e)


async def run_poc(prompt: str) -> dict:
    """核心 PoC 逻辑：bootstrap OctoHarness → 发送 task → query EventStore。"""
    OctoHarness, EventType = _import_octo()
    if OctoHarness is None:
        return {
            "status": "IMPORT_ERROR",
            "error": str(EventType),
            "hint": "请确认 sys.path 包含 gateway/src 和 core/src",
        }

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {
            "status": "LLM_UNAVAILABLE",
            "error": "ANTHROPIC_API_KEY 未设置",
            "hint": "export ANTHROPIC_API_KEY=sk-ant-... 后重跑",
        }

    with tempfile.TemporaryDirectory(prefix="poc_t1_") as tmpdir:
        tmp_path = Path(tmpdir)
        logger.info("PoC tmpdir: %s", tmp_path)

        # [TBD: 由用户/主 session 实测填入] 实际 bootstrap 参数可能需要调整
        # FastAPI app 对象在真实 e2e 测试中由 TestClient 提供；
        # 此处为 PoC 最小路径，直接实例化后尝试访问 store_group。
        harness = OctoHarness(project_root=tmp_path, data_dir=tmp_path)

        try:
            from fastapi import FastAPI  # type: ignore

            app = FastAPI()
            t_start = time.perf_counter()
            await harness.bootstrap(app)
            bootstrap_ms = (time.perf_counter() - t_start) * 1000
            logger.info("bootstrap 完成，耗时 %.0f ms", bootstrap_ms)
        except Exception as e:
            logger.exception("bootstrap 失败")
            return {
                "status": "BOOTSTRAP_ERROR",
                "error": str(e),
                "hint": "bootstrap 可能需要 Telegram token 等外部环境变量",
            }

        # 获取 store_group（通过 harness 内部私有属性）
        store_group = getattr(harness, "_store_group", None)
        if store_group is None:
            return {"status": "STORE_GROUP_NONE", "error": "bootstrap 后 _store_group 为 None"}

        event_store = getattr(store_group, "event_store", None)
        if event_store is None:
            return {"status": "EVENT_STORE_NONE", "error": "store_group.event_store 不存在"}

        # [TBD: 由用户/主 session 实测填入] 通过 HTTP 接口或 TaskService 直接发 task
        # 此处 PoC 只验证 EventStore 可被 query，实际 task 执行需完整 HTTP 路由
        logger.info("EventStore query 路径验证中...")
        t_query = time.perf_counter()
        try:
            all_events = await event_store.get_all_events()
            query_ms = (time.perf_counter() - t_query) * 1000
        except Exception as e:
            logger.exception("EventStore.get_all_events 失败")
            return {"status": "QUERY_ERROR", "error": str(e)}

        memory_events = [
            ev for ev in all_events if getattr(ev, "event_type", None) == "MEMORY_ENTRY_ADDED"
        ]

        total_ms = (time.perf_counter() - t_start) * 1000

        await harness.shutdown(app)

        return {
            "status": "PASS_PARTIAL",  # bootstrap + query 路径成功，完整 task 执行需手动补
            "task": "T-0-T1",
            "prompt_used": prompt,
            "bootstrap_ms": round(bootstrap_ms, 1),
            "query_ms": round(query_ms, 1),
            "total_ms": round(total_ms, 1),
            "all_events_count": len(all_events),
            "memory_entry_added_count": len(memory_events),
            "memory_event_found": len(memory_events) > 0,
            "note": (
                "[TBD-MANUAL-RUN] 完整 task 执行（含 LLM call + tool call）需通过 HTTP API 路径；"
                "本 PoC 仅验证 bootstrap + EventStore query 路径。"
            ),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="PoC T-0-T1: 单 Tier 1 task 验证")
    parser.add_argument(
        "--prompt",
        default="请记住这条事实：OctoBench PoC 时间戳是 2026-05-28。",
        help="发送给 Agent 的测试 prompt",
    )
    args = parser.parse_args()

    result = asyncio.run(run_poc(args.prompt))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("status") in {"PASS", "PASS_PARTIAL"} else 1)


if __name__ == "__main__":
    main()
