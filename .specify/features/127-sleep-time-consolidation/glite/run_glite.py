"""F127 G-lite — 真 LLM（DeepSeek-V3.2 via bench alias）跑巩固发现端验证。

背景（用户拍板 G→G-lite）：
- F127 全部单测/e2e 用注入 fake LLM——确定性编排已证，但"真 LLM 走一遍
  prompt→响应→解析→合法提议"从未发生过。
- G-lite 验证两件事：
  ①**管道真通**：真 LLM 响应能被 parse_llm_json_array + F127 组校验消化，
    产出合法 PENDING 候选 + PROPOSED 事件，且 C4 红线守住（源仍 CURRENT）；
  ②**提议质量下限**：植入的明显冗余事实组（3 条黑咖啡偏好小变体）能被找到，
    且提议只引用植入的冗余组（不拉不相关事实、无幻觉 id）。
- 强 model 质量评估（AC-8 完整版）归 M7 统一 OctoBench 方案，不在本脚本。

隔离性：
- core/memory SQLite 全部临时目录（每轮独立新库），**不碰 ~/.octoagent 生产数据**；
- 只借 ~/.octoagent 的 provider 配置（bench alias → siliconflow DeepSeek-V3.2）
  + .env 的 SILICONFLOW_API_KEY（脚本绝不打印 key）。
- model_alias 说明：发现端生产固定 CONSOLIDATION_MODEL_ALIAS="cheap"（实例配置
  = Qwen3.5-14B）；G-lite 按用户拍板控变量用 bench（DeepSeek-V3.2），故包一层
  alias 重定向 adapter——被测对象是 F127 管道，不是 alias 常量本身。
- temperature：openai_chat transport payload 不含 temperature 字段（provider
  默认值），与 OctoBench 跑法一致；故跑 ≥3 次看稳定性。

用法（worktree octoagent 目录下，venv editable 指向本 worktree src）：
    cd <worktree>/octoagent && uv run --no-sync python \
        ../.specify/features/127-sleep-time-consolidation/glite/run_glite.py [n_runs]

失败排查：API 调用全挂先查 SILICONFLOW_API_KEY / 网络（provider 瞬态重试
52320d7c 已在本分支，ReadError 自动重试），别误判成 F127 bug。
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

OCTOAGENT_HOME = Path.home() / ".octoagent"
ROOT_TASK_ID = "_memory_consolidation_root"
SCOPE = "agent-private/main"
BENCH_ALIAS = "bench"  # ~/.octoagent/octoagent.yaml: siliconflow / deepseek-ai/DeepSeek-V3.2


# ============================================================
# LLM 包装：alias 重定向 + 原始响应录制
# ============================================================


class BenchRecordingLLM:
    """包装 ProviderRouterMessageAdapter：

    - alias 重定向：发现端固定传 model_alias="cheap"（CONSOLIDATION_MODEL_ALIAS），
      G-lite 统一重定向到 bench（DeepSeek-V3.2，用户拍板的控变量 model）；
    - 录制原始响应文本 + 单次延迟（供 result.md 归档质量观察）。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.raw_responses: list[str] = []
        self.latencies_ms: list[int] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        **kwargs: Any,
    ) -> Any:
        start = time.monotonic()
        result = await self._inner.complete(messages, model_alias=BENCH_ALIAS)
        self.latencies_ms.append(int((time.monotonic() - start) * 1000))
        self.raw_responses.append(getattr(result, "content", "") or "")
        return result


# ============================================================
# 单轮运行（每轮独立临时库，互不污染 + 不受幂等账本跨轮影响）
# ============================================================


