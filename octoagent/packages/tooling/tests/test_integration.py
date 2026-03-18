"""集成测试 -- 完整链路端到端验证

工具声明 -> @tool_contract -> reflect_tool_schema -> broker.register
-> broker.discover -> broker.execute -> Hook 链 -> 大输出裁切
-> 事件生成 -> 结果返回

覆盖 EC-2（同一工具并发调用独立执行）。
"""

from __future__ import annotations

import asyncio

from octoagent.tooling.broker import ToolBroker
from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.hooks import EventGenerationHook, LargeOutputHandler
from octoagent.tooling.models import (
    BeforeHookResult,
    ExecutionContext,
    FailMode,
    PermissionPreset,
    SideEffectLevel,
    ToolProfile,
)
from octoagent.tooling.schema import reflect_tool_schema

# ============================================================
# 集成测试用工具
# ============================================================


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_profile=ToolProfile.MINIMAL,
    tool_group="system",
)
async def greet(name: str) -> str:
    """生成问候语。

    Args:
        name: 被问候者姓名
    """
    return f"Hello, {name}!"


@tool_contract(
    side_effect_level=SideEffectLevel.NONE,
    tool_profile=ToolProfile.MINIMAL,
    tool_group="system",
)
async def generate_large_output(size: int) -> str:
    """生成指定大小的输出。

    Args:
        size: 输出字符数
    """
    return "A" * size


@tool_contract(
    side_effect_level=SideEffectLevel.IRREVERSIBLE,
    tool_profile=ToolProfile.STANDARD,
    tool_group="filesystem",
)
async def dangerous_op(target: str) -> str:
    """模拟不可逆操作。

    Args:
        target: 操作目标
    """
    return f"Executed on {target}"


# ============================================================
# 集成测试类
# ============================================================


def _make_context(profile: ToolProfile = ToolProfile.STANDARD) -> ExecutionContext:
    return ExecutionContext(
        task_id="integration-t1",
        trace_id="integration-tr1",
        caller="integration_test",
        profile=profile,
    )


class TestFullPipeline:
    """完整链路端到端测试"""

    async def test_declare_reflect_register_discover_execute(self, mock_event_store) -> None:
        """声明 -> 反射 -> 注册 -> 发现 -> 执行 -> 结果"""
        # 1. Schema Reflection
        meta = reflect_tool_schema(greet)
        assert meta.name == "greet"
        assert meta.side_effect_level == SideEffectLevel.NONE
        assert "name" in meta.parameters_json_schema.get("properties", {})

        # 2. 注册
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(meta, greet)

        # 3. 发现
        tools = await broker.discover(profile=ToolProfile.MINIMAL)
        assert len(tools) == 1
        assert tools[0].name == "greet"

        # 4. 执行
        result = await broker.execute("greet", {"name": "World"}, _make_context())

        # 5. 验证结果
        assert result.is_error is False
        assert result.output == "Hello, World!"
        assert result.duration > 0

        # 6. 验证事件
        events = mock_event_store.events
        event_types = [e.type.value for e in events]
        assert "TOOL_CALL_STARTED" in event_types
        assert "TOOL_CALL_COMPLETED" in event_types

    async def test_full_pipeline_with_hooks(self, mock_event_store, mock_artifact_store) -> None:
        """完整链路含 Hook 链 + 大输出裁切 + 事件生成"""
        # 1. 注册工具
        meta = reflect_tool_schema(generate_large_output)
        broker = ToolBroker(
            event_store=mock_event_store,
            artifact_store=mock_artifact_store,
        )
        await broker.register(meta, generate_large_output)

        # 2. 注册 Hook
        event_hook = EventGenerationHook(event_store=mock_event_store)
        large_output_hook = LargeOutputHandler(
            artifact_store=mock_artifact_store,
        )
        broker.add_hook(event_hook)
        broker.add_hook(large_output_hook)

        # 3. 执行生成大输出（800 > 500 默认阈值）
        result = await broker.execute("generate_large_output", {"size": 800}, _make_context())

        # 4. 验证裁切
        assert result.truncated is True
        assert result.artifact_ref is not None
        assert "artifact:" in result.output
        assert len(result.output) < 500

        # 5. 验证 ArtifactStore 存储了完整内容
        assert len(mock_artifact_store.contents) == 1
        stored_content = list(mock_artifact_store.contents.values())[0]
        assert len(stored_content) == 800  # "A" * 800 的 UTF-8 字节

        # 6. 验证事件（Broker 内联 + EventGenerationHook）
        events = mock_event_store.events
        assert len(events) >= 2  # 至少 STARTED + COMPLETED

    async def test_full_pipeline_small_output_no_truncation(
        self, mock_event_store, mock_artifact_store
    ) -> None:
        """小输出不裁切"""
        meta = reflect_tool_schema(greet)
        broker = ToolBroker(
            event_store=mock_event_store,
            artifact_store=mock_artifact_store,
        )
        await broker.register(meta, greet)

        large_output_hook = LargeOutputHandler(
            artifact_store=mock_artifact_store,
        )
        broker.add_hook(large_output_hook)

        result = await broker.execute("greet", {"name": "World"}, _make_context())

        assert result.truncated is False
        assert result.artifact_ref is None
        assert result.output == "Hello, World!"
        assert len(mock_artifact_store.contents) == 0


