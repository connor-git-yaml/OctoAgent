"""Chat API 路由 -- T040, T041

对齐 contracts/policy-api.md §1.3, §1.4。
POST /api/chat/send -- 发送聊天消息 (FR-023)
GET /stream/task/{task_id} -- SSE 任务事件流（复用已有 stream 路由）(FR-024)
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from octoagent.core.models import (
    AgentSessionKind,
    EventType,
    RuntimeControlContext,
    TurnExecutorKind,
)
from octoagent.policy.models import ChatSendRequest, ChatSendResponse
from octoagent.gateway.services.control_plane.control_plane_state import ControlPlaneStateStore

from ..deps import get_store_group
from ..services.connection_metadata import (
    control_metadata_from_payload,
    resolve_explicit_session_owner_profile_id,
)
from ..services.runtime_control import RUNTIME_CONTEXT_JSON_KEY, encode_runtime_context

logger = logging.getLogger(__name__)

router = APIRouter()

# F101 Phase A：长 prompt 自动触发完整决策环（FR-D1/D2/D3）
# 单位：Unicode 字符数（len(message)），使用 Python str 的 Unicode 码点计数。
# M1 已知局限：中英文密度差异——2000 中文字符信息量约等于 6000 英文字符；
#   纯中文 prompt 实际触发门槛偏低，纯代码/英文 prompt 触发门槛偏高。
# 设计决策（GATE_DESIGN）：接受此局限，后续 F102 attention model 可按 token 估算调参。
LONG_PROMPT_THRESHOLD: int = 2000


def _resolve_long_prompt_threshold() -> int:
    """解析 LONG_PROMPT_THRESHOLD：优先 ENV `OCTOAGENT_LONG_PROMPT_THRESHOLD`，缺省 2000（FR-D3 可配置要求）。

    非法值（非整数 / 非正数）fallback 到默认值。
    """
    raw = os.environ.get("OCTOAGENT_LONG_PROMPT_THRESHOLD")
    if raw is None or raw.strip() == "":
        return LONG_PROMPT_THRESHOLD
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return LONG_PROMPT_THRESHOLD
    if value <= 0:
        return LONG_PROMPT_THRESHOLD
    return value

# 保存后台任务引用，防止 GC 回收
_background_tasks: set[asyncio.Task[None]] = set()


async def _enqueue_or_run(
    request: Request,
    service,
    task_id: str,
    message: str,
    model_alias: str | None = None,
    dispatch_metadata: dict[str, Any] | None = None,
) -> None:
    if not (
        hasattr(request.app.state, "llm_service")
        and request.app.state.llm_service
    ):
        return
    task_runner = getattr(request.app.state, "task_runner", None)
    if task_runner is not None:
        await task_runner.enqueue(task_id, message, model_alias=model_alias)
        return
    task = asyncio.create_task(
        service.process_task_with_llm(
            task_id,
            message,
            request.app.state.llm_service,
            model_alias=model_alias,
            dispatch_metadata=dispatch_metadata or {},
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _chat_send_failure(
    *,
    status_code: int,
    code: str,
    message: str,
    task_id: str | None = None,
) -> HTTPException:
    detail: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if task_id:
        detail["task_id"] = task_id
    return HTTPException(status_code=status_code, detail=detail)


def _resolve_project_root(request: Request) -> Path:
    return Path(getattr(request.app.state, "project_root", Path.cwd()))


async def _resolve_chat_scope_snapshot(
    body: ChatSendRequest,
    request: Request,
    store_group,
) -> tuple[str, str, str, str, str, str]:
    """解析聊天请求的 scope 快照。

    返回 (new_conversation_token, project_id, agent_profile_id,
          agent_runtime_id, agent_session_id, thread_id)。

    后三个在消费 new_conversation_token 时透传 Path A 写入的 ids，
    让 Path B 的 ContextResolveRequest 能直接复用，避免双写。
    """
    project_id = str(body.project_id or "").strip()
    new_conversation_token = str(body.new_conversation_token or "").strip()
    requested_agent_profile_id = str(body.agent_profile_id or "").strip()
    new_conversation_agent_runtime_id = ""
    new_conversation_agent_session_id = ""
    new_conversation_thread_id = ""

    cp_store = ControlPlaneStateStore(_resolve_project_root(request))
    state = cp_store.load()
    if new_conversation_token and new_conversation_token == state.new_conversation_token:
        project_id = state.new_conversation_project_id.strip() or project_id
        requested_agent_profile_id = (
            requested_agent_profile_id or state.new_conversation_agent_profile_id.strip()
        )
        new_conversation_agent_runtime_id = state.new_conversation_agent_runtime_id.strip()
        new_conversation_agent_session_id = state.new_conversation_agent_session_id.strip()
        new_conversation_thread_id = state.new_conversation_thread_id.strip()
        # token 消费推迟到 task 创建 + 入队成功后再清；否则首条消息中途失败用户
        # 重试时拿不到 Path A 预创建的 ids，会退化到新 thread_id 路径并留下孤儿绑定。
    elif not body.task_id:
        project_id = project_id or state.selected_project_id.strip()

    project = await store_group.project_store.get_project(project_id) if project_id else None
    if project is None:
        return (
            new_conversation_token,
            "",
            requested_agent_profile_id,
            "",
            "",
            "",
        )
    return (
        new_conversation_token,
        project.project_id,
        requested_agent_profile_id,
        new_conversation_agent_runtime_id,
        new_conversation_agent_session_id,
        new_conversation_thread_id,
    )


def _consume_new_conversation_token(request: Request, token: str) -> None:
    """task 入队成功后才真正清掉 new_conversation_token。

    chat.py `_resolve_chat_scope_snapshot` 仅 *读* state，不再立即写空；
    这里在 send 成功路径末尾调用，保证：
    - task 创建/入队失败 → token 仍然有效，前端重试时能再次拿到 Path A ids；
    - task 真正进入执行链 → 老 token 失效，避免 stale token 被复用。
    清空时仍然 re-load state，避免覆盖 send 期间其它 control 入口写入的字段。
    """
    cp_store = ControlPlaneStateStore(_resolve_project_root(request))
    current = cp_store.load()
    if current.new_conversation_token != token:
        return
    cp_store.save(
        current.model_copy(
            update={
                "new_conversation_token": "",
                "new_conversation_project_id": "",
                "new_conversation_agent_profile_id": "",
                "new_conversation_agent_runtime_id": "",
                "new_conversation_agent_session_id": "",
                "new_conversation_thread_id": "",
            }
        )
    )


async def _resolve_session_owner_profile_id(store_group, task_id: str) -> str:
    task = await store_group.task_store.get_task(task_id)
    anchored_owner_profile_id = ""
    if task is not None:
        project = await store_group.project_store.resolve_project_for_scope(task.scope_id)
        resolved_project_id = project.project_id if project is not None else None
        session_candidates = await store_group.agent_context_store.list_agent_sessions(
            legacy_session_id=task.thread_id,
            project_id=resolved_project_id,
            limit=8,
        )
        if not session_candidates and resolved_project_id:
            session_candidates = await store_group.agent_context_store.list_agent_sessions(
                legacy_session_id=task.thread_id,
                limit=8,
            )
        for session in session_candidates:
            runtime = await store_group.agent_context_store.get_agent_runtime(
                session.agent_runtime_id
            )
            if runtime is None:
                continue
            profile_id = str(
                runtime.worker_profile_id or runtime.agent_profile_id or ""
            ).strip()
            if not profile_id:
                continue
            if session.kind is AgentSessionKind.DIRECT_WORKER:
                return profile_id
            if not anchored_owner_profile_id and session.kind is AgentSessionKind.MAIN_BOOTSTRAP:
                anchored_owner_profile_id = profile_id

    events = await store_group.event_store.get_events_for_task(task_id)
    for event in reversed(events):
        if event.type is not EventType.USER_MESSAGE:
            continue
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            continue
        control = control_metadata_from_payload(payload)
        explicit_owner = resolve_explicit_session_owner_profile_id(control)
        if explicit_owner:
            return explicit_owner
        legacy_owner = str(control.get("agent_profile_id", "")).strip()
        if not legacy_owner:
            continue
        if await store_group.agent_context_store.get_worker_profile(legacy_owner):
            continue
        return legacy_owner
    return anchored_owner_profile_id


async def _resolve_profile_model_alias(store_group, profile_id: str) -> str:
    resolved_profile_id = str(profile_id or "").strip()
    if not resolved_profile_id:
        return ""

    worker_profile = await store_group.agent_context_store.get_worker_profile(resolved_profile_id)
    if worker_profile is not None:
        return str(worker_profile.model_alias or "").strip()

    agent_profile = await store_group.agent_context_store.get_agent_profile(resolved_profile_id)
    if agent_profile is not None:
        return str(agent_profile.model_alias or "").strip()

    return ""


async def _resolve_owner_turn_executor_kind(store_group, profile_id: str) -> TurnExecutorKind:
    resolved_profile_id = str(profile_id or "").strip()
    if not resolved_profile_id:
        return TurnExecutorKind.SELF
    worker_profile = await store_group.agent_context_store.get_worker_profile(
        resolved_profile_id
    )
    if worker_profile is not None:
        return TurnExecutorKind.WORKER
    return TurnExecutorKind.SELF


def _build_project_scoped_chat_scope_id(
    *,
    project_id: str,
    channel: str,
    thread_id: str,
) -> str:
    """构建 project 维度的 chat scope_id。"""
    return f"project:{project_id}:chat:{channel}:{thread_id}"


async def _record_web_conversation_binding(
    store_group,
    *,
    owner_turn_executor_kind: TurnExecutorKind,
    thread_id: str,
    scope_id: str,
    project_id: str,
) -> None:
    """F105 FR-E3：登记/touch web 会话路由绑定（OC-2 + OC-6 last-route 状态）。

    H1 排除（Codex pre-impl H4）：direct-worker 会话
    （owner_turn_executor_kind=WORKER，用户显式选 worker 直聊）不写 binding——
    ConversationBinding 只登记主 Agent 默认路由，worker 会话不得伪装成
    主 Agent last-route。失败 WARNING 降级，不阻断消息主链（Constitution #6）。
    """
    if owner_turn_executor_kind is TurnExecutorKind.WORKER:
        logger.debug(
            "web_conversation_binding_skipped_direct_worker thread_id=%s", thread_id
        )
        return
    binding_store = getattr(store_group, "conversation_binding_store", None)
    if binding_store is None:
        return
    try:
        await binding_store.upsert_runtime_binding(
            "web",
            thread_id,
            scope_id=scope_id,
            project_id=project_id,
        )
    except Exception:
        logger.warning(
            "web_conversation_binding_failed thread_id=%s", thread_id, exc_info=True
        )


def _parse_projected_session_ref(session_id: str) -> tuple[str, str]:
    thread_id = ""
    project_id = ""
    for segment in str(session_id or "").split("|"):
        normalized = segment.strip()
        if normalized.startswith("thread:") and not thread_id:
            thread_id = normalized.removeprefix("thread:").strip()
        elif normalized.startswith("project:") and not project_id:
            project_id = normalized.removeprefix("project:").strip()
    return thread_id, project_id


async def _resolve_session_owner_profile_id_from_session_ref(
    store_group,
    *,
    session_id: str,
    thread_id: str,
    project_id: str,
) -> str:
    resolved_thread_id = str(thread_id or "").strip()
    resolved_project_id = str(project_id or "").strip()
    if not resolved_thread_id:
        resolved_thread_id, parsed_project_id = _parse_projected_session_ref(session_id)
        resolved_project_id = resolved_project_id or parsed_project_id
    if not resolved_thread_id:
        return ""

    session_candidates = await store_group.agent_context_store.list_agent_sessions(
        legacy_session_id=resolved_thread_id,
        project_id=resolved_project_id or None,
        limit=8,
    )
    if not session_candidates and resolved_project_id:
        session_candidates = await store_group.agent_context_store.list_agent_sessions(
            legacy_session_id=resolved_thread_id,
            limit=8,
        )

    for session in session_candidates:
        runtime = await store_group.agent_context_store.get_agent_runtime(session.agent_runtime_id)
        if runtime is None:
            continue
        profile_id = str(runtime.agent_profile_id or runtime.worker_profile_id or "").strip()
        if profile_id:
            return profile_id
    return ""


@router.post("/api/chat/send", response_model=ChatSendResponse)
async def send_chat_message(
    body: ChatSendRequest,
    request: Request,
    store_group=Depends(get_store_group),
) -> ChatSendResponse:
    """发送聊天消息

    FR-023: 接收消息，创建/复用 Task，返回 stream_url。
    前端使用 EventSource 连接 stream_url 获取流式输出。
    """
    chat_control_metadata: dict[str, Any] = {}
    requested_session_id = str(body.session_id or "").strip()
    requested_thread_id = str(body.thread_id or "").strip()
    parsed_thread_id, parsed_project_id = _parse_projected_session_ref(requested_session_id)
    if not requested_thread_id:
        requested_thread_id = parsed_thread_id
    requested_agent_profile_id = str(body.agent_profile_id or "").strip()
    (
        new_conversation_token,
        project_id,
        requested_agent_profile_id,
        new_conversation_agent_runtime_id,
        new_conversation_agent_session_id,
        new_conversation_thread_id,
    ) = await _resolve_chat_scope_snapshot(
        body,
        request,
        store_group,
    )
    project_id = project_id or parsed_project_id
    # Path A 创建的新会话里 thread_id_seed 是真身，不能被后面 `requested_thread_id or task_id`
    # fallback 成 task_id，否则 session_state 索引不回来、runtime/session ids 的 lookup key
    # 漂移，又会触发 Path B 建第二条 row。
    if new_conversation_thread_id and not requested_thread_id:
        requested_thread_id = new_conversation_thread_id
    if body.task_id and not requested_agent_profile_id:
        requested_agent_profile_id = await _resolve_session_owner_profile_id(
            store_group, body.task_id
        )
    elif not body.task_id and not requested_agent_profile_id:
        requested_agent_profile_id = await _resolve_session_owner_profile_id_from_session_ref(
            store_group,
            session_id=requested_session_id,
            thread_id=requested_thread_id,
            project_id=project_id,
        )
    requested_model_alias = await _resolve_profile_model_alias(
        store_group,
        requested_agent_profile_id,
    )
    owner_turn_executor_kind = await _resolve_owner_turn_executor_kind(
        store_group,
        requested_agent_profile_id,
    )
    if requested_agent_profile_id:
        chat_control_metadata["session_owner_profile_id"] = requested_agent_profile_id
        chat_control_metadata["agent_profile_id"] = requested_agent_profile_id
    if requested_session_id:
        chat_control_metadata["session_id"] = requested_session_id
    if requested_thread_id:
        chat_control_metadata["thread_id"] = requested_thread_id
    if project_id:
        chat_control_metadata["project_id"] = project_id

    # F101 Phase A FR-D1/D2/D3：长 prompt 自动触发完整决策环
    # H-A1 修复：写入 chat_control_metadata（持久化到 USER_MESSAGE event 的 control_metadata），
    # 而非 dispatch_metadata 临时副本——后者在 _enqueue_or_run → task_runner.enqueue 路径中被丢弃。
    # task_runner 通过 TaskService.get_latest_user_metadata(task_id) 读取 USER_MESSAGE control_metadata，
    # orchestrator._with_delegation_mode 据此触发 FR-H force_full_recall hint。
    if len(body.message) > _resolve_long_prompt_threshold():
        chat_control_metadata["force_full_recall"] = True

    # 确定 task_id（复用已有或创建新的）
    task_id = body.task_id or f"task-{uuid.uuid4().hex[:12]}"
    after_event_id = ""
    from ..services.task_service import TaskService

    service = TaskService(store_group, request.app.state.sse_hub)

    # 创建 Task 记录（如果是新对话）
    if not body.task_id:
        # F105 FR-D2：NormalizedMessage 构造经 web adapter 工厂（module-level
        # import，channel="web"/sender 字面量收敛进 channels.web_adapter；
        # scope_id 构造留本 call site，OPUS-L1 边界）。
        from ..channels.web_adapter import build_web_inbound_message

        effective_thread_id = requested_thread_id or task_id
        effective_scope_id = (
            _build_project_scoped_chat_scope_id(
                project_id=project_id,
                channel="web",
                thread_id=effective_thread_id,
            )
            if project_id
            else f"chat:web:{effective_thread_id}"
        )

        msg = build_web_inbound_message(
            thread_id=effective_thread_id,
            scope_id=effective_scope_id,
            text=body.message,
            control_metadata=chat_control_metadata,
            idempotency_key=f"chat-{task_id}",
        )

        try:
            created_task_id, created = await service.create_task(msg)
        except Exception as exc:
            logger.warning("chat_send_create_failed", exc_info=True)
            raise _chat_send_failure(
                status_code=500,
                code="CHAT_TASK_CREATE_FAILED",
                message="任务未创建或未进入执行主链。",
                task_id=task_id if task_id.startswith("01") else None,
            ) from exc
        if created:
            task_id = created_task_id
            # F105 FR-E3：登记 web 会话路由绑定（direct-worker 排除在 helper 内）
            await _record_web_conversation_binding(
                store_group,
                owner_turn_executor_kind=owner_turn_executor_kind,
                thread_id=effective_thread_id,
                scope_id=effective_scope_id,
                project_id=project_id,
            )
            dispatch_metadata = dict(chat_control_metadata)
            runtime_metadata: dict[str, Any] = {}
            if new_conversation_token:
                runtime_metadata["new_conversation_token"] = new_conversation_token
            # 把 Path A 写到 state 的 ids 透传给 Path B（_ensure_agent_runtime/
            # _ensure_agent_session 会优先用 request.agent_runtime_id / agent_session_id,
            # 从而直接复用 Path A 创建的 ULID row 而不是建第二条）。
            if new_conversation_agent_runtime_id:
                runtime_metadata["agent_runtime_id"] = new_conversation_agent_runtime_id
            if new_conversation_agent_session_id:
                runtime_metadata["agent_session_id"] = new_conversation_agent_session_id
            # F101 Phase A FR-D1：force_full_recall 已通过 chat_control_metadata 写入 USER_MESSAGE event
            # （见 chat_control_metadata 初始化段；dict(chat_control_metadata) 已自动继承该字段）。
            dispatch_metadata[RUNTIME_CONTEXT_JSON_KEY] = encode_runtime_context(
                RuntimeControlContext(
                    task_id=task_id,
                    surface="web",
                    scope_id=effective_scope_id,
                    thread_id=effective_thread_id,
                    project_id=project_id,
                    session_owner_profile_id=requested_agent_profile_id,
                    turn_executor_kind=owner_turn_executor_kind,
                    agent_profile_id=requested_agent_profile_id,
                    metadata=runtime_metadata,
                )
            )
            try:
                await _enqueue_or_run(
                    request,
                    service,
                    task_id,
                    body.message,
                    model_alias=requested_model_alias or None,
                    dispatch_metadata=dispatch_metadata,
                )
            except Exception as exc:
                logger.warning("chat_send_enqueue_failed", exc_info=True)
                raise _chat_send_failure(
                    status_code=500,
                    code="CHAT_TASK_ENQUEUE_FAILED",
                    message="任务已创建但未能进入执行主链。",
                    task_id=task_id,
                ) from exc
            # task 已成功入队 → token 才正式失效，前序步骤失败时 token 仍可被重试。
            if new_conversation_token:
                _consume_new_conversation_token(request, new_conversation_token)
    else:
        try:
            existing_task = await store_group.task_store.get_task(task_id)
            if existing_task is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Task not found: {task_id}",
                )
            after_event_id = existing_task.pointers.latest_event_id
            await service.append_user_message(
                task_id=task_id,
                text=body.message,
                control_metadata=chat_control_metadata,
            )
            # F105 FR-E3：续聊同样 touch last-route（direct-worker 排除在 helper 内）。
            # project_id 必须从 existing_task.scope_id 反解而非请求变量（Codex Final
            # H1）：纯 task_id 续聊时请求侧 project_id 为空，会写出 (web, thread, '')
            # 第二行（四元组含 project_id），污染 last-route——从 scope 反解与该行
            # 首条创建时的 project 语义恒一致（legacy scope 反解为 '' 同样一致）。
            binding_project = await store_group.project_store.resolve_project_for_scope(
                existing_task.scope_id
            )
            await _record_web_conversation_binding(
                store_group,
                owner_turn_executor_kind=owner_turn_executor_kind,
                thread_id=existing_task.thread_id,
                scope_id=existing_task.scope_id,
                project_id=binding_project.project_id if binding_project else "",
            )
            dispatch_metadata = dict(chat_control_metadata)
            # F101 Phase A FR-D2：force_full_recall 已通过 chat_control_metadata 写入 USER_MESSAGE event
            # （append_user_message 已传 control_metadata=chat_control_metadata；dict 副本自动继承）。
            if project_id:
                dispatch_metadata[RUNTIME_CONTEXT_JSON_KEY] = encode_runtime_context(
                    RuntimeControlContext(
                        task_id=task_id,
                        surface="web",
                        thread_id=existing_task.thread_id,
                        scope_id=existing_task.scope_id,
                        project_id=project_id,
                        session_owner_profile_id=requested_agent_profile_id,
                        turn_executor_kind=owner_turn_executor_kind,
                        agent_profile_id=requested_agent_profile_id,
                    )
                )
            await _enqueue_or_run(
                request,
                service,
                task_id,
                body.message,
                model_alias=requested_model_alias or None,
                dispatch_metadata=dispatch_metadata,
            )
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("chat_send_continue_failed", exc_info=True)
            raise _chat_send_failure(
                status_code=500,
                code="CHAT_TASK_ENQUEUE_FAILED",
                message="任务已接收但未能进入执行主链。",
                task_id=task_id,
            ) from exc

    stream_url = f"/api/stream/task/{task_id}"
    if after_event_id:
        stream_url = f"{stream_url}?{urlencode({'after_event_id': after_event_id})}"

    return ChatSendResponse(
        task_id=task_id,
        status="accepted",
        stream_url=stream_url,
    )
