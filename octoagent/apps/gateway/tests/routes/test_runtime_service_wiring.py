"""F151 T086：chat/message 路由的 runtime preflight。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks, HTTPException
from octoagent.gateway.routes import chat, message
from octoagent.gateway.services import task_service as task_service_module
from octoagent.gateway.services.runtime_service_bundle import RuntimeServiceBundle
from octoagent.policy.models import ChatSendRequest


class _TaskServiceProbe:
    constructor_calls = 0
    create_calls = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        type(self).constructor_calls += 1

    async def create_task(self, normalized_message: Any) -> tuple[str, bool]:
        del normalized_message
        type(self).create_calls += 1
        return "task-runtime-wiring", True


class _TaskRunnerProbe:
    def __init__(self, runtime_services: RuntimeServiceBundle) -> None:
        self._runtime_services = runtime_services
        self.enqueued: list[tuple[str, str, str | None]] = []

    async def enqueue(
        self,
        task_id: str,
        text: str,
        model_alias: str | None = None,
    ) -> None:
        self.enqueued.append((task_id, text, model_alias))


def _request_state(
    *,
    runtime_services: RuntimeServiceBundle | None,
    task_runner: Any | None,
) -> Any:
    state = SimpleNamespace(
        runtime_services=runtime_services,
        task_runner=task_runner,
        llm_service=(runtime_services.llm_service if runtime_services else object()),
        sse_hub=object(),
        project_root=None,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


async def _call_message(request: Any) -> None:
    await message.receive_message(
        message.MessageRequest(text="message", idempotency_key="message-1"),
        request,
        BackgroundTasks(),
        store_group=object(),
        sse_hub=object(),
    )


async def _call_chat(request: Any) -> None:
    await chat.send_chat_message(
        ChatSendRequest(message="chat"),
        request,
        store_group=object(),
    )


async def _expect_preflight_rejection(call: Any, request: Any, issues: list[str]) -> None:
    before = (_TaskServiceProbe.constructor_calls, _TaskServiceProbe.create_calls)
    try:
        await call(request)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if exc.status_code != 503 or detail.get("code") != "RUNTIME_SERVICES_NOT_READY":
            issues.append(f"unexpected preflight rejection: {exc.status_code}/{exc.detail!r}")
    except Exception as exc:
        issues.append(f"{call.__name__} reached post-create fallback: {type(exc).__name__}")
    else:
        issues.append(f"{call.__name__} accepted an unavailable runtime")
    after = (_TaskServiceProbe.constructor_calls, _TaskServiceProbe.create_calls)
    if after != before:
        issues.append(f"{call.__name__} created TaskService/Task before runtime preflight")


@pytest.mark.asyncio
async def test_chat_and_message_require_task_runner_before_task_creation_and_use_bundle_background_registry(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _TaskServiceProbe.constructor_calls = 0
    _TaskServiceProbe.create_calls = 0
    monkeypatch.setattr(message, "TaskService", _TaskServiceProbe)
    monkeypatch.setattr(task_service_module, "TaskService", _TaskServiceProbe)
    monkeypatch.setattr(chat, "_resolve_chat_scope_snapshot", _empty_chat_scope)
    monkeypatch.setattr(chat, "_resolve_profile_model_alias", _empty_model_alias)
    monkeypatch.setattr(chat, "_resolve_owner_turn_executor_kind", _self_executor)
    monkeypatch.setattr(chat, "_record_web_conversation_binding", _ignore_binding)

    issues: list[str] = []
    missing = _request_state(runtime_services=None, task_runner=None)
    await _expect_preflight_rejection(_call_message, missing, issues)
    await _expect_preflight_rejection(_call_chat, missing, issues)

    bundle = RuntimeServiceBundle(object(), object(), set())
    mismatched = _request_state(
        runtime_services=bundle,
        task_runner=_TaskRunnerProbe(RuntimeServiceBundle(object(), object(), set())),
    )
    await _expect_preflight_rejection(_call_message, mismatched, issues)
    await _expect_preflight_rejection(_call_chat, mismatched, issues)

    runner = _TaskRunnerProbe(bundle)
    valid = _request_state(runtime_services=bundle, task_runner=runner)
    await _call_message(valid)
    await _call_chat(valid)
    if len(runner.enqueued) != 2:
        issues.append(f"valid routes enqueued {len(runner.enqueued)} tasks, expected 2")
    if runner._runtime_services.background_tasks is not bundle.background_tasks:
        issues.append("TaskRunner did not retain the bundle background registry")
    if hasattr(chat, "_background_tasks"):
        issues.append("chat route retains a second module-level background registry")

    if issues:
        pytest.fail(f"F151_ROUTE_RUNTIME_WIRING_MISSING: {'; '.join(issues)}", pytrace=False)


async def _empty_chat_scope(*args: Any, **kwargs: Any) -> tuple[str, str, str, str, str, str]:
    del args, kwargs
    return "", "", "", "", "", ""


async def _empty_model_alias(*args: Any, **kwargs: Any) -> str:
    del args, kwargs
    return ""


async def _self_executor(*args: Any, **kwargs: Any) -> Any:
    del args, kwargs
    return chat.TurnExecutorKind.SELF


async def _ignore_binding(*args: Any, **kwargs: Any) -> None:
    del args, kwargs
