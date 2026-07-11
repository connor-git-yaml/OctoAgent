"""F138 Phase C【keystone】：脚本化 LLM 驱动真决策环 L3 e2e（AC-3 / AC-8）。

**这是 M9 F138 的验收锚**。与 ``test_e2e_basic_tool_context.py``（从
``tool_broker.execute()`` 切进、跳过决策）的唯一差别：本测试让**脚本化 LLM
决定**调 ``user_profile.update``——完整跑决策环**前半段**（LLM 决策 → 工具
派发），这一跳在 L3 此前零覆盖。

链路：ScriptedModelClient（脚本脑）→ 真 SkillRunner 多步循环 → 真
tool_broker.execute → 真回写（USER.md / EventStore）→ 脚本 content 贯穿回
ModelCallResult。全程零真 provider HTTP。

零真调用三重防御（Codex spec review P2-2 闭环）：
1. ``provider_router.resolve_for_alias`` bomb——路径 A（router_message_adapter
   .py:66）与路径 B（provider_model_client.py:548）通往真 provider 的共同咽喉点；
2. 空 tmp CredentialStore（load 返回空 store，无宿主 OAuth 依赖 → AC-8
   CI-runnable：本文件不依赖 ``real_codex_credential_store`` fixture）；
3. 末尾 content 断言（防 FallbackManager 吞 bomb 落 Echo 假成功）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# pre-merge 窗口防御（spec §3.5）：pre-commit hook 可能以非本 worktree 的 src
# 收集本文件（共享 venv editable 指向随最近一次 sync 漂移），彼时
# octoagent.skills.testing 不存在 → 优雅 SKIP；合入 master 后恒可 import。
pytest.importorskip("octoagent.skills.testing")

from octoagent.skills.models import SkillOutputEnvelope, ToolCallSpec
from octoagent.skills.testing import ScriptedModelClient

pytestmark = [pytest.mark.e2e_scripted, pytest.mark.e2e_live]

_PREFERENCE_TEXT = "F138 keystone：用户偏好——喜欢简洁回复"


def _empty_credential_store(root: Path) -> Any:
    """空 tmp CredentialStore：路径不存在 → load() 返回空 store（零宿主 OAuth）。"""
    from octoagent.provider.auth.store import CredentialStore

    return CredentialStore(store_path=root / "creds" / "auth-profiles.json")


def _resolve_for_alias_bomb(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError(
        "F138 keystone: 真 provider 解析被触发——脚本化决策环不允许任何真 LLM 调用"
    )


@pytest.fixture
async def scripted_harness(tmp_path: Path):
    """带脚本脑的 OctoHarness：全 11 段真 bootstrap + 空凭证 + resolve bomb。"""
    from fastapi import FastAPI

    from octoagent.gateway.harness.octo_harness import OctoHarness

    e2e_root = tmp_path / "octoagent_e2e_root"
    data_dir = e2e_root / "data"
    mcp_servers_dir = e2e_root / "mcp-servers"
    e2e_root.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    mcp_servers_dir.mkdir(parents=True, exist_ok=True)
    (e2e_root / "behavior" / "system").mkdir(parents=True, exist_ok=True)

    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )
    if (fixtures_root / "octoagent.yaml.template").exists():
        from apps.gateway.tests.e2e_live.helpers.factories import (
            copy_local_instance_template,
        )

        copy_local_instance_template(fixtures_root, e2e_root)

    scripted = ScriptedModelClient([
        SkillOutputEnvelope(
            content="",
            tool_calls=[
                ToolCallSpec(
                    tool_name="user_profile.update",
                    arguments={"operation": "add", "content": _PREFERENCE_TEXT},
                ),
            ],
        ),
        SkillOutputEnvelope(content="已记录", complete=True),
    ])

    harness = OctoHarness(
        project_root=e2e_root,
        credential_store=_empty_credential_store(e2e_root),
        mcp_servers_dir=mcp_servers_dir,
        data_dir=data_dir,
        model_client=scripted,
    )
    app = FastAPI()
    await harness.bootstrap(app)
    harness.commit_to_app(app)

    # 零真调用防御 #1：两条 LLM 路径的共同咽喉点装 bomb
    app.state.provider_router.resolve_for_alias = _resolve_for_alias_bomb

    yield {
        "harness": harness,
        "app": app,
        "project_root": e2e_root,
        "scripted": scripted,
    }

    await harness.shutdown(app)


async def test_scripted_adapter_drives_real_decision_loop(
    scripted_harness: dict[str, Any],
) -> None:
    """AC-3【keystone】+ AC-8：脚本脑驱动真决策环——决策 → 派发 → 回写全链。"""
    from octoagent.core.models.enums import EventType

    from apps.gateway.tests.e2e_live.helpers.factories import _ensure_audit_task

    app = scripted_harness["app"]
    project_root = scripted_harness["project_root"]
    scripted = scripted_harness["scripted"]
    sg = app.state.store_group

    tid = "_e2e_scripted_keystone_task"
    await _ensure_audit_task(sg, tid)
    # user_profile handler 的 audit 事件在 broker 路径不绑 ExecutionRuntimeContext
    # ContextVar 时落 "_user_profile_audit" 占位 task（与 F087 smoke 域 #1 同语义）
    await _ensure_audit_task(sg, "_user_profile_audit")

    # 驱动真决策环（direct 入口；selected_tools 走 extract_mounted_tool_names 的
    # selected_tools_json 通道；permission_preset=full 让 IRREVERSIBLE 的
    # user_profile.update 直放——keystone 验证决策环不验证 ApprovalGate，
    # 同 F087 smoke 域 #1 先例）
    result = await app.state.llm_service.call(
        "记住我喜欢简洁回复",
        task_id=tid,
        trace_id=tid,
        metadata={
            "selected_tools_json": ["user_profile.update"],
            "permission_preset": "full",
        },
    )

    # 断言 1：决策环真跑了 2 轮（第 1 轮吐 tool_call，第 2 轮消费 feedback 后 complete）
    assert scripted.calls == 2, (
        f"keystone: 脚本脑应被消费 2 轮（决策→feedback→complete），实际 {scripted.calls}"
    )

    # 断言 2【前半段！】：LLM 决策 → broker 派发真发生——TOOL_CALL_STARTED 是
    # broker.execute 真路径专属事件（直调 handler 不会产）
    events = await sg.event_store.get_events_for_task(tid)
    started = [e for e in events if e.type == EventType.TOOL_CALL_STARTED]
    assert started, (
        "keystone: 应有 TOOL_CALL_STARTED 事件——证明'脚本 LLM 决定调工具'真的"
        "驱动了 tool_broker 派发（决策环前半段，L3 此前零覆盖的那一跳）"
    )
    assert started[-1].payload.get("tool_name") == "user_profile.update"
    completed = [e for e in events if e.type == EventType.TOOL_CALL_COMPLETED]
    assert completed, "keystone: 工具执行应完成（TOOL_CALL_COMPLETED）"

    # 断言 3【后半段】：回写真落盘
    user_md = project_root / "behavior" / "system" / "USER.md"
    assert user_md.exists(), "keystone: USER.md 必须存在"
    assert _PREFERENCE_TEXT in user_md.read_text(encoding="utf-8"), (
        "keystone: 脚本参数应真实写入 USER.md（决策环后半段回写）"
    )

    # 断言 4：审计事件链真产（handler 内部 MEMORY_ENTRY_ADDED）
    audit_events = await sg.event_store.get_events_for_task("_user_profile_audit")
    added = [e for e in audit_events if e.type == EventType.MEMORY_ENTRY_ADDED]
    assert added, "keystone: 应有 MEMORY_ENTRY_ADDED 审计事件"
    assert added[-1].payload.get("tool") == "user_profile.update"

    # 断言 5：脚本脑输出贯穿回 ModelCallResult（零真调用防御 #3——
    # 若任何环节落到 FallbackManager/Echo，content 不可能是脚本值）
    assert result.content == "已记录", (
        f"keystone: 结果应为脚本第 2 步 content，实际 {result.content!r}"
    )
    assert result.is_fallback is False, "keystone: 不得落 fallback 路径"


async def test_scripted_loop_emits_full_model_event_chain(
    scripted_harness: dict[str, Any],
) -> None:
    """Constitution #2：脚本化决策环与真 LLM 路径产同一条事件链（L3 可确定性断言）。"""
    from octoagent.core.models.enums import EventType

    from apps.gateway.tests.e2e_live.helpers.factories import _ensure_audit_task

    app = scripted_harness["app"]
    sg = app.state.store_group

    tid = "_e2e_scripted_event_chain_task"
    await _ensure_audit_task(sg, tid)
    await _ensure_audit_task(sg, "_user_profile_audit")

    await app.state.llm_service.call(
        "记住我喜欢简洁回复",
        task_id=tid,
        trace_id=tid,
        metadata={
            "selected_tools_json": ["user_profile.update"],
            "permission_preset": "full",
        },
    )

    events = await sg.event_store.get_events_for_task(tid)
    type_counts: dict[str, int] = {}
    for e in events:
        key = str(e.type.value if hasattr(e.type, "value") else e.type)
        type_counts[key] = type_counts.get(key, 0) + 1

    # 2 轮决策 → 2 对 MODEL_CALL_STARTED/COMPLETED；1 次工具 → 1 对 TOOL_CALL_*
    assert type_counts.get(EventType.MODEL_CALL_STARTED.value, 0) == 2, type_counts
    assert type_counts.get(EventType.MODEL_CALL_COMPLETED.value, 0) == 2, type_counts
    assert type_counts.get(EventType.TOOL_CALL_STARTED.value, 0) == 1, type_counts
    assert type_counts.get(EventType.TOOL_CALL_COMPLETED.value, 0) == 1, type_counts
    # 确定性红利：同一脚本每次跑出同一条事件链（真 LLM 路径做不到的 L3 性质）


