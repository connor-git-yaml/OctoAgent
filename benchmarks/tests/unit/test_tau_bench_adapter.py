"""benchmarks/tests/unit/test_tau_bench_adapter.py — τ-bench adapter 测试.

覆盖:
- 15 task 分层抽样（FR-E02 6 类分布）
- ToolRegistry 临时注册 contextmanager + finally 清理（FR-E01）
- 异常路径 finally 仍清理（race 防御）
- W5 actions 字段名实测对齐
"""
from __future__ import annotations

import pytest

from octoagent.gateway.harness.tool_registry import ToolRegistry

from benchmarks.tiers.tier2.tau_bench_adapter import (
    STRATIFIED_SAMPLING_PLAN,
    TAU_BENCH_SCOPE_TAG,
    TAU_BENCH_TOOL_PREFIX,
    TauBenchAdapter,
    TauBenchScopeConflictError,
    TauBenchTaskMeta,
    _REGISTRY_LOCK,
    _bucket_for_task,
    stratified_sample,
    tau_bench_tool_scope,
)


# ============================================================
# 分层抽样
# ============================================================


class TestStratifiedSample:
    def test_sampling_plan_sums_to_15(self) -> None:
        """STRATIFIED_SAMPLING_PLAN 6 类 sum = 15（FR-E02）."""
        assert sum(STRATIFIED_SAMPLING_PLAN.values()) == 15
        assert set(STRATIFIED_SAMPLING_PLAN.keys()) == {
            "booking",
            "cancellation",
            "upgrade",
            "passenger",
            "baggage",
            "payment",
        }

    def test_bucket_for_task_first_action_when_no_contains(self) -> None:
        """无稀缺 contains-action → 按首要 action 分桶."""
        task = {
            "actions": [
                {"name": "book_reservation", "arguments": {}},
                {"name": "get_user_details", "arguments": {}},  # 非稀缺 contains
            ]
        }
        # 首要 = book_reservation → booking
        assert _bucket_for_task(task) == "booking"

    def test_bucket_for_task_contains_action_priority_over_first(self) -> None:
        """contains-action（稀缺桶）优先于首要 action：含 send_certificate → payment."""
        task = {
            "actions": [
                {"name": "book_reservation", "arguments": {}},  # 首要 action
                {"name": "send_certificate", "arguments": {}},  # 稀缺 contains → 优先
            ]
        }
        # 含 send_certificate → payment 桶（覆盖首要 action booking）
        assert _bucket_for_task(task) == "payment"

    def test_bucket_for_task_contains_action_fallback(self) -> None:
        """首要 action 不在 _FIRST_ACTION_BUCKETS → 按 contains 兜底."""
        task = {
            "actions": [
                {"name": "get_user_details", "arguments": {}},
                {"name": "update_reservation_baggages", "arguments": {}},
            ]
        }
        assert _bucket_for_task(task) == "baggage"

    def test_bucket_for_task_no_match_returns_none(self) -> None:
        """全部 action 不命中任一桶 → None."""
        task = {"actions": [{"name": "search_direct_flight", "arguments": {}}]}
        assert _bucket_for_task(task) is None

    def test_stratified_sample_returns_15_from_real_tasks(self) -> None:
        """对真实 50 个 airline task 跑分层抽样，应得 15 个 task meta（PoC §4 验证）."""
        from tau_bench.envs.airline.tasks import tasks as airline_tasks

        sampled = stratified_sample(list(airline_tasks))
        assert len(sampled) == 15

        # 验证 6 类分布
        by_bucket: dict[str, int] = {}
        for s in sampled:
            by_bucket[s.bucket] = by_bucket.get(s.bucket, 0) + 1
        assert by_bucket == STRATIFIED_SAMPLING_PLAN

    def test_stratified_sample_task_ids_unique_and_formatted(self) -> None:
        """task_id 格式 T2-TAU-<BUCKET>-<idx:03d>，全唯一."""
        from tau_bench.envs.airline.tasks import tasks as airline_tasks

        sampled = stratified_sample(list(airline_tasks))
        ids = [s.task_id for s in sampled]
        assert len(set(ids)) == len(ids)  # 唯一
        for s in sampled:
            assert s.task_id.startswith(f"T2-TAU-{s.bucket.upper()}-")

    def test_stratified_sample_w5_actions_field_real(self) -> None:
        """W5 实测：sampled.actions 字段是 list[dict] of {name, arguments}."""
        from tau_bench.envs.airline.tasks import tasks as airline_tasks

        sampled = stratified_sample(list(airline_tasks))
        for s in sampled:
            assert isinstance(s.actions, list)
            assert len(s.actions) > 0
            assert isinstance(s.actions[0], dict)
            assert "name" in s.actions[0]
            assert "arguments" in s.actions[0]