async def run_once(run_idx: int) -> dict[str, Any]:
    # 延迟 import：确保 dotenv 已在 main() 加载
    from octoagent.core.models.enums import EventType, TaskStatus
    from octoagent.core.models.task import RequesterInfo
    from octoagent.core.models.task import Task as TaskModel
    from octoagent.core.store import create_store_group
    from octoagent.gateway.services.consolidation_discovery import (
        ConsolidationDiscoveryService,
    )
    from octoagent.memory import MemoryPartition, MemoryService, WriteAction
    from octoagent.memory.models import ConsolidationCandidateStatus
    from octoagent.memory.store import ConsolidationStore
    from octoagent.memory.store.sqlite_init import init_memory_db
    from octoagent.provider.provider_router import ProviderRouter
    from octoagent.provider.router_message_adapter import ProviderRouterMessageAdapter

    report: dict[str, Any] = {"run_idx": run_idx}

    with tempfile.TemporaryDirectory(prefix=f"f127-glite-{run_idx}-") as tmp:
        tmp_path = Path(tmp)
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()

        # --- core store（event_store + root task FK 占位，同 review 测试 fixture）---
        sg = await create_store_group(str(tmp_path / "core.db"), str(artifacts))
        router: ProviderRouter | None = None
        memory_conn: aiosqlite.Connection | None = None
        try:
            now = datetime.now(UTC)
            await sg.task_store.create_task(
                TaskModel(
                    task_id=ROOT_TASK_ID,
                    created_at=now,
                    updated_at=now,
                    status=TaskStatus.SUCCEEDED,
                    title="F127 G-lite root",
                    thread_id="_memory_consolidation",
                    scope_id="",
                    requester=RequesterInfo(
                        channel="system", sender_id="memory_consolidation"
                    ),
                )
            )
            await sg.conn.commit()

            # --- memory store（独立临时库）---
            memory_conn = await aiosqlite.connect(str(tmp_path / "memory.db"))
            memory_conn.row_factory = aiosqlite.Row
            await init_memory_db(memory_conn)
            memory = MemoryService(memory_conn)

            # --- 植入事实：3 条黑咖啡偏好小变体（明显冗余组）+ 2 条不相关 ---
            async def seed(subject_key: str, content: str) -> str:
                result = await memory.fast_commit(
                    scope_id=SCOPE,
                    partition=MemoryPartition.PROFILE,
                    action=WriteAction.ADD,
                    subject_key=subject_key,
                    content=content,
                    confidence=1.0,
                )
                return result.sor_id or ""

            coffee_ids = {
                await seed("pref.coffee.style", "用户喜欢黑咖啡"),
                await seed("pref.coffee.morning", "Connor 早上习惯喝一杯黑咖啡"),
                await seed("pref.coffee.additions", "偏好：咖啡不加糖不加奶"),
            }
            unrelated_ids = {
                await seed("env.workdir", "用户的主工作目录在 ~/Desktop/workspace2"),
                await seed("pet.cat", "用户养了一只叫 Tofu 的猫"),
            }
            assert all(coffee_ids) and all(unrelated_ids), "seed 事实失败"

            # --- 真 LLM client（bench alias → DeepSeek-V3.2）---
            router = ProviderRouter(project_root=OCTOAGENT_HOME)
            llm = BenchRecordingLLM(ProviderRouterMessageAdapter(router))

            consol_store = ConsolidationStore(memory_conn)
            discovery = ConsolidationDiscoveryService(
                memory_service=memory,
                memory_store=memory._store,  # noqa: SLF001 — 同 review 测试用法
                consolidation_store=consol_store,
                event_store=sg.event_store,
                llm_client=llm,
            )

            start = time.monotonic()
            outcome = await discovery.discover_and_propose(
                run_id=f"glite-run-{run_idx}",
                scope_id=SCOPE,
                root_task_id=ROOT_TASK_ID,
                window_days=7,
                max_facts=50,
            )
            report["elapsed_ms"] = int((time.monotonic() - start) * 1000)
            report["llm_latencies_ms"] = llm.latencies_ms
            report["facts_reviewed"] = outcome.facts_reviewed
            report["proposals_made"] = outcome.proposals_made
            report["fallback"] = outcome.fallback
            report["raw_llm_responses"] = llm.raw_responses

            # --- 候选与事件核验 ---
            cands = await consol_store.list_candidates(scope_id=SCOPE)
            report["candidates"] = [
                {
                    "candidate_id": c.candidate_id,
                    "status": c.status.value,
                    "source_sor_ids": list(c.source_sor_ids),
                    "merged_content": c.merged_content,
                    "subject_key": c.subject_key,
                    "rationale": c.rationale,
                    "confidence": c.confidence,
                }
                for c in cands
            ]

            events = await sg.event_store.get_events_for_task(ROOT_TASK_ID)
            proposed_events = [
                e
                for e in events
                if e.type == EventType.MEMORY_CONSOLIDATION_PROPOSED
            ]

            # --- 硬断言（管道真通 + 质量下限）---
            checks: dict[str, bool] = {}
            checks["facts_reviewed_5"] = outcome.facts_reviewed == 5
            checks["no_fallback"] = not outcome.fallback
            checks["ge_1_proposal"] = outcome.proposals_made >= 1
            checks["all_pending"] = all(
                c.status == ConsolidationCandidateStatus.PENDING for c in cands
            )
            checks["all_merged_content_nonempty"] = all(
                c.merged_content.strip() for c in cands
            )
            # 质量下限：存在一个候选，其源恰在植入冗余组内（≥2 源由服务层保证）
            checks["exists_coffee_only_proposal"] = any(
                set(c.source_sor_ids) <= coffee_ids for c in cands
            )
            # 无幻觉 id（valid_ids 白名单本就挡；真 LLM 场景复核）
            all_planted = coffee_ids | unrelated_ids
            checks["no_hallucinated_ids"] = all(
                set(c.source_sor_ids) <= all_planted for c in cands
            )
            # C4 红线：发现端绝不 commit——全部 5 条源事实仍 CURRENT
            cur = await memory_conn.execute(
                "SELECT COUNT(*) AS n FROM memory_sor WHERE status = 'current'"
            )
            row = await cur.fetchone()
            checks["sources_still_current"] = bool(row and row["n"] == 5)
            # C2：每条候选恰有一条 PROPOSED 事件
            checks["proposed_events_match"] = (
                len(proposed_events) == outcome.proposals_made
            )

            # --- 质量观察（不作硬断言）---
            report["quality_observations"] = {
                "any_unrelated_pulled_in": any(
                    set(c.source_sor_ids) & unrelated_ids for c in cands
                ),
                "coffee_group_coverage": [
                    len(set(c.source_sor_ids) & coffee_ids)
                    for c in cands
                    if set(c.source_sor_ids) <= coffee_ids
                ],
                "confidences": [c.confidence for c in cands],
            }
            report["checks"] = checks
            report["pass"] = all(checks.values())
        finally:
            if router is not None:
                await router.aclose()
            if memory_conn is not None:
                await memory_conn.close()
            await sg.close()

    return report


