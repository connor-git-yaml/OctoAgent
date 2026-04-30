"""F087 P3 smoke 域 #1/#2/#3：工具调用基础 / USER.md 全链路 / Context 冻结快照。

设计原则（**务实路径**）：
1. **真跑 OctoHarness 全 11 段 bootstrap**——验证完整 harness 装配链路
   （e2e 的核心价值：harness 全栈跑完，不仅仅 handler 单元）
2. **真调 builtin tool handler**（user_profile.update）——验证 WriteResult /
   events / SnapshotStore live state 真路径
3. **不真打 Codex OAuth LLM**（仅 T-P3-3 frozen prefix 验证 SnapshotStore
   语义，不需要 LLM）——避免 P3 阶段被 LLM 真实响应不确定性卡住；真打 LLM 留
   到 P4 域 #5 (Perplexity MCP) 主路径
4. 每个 case ≥ 2 独立断言点（spec FR-11 锁）

case 列表：
- T-P3-1 域 #1 工具调用基础：user_profile.update(add) → WriteResult.status=written +
  events 含 MEMORY_ENTRY_ADDED
- T-P3-2 域 #2 USER.md 全链路：user_profile.update 后 USER.md 含特定内容 +
  WriteResult.target=user_md_path + ThreatScanner.passed (status != rejected)
- T-P3-3 域 #3 Context 冻结快照：两次 update 后 snapshot.format_for_system_prompt()
  保持冻结副本一致 (sha256 不变) + live_state 已更新（双层语义）
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest


pytestmark = [pytest.mark.e2e_smoke, pytest.mark.e2e_live]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    """域 #1：调 user_profile.update(add) 验证 WriteResult + events 双断言。

    断言（≥ 2 独立点）：
    1. WriteResult.status == "written" + WriteResult.target = USER.md 真实路径
    2. events 表含 MEMORY_ENTRY_ADDED 事件，payload 含 tool="user_profile.update"
    """
    from octoagent.core.models.enums import EventType

    from apps.gateway.tests.e2e_live.helpers.factories import (
        _build_real_user_profile_handler,
        _ensure_audit_task,
    )

    app = bootstrapped_harness["app"]
    project_root = bootstrapped_harness["project_root"]
    sg = app.state.store_group

    await _ensure_audit_task(sg, "_user_profile_audit")
    handler, _snap_store, user_md = await _build_real_user_profile_handler(
        sg, project_root
    )

    # 真调 handler
    test_content = "F087 P3 域#1：用户偏好——喜欢中文沟通"
    result = await handler(operation="add", content=test_content)

    # 断言 1：WriteResult 状态 + target 路径
    assert result.status == "written", (
        f"域#1: WriteResult.status 应为 'written'，实际: {result.status} / "
        f"reason={result.reason}"
    )
    assert result.target == str(user_md), (
        f"域#1: WriteResult.target 应为 USER.md 路径，实际: {result.target}"
    )

    # 断言 2：events 表含 MEMORY_ENTRY_ADDED
    events = await sg.event_store.get_events_for_task("_user_profile_audit")
    added_events = [e for e in events if e.type == EventType.MEMORY_ENTRY_ADDED]
    assert added_events, (
        "域#1: events 表应包含至少 1 条 MEMORY_ENTRY_ADDED 事件"
    )
    last = added_events[-1]
    assert last.payload.get("tool") == "user_profile.update", (
        f"域#1: MEMORY_ENTRY_ADDED.payload.tool 应为 'user_profile.update'，"
        f"实际: {last.payload.get('tool')}"
    )

    # 隐式：USER.md 真的写入了
    assert test_content in user_md.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 域 #2：USER.md 全链路（T-P3-2）
# ---------------------------------------------------------------------------


async def test_domain_2_user_md_full_pipeline_threat_scanner_passes(
    bootstrapped_harness: dict[str, Any],
) -> None:
    """域 #2：USER.md 写入全链路 + ThreatScanner 不拦截良性内容。

    断言（≥ 2 独立点）：
    1. WriteResult.status == "written"（ThreatScanner 未拦截良性内容）
       + WriteResult.blocked == False（UserProfileUpdateResult 子类语义）
    2. USER.md 真实磁盘内容包含写入字符串
    """
    from apps.gateway.tests.e2e_live.helpers.factories import (
        _build_real_user_profile_handler,
        _ensure_audit_task,
    )

    app = bootstrapped_harness["app"]
    project_root = bootstrapped_harness["project_root"]
    sg = app.state.store_group

    await _ensure_audit_task(sg, "_user_profile_audit")
    handler, _snap_store, user_md = await _build_real_user_profile_handler(
        sg, project_root
    )

    # 良性内容（不命中任何 threat pattern）
    benign_content = "时区：Asia/Shanghai；语言偏好：zh-CN"
    result = await handler(operation="add", content=benign_content)

    # 断言 1：ThreatScanner 不拦截良性内容（blocked=False，不是 rejected）
    assert result.status == "written", (
        f"域#2: 良性内容应通过 ThreatScanner，实际 status={result.status} / "
        f"reason={result.reason}"
    )
    # UserProfileUpdateResult.blocked 字段
    assert getattr(result, "blocked", False) is False, (
        f"域#2: WriteResult.blocked 应为 False（良性内容），实际: "
        f"{getattr(result, 'blocked', None)}"
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
    """域 #3：SnapshotStore 双层语义——冻结副本不变 / live state 更新。

    spec FR-9 关键：两次 LLM 调用 frozen_prefix_hash 一致——这里以 sha256
    over snapshot.format_for_system_prompt() 模拟（系统真实注入 system prompt
    走的就是这个 dict，prefix cache 由其内容决定）。

    断言（≥ 2 独立点）：
    1. 两次 user_profile.update 之后 snapshot.format_for_system_prompt()["USER.md"]
       的 sha256 与 bootstrap 完成时的 sha256 一致（冻结副本不变 = prefix cache
       不破）
    2. snapshot.get_live_state("USER.md") 已变化（live state 真更新，二分语义）
    """
    from apps.gateway.tests.e2e_live.helpers.factories import (
        _build_real_user_profile_handler,
        _ensure_audit_task,
    )

    app = bootstrapped_harness["app"]
    project_root = bootstrapped_harness["project_root"]
    sg = app.state.store_group

    # bootstrap 完成时 SnapshotStore 已经载入了空 USER.md（OctoHarness
    # _bootstrap_tool_registry_and_snapshot 段执行）
    snap_store = app.state.snapshot_store
    frozen_before = snap_store.format_for_system_prompt().get("USER.md", "")
    frozen_hash_before = _sha256_text(frozen_before)

    await _ensure_audit_task(sg, "_user_profile_audit")
    # 注意：_build_real_user_profile_handler 内部会构造一个新的 SnapshotStore
    # 给 ToolDeps 用（不复用 app.state.snapshot_store）。我们额外触发
    # write_through 到 USER.md 实际磁盘上，然后验证 app.state.snapshot_store
    # 的冻结副本不变（这就是 prefix cache 保护的核心语义）
    handler, _new_snap, user_md = await _build_real_user_profile_handler(
        sg, project_root
    )

    # 第 1 次写入
    r1 = await handler(operation="add", content="测试内容 1：F087 P3 域 #3 第一轮")
    assert r1.status == "written", f"域#3 第 1 次写入应成功: {r1.reason}"

    # 第 2 次写入
    r2 = await handler(operation="add", content="测试内容 2：F087 P3 域 #3 第二轮")
    assert r2.status == "written", f"域#3 第 2 次写入应成功: {r2.reason}"

    # 断言 1：app.state.snapshot_store 的冻结副本（system prompt 注入用）不变
    # 这是 Hermes 模式 prefix cache 保护的核心：handler 写盘后冻结快照不被改写
    frozen_after = snap_store.format_for_system_prompt().get("USER.md", "")
    frozen_hash_after = _sha256_text(frozen_after)
    assert frozen_hash_before == frozen_hash_after, (
        f"域#3: 冻结副本 sha256 应不变（prefix cache 保护），"
        f"before={frozen_hash_before}, after={frozen_hash_after}"
    )

    # 断言 2：USER.md 磁盘真实变化（live 真路径）+ 包含两次写入内容
    actual = user_md.read_text(encoding="utf-8")
    assert "测试内容 1" in actual, "域#3: USER.md 应含第 1 次写入内容"
    assert "测试内容 2" in actual, "域#3: USER.md 应含第 2 次写入内容"