# ============================================================
# ToolRegistry contextmanager（FR-E01）
# ============================================================


class _FakeTauTool:
    """模拟 τ-bench tool（get_info() 返回 OpenAI function schema-like dict）."""

    def __init__(self, name: str, description: str = "test tool") -> None:
        self._name = name
        self._description = description

    def get_info(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self._name, "description": self._description},
        }


class TestTauBenchToolScope:
    def test_register_then_cleanup(self) -> None:
        """yield 中工具可见 → finally 清理后 0 残留（FR-E01 + race 防御）."""
        registry = ToolRegistry()
        fake_tools = [_FakeTauTool("book_reservation"), _FakeTauTool("calculate")]

        assert len(registry) == 0
        with tau_bench_tool_scope(registry, fake_tools) as registered_names:
            assert len(registry) == 2
            assert all(n.startswith(TAU_BENCH_TOOL_PREFIX) for n in registered_names)
            assert f"{TAU_BENCH_TOOL_PREFIX}book_reservation" in registry
            assert f"{TAU_BENCH_TOOL_PREFIX}calculate" in registry

        # 清理后 0 残留
        assert len(registry) == 0
        assert f"{TAU_BENCH_TOOL_PREFIX}book_reservation" not in registry

    def test_exception_in_yield_still_cleans_up(self) -> None:
        """yield 中抛异常 → finally 仍清理（race + leak 防御）."""
        registry = ToolRegistry()
        fake_tools = [_FakeTauTool("book_reservation")]

        with pytest.raises(RuntimeError):
            with tau_bench_tool_scope(registry, fake_tools):
                raise RuntimeError("simulated agent failure")

        # 即使 yield 中崩溃，finally 仍 deregister
        assert len(registry) == 0

    def test_scope_tag_in_metadata(self) -> None:
        """注册的 ToolEntry metadata 含 benchmark_scope tag（audit 用）."""
        registry = ToolRegistry()
        fake_tools = [_FakeTauTool("book_reservation")]

        with tau_bench_tool_scope(registry, fake_tools):
            entry = registry._entries[f"{TAU_BENCH_TOOL_PREFIX}book_reservation"]
            assert entry.metadata.get("benchmark_scope") == TAU_BENCH_SCOPE_TAG
            assert entry.toolset == TAU_BENCH_SCOPE_TAG

    def test_empty_tools_no_op(self) -> None:
        """空 tools list → 注册 0 个，正常进出 contextmanager."""
        registry = ToolRegistry()
        with tau_bench_tool_scope(registry, []) as names:
            assert names == []
        assert len(registry) == 0

    def test_codex_phase_b_high_1_lock_not_held_during_yield(self) -> None:
        """Codex Phase B review HIGH-1 修复回归: yield 期间不持 _REGISTRY_LOCK.

        修复前: `with _REGISTRY_LOCK:` 包住整个 yield → async runner 跨 await
        会阻塞 event loop (Phase D 8 并发卡死).
        修复后: lock 只包 register / deregister, yield 期间 release.
        """
        registry = ToolRegistry()
        fake_tools = [_FakeTauTool("book_reservation")]

        with tau_bench_tool_scope(registry, fake_tools):
            # yield 期间 lock 应该可被其他线程 acquire（非阻塞）
            # acquire(blocking=False) 立刻返回；True 表示成功（即锁是 free 状态）
            acquired = _REGISTRY_LOCK.acquire(blocking=False)
            assert acquired is True, "yield 期间 _REGISTRY_LOCK 不应被持有（HIGH-1）"
            _REGISTRY_LOCK.release()

        # 清理后 lock 也是 free
        acquired_after = _REGISTRY_LOCK.acquire(blocking=False)
        assert acquired_after is True
        _REGISTRY_LOCK.release()

    def test_codex_phase_b_high_2_conflict_detection_raises(self) -> None:
        """Codex Phase B review HIGH-2 修复回归: 注册前 fail-fast 冲突检测.

        修复前: ToolRegistry.register 覆盖语义 + 无 conflict check,
        会覆盖已存在的 production / 并发 task tool 并在 finally 删除.
        修复后: 注册前 check prefixed_name in registry → raise.
        """
        registry = ToolRegistry()
        fake_tools_first = [_FakeTauTool("book_reservation")]
        fake_tools_second = [_FakeTauTool("book_reservation")]

        # 第一个 scope 正常注册 + 不退出
        with tau_bench_tool_scope(registry, fake_tools_first):
            # 模拟并发：第二个 scope 尝试用同样 prefix 注册 → 冲突 raise
            with pytest.raises(TauBenchScopeConflictError) as exc:
                with tau_bench_tool_scope(registry, fake_tools_second):
                    pass  # 不应到达
            assert "book_reservation" in str(exc.value)

        # 第一个 scope 退出后清理完毕
        assert len(registry) == 0

    def test_codex_phase_b_high_2_scope_id_no_conflict(self) -> None:
        """Codex Phase B review HIGH-2 修复回归: scope_id 让并发 task 共享 registry 不冲突."""
        registry = ToolRegistry()
        fake_tools = [_FakeTauTool("book_reservation")]

        # 两个 task 各自 scope_id → 无冲突
        with tau_bench_tool_scope(registry, fake_tools, scope_id="run42") as names_a:
            with tau_bench_tool_scope(registry, fake_tools, scope_id="run43") as names_b:
                assert names_a != names_b
                # 名字分别是 tau_bench__run42__book_reservation / tau_bench__run43__...
                assert any("run42" in n for n in names_a)
                assert any("run43" in n for n in names_b)
                assert len(registry) == 2

        assert len(registry) == 0

    def test_codex_phase_b_high_2_register_failure_rollback(self) -> None:
        """Codex Phase B review HIGH-2 修复回归: 注册期失败回滚已注册的工具."""
        registry = ToolRegistry()
        # 预先注册一个冲突 entry，让第二轮 register 失败
        first_round = [_FakeTauTool("calculate")]
        with tau_bench_tool_scope(registry, first_round):
            # 此时 registry 含 tau_bench__calculate
            # 第二轮尝试注册 [book_reservation, calculate] → calculate 冲突 →
            # 已注册的 book_reservation 必须被回滚
            second_round = [_FakeTauTool("book_reservation"), _FakeTauTool("calculate")]
            with pytest.raises(TauBenchScopeConflictError):
                with tau_bench_tool_scope(registry, second_round):
                    pass
            # 验证回滚: book_reservation 不应残留
            assert f"{TAU_BENCH_TOOL_PREFIX}book_reservation" not in registry
            # 但第一轮的 calculate 还在
            assert f"{TAU_BENCH_TOOL_PREFIX}calculate" in registry
        # 第一轮也清理
        assert len(registry) == 0