# ============================================================
# 主入口
# ============================================================


async def main() -> int:
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    # 借生产 dotenv loader 加载 ~/.octoagent/.env（SILICONFLOW_API_KEY 进 environ，
    # override=False 不覆盖已有；key 值绝不打印）
    from octoagent.gateway.services.config.dotenv_loader import load_project_dotenv

    load_project_dotenv(project_root=OCTOAGENT_HOME, override=False)

    import os

    if not os.environ.get("SILICONFLOW_API_KEY", "").strip():
        print("FATAL: SILICONFLOW_API_KEY 未配置（查 ~/.octoagent/.env）", flush=True)
        return 2

    reports: list[dict[str, Any]] = []
    for i in range(1, n_runs + 1):
        print(f"\n===== G-lite run {i}/{n_runs} =====", flush=True)
        try:
            rep = await run_once(i)
        except Exception as exc:  # 环境级失败（网络/key）与 F127 断言失败分开呈现
            rep = {"run_idx": i, "pass": False, "error": repr(exc)}
        reports.append(rep)
        print(
            json.dumps(
                {k: v for k, v in rep.items() if k != "raw_llm_responses"},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            flush=True,
        )

    passed = sum(1 for r in reports if r.get("pass"))
    print(f"\n===== G-lite 汇总: {passed}/{n_runs} PASS =====", flush=True)

    # 原始 LLM 响应样例（result.md 归档用）
    for r in reports:
        for resp in r.get("raw_llm_responses", [])[:1]:
            print(f"\n--- run {r['run_idx']} 原始 LLM 响应 ---\n{resp}", flush=True)

    return 0 if passed == n_runs else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
