"""WriteResult 契约单元测试（Feature 084 Phase 2 — T021.5）。

验证范围：
1. WriteResult 基类字段校验（preview 截断、status 枚举）
2. 各子类结构化字段保留（防 F4 压扁回归）
3. _enforce_write_result_contract 的快慢路径
4. broker.execute() 序列化 BaseModel 输出（T021.6）
5. tool_contract produces_write 元数据注入
"""

from __future__ import annotations

import inspect
import typing

import pytest
from pydantic import ValidationError

from octoagent.core.models.tool_results import (
    BehaviorWriteFileResult,
    CanvasWriteResult,
    ChildSpawnInfo,
    ConfigAddProviderResult,
    FilesystemWriteTextResult,
    GraphPipelineResult,
    McpInstallResult,
    McpUninstallResult,
    MemoryWriteResult,
    SubagentsKillResult,
    SubagentsSpawnResult,
    WorkDeleteResult,
    WorkMergeResult,
    WorkSnapshot,
    WriteResult,
)
from octoagent.tooling.schema import SchemaReflectionError, _enforce_write_result_contract
from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.models import SideEffectLevel


# ============================================================
# 1. WriteResult 基类字段校验
# ============================================================

class TestWriteResultBase:
    """WriteResult 基类字段语义与校验。"""

    def test_written_status_ok(self) -> None:
        """status=written 正常构造。"""
        result = WriteResult(status="written", target="some_file.txt")
        assert result.status == "written"
        assert result.target == "some_file.txt"

    def test_invalid_status_rejected(self) -> None:
        """非法 status 应被 Pydantic 拒绝。"""
        with pytest.raises(ValidationError):
            WriteResult(status="committed", target="x")  # type: ignore[arg-type]

    def test_preview_truncated_to_200_chars(self) -> None:
        """preview 超过 200 字符时自动截断。"""
        long_text = "A" * 300
        result = WriteResult(status="written", target="x", preview=long_text)
        assert result.preview is not None
        assert len(result.preview) == 200

    def test_preview_short_unchanged(self) -> None:
        """preview 不超 200 字符时保持原样。"""
        short_text = "hello"
        result = WriteResult(status="written", target="x", preview=short_text)
        assert result.preview == "hello"

    def test_preview_none_allowed(self) -> None:
        """preview 可以是 None。"""
        result = WriteResult(status="written", target="x")
        assert result.preview is None

    def test_bytes_written_optional(self) -> None:
        """bytes_written 可以不传。"""
        result = WriteResult(status="written", target="x")
        assert result.bytes_written is None

    def test_pending_status_with_reason(self) -> None:
        """status=pending 时 reason 可填写说明。"""
        result = WriteResult(status="pending", target="job:123", reason="后台任务已提交")
        assert result.status == "pending"
        assert result.reason == "后台任务已提交"


# ============================================================
# 2. 子类结构化字段保留（防 F4 压扁回归）
# ============================================================

