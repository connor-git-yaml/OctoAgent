"""Tier 3 H1 哲学 task PoC 脚本

Task ID   : T-0-T4-PHILOSOPHY
关联 FR   : FR-G02, AC2-1, FR-F01, R-4
PoC 假设  : OctoHarness 可 bootstrap + EventStore 可 query SUBAGENT_SPAWNED
实测维度  :
  - bootstrap OctoHarness（独立 tmpdir）
  - query EventStore SUBAGENT_SPAWNED 事件路径可达
  - 记录 SUBAGENT_SPAWNED 查询 API 调用形式
期望输出  : JSON 结果到 stdout，含 event_query_path_ok + event_type_available

运行方式  ::

    export ANTHROPIC_API_KEY=sk-ant-...
    cd <project_root>
    python .specify/features/103d-octobench/poc/poc_t3.py

注意：
  - 完整 H1 验证（委托 Worker）需要 ANTHROPIC_API_KEY + 实际 task 执行
  - 本 PoC 重点验证 SUBAGENT_SPAWNED EventType 存在 + EventStore query 路径

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

logger = logging.getLogger("poc_t3")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_GATEWAY_SRC  = _PROJECT_ROOT / "octoagent" / "apps" / "gateway" / "src"
_CORE_SRC     = _PROJECT_ROOT / "octoagent" / "packages" / "core" / "src"

for _p in [str(_GATEWAY_SRC), str(_CORE_SRC)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _import_types() -> tuple:
    """导入 OctoHarness + EventType，失败返回错误信息元组。"""
    try:
        from octoagent.gateway.harness.octo_harness import OctoHarness  # type: ignore
        from octoagent.core.models.enums import EventType  # type: ignore

        return OctoHarness, EventType, None
    except ImportError as e:
        return None, None, str(e)


async def run_poc(prompt: str) -> dict:
    """PoC 核心：验证 SUBAGENT_SPAWNED 查询路径。"""
    OctoHarness, EventType, err = _import_types()
    if OctoHarness is None:
        return {"status": "IMPORT_ERROR", "error": err}

    # 验证 SUBAGENT_SPAWNED 枚举值存在
    subagent_spawned_ok = hasattr(EventType, "SUBAGENT_SPAWNED")
    subagent_completed_ok = hasattr(EventType, "SUBAGENT_COMPLETED")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {
            "status": "LLM_UNAVAILABLE",
            "event_type_check": {
                "SUBAGENT_SPAWNED": subagent_spawned_ok,
                "SUBAGENT_COMPLETED": subagent_completed_ok,
            },
            "hint": "export ANTHROPIC_API_KEY=sk-ant-... 后重跑以测试完整路径",
            "note": "[TBD-MANUAL-RUN] 完整 H1 验证需要 LLM 实际委托 Worker",
        }

    with tempfile.TemporaryDirectory(prefix="poc_t3_") as tmpdir:
        tmp_path = Path(tmpdir)
        logger.info("PoC tmpdir: %s", tmp_path)

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
                "event_type_check": {
                    "SUBAGENT_SPAWNED": subagent_spawned_ok,
                    "SUBAGENT_COMPLETED": subagent_completed_ok,
                },
            }

        store_group = getattr(harness, "_store_group", None)
        if store_group is None:
            await harness.shutdown(app)
            return {"status": "STORE_GROUP_NONE"}

        event_store = getattr(store_group, "event_store", None)
        if event_store is None:
            await harness.shutdown(app)
            return {"status": "EVENT_STORE_NONE"}

        # 验证 get_events_by_types_since API 路径可达
        t_query = time.perf_counter()
        try:
            from datetime import datetime, timezone

            since = datetime(2000, 1, 1, tzinfo=timezone.utc)
            events = await event_store.get_events_by_types_since(
                since=since,
                event_types=[EventType.SUBAGENT_SPAWNED],
            )
            query_ms = (time.perf_counter() - t_query) * 1000
            subagent_events = list(events)
            query_api = "get_events_by_types_since"
        except AttributeError:
            # 若没有该方法，退而求其次用 get_all_events + 过滤
            try:
                all_events = await event_store.get_all_events()
                subagent_events = [
                    ev for ev in all_events
                    if getattr(ev, "event_type", None) == "SUBAGENT_SPAWNED"
                ]
                query_ms = (time.perf_counter() - t_query) * 1000
                query_api = "get_all_events + filter"
            except Exception as e:
                await harness.shutdown(app)
                return {"status": "QUERY_ERROR", "error": str(e)}
        except Exception as e:
            logger.exception("EventStore query 失败")
            await harness.shutdown(app)
            return {"status": "QUERY_ERROR", "error": str(e)}

        total_ms = (time.perf_counter() - t_start) * 1000
        await harness.shutdown(app)

        return {
            "status": "PASS_PARTIAL",
            "task": "T-0-T4-PHILOSOPHY",
            "prompt_used": prompt,
            "bootstrap_ms": round(bootstrap_ms, 1),
            "query_ms": round(query_ms, 1),
            "total_ms": round(total_ms, 1),
            "event_type_check": {
                "SUBAGENT_SPAWNED": subagent_spawned_ok,
                "SUBAGENT_COMPLETED": subagent_completed_ok,
            },
            "event_query_api": query_api,
            "subagent_spawned_count_in_fresh_db": len(subagent_events),
            "h1_audit_path_ok": True,
            "note": (
                "[TBD-MANUAL-RUN] 完整 H1 哲学验证（委托 Worker + 查询 SUBAGENT_SPAWNED 非零）"
                "需要 ANTHROPIC_API_KEY + 实际 task 执行（通过 HTTP API）。"
                "本 PoC 验证：① SUBAGENT_SPAWNED EventType 枚举存在；② EventStore query 路径可达。"
            ),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="PoC T-0-T4-PHILOSOPHY: H1 audit chain 路径验证")
    parser.add_argument(
        "--prompt",
        default="帮我完成任务：统计今天是星期几。需要你委托 Worker 处理后再汇报结果。",
        help="H1 测试 prompt（委托 Worker 场景）",
    )
    args = parser.parse_args()

    result = asyncio.run(run_poc(args.prompt))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("status") in {"PASS", "PASS_PARTIAL"} else 1)


if __name__ == "__main__":
    main()