class TestFR010aIntegration:
    """Feature 061: FR-010a 硬拒绝已移除，权限检查由 Hook Chain 驱动"""

    async def test_irreversible_allowed_without_hooks_feature_061(
        self, mock_event_store
    ) -> None:
        """Feature 061: 无 Hook 注册时 irreversible 工具可正常执行

        FR-010a 硬拒绝已在 Feature 061 中移除，权限检查完全由
        PresetBeforeHook（Hook Chain）驱动。
        """
        meta = reflect_tool_schema(dangerous_op)
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(meta, dangerous_op)

        result = await broker.execute(
            "dangerous_op", {"target": "/data"}, _make_context()
        )

        # Feature 061: 无 Hook 时不再硬拒绝
        assert result.is_error is False
        assert "Executed on /data" in result.output

    async def test_irreversible_allowed_with_checkpoint(self, mock_event_store) -> None:
        """有 PolicyCheckpoint 时，irreversible 工具可执行"""
        meta = reflect_tool_schema(dangerous_op)
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(meta, dangerous_op)

        # 注册 PolicyCheckpoint（fail_mode=closed 的 before hook）
        class PolicyCheckpointHook:
            @property
            def name(self) -> str:
                return "policy_checkpoint"

            @property
            def priority(self) -> int:
                return 0

            @property
            def fail_mode(self) -> FailMode:
                return FailMode.CLOSED

            async def before_execute(self, tool_meta, args, context):
                return BeforeHookResult(proceed=True)

        broker.add_hook(PolicyCheckpointHook())

        result = await broker.execute("dangerous_op", {"target": "/data"}, _make_context())

        assert result.is_error is False
        assert "Executed on /data" in result.output


class TestConcurrentExecution:
    """EC-2: 同一工具并发调用独立执行"""

    async def test_concurrent_calls_independent(self, mock_event_store) -> None:
        """并发调用同一工具，各自独立返回"""
        meta = reflect_tool_schema(greet)
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(meta, greet)

        # 并发执行 3 个调用
        results = await asyncio.gather(
            broker.execute("greet", {"name": "Alice"}, _make_context()),
            broker.execute("greet", {"name": "Bob"}, _make_context()),
            broker.execute("greet", {"name": "Charlie"}, _make_context()),
        )

        outputs = {r.output for r in results}
        assert outputs == {
            "Hello, Alice!",
            "Hello, Bob!",
            "Hello, Charlie!",
        }
        # 每个调用都不是错误
        assert all(not r.is_error for r in results)


# ============================================================
# Feature 061 T-010: Phase 1 集成测试 — 权限 Preset 完整链路
# ============================================================


