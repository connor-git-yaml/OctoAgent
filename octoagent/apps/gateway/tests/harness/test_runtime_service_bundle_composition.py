"""F151 T085：Gateway runtime graph 的单一组合根。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from octoagent.gateway import main as gateway_main
from octoagent.gateway.harness.octo_harness import OctoHarness
from octoagent.gateway.services.agent_context import AgentContextService
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.runtime_service_bundle import RuntimeServiceBundle
from octoagent.skills.provider_model_client import ProviderModelClient
from octoagent.skills.runner import SkillRunner


class _SkillRunnerProbe:
    def __init__(self, **kwargs: Any) -> None:
        self.model_client = kwargs["model_client"]
        self.hooks = kwargs["hooks"]


class _DelegationPlaneProbe:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _TaskRunnerProbe:
    def __init__(self, **kwargs: Any) -> None:
        self.runtime_services = kwargs["runtime_services"]
        self.execution_console = object()
        self._orchestrator = SimpleNamespace()

    async def startup(self) -> None:
        return None


class _TelegramProbe:
    def bind_task_runner(self, task_runner: Any) -> None:
        self.task_runner = task_runner

    def bind_notification_service(self, notification_service: Any) -> None:
        self.notification_service = notification_service


class _CapabilityPackProbe:
    def __init__(self) -> None:
        self._tool_deps = None
        self.delegation_plane: Any | None = None
        self.task_runner: Any | None = None

    def bind_delegation_plane(self, delegation_plane: Any) -> None:
        self.delegation_plane = delegation_plane

    def bind_task_runner(self, task_runner: Any) -> None:
        self.task_runner = task_runner

    async def refresh(self) -> None:
        return None


class _PlatformRegistryProbe:
    def list_adapters(self) -> list[Any]:
        return []

    async def notify_task_completion(self, task_id: str) -> None:
        del task_id


class _RouterCloseProbe:
    def __init__(self) -> None:
        self.aclose_calls = 0
        self.invalidated: list[str] = []

    def invalidate_task(self, key: str) -> None:
        self.invalidated.append(key)

    async def aclose(self) -> None:
        self.aclose_calls += 1


def _prepared_composition(
    tmp_path: Any,
) -> tuple[OctoHarness, FastAPI, object, set[Any]]:
    model_client = object()
    provider_router = object()
    background_tasks: set[Any] = set()
    harness = OctoHarness(tmp_path, model_client=model_client)
    harness._store_group = SimpleNamespace(event_store=object(), notification_store=None)
    harness._tool_broker = object()
    harness._snapshot_store = object()
    harness._fallback_manager = None
    harness._alias_registry = None
    harness._llm_mode_env = "echo"
    harness._platform_registry = _PlatformRegistryProbe()
    harness._telegram_service = _TelegramProbe()

    app = FastAPI()
    app.state.provider_router = provider_router
    app.state.background_tasks = background_tasks
    app.state.skill_discovery = object()
    app.state.sse_hub = object()
    app.state.approval_manager = object()
    app.state.capability_pack_service = _CapabilityPackProbe()
    app.state.pipeline_registry = None
    return harness, app, provider_router, background_tasks


@pytest.mark.asyncio
async def test_composition_root_shares_one_bundle_llm_router_and_background_registry(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness, app, provider_router, background_tasks = _prepared_composition(tmp_path)
    shared_setter_calls: list[Any] = []

    monkeypatch.setattr(gateway_main, "SkillRunner", _SkillRunnerProbe)
    monkeypatch.setattr(gateway_main, "DelegationPlaneService", _DelegationPlaneProbe)
    monkeypatch.setattr(gateway_main, "TaskRunner", _TaskRunnerProbe)
    monkeypatch.setattr(
        AgentContextService,
        "set_llm_service",
        classmethod(lambda cls, value: shared_setter_calls.append(value)),
    )

    await harness._bootstrap_executors(app)

    issues: list[str] = []
    bundle = getattr(app.state, "runtime_services", None)
    if not isinstance(bundle, RuntimeServiceBundle):
        issues.append("app.state.runtime_services is not the composition-root bundle")
    else:
        if app.state.task_runner.runtime_services is not bundle:
            issues.append("TaskRunner did not receive the exact composition-root bundle")
        if bundle.llm_service is not app.state.llm_service:
            issues.append("bundle did not receive the final LLMService")
        if bundle.provider_router is not provider_router:
            issues.append("bundle did not preserve ProviderRouter identity")
        if bundle.background_tasks is not background_tasks:
            issues.append("bundle did not preserve the background registry identity")

    skill_runner = getattr(app.state.llm_service, "_skill_runner", None)
    if not isinstance(skill_runner, _SkillRunnerProbe):
        issues.append("final LLMService did not retain the constructed SkillRunner")
    elif len(skill_runner.hooks) != 1:
        issues.append("SkillRunner must receive exactly one storage hook")
    else:
        agent_context = getattr(skill_runner.hooks[0], "_agent_context", None)
        if not getattr(agent_context, "_storage_only", False):
            issues.append("AgentSessionTurnHook is not storage-only")
        if getattr(agent_context, "_runtime_services", object()) is not None:
            issues.append("storage hook unexpectedly received runtime services")

    if shared_setter_calls:
        issues.append("composition wrote the retired AgentContextService class locator")

    if issues:
        pytest.fail(
            f"F151_COMPOSITION_IDENTITY_MISSING: {'; '.join(issues)}",
            pytrace=False,
        )


@pytest.mark.asyncio
async def test_local_llm_aclose_does_not_aclose_shared_router() -> None:
    router = _RouterCloseProbe()
    model_client = ProviderModelClient(provider_router=router)
    model_client._histories["task:trace"] = []
    model_client._last_access["task:trace"] = 1.0
    model_client._fold_meta["task:trace"] = {}
    skill_runner = SkillRunner(model_client=model_client, tool_broker=object())
    llm_service = LLMService(skill_runner=skill_runner)

    issues: list[str] = []
    if hasattr(model_client, "close"):
        issues.append("ProviderModelClient retained the noncanonical close API")
    for owner, value in (
        ("ProviderModelClient", model_client),
        ("SkillRunner", skill_runner),
        ("LLMService", llm_service),
    ):
        if not callable(getattr(value, "aclose", None)):
            issues.append(f"{owner} is missing the canonical aclose API")

    if not issues:
        await llm_service.aclose()
        await llm_service.aclose()
        if model_client._histories or model_client._last_access or model_client._fold_meta:
            issues.append("local model state survived LLMService.aclose")
        if router.aclose_calls:
            issues.append("local LLM teardown closed the shared ProviderRouter")

        bundle = RuntimeServiceBundle(llm_service, router, set())
        await bundle.aclose()
        await bundle.aclose()
        if router.aclose_calls != 1:
            issues.append("RuntimeServiceBundle did not exclusively close the shared router once")

    if issues:
        pytest.fail(
            f"F151_LOCAL_ACLOSE_OWNERSHIP_MISSING: {'; '.join(issues)}",
            pytrace=False,
        )
