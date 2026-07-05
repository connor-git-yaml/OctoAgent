"""write_approval：写工具的服务端审批绑定（F136）。

关闭 `behavior.write_file` 的 LLM 一轮自确认绕过（F135 Codex P1）：REVIEW_REQUIRED 文件的
confirmed=true 不再是"LLM 自证用户已确认"，而是发起**服务端** ApprovalGate 审批——只有用户在
Web 审批卡片 / Telegram 真实批准（APPROVAL_DECIDED=approved）后，调用方才允许落盘。

调用序列镜像 `ask_back_tools.escalate_permission_handler`（生产唯一先例）：
request_approval → ApprovalManager 双注册（Web resolve 依赖，否则 404）→ mark_waiting_approval
→ notify_approval_request(CRITICAL，豁免 quiet hours) → wait_for_decision(300s) → 条件恢复 RUNNING。

与 escalate_permission 的**刻意差异**（spec DP-4）：用户显式拒绝也恢复 RUNNING——escalate 的
rejected 意味"任务核心动作被禁止"走向 FAILED；写工具的 rejected 只是用户否决一次写入，对话
必须能继续。超时（operator=system_timeout）仍不恢复：task_runner 是终态唯一 owner
（F101 HIGH-02 v3）。

审批作用域（spec DP-3）：每次写独立审批。本模块不查、不填 session allowlist——allowlist 以
operation_type 为粒度无法区分内容，一次批准会变成本 session 任意内容静默改写，缝重开。

依赖缺失分层降级（spec DP-5，Constitution #6：降级=功能不可用，不是安全绕过）：
- approval_gate 缺失 → fail-closed（decision=unavailable，调用方不得写盘）；
- approval_manager / notification_service 缺失 → 仅降级对应通道，审批主路径继续。

供未来复用：`user_profile.replace/remove`（F084 Phase 3 至今 fail-closed）接线时可直接改用
本模块的同款序列（见 F136 handoff）。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any, Literal

import structlog

log = structlog.get_logger(__name__)

# 与 task_runner approval_timeout_seconds / escalate_permission / ApprovalManager expires_at
# 保持同值（300s）：monitor 的 FAILED 阈值与 wait_for_decision 超时对齐，避免语义漂移。
BEHAVIOR_WRITE_APPROVAL_TIMEOUT_SECONDS = 300.0

# ApprovalGate 事件 diff_content 上限（结构化字段 / 事件体积护栏）。
_DIFF_MAX_CHARS = 4000
# 拼进 risk_explanation 的 diff 上限——所有审批渲染渠道（Web 卡片 / OperatorInbox /
# Telegram，均读 risk_explanation 而非 diff_content）都要看到内容变更；给 Telegram
# 4096 消息上限留足余量，故比结构化 diff_content 更短。
_RISK_DIFF_MAX_CHARS = 1500


@dataclass(frozen=True, slots=True)
class WriteApprovalOutcome:
    """服务端审批结果（调用方仅在 decision=="approved" 时允许落盘）。"""

    decision: Literal["approved", "rejected", "timeout", "unavailable"]
    approval_id: str = ""
    reason: str = ""


def _build_unified_diff(
    old_content: str, new_content: str, file_id: str, *, max_chars: int = _DIFF_MAX_CHARS
) -> str:
    """构造审批卡片展示的 unified diff（用户批准的是这份具体修改，不是抽象权限）。"""
    diff_text = "".join(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"{file_id}（当前）",
            tofile=f"{file_id}（提议）",
        )
    )
    if not diff_text:
        # 无行级差异（如仅尾部空白/编码差异）——给渲染渠道一个明确提示，别显示空 diff。
        diff_text = "（无行级差异——可能仅空白或元数据变化）\n"
    if len(diff_text) > max_chars:
        diff_text = diff_text[:max_chars] + "\n…（diff 超长已截断）"
    return diff_text


async def gate_behavior_write(
    deps: Any,
    *,
    exec_ctx: Any,
    file_id: str,
    resolved: Any,
    old_content: str,
    new_content: str,
    budget_chars: int,
) -> WriteApprovalOutcome:
    """REVIEW_REQUIRED 写入的服务端审批门。不 raise（任何异常 → fail-closed）。

    Args:
        deps: ToolDeps（读 _approval_gate / _approval_manager / _notification_service）。
        exec_ctx: 当前 ExecutionRuntimeContext（session_id / task_id / WAITING_APPROVAL 转移）。
        file_id: 行为文件短名（如 USER.md）。
        resolved: 已解析的磁盘路径（卡片摘要展示）。
        old_content: 请求时的磁盘旧内容快照（仅用于 diff；版本 baseline 由调用方批准后重读）。
        new_content: 提议写入的完整新内容。
        budget_chars: 该文件字符预算（卡片摘要展示）。
    """
    approval_gate = getattr(deps, "_approval_gate", None)
    if approval_gate is None:
        # fail-closed：审批基础设施不可用时绝不落盘（Constitution #7）。
        log.warning(
            "behavior_write_approval_gate_unavailable",
            file_id=file_id,
            hint="approval_gate is None，REVIEW_REQUIRED 写入 fail-closed 拒绝",
        )
        return WriteApprovalOutcome(
            decision="unavailable",
            reason=(
                "APPROVAL_UNAVAILABLE: 审批通道不可用，REVIEW_REQUIRED 文件写入已拒绝"
                "（未落盘）。请稍后重试或让用户在 Web 行为工作台手动修改"
            ),
        )

    task_id = getattr(exec_ctx, "task_id", "") or ""
    session_id = getattr(exec_ctx, "session_id", "") or ""

    operation_summary = (
        f"Agent 请求写入行为文件 {file_id}（REVIEW_REQUIRED）\n"
        f"目标路径：{resolved}\n"
        f"新内容 {len(new_content)} 字符（预算 {budget_chars}）"
    )
    diff_content = _build_unified_diff(old_content, new_content, file_id)
    # risk_explanation 是所有审批渲染渠道（Web 卡片 / OperatorInbox / Telegram）实际
    # 展示的字段——把内容 diff 一并放进去，用户批准前才真能看到增删了什么（DP-6）。
    risk_explanation = (
        operation_summary
        + "\n\n变更预览（unified diff）：\n"
        + _build_unified_diff(
            old_content, new_content, file_id, max_chars=_RISK_DIFF_MAX_CHARS
        )
    )

    try:
        handle = await approval_gate.request_approval(
            session_id=session_id,
            tool_name="behavior.write_file",
            scan_result=None,  # 非 ThreatScanner 触发（Two-Phase 治理门）
            operation_summary=risk_explanation,
            diff_content=diff_content,
            task_id=task_id,
        )
    except Exception as exc:
        log.warning(
            "behavior_write_approval_request_failed",
            file_id=file_id,
            error=str(exc),
        )
        return WriteApprovalOutcome(
            decision="unavailable",
            reason="APPROVAL_UNAVAILABLE: 审批请求创建失败，写入已拒绝（未落盘）",
        )

    # ApprovalManager 双注册：Web resolve（routes/approvals.py）先查 ApprovalManager，
    # 未注册 → 404 → approval_gate.resolve_approval 永不触发 → 等满超时。
    # 注册失败仅降级 Web 列表/resolve 通道（Telegram operator_actions 持 gate 仍可 resolve）。
    approval_manager = getattr(deps, "_approval_manager", None)
    if approval_manager is not None:
        try:
            from datetime import UTC, datetime, timedelta

            from octoagent.core.models.enums import SideEffectLevel
            from octoagent.policy.models import ApprovalRequest

            _now = datetime.now(tz=UTC)
            await approval_manager.register(
                ApprovalRequest(
                    approval_id=handle.handle_id,
                    task_id=task_id,
                    tool_name="behavior.write_file",
                    tool_args_summary=(
                        f"file_id={file_id!r}, chars={len(new_content)}"
                    ),
                    # risk_explanation 含 diff——审批面板 / OperatorInbox / Telegram 展示的就是它。
                    risk_explanation=risk_explanation,
                    policy_label="behavior.write_file",
                    side_effect_level=SideEffectLevel.REVERSIBLE,
                    expires_at=_now
                    + timedelta(seconds=BEHAVIOR_WRITE_APPROVAL_TIMEOUT_SECONDS),
                    created_at=_now,
                    # DP-3：每次写独立审批——即便用户点"总是批准"也不写全局白名单，
                    # 否则下次 register 短路不入 pending → resolve 404 → 写入超时。
                    allow_always_eligible=False,
                )
            )
        except Exception as reg_exc:
            log.warning(
                "behavior_write_approval_manager_register_failed",
                approval_id=handle.handle_id,
                file_id=file_id,
                error=str(reg_exc),
                hint="ApprovalManager 注册失败，Web resolve 路径不可用（Telegram 仍可）",
            )
    else:
        log.debug(
            "behavior_write_approval_manager_unavailable",
            approval_id=handle.handle_id,
            hint="approval_manager is None，跳过双注册（测试/CLI 路径）",
        )

    # RUNNING → WAITING_APPROVAL 中间转移（best-effort，失败不阻塞审批等待）。
    try:
        await exec_ctx.mark_waiting_approval()
    except Exception as mark_exc:
        log.warning(
            "behavior_write_approval_mark_waiting_failed",
            task_id=task_id,
            error=str(mark_exc),
        )

    # CRITICAL 审批通知（豁免 quiet hours）——用户不在 Web 页面时经 Telegram 可达。
    notification_service = getattr(deps, "_notification_service", None)
    if notification_service is not None:
        try:
            from ...services.notification import NotificationPriority

            await notification_service.notify_approval_request(
                task_id=task_id,
                tool_name="behavior.write_file",
                ask_reason=operation_summary,
                payload={
                    "file_id": file_id,
                    "chars": len(new_content),
                    "timeout_seconds": int(BEHAVIOR_WRITE_APPROVAL_TIMEOUT_SECONDS),
                },
                priority=NotificationPriority.CRITICAL,
                state_transition_event_id=handle.handle_id,
                session_id=session_id,
            )
        except Exception as notif_exc:
            log.debug(
                "behavior_write_approval_notification_failed",
                task_id=task_id,
                error=str(notif_exc),
            )

    decision = "rejected"
    try:
        decision = await approval_gate.wait_for_decision(
            handle, timeout_seconds=BEHAVIOR_WRITE_APPROVAL_TIMEOUT_SECONDS
        )
    except Exception as wait_exc:
        log.warning(
            "behavior_write_approval_wait_failed",
            approval_id=handle.handle_id,
            error=str(wait_exc),
        )

    timed_out = getattr(handle, "operator", "") == "system_timeout"

    # 条件恢复 RUNNING（spec DP-4）：
    # - approved → 恢复，继续写盘；
    # - 显式拒绝 → 恢复（对话继续；与 escalate 差异见模块 docstring）；
    # - 超时 → 不恢复（task_runner monitor 是终态唯一 owner，F101 HIGH-02 v3）。
    if decision == "approved" or (decision == "rejected" and not timed_out):
        try:
            await exec_ctx.mark_running_from_waiting_approval()
        except Exception as restore_exc:
            log.warning(
                "behavior_write_approval_restore_running_failed",
                task_id=task_id,
                error=str(restore_exc),
            )

    if decision == "approved":
        return WriteApprovalOutcome(
            decision="approved",
            approval_id=handle.handle_id,
        )
    if timed_out:
        return WriteApprovalOutcome(
            decision="timeout",
            approval_id=handle.handle_id,
            reason=(
                "APPROVAL_TIMEOUT: 审批等待超时"
                f"（{int(BEHAVIOR_WRITE_APPROVAL_TIMEOUT_SECONDS)}s），未落盘。"
                "用户可稍后让我重新发起本次修改"
            ),
        )
    return WriteApprovalOutcome(
        decision="rejected",
        approval_id=handle.handle_id,
        reason="APPROVAL_REJECTED: 用户在审批卡片拒绝了本次写入，未落盘",
    )