# ============================================================
# Adapter 入口
# ============================================================


class TestTauBenchAdapter:
    def test_default_user_simulator_is_sonnet_46(self) -> None:
        """user_simulator_model 默认 claude-sonnet-4-6（FR-B05 GATE_DESIGN OQ-1）."""
        adapter = TauBenchAdapter()
        assert adapter.user_simulator_model == "claude-sonnet-4-6"

    def test_load_tasks_returns_50(self) -> None:
        """load_tasks 返回完整 50 个 airline task（PoC-H2 验证）."""
        adapter = TauBenchAdapter()
        tasks = adapter.load_tasks()
        assert len(tasks) == 50

    def test_load_tools_returns_14(self) -> None:
        """load_tools 返回 14 个 ALL_TOOLS（PoC §4 实测）."""
        adapter = TauBenchAdapter()
        tools = adapter.load_tools()
        assert len(tools) == 14

    def test_stratified_15_tasks_returns_15(self) -> None:
        """stratified_15_tasks 返回 15 个 TauBenchTaskMeta（FR-E02）."""
        adapter = TauBenchAdapter()
        sampled = adapter.stratified_15_tasks()
        assert len(sampled) == 15
        assert all(isinstance(s, TauBenchTaskMeta) for s in sampled)

    def test_codex_phase_b_med_6_shortage_raises(self) -> None:
        """Codex Phase B review MED-6 修复回归: 缺额 fail-fast (raise), 不 silent drop.

        修复前: stratified_15_tasks 返回部分样本时不检查 == 15, Daily Bench 分母错误.
        修复后: != 15 时 raise ValueError + 输出 actual/target 分桶详情.
        """
        adapter = TauBenchAdapter()
        # 用一个不足以覆盖所有桶的小 task 集触发缺额
        # 直接 monkey-patch adapter._tasks_cache 让它只有 1 个 task → 必然缺额
        adapter._tasks_cache = [
            {
                "user_id": "u1",
                "instruction": "test",
                "actions": [{"name": "book_reservation", "arguments": {}}],
            }
        ]
        with pytest.raises(ValueError) as exc:
            adapter.stratified_15_tasks()
        assert "缺额" in str(exc.value)
        assert "15" in str(exc.value)
