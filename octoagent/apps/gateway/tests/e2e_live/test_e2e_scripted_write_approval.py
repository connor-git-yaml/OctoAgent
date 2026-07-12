"""F144 交付③：F135 gap-1 的 L3 scripted 用例（USER.md 初始化 + F136 审批全链）。

**吸收对象**：F136 completion-report §8 的真机手工验证——「让 Agent 把画像写进
USER.md → proposal → 确认 → **审批卡片** → 批准 → 落盘；不批 → 文件不变、对话
继续」。此前这条链只有真机手工步骤 + F136 单测（直调 handler/gate 层）；本文件
把它变成**决策环全链** L3：

    ScriptedModelClient（脚本脑，F138）→ 真 SkillRunner → 真 tool_broker
    → 真 behavior.write_file handler → 真 gate_behavior_write（F136 服务端
    审批绑定）→ 真 ApprovalGate/ApprovalManager → **真 REST POST
    /api/approve/{id}**（与 Web 审批卡片同一条路）→ 落盘/拒绝。

**ExecutionRuntimeContext 绑定**：``behavior.write_file`` handler 裸调
``get_current_execution_context()``（misc_tools.py:218，未绑即 raise）——
生产路径由 ``task_service.py:745`` 的 ``bind_execution_context`` 包住
``llm_service`` 调用。本测试忠实复刻：构造真 ``ExecutionRuntimeContext``
（console 取 harness 的 ``app.state.execution_console``）并绑定后再驱动
决策环——审批事件/WAITING_APPROVAL 转移因此挂在真实 task 上。

**为什么 permission_preset=full 不是绕过审批（评审挑战点，先答）**：F136 的
gate 在 handler 内部、对 REVIEW_REQUIRED 文件无条件触发——broker 层 policy
preset 管不到它。在**最宽** preset 下审批仍拦住，恰是「服务端审批绑定不可被
policy 放宽绕过」的最强形态断言（F135 Codex P1 关闭的正是 LLM 一轮自确认
`confirmed=true` 的绕过；本用例脚本直接给 confirmed=true，仍必须等真实批准）。

零真调用三重防御同 F138 keystone：resolve_for_alias bomb + 空 tmp
CredentialStore + 末尾脚本 content 断言。零宿主 OAuth → CI-runnable。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

# pre-merge 窗口防御（F138 spec §3.5 同款）：pre-commit hook 可能以非本 worktree
# 的 src 收集本文件，彼时 octoagent.skills.testing 不存在 → 优雅 SKIP。
pytest.importorskip("octoagent.skills.testing")

from octoagent.skills.models import SkillOutputEnvelope, ToolCallSpec
from octoagent.skills.testing import ScriptedModelClient

pytestmark = [pytest.mark.e2e_scripted, pytest.mark.e2e_live]

_USER_MD_CONTENT = (
    "# USER\n\n- 时区：Asia/Shanghai\n- 偏好：简洁回复（F144 gap-1 scripted）\n"
)
#: ApprovalGate 在 exec_ctx 未绑（broker 路径）时的占位审计 task
#: （approval_gate.py:_APPROVAL_AUDIT_TASK_ID，_emit_event 自 ensure）。
_APPROVAL_AUDIT_TASK = "_approval_gate_audit"


def _empty_credential_store(root: Path) -> Any:
    from octoagent.provider.auth.store import CredentialStore

    return CredentialStore(store_path=root / "creds" / "auth-profiles.json")


def _resolve_for_alias_bomb(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError(
        "F144 gap-1: 真 provider 解析被触发——脚本化决策环不允许任何真 LLM 调用"
    )


def _write_file_script(final_reply: str) -> ScriptedModelClient:
    """脚本：第 1 轮直接 confirmed=true 写 USER.md（模拟被诱导/自确认的 LLM——
    F136 关闭的绕过形态），第 2 轮消费工具结果后 complete。"""
    return ScriptedModelClient([
        SkillOutputEnvelope(
            content="",
            tool_calls=[
                ToolCallSpec(
                    tool_name="behavior.write_file",
                    arguments={
                        "file_id": "USER.md",
                        "content": _USER_MD_CONTENT,
                        "confirmed": True,
                    },
                ),
            ],
        ),
        SkillOutputEnvelope(content=final_reply, complete=True),
    ])


async def _build_scripted_app(root: Path, scripted: ScriptedModelClient):
    """keystone 同款 harness + **真 approvals REST 路由**（与 Web 审批卡片同路）。

    返回 (harness, app)。调用方负责 harness.shutdown(app)。
    """
    from fastapi import FastAPI
    from octoagent.gateway.harness.octo_harness import OctoHarness
    from octoagent.gateway.routes import approvals as approvals_routes

    (root / "behavior" / "system").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "mcp-servers").mkdir(parents=True, exist_ok=True)
    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )
    if (fixtures_root / "octoagent.yaml.template").exists():
        from apps.gateway.tests.e2e_live.helpers.factories import (
            copy_local_instance_template,
        )

        copy_local_instance_template(fixtures_root, root)

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
    # 真 REST 审批路由：deps.get_approval_manager / get_approval_gate 读
    # app.state（harness bootstrap 已放好真件）——POST /api/approve/{id}
    # 即 Web 审批卡片实际走的双 resolve（manager 持久化 + gate 唤醒）。
    app.include_router(approvals_routes.router)
    app.state.provider_router.resolve_for_alias = _resolve_for_alias_bomb
    return harness, app


async def _wait_for_pending_approval(
    approval_manager: Any, *, tool_name: str, timeout_s: float = 10.0
) -> Any:
    """轮询 ApprovalManager 直到目标工具的审批出现（e2e_live 30s SIGALRM 内）。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        for record in approval_manager.get_pending_approvals():
            if record.request.tool_name == tool_name:
                return record.request
        await asyncio.sleep(0.05)
    raise AssertionError(f"{timeout_s}s 内未出现 {tool_name} 审批请求——F136 门未触发？")