class TestPresetE2EFullPipeline:
    """Phase 1 集成: Agent 创建 → 工具注册 → Hook Chain → Preset 检查 → 事件审计

    覆盖 US-001 场景 1-5, 8, 9-10 + SC-002 + SC-003。
    """

    async def test_us001_s1_minimal_allows_none_blocks_reversible(
        self, mock_event_store
    ) -> None:
        """US-001 场景 1: MINIMAL Preset 允许只读、拦截可逆操作"""
        from octoagent.tooling.hooks import ApprovalOverrideHook, PresetBeforeHook

        cache = _MockOverrideCache()
        broker = ToolBroker(event_store=mock_event_store)
        for fn in [greet, dangerous_op]:
            await broker.register(reflect_tool_schema(fn), fn)

        broker.add_hook(ApprovalOverrideHook(cache=cache, event_store=mock_event_store))
        broker.add_hook(PresetBeforeHook(event_store=mock_event_store, override_cache=cache))

        ctx = ExecutionContext(
            task_id="t-us001-s1",
            trace_id="tr-us001-s1",
            caller="test",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.MINIMAL,
            agent_runtime_id="agent-minimal",
        )

        # NONE 工具（greet）→ allow
        r1 = await broker.execute("greet", {"name": "World"}, ctx)
        assert r1.is_error is False
        assert "Hello, World!" in r1.output

        # IRREVERSIBLE 工具（dangerous_op）→ ask
        r2 = await broker.execute("dangerous_op", {"target": "/data"}, ctx)
        assert r2.is_error is True
        assert r2.error.startswith("ask:")

    async def test_us001_s2_normal_allows_reversible_blocks_irreversible(
        self, mock_event_store
    ) -> None:
        """US-001 场景 2: NORMAL Preset 允许只读+可逆、拦截不可逆"""
        from octoagent.tooling.hooks import ApprovalOverrideHook, PresetBeforeHook

        @tool_contract(
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_profile=ToolProfile.STANDARD,
            tool_group="filesystem",
        )
        async def write_file(path: str, content: str) -> str:
            """写入文件。

            Args:
                path: 路径
                content: 内容
            """
            return f"wrote {path}"

        cache = _MockOverrideCache()
        broker = ToolBroker(event_store=mock_event_store)
        for fn in [greet, write_file, dangerous_op]:
            await broker.register(reflect_tool_schema(fn), fn)

        broker.add_hook(ApprovalOverrideHook(cache=cache, event_store=mock_event_store))
        broker.add_hook(PresetBeforeHook(event_store=mock_event_store, override_cache=cache))

        ctx = ExecutionContext(
            task_id="t-us001-s2",
            trace_id="tr-us001-s2",
            caller="test",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.NORMAL,
            agent_runtime_id="agent-normal",
        )

        # NONE → allow
        r1 = await broker.execute("greet", {"name": "World"}, ctx)
        assert r1.is_error is False

        # REVERSIBLE → allow
        r2 = await broker.execute("write_file", {"path": "/tmp/a", "content": "x"}, ctx)
        assert r2.is_error is False
        assert "wrote /tmp/a" in r2.output

        # IRREVERSIBLE → ask
        r3 = await broker.execute("dangerous_op", {"target": "/data"}, ctx)
        assert r3.is_error is True
        assert "ask:" in r3.error

    async def test_us001_s3_full_allows_all(
        self, mock_event_store
    ) -> None:
        """US-001 场景 3: FULL Preset 允许所有操作包括不可逆"""
        from octoagent.tooling.hooks import ApprovalOverrideHook, PresetBeforeHook

        cache = _MockOverrideCache()
        broker = ToolBroker(event_store=mock_event_store)
        for fn in [greet, dangerous_op]:
            await broker.register(reflect_tool_schema(fn), fn)

        broker.add_hook(ApprovalOverrideHook(cache=cache, event_store=mock_event_store))
        broker.add_hook(PresetBeforeHook(event_store=mock_event_store, override_cache=cache))

        ctx = ExecutionContext(
            task_id="t-us001-s3",
            trace_id="tr-us001-s3",
            caller="test",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.FULL,
            agent_runtime_id="agent-full",
        )

        # 全部放行
        r1 = await broker.execute("greet", {"name": "World"}, ctx)
        assert r1.is_error is False

        r2 = await broker.execute("dangerous_op", {"target": "/data"}, ctx)
        assert r2.is_error is False
        assert "Executed on /data" in r2.output

    async def test_us001_s4_ask_contains_tool_and_level_info(
        self, mock_event_store
    ) -> None:
        """US-001 场景 4: ask 拒绝信息包含工具名和副作用等级"""
        from octoagent.tooling.hooks import PresetBeforeHook

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(dangerous_op), dangerous_op)
        broker.add_hook(PresetBeforeHook(event_store=mock_event_store))

        ctx = ExecutionContext(
            task_id="t-us001-s4",
            trace_id="tr-us001-s4",
            caller="test",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.NORMAL,
        )
        result = await broker.execute("dangerous_op", {"target": "/data"}, ctx)
        assert result.is_error is True
        assert "ask:preset_denied:dangerous_op" in result.error
        assert "irreversible" in result.error

    async def test_us001_s5_ask_is_soft_deny_not_hard_deny(
        self, mock_event_store
    ) -> None:
        """US-001 场景 5: ask 是 soft deny，error 以 'ask:' 前缀区分"""
        from octoagent.tooling.hooks import PresetBeforeHook

        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(dangerous_op), dangerous_op)
        broker.add_hook(PresetBeforeHook(event_store=mock_event_store))

        ctx = ExecutionContext(
            task_id="t-us001-s5",
            trace_id="tr-us001-s5",
            caller="test",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.MINIMAL,
        )
        result = await broker.execute("dangerous_op", {"target": "/sys"}, ctx)
        assert result.is_error is True
        # soft deny 标志: error 以 "ask:" 开头
        assert result.error.startswith("ask:")

    async def test_us001_s8_subagent_inherits_worker_preset(
        self, mock_event_store
    ) -> None:
        """US-001 场景 8: Subagent 继承 Worker 的 Preset

        验证方式: 使用相同 permission_preset 值模拟继承关系，
        Worker 和 Subagent 对同一工具的权限决策一致。
        """
        from octoagent.tooling.hooks import ApprovalOverrideHook, PresetBeforeHook

        cache = _MockOverrideCache()
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(dangerous_op), dangerous_op)
        broker.add_hook(ApprovalOverrideHook(cache=cache, event_store=mock_event_store))
        broker.add_hook(PresetBeforeHook(event_store=mock_event_store, override_cache=cache))

        # Worker 使用 NORMAL preset
        worker_ctx = ExecutionContext(
            task_id="t-us001-s8-w",
            trace_id="tr-us001-s8-w",
            caller="worker-alpha",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.NORMAL,
            agent_runtime_id="worker-alpha",
        )
        # Subagent 继承同一 preset
        subagent_ctx = ExecutionContext(
            task_id="t-us001-s8-s",
            trace_id="tr-us001-s8-s",
            caller="subagent-alpha-1",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.NORMAL,  # 继承 Worker
            agent_runtime_id="subagent-alpha-1",
        )

        r_worker = await broker.execute("dangerous_op", {"target": "/data"}, worker_ctx)
        r_subagent = await broker.execute("dangerous_op", {"target": "/data"}, subagent_ctx)

        # 两者行为一致: 都 ask（NORMAL + IRREVERSIBLE）
        assert r_worker.is_error is True
        assert r_worker.error.startswith("ask:")
        assert r_subagent.is_error is True
        assert r_subagent.error.startswith("ask:")

    async def test_us001_s9_s10_default_preset_assignment(
        self, mock_event_store
    ) -> None:
        """US-001 场景 9-10: 默认 Preset 分配规则

        Butler → FULL, Worker → NORMAL (default)。
        验证不同默认 Preset 对同一工具的不同权限决策。
        """
        from octoagent.tooling.hooks import ApprovalOverrideHook, PresetBeforeHook

        cache = _MockOverrideCache()
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(dangerous_op), dangerous_op)
        broker.add_hook(ApprovalOverrideHook(cache=cache, event_store=mock_event_store))
        broker.add_hook(PresetBeforeHook(event_store=mock_event_store, override_cache=cache))

        # Butler: 默认 FULL
        butler_ctx = ExecutionContext(
            task_id="t-butler",
            trace_id="tr-butler",
            caller="butler",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.FULL,
            agent_runtime_id="butler-main",
        )
        # Worker: 默认 NORMAL
        worker_ctx = ExecutionContext(
            task_id="t-worker",
            trace_id="tr-worker",
            caller="worker-ops",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.NORMAL,
            agent_runtime_id="worker-ops",
        )

        r_butler = await broker.execute("dangerous_op", {"target": "/sys"}, butler_ctx)
        r_worker = await broker.execute("dangerous_op", {"target": "/sys"}, worker_ctx)

        # Butler FULL → allow
        assert r_butler.is_error is False
        assert "Executed on /sys" in r_butler.output

        # Worker NORMAL + IRREVERSIBLE → ask
        assert r_worker.is_error is True
        assert r_worker.error.startswith("ask:")

    async def test_sc002_preset_hook_latency_under_1ms(
        self, mock_event_store
    ) -> None:
        """SC-002: PresetBeforeHook 检查延迟 <1ms"""
        import time

        from octoagent.tooling.hooks import PresetBeforeHook

        hook = PresetBeforeHook(event_store=mock_event_store)
        meta = reflect_tool_schema(greet)
        ctx = ExecutionContext(
            task_id="t-perf",
            trace_id="tr-perf",
            caller="test",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.NORMAL,
        )

        # 预热
        await hook.before_execute(meta, {}, ctx)
        mock_event_store.events.clear()

        # 测量 100 次的平均延迟
        start = time.perf_counter_ns()
        for _ in range(100):
            await hook.before_execute(meta, {}, ctx)
        elapsed_ns = time.perf_counter_ns() - start

        avg_us = elapsed_ns / 100 / 1_000  # 平均微秒
        assert avg_us < 1_000, f"PresetBeforeHook 平均延迟 {avg_us:.1f}us 超过 1ms"

    async def test_sc003_all_tool_calls_produce_preset_check_events(
        self, mock_event_store
    ) -> None:
        """SC-003: 100% 工具调用经过 Preset 检查，每次生成事件"""
        from octoagent.tooling.hooks import ApprovalOverrideHook, PresetBeforeHook

        cache = _MockOverrideCache()
        broker = ToolBroker(event_store=mock_event_store)
        for fn in [greet, dangerous_op]:
            await broker.register(reflect_tool_schema(fn), fn)

        broker.add_hook(ApprovalOverrideHook(cache=cache, event_store=mock_event_store))
        broker.add_hook(PresetBeforeHook(event_store=mock_event_store, override_cache=cache))

        ctx = ExecutionContext(
            task_id="t-audit",
            trace_id="tr-audit",
            caller="test",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.NORMAL,
            agent_runtime_id="agent-audit",
        )

        # 执行 3 次工具调用（2 个 allow + 1 个 ask）
        await broker.execute("greet", {"name": "A"}, ctx)
        await broker.execute("greet", {"name": "B"}, ctx)
        await broker.execute("dangerous_op", {"target": "x"}, ctx)

        # 审计: 每次调用都有 PRESET_CHECK 事件
        preset_events = [
            e for e in mock_event_store.events if e.type == "PRESET_CHECK"
        ]
        assert len(preset_events) == 3, (
            f"期望 3 个 PRESET_CHECK 事件，实际 {len(preset_events)}: "
            f"{[e.type for e in mock_event_store.events]}"
        )

    async def test_always_override_bypasses_preset_in_full_pipeline(
        self, mock_event_store
    ) -> None:
        """集成: always 覆盖后 Preset 检查被跳过，工具正常执行"""
        from octoagent.tooling.hooks import ApprovalOverrideHook, PresetBeforeHook

        cache = _MockOverrideCache()
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(dangerous_op), dangerous_op)

        broker.add_hook(ApprovalOverrideHook(cache=cache, event_store=mock_event_store))
        broker.add_hook(PresetBeforeHook(event_store=mock_event_store, override_cache=cache))

        ctx = ExecutionContext(
            task_id="t-override",
            trace_id="tr-override",
            caller="test",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.NORMAL,
            agent_runtime_id="agent-override",
        )

        # 无覆盖 → ask
        r1 = await broker.execute("dangerous_op", {"target": "/data"}, ctx)
        assert r1.is_error is True
        assert r1.error.startswith("ask:")

        # 设置 always 覆盖
        cache.set("agent-override", "dangerous_op")

        # 有覆盖 → allow
        r2 = await broker.execute("dangerous_op", {"target": "/data"}, ctx)
        assert r2.is_error is False
        assert "Executed on /data" in r2.output

        # 验证 APPROVAL_OVERRIDE_HIT 事件
        override_events = [
            e for e in mock_event_store.events if e.type == "APPROVAL_OVERRIDE_HIT"
        ]
        assert len(override_events) >= 1

    async def test_agent_isolation_in_full_pipeline(
        self, mock_event_store
    ) -> None:
        """集成: 不同 Agent 实例的权限检查和覆盖互相隔离"""
        from octoagent.tooling.hooks import ApprovalOverrideHook, PresetBeforeHook

        cache = _MockOverrideCache()
        broker = ToolBroker(event_store=mock_event_store)
        await broker.register(reflect_tool_schema(dangerous_op), dangerous_op)

        broker.add_hook(ApprovalOverrideHook(cache=cache, event_store=mock_event_store))
        broker.add_hook(PresetBeforeHook(event_store=mock_event_store, override_cache=cache))

        # Agent A: NORMAL + always 覆盖
        cache.set("agent-A", "dangerous_op")
        ctx_a = ExecutionContext(
            task_id="t-iso-a",
            trace_id="tr-iso-a",
            caller="test",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.NORMAL,
            agent_runtime_id="agent-A",
        )
        # Agent B: NORMAL + 无覆盖
        ctx_b = ExecutionContext(
            task_id="t-iso-b",
            trace_id="tr-iso-b",
            caller="test",
            profile=ToolProfile.PRIVILEGED,
            permission_preset=PermissionPreset.NORMAL,
            agent_runtime_id="agent-B",
        )

        # Agent A → allow（覆盖命中）
        r_a = await broker.execute("dangerous_op", {"target": "x"}, ctx_a)
        assert r_a.is_error is False

        # Agent B → ask（无覆盖）
        r_b = await broker.execute("dangerous_op", {"target": "x"}, ctx_b)
        assert r_b.is_error is True
        assert r_b.error.startswith("ask:")