async def test_scripted_harness_bootstrap_needs_no_host_oauth(
    scripted_harness: dict[str, Any],
) -> None:
    """AC-8 显式验收：全 11 段 bootstrap + 决策环在空凭证下可跑（CI-runnable）。

    对照面：e2e_smoke 依赖 ``real_codex_credential_store``（宿主缺 OAuth 即
    SKIP）；本套件用空 tmp CredentialStore 恒可跑——是"脚本路径不需真 OAuth
    可进干净 CI"的机械证据。
    """
    app = scripted_harness["app"]
    project_root = scripted_harness["project_root"]

    # bootstrap 已在 fixture 完成；这里验证凭证面貌：store 真是空的
    cred_store = _empty_credential_store(project_root)
    data = cred_store.load()
    profiles = getattr(data, "profiles", None)
    assert not profiles, f"AC-8: 凭证 store 必须为空，实际 {profiles!r}"

    # 决策环可用（SkillRunner 已建，用的是脚本脑不是 ProviderModelClient）
    skill_runner = app.state.llm_service._skill_runner
    assert skill_runner is not None
    assert isinstance(skill_runner._model_client, ScriptedModelClient)


async def test_scripted_loop_is_deterministic_across_runs(tmp_path: Path) -> None:
    """确定性验证：同一脚本两次独立 bootstrap + 驱动，产出逐值一致（L3 硬性质）。"""
    from fastapi import FastAPI

    from octoagent.core.models.enums import EventType
    from octoagent.gateway.harness.octo_harness import OctoHarness

    from apps.gateway.tests.e2e_live.helpers.factories import (
        _ensure_audit_task,
        copy_local_instance_template,
    )

    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )

    async def _one_run(run_id: int) -> tuple[str, list[str], str]:
        root = tmp_path / f"run_{run_id}"
        (root / "behavior" / "system").mkdir(parents=True, exist_ok=True)
        (root / "data").mkdir(parents=True, exist_ok=True)
        (root / "mcp-servers").mkdir(parents=True, exist_ok=True)
        if (fixtures_root / "octoagent.yaml.template").exists():
            copy_local_instance_template(fixtures_root, root)

        scripted = ScriptedModelClient([
            SkillOutputEnvelope(
                content="",
                tool_calls=[
                    ToolCallSpec(
                        tool_name="user_profile.update",
                        arguments={"operation": "add", "content": _PREFERENCE_TEXT},
                    ),
                ],
            ),
            SkillOutputEnvelope(content="已记录", complete=True),
        ])
        harness = OctoHarness(
            project_root=root,
            credential_store=_empty_credential_store(root),
            mcp_servers_dir=root / "mcp-servers",
            data_dir=root / "data",
            model_client=scripted,
        )
        app = FastAPI()
        await harness.bootstrap(app)
        harness.commit_to_app(app)
        app.state.provider_router.resolve_for_alias = _resolve_for_alias_bomb
        try:
            sg = app.state.store_group
            tid = "_e2e_scripted_determinism_task"
            await _ensure_audit_task(sg, tid)
            await _ensure_audit_task(sg, "_user_profile_audit")
            result = await app.state.llm_service.call(
                "记住我喜欢简洁回复",
                task_id=tid,
                trace_id=tid,
                metadata={
                    "selected_tools_json": ["user_profile.update"],
                    "permission_preset": "full",
                },
            )
            events = await sg.event_store.get_events_for_task(tid)
            event_types = [
                str(e.type.value if hasattr(e.type, "value") else e.type) for e in events
            ]
            # 只对比事件类型序列中稳定的决策环骨架（MODEL_*/TOOL_*）
            loop_skeleton = [
                t for t in event_types
                if t in {
                    EventType.MODEL_CALL_STARTED.value,
                    EventType.MODEL_CALL_COMPLETED.value,
                    EventType.TOOL_CALL_STARTED.value,
                    EventType.TOOL_CALL_COMPLETED.value,
                }
            ]
            user_md_text = (root / "behavior" / "system" / "USER.md").read_text(
                encoding="utf-8"
            )
            preference_lines = json.dumps(
                [ln for ln in user_md_text.splitlines() if _PREFERENCE_TEXT in ln],
                ensure_ascii=False,
            )
            return result.content, loop_skeleton, preference_lines
        finally:
            await harness.shutdown(app)

    content_1, skeleton_1, pref_1 = await _one_run(1)
    content_2, skeleton_2, pref_2 = await _one_run(2)

    assert content_1 == content_2 == "已记录"
    assert skeleton_1 == skeleton_2, (
        f"确定性: 两次运行决策环事件骨架应逐值一致，run1={skeleton_1} run2={skeleton_2}"
    )
    assert pref_1 == pref_2, "确定性: USER.md 回写内容应逐值一致"