async def _post_decision(app: Any, approval_id: str, decision: str) -> None:
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/approve/{approval_id}", json={"decision": decision}
        )
    assert resp.status_code == 200, f"REST 审批失败：{resp.status_code} {resp.text}"


async def _drive_write_and_decide(
    tmp_path: Path, *, decision: str, final_reply: str
) -> dict[str, Any]:
    """驱动完整链路：脚本决策环 → 审批出现 → REST 决策 → 决策环收尾。

    返回断言所需的全部现场（result / events / 审批记录 / 文件状态 / scripted）。
    """
    from octoagent.core.models.enums import EventType

    from apps.gateway.tests.e2e_live.helpers.factories import _ensure_audit_task

    root = tmp_path / "octoagent_e2e_root"
    scripted = _write_file_script(final_reply)
    harness, app = await _build_scripted_app(root, scripted)
    try:
        sg = app.state.store_group
        tid = f"_e2e_gap1_write_approval_{decision.replace('-', '_')}"
        await _ensure_audit_task(sg, tid)

        user_md = root / "behavior" / "system" / "USER.md"
        content_before = (
            user_md.read_text(encoding="utf-8") if user_md.exists() else None
        )

        # 忠实复刻生产（task_service.py:745）：绑定真 ExecutionRuntimeContext 后
        # 驱动决策环——create_task 复制当前 contextvars，协程内 handler 可取到。
        from octoagent.gateway.services.execution_context import (
            ExecutionRuntimeContext,
            bind_execution_context,
        )

        exec_ctx = ExecutionRuntimeContext(
            task_id=tid,
            trace_id=tid,
            session_id=f"session-{tid}",
            worker_id="main",
            backend="inline",
            console=app.state.execution_console,
        )
        with bind_execution_context(exec_ctx):
            call_task = asyncio.create_task(
                app.state.llm_service.call(
                    "请把我的画像写进 USER.md",
                    task_id=tid,
                    trace_id=tid,
                    metadata={
                        "selected_tools_json": ["behavior.write_file"],
                        # 最宽 preset：F136 服务端审批仍必须触发（见模块 docstring）
                        "permission_preset": "full",
                    },
                )
            )
        try:
            approval = await _wait_for_pending_approval(
                app.state.approval_manager, tool_name="behavior.write_file"
            )

            # ★ 批准前断言：脚本已 confirmed=true，但内容必须尚未落盘
            #  （「不批不写」的 e2e 半边——F136 AC-1 在决策环全链上的形态）
            landed_before_decision = (
                user_md.exists()
                and _USER_MD_CONTENT in user_md.read_text(encoding="utf-8")
            )
            assert not landed_before_decision, (
                "审批未决时 USER.md 已落盘——F136 服务端审批绑定被绕过！"
            )

            await _post_decision(app, approval.approval_id, decision)
            result = await asyncio.wait_for(call_task, timeout=20.0)
        finally:
            if not call_task.done():
                call_task.cancel()

        events = list(await sg.event_store.get_events_for_task(tid))
        events += list(await sg.event_store.get_events_for_task(_APPROVAL_AUDIT_TASK))
        record = app.state.approval_manager.get_approval(approval.approval_id)
        return {
            "result": result,
            "scripted": scripted,
            "approval": approval,
            "record": record,
            "events": events,
            "event_types": {
                str(e.type.value if hasattr(e.type, "value") else e.type)
                for e in events
            },
            "user_md": user_md,
            "content_before": content_before,
            "EventType": EventType,
        }
    finally:
        await harness.shutdown(app)


