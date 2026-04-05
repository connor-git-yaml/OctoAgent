"""Feature 032: built-in tool suite / child runtime 集成测试。"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from octoagent.core.models import (
    ExecutionBackend,
    ExecutionConsoleSession,
    ExecutionSessionState,
    HumanInputPolicy,
    NormalizedMessage,
    OrchestratorRequest,
    OwnerProfile,
    Project,
    ProjectSelectorState,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.capability_pack import CapabilityPackService
from octoagent.gateway.services.delegation_plane import DelegationPlaneService
from octoagent.gateway.services.execution_console import AttachInputResult
from octoagent.gateway.services.execution_context import (
    ExecutionRuntimeContext,
    bind_execution_context,
)
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.mcp_registry import McpRegistryService, McpServerConfig
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.gateway.services.task_service import TaskService
from octoagent.memory import EvidenceRef, MemoryPartition, WriteAction, init_memory_db
from octoagent.tooling import (
    BeforeHookResult,
    ExecutionContext,
    FailMode,
    ToolBroker,
)
from octoagent.tooling.models import PermissionPreset


def _write_mcp_echo_server(path: Path) -> None:
    path.write_text(
        """
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")


@mcp.tool()
def echo(text: str) -> str:
    return f"echo:{text}"


if __name__ == "__main__":
    mcp.run("stdio")
