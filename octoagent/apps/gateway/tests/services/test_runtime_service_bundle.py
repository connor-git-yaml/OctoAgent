"""F151 RuntimeServiceBundle 模式与 storage-only 构造合同。"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_XOR_ORACLE = "F151_RUNTIME_BUNDLE_XOR_MISSING"
_STORAGE_ORACLE = "F151_AGENT_CONTEXT_STORAGE_ONLY_CONSTRUCTS_RUNTIME_CAPABILITY"
_DUPLICATE_ORACLE = "F151_DUPLICATE_TEST_QUALNAME_SHADOWS_RUNTIME_CONTRACT"
_PRODUCTION_CONSTRUCTOR_ORACLE = "F151_TASK_SERVICE_TARGET_45_3_42_NOT_REACHED"
_TEST_CONSTRUCTOR_ORACLE = "F151_TEST_RUNTIME_CONSTRUCTORS_UNCLASSIFIED"
_CONTEXT_TEST = "octoagent/apps/gateway/tests/test_task_service_context_integration.py"
_RUNTIME_FIXTURE = "octoagent/apps/gateway/tests/runtime_service_fixtures.py"
_RESTORED_NODES = (
    "test_worker_tool_writeback_and_private_memory_are_isolated_across_sessions",
    "test_task_service_prompt_context_only_exposes_sanitized_control_metadata",
)


@dataclass(frozen=True, slots=True)
class _ConstructorCall:
    path: str
    qualname: str
    definition_ordinal: int
    ordinal: int
    mode: str
    runtime_expression: ast.expr | None


class _ConstructorVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self._path = path
        self._qualname: list[str] = []
        self._definition_ordinals: list[int] = []
        self._definition_counts: Counter[str] = Counter()
        self._constructor_counts: Counter[tuple[str, int, str]] = Counter()
        self.calls: list[_ConstructorCall] = []
        self.llm_overrides: list[tuple[str, str, int]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._qualname.append(node.name)
        self.generic_visit(node)
        self._qualname.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._qualname.append(node.name)
        qualname = ".".join(self._qualname)
        self._definition_counts[qualname] += 1
        self._definition_ordinals.append(self._definition_counts[qualname])
        self.generic_visit(node)
        self._definition_ordinals.pop()
        self._qualname.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        if name in {"TaskService", "AgentContextService"}:
            qualname = ".".join(self._qualname) or "<module>"
            definition_ordinal = self._definition_ordinals[-1] if self._definition_ordinals else 1
            key = (qualname, definition_ordinal, name)
            self._constructor_counts[key] += 1
            mode, runtime_expression = _constructor_mode(node)
            self.calls.append(
                _ConstructorCall(
                    path=self._path,
                    qualname=qualname,
                    definition_ordinal=definition_ordinal,
                    ordinal=self._constructor_counts[key],
                    mode=f"{name}:{mode}",
                    runtime_expression=runtime_expression,
                )
            )
        if name == "process_task_with_llm" and any(
            keyword.arg == "llm_service" for keyword in node.keywords
        ):
            self.llm_overrides.append((self._path, ".".join(self._qualname), node.lineno))
        self.generic_visit(node)


class _CloseProbe:
    def __init__(self) -> None:
        self.close_count = 0

    async def aclose(self) -> None:
        self.close_count += 1


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _constructor_mode(node: ast.Call) -> tuple[str, ast.expr | None]:
    if any(keyword.arg is None for keyword in node.keywords):
        return "unknown", None
    keywords = {keyword.arg: keyword.value for keyword in node.keywords}
    runtime_expression = keywords.get("runtime_services")
    has_runtime = runtime_expression is not None and not (
        isinstance(runtime_expression, ast.Constant) and runtime_expression.value is None
    )
    storage_expression = keywords.get("storage_only")
    has_storage = isinstance(storage_expression, ast.Constant) and storage_expression.value is True
    if has_runtime == has_storage:
        return "unknown", runtime_expression
    return ("runtime-bundle", runtime_expression) if has_runtime else ("storage-only", None)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _inventory(name: str) -> dict[str, Any]:
    path = (
        _repo_root()
        / ".specify/features/151-runtime-boundary-architecture-truth/inventories"
        / name
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _scan_constructors(
    paths: set[str],
) -> tuple[list[_ConstructorCall], list[tuple[str, str, int]]]:
    calls: list[_ConstructorCall] = []
    overrides: list[tuple[str, str, int]] = []
    for path in sorted(paths):
        source_path = _repo_root() / path
        visitor = _ConstructorVisitor(path)
        visitor.visit(ast.parse(source_path.read_text(encoding="utf-8")))
        calls.extend(visitor.calls)
        overrides.extend(visitor.llm_overrides)
    return calls, overrides


def _path_qualname_counts(
    calls: list[_ConstructorCall],
    *,
    service: str,
) -> Counter[tuple[str, str]]:
    return Counter(
        (call.path, call.qualname) for call in calls if call.mode.startswith(f"{service}:")
    )


def _fail(oracle: str, reason: str) -> None:
    pytest.fail(f"{oracle}: {reason}", pytrace=False)


def _patch_runtime_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    from octoagent.gateway.services import agent_context as agent_context_module
    from octoagent.gateway.services.agent_context_memory_services import (
        AgentContextMemoryServiceMixin,
    )

    monkeypatch.setattr(
        AgentContextMemoryServiceMixin,
        "get_reranker_service",
        lambda _self: object(),
    )
    monkeypatch.setattr(
        agent_context_module,
        "MemoryRuntimeService",
        lambda *_args, **_kwargs: object(),
    )


def _runtime_contract() -> tuple[type[Any], type[Exception]]:
    try:
        from octoagent.gateway.services.runtime_service_bundle import (
            RuntimeServiceBundle,
            RuntimeServiceModeError,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        _fail(_XOR_ORACLE, f"missing runtime bundle contract: {exc}")
    return RuntimeServiceBundle, RuntimeServiceModeError


def _assert_mode_matrix(
    constructor: Any,
    *,
    bundle: Any,
    error_type: type[Exception],
) -> tuple[Any, Any]:
    with pytest.raises(error_type):
        constructor()
    with pytest.raises(error_type):
        constructor(storage_only=False)
    with pytest.raises(error_type):
        constructor(runtime_services=None)
    with pytest.raises(error_type):
        constructor(runtime_services=bundle, storage_only=True)

    storage_service = constructor(storage_only=True)
    runtime_service = constructor(runtime_services=bundle)
    assert storage_service._storage_only is True
    assert storage_service._runtime_services is None
    assert runtime_service._storage_only is False
    assert runtime_service._runtime_services is bundle
    return storage_service, runtime_service


@pytest.mark.asyncio
async def test_runtime_bundle_is_minimal_instance_holder_and_task_service_and_agent_context_require_exactly_one_mode(  # noqa: E501
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        runtime_bundle_type, mode_error = _runtime_contract()
        _patch_runtime_construction(monkeypatch)
        from octoagent.gateway.services.agent_context import AgentContextService
        from octoagent.gateway.services.task_service import TaskService

        llm = _CloseProbe()
        router = _CloseProbe()
        background_tasks: set[asyncio.Task[Any]] = set()
        bundle = runtime_bundle_type(
            llm_service=llm,
            provider_router=router,
            background_tasks=background_tasks,
        )
        assert bundle.llm_service is llm
        assert bundle.provider_router is router
        assert bundle.background_tasks is background_tasks

        stores = SimpleNamespace(conn=object())
        task_storage, task_runtime = _assert_mode_matrix(
            lambda **kwargs: TaskService(
                stores,
                project_root=tmp_path,
                **kwargs,
            ),
            bundle=bundle,
            error_type=mode_error,
        )
        context_storage, context_runtime = _assert_mode_matrix(
            lambda **kwargs: AgentContextService(
                stores,
                project_root=tmp_path,
                **kwargs,
            ),
            bundle=bundle,
            error_type=mode_error,
        )
        assert task_storage._agent_context._storage_only is True
        assert task_runtime._agent_context._runtime_services is bundle
        assert context_storage._llm_service is None
        assert context_runtime._llm_service is llm

        await bundle.aclose()
        await bundle.aclose()
        assert llm.close_count == 1
        assert router.close_count == 1
    except pytest.fail.Exception:
        raise
    except Exception as exc:
        _fail(_XOR_ORACLE, f"runtime bundle XOR contract incomplete: {exc}")


@pytest.mark.asyncio
async def test_agent_context_storage_only_constructs_no_memory_runtime_reranker_background_task_or_network(  # noqa: E501
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        from octoagent.gateway.services import agent_context as agent_context_module
        from octoagent.gateway.services.agent_context import AgentContextService
        from octoagent.gateway.services.inference import model_reranker_service
        from octoagent.gateway.services.runtime_service_bundle import RuntimeServiceModeError

        constructed: list[str] = []

        class _ForbiddenMemoryRuntime:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                constructed.append("memory-runtime")

        class _ForbiddenReranker:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                constructed.append("reranker")

        def _forbidden_create_task(coro: Any, *_args: Any, **_kwargs: Any) -> Any:
            if hasattr(coro, "close"):
                coro.close()
            constructed.append("background-task")
            raise AssertionError("storage-only constructor spawned background task")

        monkeypatch.setattr(
            agent_context_module,
            "MemoryRuntimeService",
            _ForbiddenMemoryRuntime,
        )
        monkeypatch.setattr(
            model_reranker_service,
            "ModelRerankerService",
            _ForbiddenReranker,
        )
        monkeypatch.setattr(asyncio, "create_task", _forbidden_create_task)
        monkeypatch.setattr(AgentContextService, "_shared_llm_service", object())
        monkeypatch.setattr(AgentContextService, "_shared_provider_router", object())
        monkeypatch.setattr(AgentContextService, "_shared_background_tasks", set())

        service = AgentContextService(
            SimpleNamespace(conn=object()),
            project_root=tmp_path,
            storage_only=True,
        )
        assert constructed == []
        assert service._llm_service is None
        assert service._provider_router is None
        assert service._memory_runtime is None

        with pytest.raises(RuntimeServiceModeError):
            service.get_reranker_service()
        with pytest.raises(RuntimeServiceModeError):
            await service.get_memory_service(project=None)
        with pytest.raises(RuntimeServiceModeError):
            await service.build_task_context(task=None, compiled=None)  # type: ignore[arg-type]
        assert constructed == []
    except pytest.fail.Exception:
        raise
    except Exception as exc:
        _fail(_STORAGE_ORACLE, f"storage-only purity contract incomplete: {exc}")


def test_runtime_test_definitions_have_unique_collectable_qualnames() -> None:
    repo_root = _repo_root()
    test_path = repo_root / _CONTEXT_TEST
    tree = ast.parse(test_path.read_text(encoding="utf-8"))
    names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        _fail(_DUPLICATE_ORACLE, f"duplicate module test qualnames: {duplicates}")

    selectors = [f"{_CONTEXT_TEST}::{name}" for name in _RESTORED_NODES]
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *selectors],
        cwd=repo_root,
        env=os.environ.copy(),
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    collected = set(completed.stdout.splitlines())
    for selector in selectors:
        assert selector.removeprefix("octoagent/") in collected


def test_task_service_constructor_inventory_targets_three_runtime_and_forty_two_storage() -> None:
    inventory = _inventory("runtime-operation-modes.v1.json")
    task_universe = inventory["task_service_constructor_universe"]
    runtime_entries = task_universe["runtime_bundle_callsites"]
    storage_entries = [
        {"path": group["path"], **callsite}
        for group in task_universe["storage_only_callsites_by_file"]
        for callsite in group["callsites"]
    ]
    agent_entries = inventory["agent_context_constructor_universe"]["direct_storage_only_callsites"]
    paths = {entry["path"] for entry in [*runtime_entries, *storage_entries, *agent_entries]}
    calls, _overrides = _scan_constructors(paths)

    expected_task = Counter(
        (entry["path"], entry["enclosing_qualname"])
        for entry in [*runtime_entries, *storage_entries]
    )
    actual_task = _path_qualname_counts(calls, service="TaskService")
    task_modes = Counter(
        call.mode.removeprefix("TaskService:")
        for call in calls
        if call.mode.startswith("TaskService:")
    )
    expected_agent = Counter(
        (entry["path"], entry["enclosing_qualname"]) for entry in agent_entries
    )
    actual_agent = _path_qualname_counts(calls, service="AgentContextService")
    agent_modes = Counter(
        call.mode.removeprefix("AgentContextService:")
        for call in calls
        if call.mode.startswith("AgentContextService:")
    )
    if (
        actual_task != expected_task
        or task_modes != Counter({"runtime-bundle": 3, "storage-only": 42})
        or actual_agent != expected_agent
        or agent_modes != Counter({"storage-only": 3})
    ):
        _fail(
            _PRODUCTION_CONSTRUCTOR_ORACLE,
            "production constructor target mismatch: "
            f"task-count={sum(actual_task.values())}, task-modes={dict(task_modes)}, "
            f"task-missing={list((expected_task - actual_task).elements())[:5]}, "
            f"task-extra={list((actual_task - expected_task).elements())[:5]}, "
            f"agent-count={sum(actual_agent.values())}, agent-modes={dict(agent_modes)}",
        )


def test_runtime_test_constructor_inventory_has_no_unknown_mode_or_llm_override() -> None:
    task_inventory = _inventory("runtime-test-constructors.v1.json")
    agent_inventory = _inventory("agent-context-test-constructors.v1.json")
    behavior_inventory = _inventory("runtime-test-behavior-owners.v1.json")
    task_paths = {entry["path"] for entry in task_inventory["entries"]}
    agent_paths = {entry["projected_path"] for entry in agent_inventory["entries"]}
    owner_paths = {entry["owner_path"] for entry in behavior_inventory["owners"]}
    constructor_paths = task_paths | agent_paths
    calls, overrides = _scan_constructors(constructor_paths)
    unknown = [call for call in calls if call.mode.endswith(":unknown")]
    if owner_paths != constructor_paths or unknown or overrides:
        _fail(
            _TEST_CONSTRUCTOR_ORACLE,
            "test constructor inventory mismatch: "
            f"owner-missing={sorted(constructor_paths - owner_paths)}, "
            f"owner-extra={sorted(owner_paths - constructor_paths)}, "
            f"unknown={[(item.path, item.qualname) for item in unknown[:5]]}, "
            f"llm-overrides={overrides[:5]}",
        )


def test_runtime_test_factory_shares_exact_bundle_llm_router_and_background_identity() -> None:
    fixture_path = _repo_root() / _RUNTIME_FIXTURE
    if not fixture_path.is_file():
        _fail(_TEST_CONSTRUCTOR_ORACLE, f"missing typed test fixture: {_RUNTIME_FIXTURE}")
    spec = importlib.util.spec_from_file_location("f151_runtime_service_fixtures", fixture_path)
    if spec is None or spec.loader is None:
        _fail(_TEST_CONSTRUCTOR_ORACLE, "typed test fixture is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    llm = object()
    router = object()
    background_tasks: set[asyncio.Task[Any]] = set()
    fixture = module.runtime_service_fixture(
        llm_service=llm,
        provider_router=router,
        background_tasks=background_tasks,
    )
    assert fixture.llm_service is llm
    assert fixture.provider_router is router
    assert fixture.background_tasks is background_tasks
    assert fixture.bundle.llm_service is llm
    assert fixture.bundle.provider_router is router
    assert fixture.bundle.background_tasks is background_tasks


def test_e2e_live_factory_uses_explicit_runtime_bundle_without_live_io() -> None:
    calls, _overrides = _scan_constructors(
        {"octoagent/apps/gateway/tests/e2e_live/helpers/factories.py"}
    )
    task_calls = [call for call in calls if call.mode.startswith("TaskService:")]
    assert len(task_calls) == 1
    if task_calls[0].mode != "TaskService:storage-only":
        _fail(
            _TEST_CONSTRUCTOR_ORACLE,
            f"e2e helper constructor mode is {task_calls[0].mode}",
        )