async def test_scripted_write_approval_approved_lands(tmp_path: Path) -> None:
    """FR-D1（approve 路径）：脚本 confirmed=true → 服务端审批出现 → 批准前不
    落盘 → REST allow-once → 真落盘 + 事件链 + 决策环正常收尾。"""
    ctx = await _drive_write_and_decide(
        tmp_path, decision="allow-once", final_reply="画像已写入"
    )
    EventType = ctx["EventType"]

    # 1. 落盘：批准后内容真实写入（决策环后半段回写）
    user_md: Path = ctx["user_md"]
    assert user_md.exists(), "批准后 USER.md 必须存在"
    assert user_md.read_text(encoding="utf-8") == _USER_MD_CONTENT

    # 2. 决策环真跑 2 轮且脚本 content 贯穿回 result（零真调用防御 #3）
    assert ctx["scripted"].calls == 2
    assert ctx["result"].content == "画像已写入"
    assert ctx["result"].is_fallback is False

    # 3. 审批链事件与终态：APPROVAL_REQUESTED（gate 写）+ 记录终态 approved
    requested = [
        e
        for e in ctx["events"]
        if str(e.type.value if hasattr(e.type, "value") else e.type)
        == EventType.APPROVAL_REQUESTED.value
        and e.payload.get("approval_id") == ctx["approval"].approval_id
    ]
    assert requested, "应有本次审批的 APPROVAL_REQUESTED 审计事件"
    assert ctx["record"] is not None
    assert str(ctx["record"].status.value).lower() == "approved"

    # 4. 决策环前半段证据：broker 真派发 behavior.write_file
    started = [
        e
        for e in ctx["events"]
        if str(e.type.value if hasattr(e.type, "value") else e.type)
        == EventType.TOOL_CALL_STARTED.value
        and e.payload.get("tool_name") == "behavior.write_file"
    ]
    assert started, "应有 TOOL_CALL_STARTED(behavior.write_file)——脚本决策真驱动了派发"
    assert EventType.TOOL_CALL_COMPLETED.value in ctx["event_types"]

    # 5. F136 P1 语义回归：审批卡片渲染字段（risk_explanation）带内容 diff
    assert "变更预览" in ctx["approval"].risk_explanation, (
        "审批卡片 risk_explanation 应含 unified diff（F136 Codex P1 修复语义）"
    )
    # 6. DP-3：behavior.write_file 审批不参与 allow-always 白名单
    assert ctx["approval"].allow_always_eligible is False


async def test_scripted_write_approval_rejected_no_write(tmp_path: Path) -> None:
    """FR-D2（reject 路径）：用户拒绝 → 文件不写 + 决策环继续（对话不中断，
    F136 DP-4）——「不批准就不写」的机械化。"""
    ctx = await _drive_write_and_decide(
        tmp_path, decision="deny", final_reply="收到，本次不写入"
    )

    # 1. 文件状态与决策前完全一致（模板初始态或不存在）
    user_md: Path = ctx["user_md"]
    content_after = (
        user_md.read_text(encoding="utf-8") if user_md.exists() else None
    )
    assert content_after == ctx["content_before"], "拒绝后 USER.md 必须保持原样"
    if content_after is not None:
        assert _USER_MD_CONTENT not in content_after

    # 2. 决策环继续：脚本第 2 轮仍被消费、正常收尾（F136 DP-4「显式拒绝恢复
    #    RUNNING、对话继续」在决策环全链上的证据——拒绝 ≠ 任务失败）
    assert ctx["scripted"].calls == 2
    assert ctx["result"].content == "收到，本次不写入"

    # 3. 审批记录终态 rejected
    assert ctx["record"] is not None
    assert str(ctx["record"].status.value).lower() == "rejected"