class TestSubclassFields:
    """子类保留结构化字段，防止 model_dump_json() 压扁信息。"""

    def test_config_add_provider_result_fields(self) -> None:
        """ConfigAddProviderResult 保留 provider_id / action / hint。"""
        r = ConfigAddProviderResult(
            status="written",
            target="octoagent.yaml",
            provider_id="siliconflow",
            action="added",
            hint="测试成功",
        )
        assert r.provider_id == "siliconflow"
        assert r.action == "added"
        assert r.hint == "测试成功"

    def test_mcp_install_result_pending_with_task_id(self) -> None:
        """McpInstallResult status=pending 时 task_id 非空（防 F14 回归）。"""
        r = McpInstallResult(
            status="pending",
            target="npm:@org/mcp-server-test",
            preview="安装任务已启动",
            reason="后台 npm install 已触发",
            task_id="TASK-123",
        )
        assert r.task_id == "TASK-123"
        assert r.status == "pending"

    def test_mcp_install_result_local_mode(self) -> None:
        """McpInstallResult local 模式 status=written + server_name。"""
        r = McpInstallResult(
            status="written",
            target="/path/to/mcp-servers.json",
            preview="MCP server 'myserver' 已注册",
            server_name="myserver",
        )
        assert r.server_name == "myserver"
        assert r.task_id is None

    def test_mcp_uninstall_result_fields(self) -> None:
        """McpUninstallResult 保留 server_id。"""
        r = McpUninstallResult(
            status="written",
            target="mcp-server-xyz",
            preview="已卸载",
            server_id="mcp-server-xyz",
        )
        assert r.server_id == "mcp-server-xyz"

    def test_subagents_spawn_result_children(self) -> None:
        """SubagentsSpawnResult 保留 requested / created / children。"""
        r = SubagentsSpawnResult(
            status="written",
            target="task_store",
            preview="派发 2 个子任务",
            requested=2,
            created=2,
            children=[
                ChildSpawnInfo(
                    task_id="task-1",
                    worker_type="research",
                    tool_profile="standard",
                    parent_work_id="work-parent",
                ),
                ChildSpawnInfo(
                    task_id="task-2",
                    worker_type="general",
                ),
            ],
        )
        assert r.requested == 2
        assert len(r.children) == 2
        assert r.children[0].tool_profile == "standard"
        assert r.children[0].parent_work_id == "work-parent"

    def test_subagents_kill_result_fields(self) -> None:
        """SubagentsKillResult 保留 task_id / work_id / runtime_cancelled / work。"""
        snap = WorkSnapshot(work_id="w1", status="cancelled", title="test work")
        r = SubagentsKillResult(
            status="written",
            target="task-abc",
            preview="已取消",
            task_id="task-abc",
            work_id="w1",
            runtime_cancelled=True,
            work=snap,
        )
        assert r.task_id == "task-abc"
        assert r.work_id == "w1"
        assert r.runtime_cancelled is True
        assert r.work is not None
        assert r.work.status == "cancelled"

    def test_memory_write_result_fields(self) -> None:
        """MemoryWriteResult 保留 memory_id / version / action / scope_id。"""
        r = MemoryWriteResult(
            status="written",
            target="memory_store",
            preview="用户偏好/语言 → Python",
            memory_id="mem-abc-123",
            version=2,
            action="update",
            scope_id="scope-default",
        )
        assert r.memory_id == "mem-abc-123"
        assert r.version == 2
        assert r.action == "update"
        assert r.scope_id == "scope-default"

    def test_filesystem_write_text_result_fields(self) -> None:
        """FilesystemWriteTextResult 保留 workspace_root / path / created_dirs。"""
        r = FilesystemWriteTextResult(
            status="written",
            target="/workspace/notes.txt",
            preview="Hello",
            bytes_written=5,
            workspace_root="/workspace",
            path="notes.txt",
            created_dirs=False,
        )
        assert r.workspace_root == "/workspace"
        assert r.path == "notes.txt"
        assert r.created_dirs is False

    def test_behavior_write_file_result_proposal_mode(self) -> None:
        """BehaviorWriteFileResult proposal=True 时 status=skipped。"""
        r = BehaviorWriteFileResult(
            status="skipped",
            target="/path/to/USER.md",
            preview="需要用户确认",
            reason="REVIEW_REQUIRED",
            file_id="USER.md",
            written=False,
            proposal=True,
        )
        assert r.status == "skipped"
        assert r.proposal is True
        assert r.written is False

    def test_canvas_write_result_fields(self) -> None:
        """CanvasWriteResult 保留 artifact_id / task_id。"""
        r = CanvasWriteResult(
            status="written",
            target="artifact:art-001",
            preview="artifact content",
            artifact_id="art-001",
            task_id="task-001",
        )
        assert r.artifact_id == "art-001"
        assert r.task_id == "task-001"

    def test_graph_pipeline_result_start_pending(self) -> None:
        """GraphPipelineResult start 成功后 status=pending + run_id + task_id。"""
        r = GraphPipelineResult(
            status="pending",
            target="pipeline:deploy",
            preview="Pipeline 'deploy' started",
            reason="后台执行中",
            action="start",
            run_id="run-001",
            task_id="task-child-001",
        )
        assert r.action == "start"
        assert r.run_id == "run-001"
        assert r.task_id == "task-child-001"
        assert r.status == "pending"

    def test_graph_pipeline_result_cancel_written(self) -> None:
        """GraphPipelineResult cancel 成功后 status=written。"""
        r = GraphPipelineResult(
            status="written",
            target="run-abc",
            preview="Pipeline run cancelled.",
            action="cancel",
            run_id="run-abc",
        )
        assert r.action == "cancel"
        assert r.status == "written"

    def test_work_merge_result_fields(self) -> None:
        """WorkMergeResult 保留 child_work_ids / merged。"""
        snap = WorkSnapshot(work_id="w-parent", status="succeeded", title="main")
        r = WorkMergeResult(
            status="written",
            target="w-parent",
            preview="合并 2 个 child works",
            child_work_ids=["w-1", "w-2"],
            merged=snap,
        )
        assert r.child_work_ids == ["w-1", "w-2"]
        assert r.merged is not None
        assert r.merged.work_id == "w-parent"

    def test_work_delete_result_fields(self) -> None:
        """WorkDeleteResult 保留 child_work_ids / deleted。"""
        r = WorkDeleteResult(
            status="written",
            target="w-parent",
            preview="已软删除 work_id=w-parent",
            child_work_ids=["w-1"],
            deleted=WorkSnapshot(work_id="w-parent", status="deleted"),
        )
        assert len(r.child_work_ids) == 1
        assert r.deleted is not None


