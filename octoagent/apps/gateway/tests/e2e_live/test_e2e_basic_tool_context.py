"""F087 P3 smoke 域 #1/#2/#3：工具调用基础 / USER.md 全链路 / Context 冻结快照。

设计原则（**真集成层**——P3 Codex Finding 1 修复）：
1. **真跑 OctoHarness 全 11 段 bootstrap**——验证完整 harness 装配链路
2. **真走 ``app.state.tool_broker.execute(...)`` 路径**——验证 OctoHarness 的
   ToolDeps 注入 / 工具注册 / broker wiring 真实端到端；不再绕过 broker 直调
   handler helper（绕过会让 broker wiring 漂移时 smoke 仍 PASS，P3 集成层
   退化为 handler 单元回归测试）
3. **不真打 Codex OAuth LLM**（仅 T-P3-3 frozen prefix 验证 SnapshotStore
   语义，不需要 LLM）——避免 P3 阶段被 LLM 真实响应不确定性卡住；真打 LLM 留
   到 P4 域 #5 (Perplexity MCP) 主路径
4. 每个 case ≥ 2 独立断言点（spec FR-11 锁）

case 列表：
- T-P3-1 域 #1 工具调用基础：tool_broker.execute("user_profile.update", add) →
  WriteResult.status=written + events 含 MEMORY_ENTRY_ADDED + 注册表自检
- T-P3-2 域 #2 USER.md 全链路：tool_broker.execute 后 USER.md 含特定内容 +
  WriteResult.target=user_md_path + ThreatScanner.passed (status != rejected)
- T-P3-3 域 #3 Context 冻结快照：两次 tool_broker.execute 后 snapshot.format_for_system_prompt()
  保持冻结副本一致 (sha256 不变) + USER.md 真实磁盘已更新（双层语义）
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest


pytestmark = [pytest.mark.e2e_smoke, pytest.mark.e2e_live]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_full_preset_context(task_id: str) -> Any:
    """构造 PermissionPreset.FULL 的 ExecutionContext（IRREVERSIBLE 工具直放）。

    P3 Codex Finding 1：smoke 域目标是验证 broker wiring + tool 注册完整性，
    不验证 ApprovalGate（域 #12 单独验证）；用 FULL preset 让 IRREVERSIBLE
    工具直接放行，避免触发 approval。
    """
    from octoagent.tooling.models import ExecutionContext, PermissionPreset

    return ExecutionContext(
        task_id=task_id,
        trace_id=task_id,
        caller="e2e_smoke",
        permission_preset=PermissionPreset.FULL,
    )


@pytest.fixture
async def bootstrapped_harness(octo_harness_e2e: dict[str, Any]) -> dict[str, Any]:
    """真跑 OctoHarness.bootstrap 全 11 段，给 P3 case 使用。

    复用 octo_harness_e2e fixture（已注入 4 DI），加 bootstrap 调用。
    teardown 由 octo_harness_e2e 处理 shutdown。
    """
    harness = octo_harness_e2e["harness"]
    app = octo_harness_e2e["app"]

    # 在 bootstrap 前把 local-instance 模板复制到 e2e tmp project_root
    # 否则 _build_runtime_alias_registry 拿不到 alias 配置（即便 P3 不真打 LLM
    # 也需要 yaml 存在，否则 _bootstrap_llm 后续会报错）
    project_root = octo_harness_e2e["project_root"]

    # 从 fixtures 模板根算路径
    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )
    if (fixtures_root / "octoagent.yaml.template").exists():
        from apps.gateway.tests.e2e_live.helpers.factories import (
            copy_local_instance_template,
        )
        copy_local_instance_template(fixtures_root, project_root)

    await harness.bootstrap(app)
    harness.commit_to_app(app)

    return {
        "harness": harness,
        "app": app,
        "project_root": project_root,
    }


# ---------------------------------------------------------------------------
# 域 #1：工具调用基础（T-P3-1）
# ---------------------------------------------------------------------------


async def test_domain_1_basic_tool_call_writes_user_md_and_emits_event(
    bootstrapped_harness: dict[str, Any],
) -> None:
    """域 #1：tool_broker.execute("user_profile.update", add) 真路径验证。

    断言（≥ 2 独立点 + 1 注册表自检）：
    0. (前置自检) ``app.state.tool_broker._registry`` 含 "user_profile.update"
       —— 验证 OctoHarness bootstrap 真实注册了工具（broker wiring 完整）
    1. ToolResult.is_error == False + WriteResult.status == "written" +
       WriteResult.target == USER.md 路径
    2. events 表含 MEMORY_ENTRY_ADDED 事件 + TOOL_CALL_STARTED 事件
       （走 broker 真路径才会有 TOOL_CALL_STARTED）
    """
    from octoagent.core.models.enums import EventType

    from apps.gateway.tests.e2e_live.helpers.factories import _ensure_audit_task

    app = bootstrapped_harness["app"]
    project_root = bootstrapped_harness["project_root"]
    sg = app.state.store_group
    tool_broker = app.state.tool_broker

    # 前置自检：OctoHarness 真实注册了 user_profile.update
    assert "user_profile.update" in tool_broker._registry, (
        f"域#1 前置: tool_broker._registry 应含 'user_profile.update'，"
        f"实际注册: {sorted(tool_broker._registry.keys())[:5]}..."
    )

    test_task_id = "_e2e_domain_1_task"
    await _ensure_audit_task(sg, test_task_id)
    # user_profile_tools._emit_event 内部用独立 ContextVar
    # (ExecutionRuntimeContext)，broker.execute 并不绑定它——所以
    # MEMORY_ENTRY_ADDED 事件会走 fallback 到 "_user_profile_audit" 占位 task_id
    # （工具内部 audit 模式）。这个二分语义在生产路径是 worker_runtime 包绑
    # bind_execution_context 后再调 broker，此处 e2e smoke 不模拟 worker_runtime 层。
    await _ensure_audit_task(sg, "_user_profile_audit")

    # 真走 broker.execute 路径（非 handler 直调）
    test_content = "F087 P3 域#1：用户偏好——喜欢中文沟通"
    ctx = _build_full_preset_context(test_task_id)
    tool_result = await tool_broker.execute(
        tool_name="user_profile.update",
        args={"operation": "add", "content": test_content},
        context=ctx,
    )

    # 断言 1：ToolResult 非错 + WriteResult 状态 + target
    assert not tool_result.is_error, (
        f"域#1: ToolResult 应 is_error=False，实际 error={tool_result.error}"
    )
    # ToolResult.output 是 model_dump_json() 序列化的 WriteResult JSON
    payload = json.loads(tool_result.output) if tool_result.output else {}
    assert payload.get("status") == "written", (
        f"域#1: WriteResult.status 应为 'written'，实际: {payload.get('status')} / "
        f"reason={payload.get('reason')}"
    )
    user_md = project_root / "behavior" / "system" / "USER.md"
    assert payload.get("target") == str(user_md), (
        f"域#1: WriteResult.target 应为 USER.md 路径 ({user_md})，"
        f"实际: {payload.get('target')}"
    )

    # 断言 2A：broker 路径专属事件 TOOL_CALL_STARTED 必须写入 broker context.task_id
    events_at_broker_task = await sg.event_store.get_events_for_task(test_task_id)
    started_events = [
        e for e in events_at_broker_task if e.type == EventType.TOOL_CALL_STARTED
    ]
    assert started_events, (
        f"域#1: events[task={test_task_id}] 应包含至少 1 条 TOOL_CALL_STARTED 事件"
        "（broker.execute 真路径专属——直调 handler 不会写）"
    )
    assert started_events[-1].payload.get("tool_name") == "user_profile.update", (
        f"域#1: TOOL_CALL_STARTED.payload.tool_name 应为 'user_profile.update'，"
        f"实际: {started_events[-1].payload.get('tool_name')}"
    )

    # 断言 2B：handler 内部 audit 事件 MEMORY_ENTRY_ADDED（写入 _user_profile_audit）
    events_at_audit = await sg.event_store.get_events_for_task("_user_profile_audit")
    added_events = [e for e in events_at_audit if e.type == EventType.MEMORY_ENTRY_ADDED]
    assert added_events, (
        "域#1: events[task=_user_profile_audit] 应包含至少 1 条 MEMORY_ENTRY_ADDED 事件"
    )
    assert added_events[-1].payload.get("tool") == "user_profile.update"

    # 隐式：USER.md 真的写入了
    assert test_content in user_md.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 域 #2：USER.md 全链路（T-P3-2）
# ---------------------------------------------------------------------------


async def test_domain_2_user_md_full_pipeline_threat_scanner_passes(
    bootstrapped_harness: dict[str, Any],
) -> None:
    """域 #2：USER.md 写入全链路 + ThreatScanner 不拦截良性内容（broker 真路径）。

    断言（≥ 2 独立点）：
    1. ToolResult.is_error == False + WriteResult.status == "written" +
       WriteResult.blocked == False
    2. USER.md 真实磁盘内容包含写入字符串
    """
    from apps.gateway.tests.e2e_live.helpers.factories import _ensure_audit_task

    app = bootstrapped_harness["app"]
    project_root = bootstrapped_harness["project_root"]
    sg = app.state.store_group
    tool_broker = app.state.tool_broker

    test_task_id = "_e2e_domain_2_task"
    await _ensure_audit_task(sg, test_task_id)

    user_md = project_root / "behavior" / "system" / "USER.md"
    user_md.parent.mkdir(parents=True, exist_ok=True)

    # 良性内容（不命中任何 threat pattern）
    benign_content = "时区：Asia/Shanghai；语言偏好：zh-CN"
    ctx = _build_full_preset_context(test_task_id)
    tool_result = await tool_broker.execute(
        tool_name="user_profile.update",
        args={"operation": "add", "content": benign_content},
        context=ctx,
    )

    # 断言 1：ThreatScanner 不拦截良性内容
    assert not tool_result.is_error, (
        f"域#2: ToolResult 应 is_error=False，实际 error={tool_result.error}"
    )
    payload = json.loads(tool_result.output) if tool_result.output else {}
    assert payload.get("status") == "written", (
        f"域#2: 良性内容应通过 ThreatScanner，实际 status={payload.get('status')} / "
        f"reason={payload.get('reason')}"
    )
    assert payload.get("blocked") is False, (
        f"域#2: WriteResult.blocked 应为 False（良性内容），实际: {payload.get('blocked')}"
    )

    # 断言 2：USER.md 磁盘内容包含写入字符串
    assert user_md.exists(), "域#2: USER.md 必须存在"
    actual = user_md.read_text(encoding="utf-8")
    assert benign_content in actual, (
        f"域#2: USER.md 应含 '{benign_content}'，实际前 200 字符: {actual[:200]!r}"
    )


# ---------------------------------------------------------------------------
# 域 #3：Context 冻结快照（T-P3-3）
# ---------------------------------------------------------------------------


async def test_domain_3_frozen_prefix_invariant_with_live_state_drift(
    bootstrapped_harness: dict[str, Any],
) -> None:
    """域 #3：SnapshotStore 双层语义——冻结副本不变 / 磁盘真实更新（broker 真路径）。

    spec FR-9 关键：两次 LLM 调用 frozen_prefix_hash 一致——这里以 sha256
    over snapshot.format_for_system_prompt() 模拟（系统真实注入 system prompt
    走的就是这个 dict，prefix cache 由其内容决定）。

    断言（≥ 2 独立点）：
    1. 两次 tool_broker.execute("user_profile.update") 之后
       app.state.snapshot_store.format_for_system_prompt()["USER.md"]
       的 sha256 与 bootstrap 完成时的 sha256 一致（冻结副本不变 = prefix cache
       不破）
    2. USER.md 磁盘真实变化 + 包含两次写入内容（live 真路径）
    """
    from apps.gateway.tests.e2e_live.helpers.factories import _ensure_audit_task

    app = bootstrapped_harness["app"]
    project_root = bootstrapped_harness["project_root"]
    sg = app.state.store_group
    tool_broker = app.state.tool_broker

    # bootstrap 完成时 SnapshotStore 已经载入了空 USER.md（OctoHarness
    # _bootstrap_tool_registry_and_snapshot 段执行）
    snap_store = app.state.snapshot_store
    frozen_before = snap_store.format_for_system_prompt().get("USER.md", "")
    frozen_hash_before = _sha256_text(frozen_before)

    test_task_id = "_e2e_domain_3_task"
    await _ensure_audit_task(sg, test_task_id)
    ctx = _build_full_preset_context(test_task_id)

    # 第 1 次写入（broker 真路径）
    r1 = await tool_broker.execute(
        tool_name="user_profile.update",
        args={"operation": "add", "content": "测试内容 1：F087 P3 域 #3 第一轮"},
        context=ctx,
    )
    assert not r1.is_error, f"域#3 第 1 次 broker.execute 应成功: {r1.error}"
    p1 = json.loads(r1.output) if r1.output else {}
    assert p1.get("status") == "written", f"域#3 第 1 次写入应 written: {p1.get('reason')}"

    # 第 2 次写入
    r2 = await tool_broker.execute(
        tool_name="user_profile.update",
        args={"operation": "add", "content": "测试内容 2：F087 P3 域 #3 第二轮"},
        context=ctx,
    )
    assert not r2.is_error, f"域#3 第 2 次 broker.execute 应成功: {r2.error}"
    p2 = json.loads(r2.output) if r2.output else {}
    assert p2.get("status") == "written", f"域#3 第 2 次写入应 written: {p2.get('reason')}"

    # 断言 1：app.state.snapshot_store 的冻结副本（system prompt 注入用）不变
    # 这是 Hermes 模式 prefix cache 保护的核心：handler 写盘后冻结快照不被改写
    frozen_after = snap_store.format_for_system_prompt().get("USER.md", "")
    frozen_hash_after = _sha256_text(frozen_after)
    assert frozen_hash_before == frozen_hash_after, (
        f"域#3: 冻结副本 sha256 应不变（prefix cache 保护），"
        f"before={frozen_hash_before}, after={frozen_hash_after}"
    )

    # 断言 2：USER.md 磁盘真实变化 + 包含两次写入内容
    user_md = project_root / "behavior" / "system" / "USER.md"
    actual = user_md.read_text(encoding="utf-8")
    assert "测试内容 1" in actual, "域#3: USER.md 应含第 1 次写入内容"
    assert "测试内容 2" in actual, "域#3: USER.md 应含第 2 次写入内容"