class _MockOverrideCache:
    """T-010 测试用 ApprovalOverrideCache"""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], bool] = {}

    def has(self, agent_runtime_id: str, tool_name: str) -> bool:
        return self._data.get((agent_runtime_id, tool_name), False)

    def set(self, agent_runtime_id: str, tool_name: str) -> None:
        self._data[(agent_runtime_id, tool_name)] = True

    def remove(self, agent_runtime_id: str, tool_name: str) -> None:
        self._data.pop((agent_runtime_id, tool_name), None)


class TestMultiToolBroker:
    """多工具注册和发现"""

    async def test_multiple_tools_registered(self, mock_event_store) -> None:
        """多工具注册后可按 profile/group 发现"""
        broker = ToolBroker(event_store=mock_event_store)

        # 注册 3 个工具
        for tool_fn in [greet, generate_large_output, dangerous_op]:
            meta = reflect_tool_schema(tool_fn)
            await broker.register(meta, tool_fn)

        # 全部发现
        all_tools = await broker.discover()
        assert len(all_tools) == 3

        # minimal 只能发现 2 个（greet + generate_large_output）
        minimal_tools = await broker.discover(profile=ToolProfile.MINIMAL)
        assert len(minimal_tools) == 2

        # standard 发现全部 3 个
        standard_tools = await broker.discover(profile=ToolProfile.STANDARD)
        assert len(standard_tools) == 3

        # group 过滤
        system_tools = await broker.discover(group="system")
        assert len(system_tools) == 2
        filesystem_tools = await broker.discover(group="filesystem")
        assert len(filesystem_tools) == 1

    async def test_unregister_and_rediscover(self, mock_event_store) -> None:
        """注销后不再可发现"""
        broker = ToolBroker(event_store=mock_event_store)

        meta = reflect_tool_schema(greet)
        await broker.register(meta, greet)

        assert len(await broker.discover()) == 1

        result = await broker.unregister("greet")
        assert result is True

        assert len(await broker.discover()) == 0

        # 执行已注销的工具应返回错误
        exec_result = await broker.execute("greet", {"name": "World"}, _make_context())
        assert exec_result.is_error is True
        assert "not found" in exec_result.error