# ============================================================
# 3. _enforce_write_result_contract 快/慢路径
# ============================================================

class TestEnforceWriteResultContract:
    """_enforce_write_result_contract 行为测试。"""

    def test_non_write_tool_skipped(self) -> None:
        """produces_write=False 时直接跳过，不抛异常。"""
        def read_only_tool(x: str) -> str:
            return x

        # 不应抛出任何异常
        _enforce_write_result_contract(read_only_tool, False)

    def test_write_tool_with_correct_return_type_passes(self) -> None:
        """produces_write=True 且 return type 是 WriteResult 子类时通过。"""
        # 注意：本文件顶部有 from __future__ import annotations，所以注解均为字符串
        # _enforce_write_result_contract 通过 typing.get_type_hints 解析字符串注解
        def write_tool_ok(path: str) -> FilesystemWriteTextResult:
            ...

        _enforce_write_result_contract(write_tool_ok, True)

    def test_write_tool_with_str_return_type_fails(self) -> None:
        """produces_write=True 且 return type 是 str 时抛 SchemaReflectionError。"""
        def write_tool_bad(path: str) -> str:
            return ""

        with pytest.raises(SchemaReflectionError, match="WriteResult"):
            _enforce_write_result_contract(write_tool_bad, True)

    def test_write_tool_with_no_return_annotation_fails(self) -> None:
        """produces_write=True 且无 return annotation 时抛 SchemaReflectionError。"""
        def write_tool_no_return(path: str):  # type: ignore[return]
            return FilesystemWriteTextResult(
                status="written", target="x", workspace_root="", path=""
            )

        with pytest.raises(SchemaReflectionError, match="WriteResult"):
            _enforce_write_result_contract(write_tool_no_return, True)

    def test_forward_ref_resolution(self) -> None:
        """使用 from __future__ import annotations 的函数，get_type_hints 应能正确解析。"""
        # 模拟 from __future__ import annotations 环境中的 forward-referenced return type
        # 通过 __annotations__ 强制为 string 形式
        def write_tool_forward(path: str) -> "FilesystemWriteTextResult":
            ...

        # 注意：在本文件顶部已有 from __future__ import annotations，
        # 所以所有注解都是字符串形式，_enforce_write_result_contract 应能通过
        _enforce_write_result_contract(write_tool_forward, True)