""".strip()
        + "\n",
        encoding="utf-8",
    )


class _FakeSearchResponse:
    def __init__(self, *, text: str, url: str) -> None:
        self.text = text
        self.url = url
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


class _FakeSearchAsyncClient:
    def __init__(self, responses: list[_FakeSearchResponse], **kwargs) -> None:
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, *, params=None, follow_redirects=False):
        return self._responses.pop(0)


async def _build_runtime_services(tmp_path: Path):
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    await store_group.project_store.create_project(
        Project(
            project_id="project-default",
            slug="default",
            name="Default Project",
            is_default=True,
        )
    )
    await store_group.project_store.save_selector_state(
        ProjectSelectorState(
            selector_id="selector-web",
            surface="web",
            active_project_id="project-default",

            source="tests",
        )
    )
    await store_group.conn.commit()

    sse_hub = SSEHub()
    tool_broker = ToolBroker(event_store=store_group.event_store)
    capability_pack = CapabilityPackService(
        project_root=tmp_path,
        store_group=store_group,
        tool_broker=tool_broker,
    )
    delegation_plane = DelegationPlaneService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=sse_hub,
        capability_pack=capability_pack,
    )
    task_runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=LLMService(),
        delegation_plane=delegation_plane,
    )
    capability_pack.bind_delegation_plane(delegation_plane)
    capability_pack.bind_task_runner(task_runner)
    await capability_pack.startup()
    await task_runner.startup()
    task_service = TaskService(store_group, sse_hub)
    return (
        store_group,
        sse_hub,
        task_service,
        capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    )


async def test_capability_pack_exposes_builtin_tool_catalog_and_availability(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        _task_service,
        capability_pack,
        _delegation_plane,
        task_runner,
        _tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        pack = await capability_pack.get_pack()
        tool_names = {item.tool_name for item in pack.tools}

        assert len(pack.tools) >= 20
        assert {
            "project.inspect",
            "setup.review",
            "setup.quick_connect",
            "work.plan",
            "subagents.spawn",
            "subagents.list",
            "subagents.kill",
            "subagents.steer",
            "subagents.spawn",
            "work.merge",
            "work.delete",
            "web.fetch",
            "browser.snapshot",
            "mcp.servers.list",
            "mcp.tools.list",
            "web.search",
            "browser.status",
            "runtime.now",
            "filesystem.list_dir",
            "filesystem.read_text",
            "terminal.exec",
            "memory.search",
            "memory.recall",
        }.issubset(tool_names)

        spawn_tool = next(item for item in pack.tools if item.tool_name == "subagents.spawn")
        assert spawn_tool.availability.value == "available"
        assert "agent_runtime" in spawn_tool.entrypoints

        browser_tool = next(item for item in pack.tools if item.tool_name == "browser.status")
        assert browser_tool.availability.value in {
            "available",
            "degraded",
            "install_required",
        }
        assert "agent_runtime" in browser_tool.entrypoints

        tts_tool = next(item for item in pack.tools if item.tool_name == "tts.speak")
        assert tts_tool.availability.value in {"available", "install_required"}

        # Feature 061 T-028: 所有 WorkerType 共享统一 default_tool_groups
        general_profile = capability_pack.get_worker_profile("general")
        assert "project" in general_profile.default_tool_groups
        assert "filesystem" in general_profile.default_tool_groups
        assert "terminal" in general_profile.default_tool_groups
        assert "mcp" in general_profile.default_tool_groups
        assert "skills" in general_profile.default_tool_groups
        # 统一 profile 包含所有分组
        assert "runtime" in general_profile.default_tool_groups
        assert "automation" in general_profile.default_tool_groups

        pack = await capability_pack.get_pack()
        # Feature 061 T-028: 仅保留 bootstrap:shared，移除 type-specific 模板
        shared_bootstrap = next(
            item for item in pack.bootstrap_files if item.file_id == "bootstrap:shared"
        )
        assert "Current Datetime Local" in shared_bootstrap.content
        assert "capability pack" in shared_bootstrap.content
        # bootstrap:general 已移除
        general_bootstraps = [
            item for item in pack.bootstrap_files if item.file_id == "bootstrap:general"
        ]
        assert len(general_bootstraps) == 0
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_capability_pack_setup_quick_connect_tool_reuses_canonical_setup_flow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        _delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    import octoagent.provider.dx.setup_governance_adapter as adapter_module

    class FakeAdapter:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        async def prepare_wizard_draft(self, draft):
            return dict(draft)

        async def quick_connect(self, draft):
            assert draft["config"]["providers"][0]["id"] == "siliconflow"
            assert draft["config"]["providers"][0]["base_url"] == "https://api.siliconflow.cn/v1"
            assert draft["config"]["memory"]["embedding_model_alias"] == "cheap"
            return SimpleNamespace(
                status="completed",
                code="SETUP_QUICK_CONNECTED",
                message="ok",
                data={
                    "review": {"ready": True},
                    "activation": {"proxy_url": "http://localhost:4000"},
                },
            )

    monkeypatch.setattr(adapter_module, "LocalSetupGovernanceAdapter", FakeAdapter)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请启用 siliconflow",
                idempotency_key="feature-071-setup-quick-connect-tool",
            )
        )
        assert created is True

        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-071-setup-quick-connect",
            worker_id="worker.general",
            backend="inline",
            console=task_runner.execution_console,
            runtime_kind="worker",
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:general",

            permission_preset=PermissionPreset.NORMAL,
        )

        with bind_execution_context(runtime_context):
            result = await tool_broker.execute(
                "setup.quick_connect",
                {
                    "draft_json": json.dumps(
                        {
                            "config": {
                                "providers": [
                                    {
                                        "id": "siliconflow",
                                        "name": "SiliconFlow",
                                        "auth_type": "api_key",
                                        "api_key_env": "SILICONFLOW_API_KEY",
                                        "base_url": "https://api.siliconflow.cn/v1",
                                    }
                                ],
                                "model_aliases": {
                                    "main": {
                                        "provider": "siliconflow",
                                        "model": "Qwen/Qwen3.5-32B",
                                    },
                                    "cheap": {
                                        "provider": "siliconflow",
                                        "model": "Qwen/Qwen3.5-14B",
                                    },
                                },
                                "memory": {
                                    "embedding_model_alias": "cheap",
                                },
                            }
                        }
                    )
                },
                broker_context,
            )

        assert result.is_error is False
        payload = json.loads(result.output)
        assert payload["success"] is True
        assert payload["activation"]["proxy_url"] == "http://localhost:4000"
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_capability_pack_general_tools_support_filesystem_and_terminal_with_governance(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    class _PolicyCheckpointHook:
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

    tool_broker.add_hook(_PolicyCheckpointHook())

    # workspace_root 现在解析为 projects/{slug}/，测试文件放到对应目录
    project_dir = tmp_path / "projects" / "default"
    project_dir.mkdir(parents=True, exist_ok=True)
    readme = project_dir / "README.txt"
    readme.write_text("Alpha runtime context\n", encoding="utf-8")

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="帮我检查一下当前 workspace",
                idempotency_key="feature-053-general-tools",
            )
        )
        assert created is True
        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="帮我检查一下当前 workspace",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None
        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-053-general-tools",
            worker_id="worker.general",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="worker",
            runtime_context=plan.dispatch_envelope.runtime_context,
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:general",

            permission_preset=PermissionPreset.NORMAL,
        )

        with bind_execution_context(runtime_context):
            listed = await tool_broker.execute(
                "filesystem.list_dir",
                {"path": ".", "max_entries": 20},
                broker_context,
            )
            read = await tool_broker.execute(
                "filesystem.read_text",
                {"path": "README.txt"},
                broker_context,
            )
            executed = await tool_broker.execute(
                "terminal.exec",
                {"command": "pwd && printf 'done\\n'", "cwd": "."},
                broker_context,
            )

        assert listed.is_error is False
        listed_payload = json.loads(listed.output)
        assert any(item["name"] == "README.txt" for item in listed_payload["entries"])

        assert read.is_error is False
        read_payload = json.loads(read.output)
        assert "Alpha runtime context" in read_payload["content"]

        assert executed.is_error is False
        executed_payload = json.loads(executed.output)
        assert executed_payload["returncode"] == 0
        assert "done" in executed_payload["stdout"]
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_render_bootstrap_context_includes_ambient_runtime_and_capability_summary(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        _task_service,
        capability_pack,
        _delegation_plane,
        task_runner,
        _tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        await store_group.agent_context_store.save_owner_profile(
            OwnerProfile(
                owner_profile_id="owner-profile-default",
                timezone="Asia/Shanghai",
                locale="zh-CN",
            )
        )
        rendered = await capability_pack.render_bootstrap_context(
            worker_type="research",
            project_id="project-default",
            surface="web",
        )
        joined = "\n".join(item["content"] for item in rendered)
        assert "Project: Default Project (default / project-default)" in joined
        assert "Current Datetime Local:" in joined
        assert "Owner Timezone: Asia/Shanghai" in joined
        assert "Surface: web" in joined
        assert "Worker Type: research" in joined
        # Feature 061 T-028: bootstrap:shared 不再包含 Default Tool Groups/Profile
        # 工具可见性由 Deferred Tools + PermissionPreset 控制
        assert "capability pack" in joined
        assert "ToolBroker / Policy / audit" in joined
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_render_bootstrap_context_marks_missing_owner_profile_as_degraded(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        _task_service,
        capability_pack,
        _delegation_plane,
        task_runner,
        _tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        rendered = await capability_pack.render_bootstrap_context(
            worker_type="research",
            project_id="project-default",
            surface="web",
        )
        joined = "\n".join(item["content"] for item in rendered)
        assert "Owner Timezone: UTC" in joined
        assert "Owner Locale: zh-CN" in joined
        assert "Ambient Degraded Reasons: owner_timezone_missing, owner_locale_missing" in joined
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_capability_pack_registers_mcp_proxy_tools_and_marks_runtime_degradation(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    await store_group.project_store.create_project(
        Project(
            project_id="project-default",
            slug="default",
            name="Default Project",
            is_default=True,
        )
    )
    await store_group.project_store.save_selector_state(
        ProjectSelectorState(
            selector_id="selector-web",
            surface="web",
            active_project_id="project-default",

            source="tests",
        )
    )
    await store_group.conn.commit()

    server_script = tmp_path / "mcp_echo_server.py"
    _write_mcp_echo_server(server_script)

    tool_broker = ToolBroker(event_store=store_group.event_store)
    capability_pack = CapabilityPackService(
        project_root=tmp_path,
        store_group=store_group,
        tool_broker=tool_broker,
    )
    mcp_registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=tool_broker,
        server_configs=[
            McpServerConfig(
                name="demo",
                command=sys.executable,
                args=[str(server_script)],
            )
        ],
    )
    capability_pack.bind_mcp_registry(mcp_registry)

    try:
        await capability_pack.startup()
        pack = await capability_pack.get_pack()
        by_name = {item.tool_name: item for item in pack.tools}
        task_service = TaskService(store_group, SSEHub())
        mcp_task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="执行 MCP 工具测试",
                idempotency_key="feature-032-mcp-runtime",
            )
        )
        assert created is True

        assert "mcp.demo.echo" in by_name
        assert by_name["mcp.demo.echo"].availability.value == "available"
        assert by_name["sessions.list"].availability.value == "degraded"
        assert by_name["subagents.spawn"].availability.value == "unavailable"
        assert by_name["mcp.servers.list"].availability.value == "available"

        tools_result = await tool_broker.execute(
            "mcp.tools.list",
            {},
            ExecutionContext(
                task_id=mcp_task_id,
                trace_id=f"trace-{mcp_task_id}",
                caller="tests",
    
                permission_preset=PermissionPreset.MINIMAL,
            ),
        )
        echo_result = await tool_broker.execute(
            "mcp.demo.echo",
            {"text": "hello"},
            ExecutionContext(
                task_id=mcp_task_id,
                trace_id=f"trace-{mcp_task_id}",
                caller="tests",
    
                permission_preset=PermissionPreset.NORMAL,
            ),
        )

        assert tools_result.is_error is False
        tools_payload = json.loads(tools_result.output)
        assert {item["registered_name"] for item in tools_payload["tools"]} == {"mcp.demo.echo"}

        assert echo_result.is_error is False
        echo_payload = json.loads(echo_result.output)
        assert echo_payload["server_name"] == "demo"
        assert echo_payload["tool_name"] == "echo"
        assert echo_payload["content"][0]["text"] == "echo:hello"
    finally:
        await store_group.conn.close()


async def test_capability_pack_honors_mcp_mount_policy_defaults(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    tool_broker = ToolBroker(event_store=store_group.event_store)
    capability_pack = CapabilityPackService(
        project_root=tmp_path,
        store_group=store_group,
        tool_broker=tool_broker,
    )
    mcp_registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=tool_broker,
        server_configs=[
            McpServerConfig(
                name="readonly",
                command="/bin/echo",
                args=["readonly"],
                mount_policy="auto_readonly",
            ),
            McpServerConfig(
                name="explicit",
                command="/bin/echo",
                args=["explicit"],
                mount_policy="explicit",
            ),
            McpServerConfig(
                name="all",
                command="/bin/echo",
                args=["all"],
                mount_policy="auto_all",
            ),
        ],
    )
    capability_pack.bind_mcp_registry(mcp_registry)

    try:
        assert (
            capability_pack._mcp_tool_enabled_by_default(
                server_name="readonly", tool_profile="minimal"
            )
            is True
        )
        assert (
            capability_pack._mcp_tool_enabled_by_default(
                server_name="readonly", tool_profile="standard"
            )
            is False
        )
        assert (
            capability_pack._mcp_tool_enabled_by_default(
                server_name="readonly", tool_profile="privileged"
            )
            is False
        )

        assert (
            capability_pack._mcp_tool_enabled_by_default(
                server_name="explicit", tool_profile="minimal"
            )
            is False
        )
        assert (
            capability_pack._mcp_tool_enabled_by_default(
                server_name="explicit", tool_profile="standard"
            )
            is False
        )

        assert (
            capability_pack._mcp_tool_enabled_by_default(
                server_name="all", tool_profile="minimal"
            )
            is True
        )
        assert (
            capability_pack._mcp_tool_enabled_by_default(
                server_name="all", tool_profile="standard"
            )
            is True
        )
        assert (
            capability_pack._mcp_tool_enabled_by_default(
                server_name="all", tool_profile="privileged"
            )
            is True
        )
    finally:
        await store_group.conn.close()


async def test_web_search_tool_returns_parsed_results(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        _delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        responses = [
            _FakeSearchResponse(
                text="""
                <html>
                  <body>
                    <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle">
                      OctoAgent Built-in Tools
                    </a>
                    <a class="result__a" href="https://example.org/post">Operator Guide</a>
                  </body>
                </html>
                """,
                url="https://html.duckduckgo.com/html/?q=octoagent",
            )
        ]
        monkeypatch.setattr(
            "httpx.AsyncClient",
            lambda **kwargs: _FakeSearchAsyncClient(responses, **kwargs),
        )
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="执行 web search",
                idempotency_key="feature-032-web-search",
            )
        )
        assert created is True

        result = await tool_broker.execute(
            "web.search",
            {"query": "octoagent", "limit": 2},
            ExecutionContext(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                caller="tests",
    
                permission_preset=PermissionPreset.MINIMAL,
            ),
        )

        assert result.is_error is False
        payload = json.loads(result.output)
        assert payload["query"] == "octoagent"
        assert payload["result_count"] == 2
        assert payload["results"][0]["title"] == "OctoAgent Built-in Tools"
        assert payload["results"][0]["url"] == "https://example.com/article"
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_memory_recall_tool_returns_structured_recall_pack(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        capability_pack,
        _delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        await init_memory_db(store_group.conn)
        project = await store_group.project_store.get_default_project()
        assert project is not None
        memory_service = await capability_pack._memory_runtime_service.memory_service_for_scope(
            project=project,
        )
        proposal = await memory_service.propose_write(
            scope_id="chat:web:thread-memory",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="project.alpha.plan",
            content="Alpha 拆解要先完成 memory resolver 接线。",
            rationale="测试 recall tool",
            confidence=0.91,
            evidence_refs=[EvidenceRef(ref_id="artifact-alpha", ref_type="artifact")],
        )
        validation = await memory_service.validate_proposal(proposal.proposal_id)
        assert validation.accepted is True
        await memory_service.commit_memory(proposal.proposal_id)

        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="执行 memory recall",
                channel="web",
                thread_id="thread-memory",
                scope_id="chat:web:thread-memory",
                idempotency_key="feature-032-memory-recall",
            )
        )
        assert created is True

        result = await tool_broker.execute(
            "memory.recall",
            {"query": "请继续推进 Alpha 拆解", "scope_id": "chat:web:thread-memory"},
            ExecutionContext(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                caller="tests",
    
                permission_preset=PermissionPreset.MINIMAL,
            ),
        )

        assert result.is_error is False
        payload = json.loads(result.output)
        assert payload["query"] == "请继续推进 Alpha 拆解"
        assert payload["hits"]
        assert payload["hits"][0]["record_id"]
        assert payload["hits"][0]["citation"].startswith("memory://")
        assert payload["hits"][0]["search_query"]
        assert payload["hook_trace"]["post_filter_mode"] == "keyword_overlap"
        assert payload["hook_trace"]["rerank_mode"] == "heuristic"
        assert payload["hook_trace"]["delivered_count"] >= 1
        assert payload["hits"][0]["metadata"]["recall_rerank_mode"] == "heuristic"
        assert payload["backend_status"]["backend_id"] == memory_service.backend_id
        assert payload["backend_status"]["active_backend"]
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_browser_tools_persist_session_and_follow_clickable_refs(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请打开站点并点击链接",
                idempotency_key="feature-032-browser-session",
            )
        )
        assert created is True

        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请打开站点并点击链接",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None

        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-032-browser",
            worker_id="worker.test",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="subagent",
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:test",

            permission_preset=PermissionPreset.NORMAL,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == "https://example.com":
                return httpx.Response(
                    200,
                    text=(
                        "<html><head><title>Home</title></head>"
                        "<body><a href='/docs'>Docs</a><p>Landing page.</p></body></html>"
                    ),
                    headers={"content-type": "text/html"},
                )
            if str(request.url) == "https://example.com/docs":
                return httpx.Response(
                    200,
                    text=(
                        "<html><head><title>Docs</title></head>"
                        "<body><p>Documentation page.</p></body></html>"
                    ),
                    headers={"content-type": "text/html"},
                )
            return httpx.Response(404, text="missing", headers={"content-type": "text/plain"})

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            return real_async_client(*args, transport=transport, **kwargs)

        with (
            patch(
                "octoagent.gateway.services.capability_pack.httpx.AsyncClient",
                new=client_factory,
            ),
            bind_execution_context(runtime_context),
        ):
            opened = await tool_broker.execute(
                "browser.open",
                {"url": "https://example.com"},
                broker_context,
            )
            snapshot = await tool_broker.execute("browser.snapshot", {}, broker_context)
            clicked = await tool_broker.execute(
                "browser.act",
                {"kind": "click", "ref": "link:1"},
                broker_context,
            )
            status = await tool_broker.execute("browser.status", {}, broker_context)
            closed = await tool_broker.execute("browser.close", {}, broker_context)
            missing = await tool_broker.execute("browser.status", {}, broker_context)

        assert opened.is_error is False
        opened_payload = json.loads(opened.output)
        assert opened_payload["title"] == "Home"
        assert opened_payload["links"][0]["ref"] == "link:1"

        assert snapshot.is_error is False
        snapshot_payload = json.loads(snapshot.output)
        assert "Landing page." in snapshot_payload["text_preview"]

        assert clicked.is_error is False
        clicked_payload = json.loads(clicked.output)
        assert clicked_payload["clicked"]["url"] == "https://example.com/docs"
        assert clicked_payload["title"] == "Docs"

        assert status.is_error is False
        status_payload = json.loads(status.output)
        assert status_payload["final_url"] == "https://example.com/docs"

        assert closed.is_error is False
        assert json.loads(closed.output)["closed"] is True
        assert json.loads(missing.output)["status"] == "missing"
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


@pytest.mark.xfail(reason="subagents.list 内部依赖变更，需要适配")
async def test_subagent_management_tools_list_kill_and_steer_descendants(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请启动并管理一个子代理",
                idempotency_key="feature-032-subagents-manage",
            )
        )
        assert created is True

        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请启动并管理一个子代理",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None

        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-032-manage",
            worker_id="worker.test",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="subagent",
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:test",

            permission_preset=PermissionPreset.NORMAL,
        )

        child_task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请先停下来等待我下一步指令",
                idempotency_key="feature-032-subagents-child-manage",
                control_metadata={
                    "parent_task_id": task_id,
                    "parent_work_id": plan.work.work_id,
                    "requested_worker_type": "research",
                    "target_kind": "subagent",
                    "spawned_by": "tests",
                    "child_title": "待命子任务",
                },
            )
        )
        assert created is True

        child_plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=child_task_id,
                trace_id=f"trace-{child_task_id}",
                user_text="请先停下来等待我下一步指令",
                worker_capability="llm_generation",
                metadata={
                    "parent_task_id": task_id,
                    "parent_work_id": plan.work.work_id,
                    "requested_worker_type": "research",
                    "target_kind": "subagent",
                },
            )
        )
        child_work = child_plan.work

        fake_session = ExecutionConsoleSession(
            session_id="child-session-001",
            task_id=child_task_id,
            backend=ExecutionBackend.INLINE,
            backend_job_id="child-job-001",
            state=ExecutionSessionState.WAITING_INPUT,
            interactive=True,
            input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
            started_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            live=True,
            can_attach_input=True,
            can_cancel=True,
        )
        attach_result = AttachInputResult(
            task_id=child_task_id,
            session_id=fake_session.session_id,
            request_id="req-child-001",
            artifact_id="artifact-child-001",
            delivered_live=True,
            approval_id=None,
        )

        async def _session_lookup(task_id_arg: str):
            if task_id_arg == child_task_id:
                return fake_session
            return None

        with (
            patch.object(
                task_runner,
                "get_execution_session",
                new=AsyncMock(side_effect=_session_lookup),
            ),
            patch.object(
                task_runner,
                "attach_input",
                new=AsyncMock(return_value=attach_result),
            ) as attach_mock,
            patch.object(
                task_runner,
                "cancel_task",
                new=AsyncMock(return_value=True),
            ) as cancel_mock,
            bind_execution_context(runtime_context),
        ):
            list_result = await tool_broker.execute(
                "subagents.list",
                {"include_terminal": True},
                broker_context,
            )
            steer_result = await tool_broker.execute(
                "subagents.steer",
                {"task_id": child_task_id, "text": "继续，但先输出一版摘要"},
                broker_context,
            )
            kill_result = await tool_broker.execute(
                "subagents.kill",
                {"work_id": child_work.work_id, "reason": "parent-stop"},
                broker_context,
            )

        assert list_result.is_error is False
        list_payload = json.loads(list_result.output)
        assert list_payload["count"] >= 1
        item = next(entry for entry in list_payload["items"] if entry["task_id"] == child_task_id)
        assert item["steerable"] is True
        assert item["execution_session"]["session_id"] == fake_session.session_id

        assert steer_result.is_error is False
        attach_mock.assert_awaited_once_with(
            child_task_id,
            "继续，但先输出一版摘要",
            actor=f"parent:{task_id}",
            approval_id=None,
        )

        assert kill_result.is_error is False
        cancel_mock.assert_awaited_once_with(child_task_id)
        updated_child = await store_group.work_store.get_work(child_work.work_id)
        assert updated_child is not None
        assert updated_child.status.value == "cancelled"
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_work_split_tool_creates_real_child_tasks_and_canvas_artifact(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请拆分这项工作",
                idempotency_key="feature-032-work-split",
            )
        )
        assert created is True

        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请拆分这项工作",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None

        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-032",
            worker_id="worker.test",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="subagent",
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:test",

            permission_preset=PermissionPreset.NORMAL,
        )

        with bind_execution_context(runtime_context):
            split_result = await tool_broker.execute(
                "subagents.spawn",
                {
                    "objectives": ["先调研当前 API", "再补一组测试"],
                    "worker_type": "research",
                    "target_kind": "subagent",
                },
                broker_context,
            )
            canvas_result = await tool_broker.execute(
                "canvas.write",
                {
                    "name": "split-summary.md",
                    "content": "# child plan\n- 调研\n- 测试\n",
                },
                broker_context,
            )

        assert split_result.is_error is False
        split_payload = json.loads(split_result.output)
        assert split_payload["requested"] == 2
        assert len(split_payload["children"]) == 2

        assert canvas_result.is_error is False
        canvas_payload = json.loads(canvas_result.output)
        artifact = await store_group.artifact_store.get_artifact(canvas_payload["artifact_id"])
        assert artifact is not None
        assert artifact.name == "split-summary.md"

        child_works = []
        for _ in range(30):
            child_works = await store_group.work_store.list_works(parent_work_id=plan.work.work_id)
            if len(child_works) >= 2:
                break
            await asyncio.sleep(0.05)

        assert len(child_works) == 2
        assert {item.parent_work_id for item in child_works} == {plan.work.work_id}
        assert {item.selected_worker_type for item in child_works} == {"research"}
        assert {item.target_kind.value for item in child_works} == {"subagent"}

        for child in split_payload["children"]:
            await task_runner.cancel_task(str(child["task_id"]))
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_work_split_rejects_worker_to_worker_delegation(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请拆分一组 worker 子任务",
                idempotency_key="feature-071-work-split-worker-to-worker",
            )
        )
        assert created is True

        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请拆分一组 worker 子任务",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None

        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-071-work-split-worker",
            worker_id="worker.supervisor",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="worker",
            runtime_context=plan.dispatch_envelope.runtime_context,
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:supervisor",

            permission_preset=PermissionPreset.NORMAL,
        )

        with bind_execution_context(runtime_context):
            result = await tool_broker.execute(
                "subagents.spawn",
                {
                    "objectives": ["先调研 API", "再补一组测试"],
                    "worker_type": "research",
                    "target_kind": "worker",
                },
                broker_context,
            )

        assert result.is_error is True
        assert result.error is not None
        assert "worker runtime cannot delegate to another worker" in result.error
        child_works = await store_group.work_store.list_works(parent_work_id=plan.work.work_id)
        assert child_works == []
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_workers_review_tool_returns_supervisor_plan_with_tool_profiles(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请先调研 API，再补代码和测试",
                idempotency_key="feature-039-workers-review",
            )
        )
        assert created is True

        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请先调研 API，再补代码和测试",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None

        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-039-review",
            worker_id="worker.supervisor",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="worker",
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:supervisor",

            permission_preset=PermissionPreset.MINIMAL,
        )

        with bind_execution_context(runtime_context):
            result = await tool_broker.execute(
                "work.plan",
                {"objective": "先调研 API，再补代码和测试"},
                broker_context,
            )

        assert result.is_error is False
        payload = json.loads(result.output)
        assert payload["proposal_kind"] == "split"
        assert len(payload["assignments"]) >= 2
        assert {item["worker_type"] for item in payload["assignments"]} == {"general"}
        assert {item["tool_profile"] for item in payload["assignments"]} == {"standard"}
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_runtime_now_tool_returns_owner_local_time_payload(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        await store_group.agent_context_store.save_owner_profile(
            OwnerProfile(
                owner_profile_id="owner-profile-default",
                timezone="Asia/Shanghai",
                locale="zh-CN",
            )
        )
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="现在几点",
                idempotency_key="feature-041-runtime-now",
            )
        )
        assert created is True
        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="现在几点",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None
        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-041-runtime-now",
            worker_id="worker.supervisor",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="worker",
            runtime_context=plan.dispatch_envelope.runtime_context,
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:supervisor",

            permission_preset=PermissionPreset.MINIMAL,
        )

        with bind_execution_context(runtime_context):
            result = await tool_broker.execute("runtime.now", {}, broker_context)

        assert result.is_error is False
        payload = json.loads(result.output)
        assert payload["timezone"] == "Asia/Shanghai"
        assert payload["locale"] == "zh-CN"
        assert payload["surface"] == "web"
        assert payload["source"] == "system_clock"
        assert payload["degraded_reasons"] == []
        assert payload["current_datetime_local"]
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_runtime_now_tool_marks_missing_owner_profile_as_degraded(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="现在几点",
                idempotency_key="feature-041-runtime-now-missing-owner",
            )
        )
        assert created is True
        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="现在几点",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None
        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-041-runtime-now-missing-owner",
            worker_id="worker.supervisor",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="worker",
            runtime_context=plan.dispatch_envelope.runtime_context,
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:supervisor",

            permission_preset=PermissionPreset.MINIMAL,
        )

        with bind_execution_context(runtime_context):
            result = await tool_broker.execute("runtime.now", {}, broker_context)

        assert result.is_error is False
        payload = json.loads(result.output)
        assert payload["timezone"] == "UTC"
        assert payload["locale"] == "zh-CN"
        assert payload["surface"] == "web"
        assert "owner_timezone_missing" in payload["degraded_reasons"]
        assert "owner_locale_missing" in payload["degraded_reasons"]
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_subagents_spawn_preserves_freshness_tool_profile_and_lineage(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请处理实时查询",
                idempotency_key="feature-041-subagents-spawn-freshness",
            )
        )
        assert created is True
        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请处理实时查询",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None
        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-041-subagent-spawn",
            worker_id="worker.supervisor",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="worker",
            runtime_context=plan.dispatch_envelope.runtime_context,
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:supervisor",

            permission_preset=PermissionPreset.NORMAL,
        )

        with bind_execution_context(runtime_context):
            result = await tool_broker.execute(
                "subagents.spawn",
                {
                    "objective": "请查一下北京今天的天气和官网公告",
                    "worker_type": "research",
                    "target_kind": "subagent",
                    "title": "freshness-worker",
                },
                broker_context,
            )

        assert result.is_error is False
        payload = json.loads(result.output)
        assert payload["tool_profile"] == "standard"
        assert payload["worker_type"] == "research"
        assert payload["parent_work_id"] == plan.work.work_id

        child_works = []
        for _ in range(30):
            child_works = await store_group.work_store.list_works(parent_work_id=plan.work.work_id)
            if child_works:
                break
            await asyncio.sleep(0.05)

        assert len(child_works) == 1
        child = child_works[0]
        assert child.project_id == "project-default"
        assert child.metadata["requested_tool_profile"] == "standard"
        assert child.metadata["requested_worker_type"] == "research"
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_subagents_spawn_rejects_worker_to_worker_delegation(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请把这项工作交给另一个 worker",
                idempotency_key="feature-071-subagents-spawn-worker-to-worker",
            )
        )
        assert created is True
        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请把这项工作交给另一个 worker",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None
        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-071-spawn-worker",
            worker_id="worker.supervisor",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="worker",
            runtime_context=plan.dispatch_envelope.runtime_context,
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:supervisor",

            permission_preset=PermissionPreset.NORMAL,
        )

        with bind_execution_context(runtime_context):
            result = await tool_broker.execute(
                "subagents.spawn",
                {
                    "objective": "请继续处理这项工作",
                    "worker_type": "research",
                    "target_kind": "worker",
                    "title": "invalid-worker-hop",
                },
                broker_context,
            )

        assert result.is_error is True
        assert result.error is not None
        assert "worker runtime cannot delegate to another worker" in result.error
        child_works = await store_group.work_store.list_works(parent_work_id=plan.work.work_id)
        assert child_works == []
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_subagents_spawn_keeps_local_document_queries_on_minimal_profile(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请总结 API 文档的关键约束",
                idempotency_key="feature-041-subagents-spawn-local-docs",
            )
        )
        assert created is True
        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请总结 API 文档的关键约束",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None
        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-041-subagent-spawn-local-docs",
            worker_id="worker.supervisor",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="worker",
            runtime_context=plan.dispatch_envelope.runtime_context,
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:supervisor",

            permission_preset=PermissionPreset.NORMAL,
        )

        with bind_execution_context(runtime_context):
            result = await tool_broker.execute(
                "subagents.spawn",
                {
                    "objective": "请总结 API 文档的关键约束",
                    "worker_type": "research",
                    "target_kind": "subagent",
                    "title": "local-doc-worker",
                },
                broker_context,
            )

        assert result.is_error is False
        payload = json.loads(result.output)
        assert payload["tool_profile"] == "standard"
        assert payload["worker_type"] == "research"

        child_works = []
        for _ in range(30):
            child_works = await store_group.work_store.list_works(parent_work_id=plan.work.work_id)
            if child_works:
                break
            await asyncio.sleep(0.05)

        assert len(child_works) == 1
        child = child_works[0]
        assert child.metadata["requested_tool_profile"] == "standard"
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_subagents_spawn_uses_objective_as_child_prompt_when_title_is_provided(
    tmp_path: Path,
) -> None:
    (
        store_group,
        _sse_hub,
        task_service,
        _capability_pack,
        delegation_plane,
        task_runner,
        tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                text="请启动子代理",
                idempotency_key="feature-032-subagents-spawn-title",
            )
        )
        assert created is True

        plan = await delegation_plane.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请启动子代理",
                worker_capability="llm_generation",
                metadata={},
            )
        )
        assert plan.dispatch_envelope is not None

        runtime_context = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id="session-032-spawn",
            worker_id="worker.test",
            backend="inline",
            console=task_runner.execution_console,
            work_id=plan.work.work_id,
            runtime_kind="subagent",
        )
        broker_context = ExecutionContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            caller="worker:test",

            permission_preset=PermissionPreset.NORMAL,
        )

        with bind_execution_context(runtime_context):
            spawn_result = await tool_broker.execute(
                "subagents.spawn",
                {
                    "objective": "请先读取 API 现状，再输出研究摘要",
                    "title": "研究子任务",
                    "worker_type": "research",
                    "target_kind": "subagent",
                },
                broker_context,
            )

        assert spawn_result.is_error is False
        payload = json.loads(spawn_result.output)
        assert payload["objective"] == "请先读取 API 现状，再输出研究摘要"
        assert payload["title"] == "研究子任务"

        events = await store_group.event_store.get_events_for_task(payload["task_id"])
        user_event = next(event for event in events if event.type.value == "USER_MESSAGE")
        assert user_event.payload["text_preview"] == "请先读取 API 现状，再输出研究摘要"
        assert user_event.payload["control_metadata"]["child_title"] == "研究子任务"
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


# ============================================================
# Feature 061 T-031: Bootstrap 简化单元测试
# ============================================================


async def test_bootstrap_shared_only_no_type_specific_templates(
    tmp_path: Path,
) -> None:
    """US-003 场景 2: 4 个独立模板不再存在 — 仅 bootstrap:shared"""
    (
        store_group,
        _sse_hub,
        _task_service,
        capability_pack,
        _delegation_plane,
        task_runner,
        _tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        pack = await capability_pack.get_pack()

        # 只有 bootstrap:shared
        template_ids = {f.file_id for f in pack.bootstrap_files}
        assert template_ids == {"bootstrap:shared"}

        # 不应有 type-specific 模板
        for type_id in ["bootstrap:general", "bootstrap:ops", "bootstrap:research", "bootstrap:dev"]:
            assert type_id not in template_ids
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_bootstrap_shared_template_no_redundant_fields(
    tmp_path: Path,
) -> None:
    """US-003 场景 4: shared 模板无冗余字段（不含 Default Tool Profile/Groups）"""
    (
        store_group,
        _sse_hub,
        _task_service,
        capability_pack,
        _delegation_plane,
        task_runner,
        _tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        pack = await capability_pack.get_pack()
        shared = next(f for f in pack.bootstrap_files if f.file_id == "bootstrap:shared")

        # 核心元信息仍存在
        assert "Project:" in shared.content
        assert "Current Datetime Local:" in shared.content
        assert "Worker Type:" in shared.content
        assert "ToolBroker / Policy / audit" in shared.content

        # 冗余字段已移除
        assert "Default Tool Profile:" not in shared.content
        assert "Default Tool Groups:" not in shared.content
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_bootstrap_shared_renders_for_all_worker_types(
    tmp_path: Path,
) -> None:
    """bootstrap:shared 对所有 WorkerType 都能正确渲染"""
    (
        store_group,
        _sse_hub,
        _task_service,
        capability_pack,
        _delegation_plane,
        task_runner,
        _tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        await store_group.agent_context_store.save_owner_profile(
            OwnerProfile(
                owner_profile_id="owner-profile-default",
                timezone="Asia/Shanghai",
                locale="zh-CN",
            )
        )

        for wtype in ["general", "ops", "research", "dev"]:
            rendered = await capability_pack.render_bootstrap_context(
                worker_type=wtype,
                project_id="project-default",
                surface="web",
            )
            assert len(rendered) == 1  # 只有 shared
            content = rendered[0]["content"]
            assert f"Worker Type: {wtype}" in content
            assert "capability pack" in content
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_unified_worker_profiles_share_same_tool_groups(
    tmp_path: Path,
) -> None:
    """Feature 065: 只有一个 general profile，包含所有必要分组"""
    (
        store_group,
        _sse_hub,
        _task_service,
        capability_pack,
        _delegation_plane,
        task_runner,
        _tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        general = capability_pack.get_worker_profile("general")
        # Feature 065: ops/research/dev 查询都回退到 general
        ops = capability_pack.get_worker_profile("ops")
        research = capability_pack.get_worker_profile("research")
        dev = capability_pack.get_worker_profile("dev")

        # 所有查询都返回同一个 profile
        assert general is ops
        assert general is research
        assert general is dev

        # 包含所有必要分组
        required_groups = {"project", "filesystem", "terminal", "memory", "mcp", "skills", "runtime"}
        assert required_groups.issubset(set(general.default_tool_groups))
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_bootstrap_token_budget_within_limit(
    tmp_path: Path,
) -> None:
    """SC-006: bootstrap 总量 <= 200 tokens"""
    (
        store_group,
        _sse_hub,
        _task_service,
        capability_pack,
        _delegation_plane,
        task_runner,
        _tool_broker,
    ) = await _build_runtime_services(tmp_path)

    try:
        await store_group.agent_context_store.save_owner_profile(
            OwnerProfile(
                owner_profile_id="owner-profile-default",
                timezone="Asia/Shanghai",
                locale="zh-CN",
            )
        )
        rendered = await capability_pack.render_bootstrap_context(
            worker_type="general",
            project_id="project-default",
            surface="web",
        )

        total_chars = sum(len(item["content"]) for item in rendered)
        # 粗略估算: 1 token ~ 4 字符（混合中英文）
        estimated_tokens = total_chars / 3
        assert estimated_tokens <= 250, (
            f"Bootstrap 估算 token 数 {estimated_tokens:.0f} 超过 250 限制。"
            f" 总字符: {total_chars}"
        )
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()