# ============================================================
# 4. tool_contract produces_write 元数据注入
# ============================================================

class TestToolContractMetadata:
    """tool_contract produces_write 参数注入到 _tool_meta。"""

    def test_produces_write_injected_into_metadata(self) -> None:
        """produces_write=True 时 _tool_meta['metadata']['produces_write'] 为 True。"""
        @tool_contract(
            name="test.write_meta",
            side_effect_level=SideEffectLevel.REVERSIBLE,
            tool_group="test",
            produces_write=True,
        )
        def tool_fn(path: str) -> FilesystemWriteTextResult:
            ...

        meta = tool_fn._tool_meta  # type: ignore[attr-defined]
        assert meta["metadata"]["produces_write"] is True

    def test_produces_write_default_false(self) -> None:
        """未传 produces_write 时，默认 False。"""
        @tool_contract(
            name="test.read_only",
            side_effect_level=SideEffectLevel.NONE,
            tool_group="test",
        )
        def tool_fn(path: str) -> str:
            ...

        meta = tool_fn._tool_meta  # type: ignore[attr-defined]
        assert meta["metadata"].get("produces_write", False) is False

    def test_produces_write_false_no_enforcement(self) -> None:
        """produces_write=False 的工具即使 return type 是 str 也不触发 enforcement。"""
        @tool_contract(
            name="test.str_return",
            side_effect_level=SideEffectLevel.NONE,
            tool_group="test",
            produces_write=False,
        )
        def tool_fn(path: str) -> str:
            ...

        # 不应抛出异常
        from octoagent.tooling.schema import reflect_tool_schema
        schema = reflect_tool_schema(tool_fn)
        assert schema.name == "test.str_return"


# ============================================================
# 5. model_dump_json 序列化正确性（T021.6 回归防护）
# ============================================================

class TestModelDumpJsonSerialization:
    """WriteResult.model_dump_json() 产出 JSON 可被 json.loads 解析。"""

    def test_write_result_json_round_trip(self) -> None:
        """WriteResult 序列化 / 反序列化回路正确。"""
        import json
        r = FilesystemWriteTextResult(
            status="written",
            target="/workspace/foo.txt",
            preview="hello",
            bytes_written=5,
            workspace_root="/workspace",
            path="foo.txt",
        )
        json_str = r.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["status"] == "written"
        assert parsed["target"] == "/workspace/foo.txt"
        assert parsed["workspace_root"] == "/workspace"
        assert parsed["path"] == "foo.txt"
        assert parsed["bytes_written"] == 5

    def test_subagents_spawn_json_preserves_children(self) -> None:
        """SubagentsSpawnResult 序列化后 children 数组保留字段（防 F4 压扁）。"""
        import json
        r = SubagentsSpawnResult(
            status="written",
            target="task_store",
            requested=1,
            created=1,
            children=[
                ChildSpawnInfo(
                    task_id="task-001",
                    worker_type="research",
                    tool_profile="standard",
                    parent_work_id="work-parent",
                )
            ],
        )
        parsed = json.loads(r.model_dump_json())
        assert parsed["requested"] == 1
        assert parsed["children"][0]["task_id"] == "task-001"
        assert parsed["children"][0]["tool_profile"] == "standard"
        assert parsed["children"][0]["parent_work_id"] == "work-parent"

    def test_memory_write_result_json_preserves_structured_fields(self) -> None:
        """MemoryWriteResult 序列化后保留 memory_id / version。"""
        import json
        r = MemoryWriteResult(
            status="written",
            target="memory_store",
            memory_id="mem-xyz",
            version=3,
            action="create",
            scope_id="scope-1",
        )
        parsed = json.loads(r.model_dump_json())
        assert parsed["memory_id"] == "mem-xyz"
        assert parsed["version"] == 3
        assert parsed["action"] == "create"
