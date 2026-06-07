"""Feature 060 T038: Worker 进度笔记单元测试。

覆盖：笔记写入 Artifact Store、Artifact Store 不可用降级、
上下文注入最近 5 条、自动合并阈值、格式化输出。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from octoagent.core.models import Artifact, ArtifactPart, PartType
from octoagent.tooling.progress_note import (
    DEFAULT_INJECT_LIMIT,
    DEFAULT_MERGE_THRESHOLD,
    ProgressNoteInput,
    ProgressNoteOutput,
    execute_progress_note,
    format_progress_notes_block,
    load_recent_progress_notes,
)


class MockArtifactStore:
    """轻量 Artifact Store mock，支持写入和列表查询。"""

    def __init__(self) -> None:
        self.artifacts: dict[str, Artifact] = {}
        self.contents: dict[str, bytes] = {}
        # F104：记录每次写入的 versionable / logical_file_id 调用参数，供断言
        self.put_calls: list[dict] = []

    async def put_artifact(
        self,
        artifact: Artifact,
        content: bytes,
        *,
        versionable: bool = False,
        logical_file_id: str | None = None,
    ) -> None:
        self.artifacts[artifact.artifact_id] = artifact
        self.contents[artifact.artifact_id] = content
        self.put_calls.append(
            {
                "artifact_id": artifact.artifact_id,
                "name": artifact.name,
                "versionable": versionable,
                "logical_file_id": logical_file_id,
            }
        )

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        return self.artifacts.get(artifact_id)

    async def list_artifacts_for_task(self, task_id: str) -> list[Artifact]:
        return [a for a in self.artifacts.values() if a.task_id == task_id]


class MockConn:
    """模拟数据库连接的 commit。"""

    async def commit(self) -> None:
        pass


class TestProgressNoteExecution:
    """T034: 笔记写入 Artifact Store。"""

    async def test_write_note_success(self) -> None:
        """正常写入进度笔记到 Artifact Store。"""
        store = MockArtifactStore()
        conn = MockConn()
        input_data = ProgressNoteInput(
            step_id="data_collection",
            description="从 GitHub API 获取了 42 个 PR",
            status="completed",
            key_decisions=["使用批量 API"],
            next_steps=["解析 PR diff"],
        )

        result = await execute_progress_note(
            input_data=input_data,
            task_id="task-001",
            agent_session_id="sess-001",
            artifact_store=store,
            conn=conn,
        )

        assert result.persisted is True
        assert result.note_id.startswith("pn-task-001")
        assert "data_collection" in result.note_id

        # 验证 Artifact Store 中的数据
        assert len(store.artifacts) == 1
        artifact = list(store.artifacts.values())[0]
        assert artifact.name == "progress-note:data_collection"
        assert artifact.task_id == "task-001"
        assert len(artifact.parts) == 1
        assert artifact.parts[0].type == PartType.JSON

        # 验证 JSON 内容
        content = json.loads(artifact.parts[0].content)
        assert content["step_id"] == "data_collection"
        assert content["description"] == "从 GitHub API 获取了 42 个 PR"
        assert content["status"] == "completed"
        assert content["key_decisions"] == ["使用批量 API"]
        assert content["next_steps"] == ["解析 PR diff"]
        assert content["task_id"] == "task-001"
        assert content["agent_session_id"] == "sess-001"

    async def test_user_step_passes_versionable_true(self) -> None:
        """F104 T1.8/SD-9：user step 写入传 versionable=True + logical_file_id=progress-note:{step_id}。"""
        store = MockArtifactStore()
        conn = MockConn()
        input_data = ProgressNoteInput(step_id="impl_step", description="实现进度")
        result = await execute_progress_note(
            input_data=input_data,
            task_id="task-vt",
            agent_session_id="sess-vt",
            artifact_store=store,
            conn=conn,
        )
        assert result.persisted is True
        user_calls = [c for c in store.put_calls if c["name"] == "progress-note:impl_step"]
        assert len(user_calls) == 1
        assert user_calls[0]["versionable"] is True
        assert user_calls[0]["logical_file_id"] == "progress-note:impl_step"

    async def test_caller_merged_history_step_id_forced_non_versionable(self) -> None:
        """F104/SD-9：调用方（非自动合并路径）显式传 step_id='__merged_history__' 写多次，
        必须强制 versionable=False + logical_file_id=None，不进版本表（不污染 Files Tab）。

        负向断言：__merged_history__ 命名空间无论谁写都不版本化（与自动合并一致）。
        """
        store = MockArtifactStore()
        conn = MockConn()
        for i in range(2):
            result = await execute_progress_note(
                input_data=ProgressNoteInput(
                    step_id="__merged_history__",
                    description=f"调用方手动合并 {i}",
                ),
                task_id="task-merged-guard",
                agent_session_id="sess-guard",
                artifact_store=store,
                conn=conn,
            )
            assert result.persisted is True

        merged_calls = [
            c for c in store.put_calls
            if c["name"] == "progress-note:__merged_history__"
        ]
        assert len(merged_calls) == 2
        for c in merged_calls:
            assert c["versionable"] is False
            assert c["logical_file_id"] is None

    async def test_write_note_without_artifact_store(self) -> None:
        """Artifact Store 不可用时返回 persisted=False。"""
        input_data = ProgressNoteInput(
            step_id="step_1",
            description="做了一些事情",
        )

        result = await execute_progress_note(
            input_data=input_data,
            task_id="task-002",
            artifact_store=None,
        )

        assert result.persisted is False
        assert result.note_id.startswith("pn-task-002")

    async def test_write_note_store_error_degrades(self) -> None:
        """Artifact Store 抛异常时降级为 persisted=False。"""

        class FailingStore:
            async def put_artifact(self, artifact, content, **kwargs):
                raise RuntimeError("存储不可用")
            async def list_artifacts_for_task(self, task_id):
                return []

        input_data = ProgressNoteInput(
            step_id="step_err",
            description="应该降级",
        )

        result = await execute_progress_note(
            input_data=input_data,
            task_id="task-003",
            artifact_store=FailingStore(),
        )

        assert result.persisted is False

    async def test_note_id_format(self) -> None:
        """note_id 格式为 pn-{task_id[:8]}-{step_id}-{ulid}。"""
        store = MockArtifactStore()
        conn = MockConn()
        input_data = ProgressNoteInput(
            step_id="my_step",
            description="测试 ID 格式",
        )

        result = await execute_progress_note(
            input_data=input_data,
            task_id="01HXYZ1234567890",
            artifact_store=store,
            conn=conn,
        )

        assert result.note_id.startswith("pn-01HXYZ12-my_step-")

    async def test_same_step_id_creates_multiple_notes(self) -> None:
        """相同 step_id 多次调用创建多条笔记（非覆盖）。"""
        store = MockArtifactStore()
        conn = MockConn()

        for i in range(3):
            input_data = ProgressNoteInput(
                step_id="recurring_step",
                description=f"第{i+1}次更新",
                status="in_progress" if i < 2 else "completed",
            )
            await execute_progress_note(
                input_data=input_data,
                task_id="task-004",
                artifact_store=store,
                conn=conn,
            )

        # 应该有 3 条笔记
        artifacts = await store.list_artifacts_for_task("task-004")
        assert len(artifacts) == 3


class TestProgressNoteLoading:
    """T036: 进度笔记加载与上下文注入。"""

    async def test_load_recent_notes(self) -> None:
        """加载最近 5 条进度笔记。"""
        store = MockArtifactStore()
        conn = MockConn()

        # 写入 8 条笔记
        for i in range(8):
            input_data = ProgressNoteInput(
                step_id=f"step_{i}",
                description=f"步骤 {i}",
                status="completed",
            )
            await execute_progress_note(
                input_data=input_data,
                task_id="task-load-1",
                artifact_store=store,
                conn=conn,
            )

        notes = await load_recent_progress_notes(
            task_id="task-load-1",
            artifact_store=store,
            limit=5,
        )

        assert len(notes) == 5
        # 应该是最近 5 条（step_3 到 step_7）
        step_ids = [n["step_id"] for n in notes]
        assert step_ids == ["step_3", "step_4", "step_5", "step_6", "step_7"]

    async def test_load_notes_empty_task(self) -> None:
        """没有笔记的 task 返回空列表。"""
        store = MockArtifactStore()
        notes = await load_recent_progress_notes(
            task_id="nonexistent",
            artifact_store=store,
        )
        assert notes == []

    async def test_load_notes_mixed_artifacts(self) -> None:
        """Artifact Store 中混有非进度笔记的 Artifact，正确过滤。"""
        store = MockArtifactStore()
        conn = MockConn()

        # 写入一条进度笔记
        await execute_progress_note(
            input_data=ProgressNoteInput(step_id="real", description="真实笔记"),
            task_id="task-mixed",
            artifact_store=store,
            conn=conn,
        )

        # 写入一条普通 Artifact
        other_artifact = Artifact(
            artifact_id="other-artifact",
            task_id="task-mixed",
            ts=datetime.now(UTC),
            name="llm-response",
            description="普通 Artifact",
            parts=[ArtifactPart(type=PartType.TEXT, content="hello")],
        )
        await store.put_artifact(other_artifact, b"hello")

        notes = await load_recent_progress_notes(
            task_id="task-mixed",
            artifact_store=store,
        )
        assert len(notes) == 1
        assert notes[0]["step_id"] == "real"


class TestProgressNotesFormatting:
    """T035: ProgressNotes 系统块格式化。"""

    def test_format_empty_notes(self) -> None:
        """空笔记列表返回空字符串。"""
        result = format_progress_notes_block([])
        assert result == ""

    def test_format_single_note(self) -> None:
        """单条笔记格式正确。"""
        notes = [
            {
                "step_id": "data_collection",
                "status": "completed",
                "description": "收集了 42 条数据",
                "next_steps": ["分析数据"],
            },
        ]
        result = format_progress_notes_block(notes)
        assert "## Progress Notes" in result
        assert "[data_collection] completed: 收集了 42 条数据" in result
        assert "Next: 分析数据" in result

    def test_format_multiple_notes(self) -> None:
        """多条笔记按顺序展示。"""
        notes = [
            {"step_id": "s1", "status": "completed", "description": "步骤1"},
            {"step_id": "s2", "status": "in_progress", "description": "步骤2", "next_steps": ["完成"]},
            {"step_id": "s3", "status": "blocked", "description": "步骤3"},
        ]
        result = format_progress_notes_block(notes)
        lines = result.split("\n")
        # 应该包含所有 3 条
        assert sum(1 for line in lines if line.startswith("- [")) == 3

    def test_format_respects_limit(self) -> None:
        """超过 limit 的笔记只展示最近 N 条。"""
        notes = [
            {"step_id": f"s{i}", "status": "completed", "description": f"步骤{i}"}
            for i in range(10)
        ]
        result = format_progress_notes_block(notes, limit=3)
        lines = result.split("\n")
        note_lines = [line for line in lines if line.startswith("- [")]
        assert len(note_lines) == 3
        # 应该是最后 3 条
        assert "[s7]" in note_lines[0]
        assert "[s8]" in note_lines[1]
        assert "[s9]" in note_lines[2]


class TestProgressNoteAutoMerge:
    """T037: 进度笔记自动合并。"""

    async def test_no_merge_below_threshold(self) -> None:
        """笔记数量未超阈值时不触发合并。"""
        store = MockArtifactStore()
        conn = MockConn()

        # 写入 10 条（低于默认阈值 50）
        for i in range(10):
            await execute_progress_note(
                input_data=ProgressNoteInput(step_id=f"s{i}", description=f"步骤{i}"),
                task_id="task-nomerge",
                artifact_store=store,
                conn=conn,
            )

        artifacts = await store.list_artifacts_for_task("task-nomerge")
        # 不应该有合并笔记
        merged = [a for a in artifacts if "merged" in a.name]
        assert len(merged) == 0

    async def test_merge_above_threshold(self) -> None:
        """笔记超过阈值时自动合并旧笔记。"""
        store = MockArtifactStore()
        conn = MockConn()

        # 写入 52 条（超过阈值 50 时触发合并）
        # 使用低阈值加速测试
        threshold = 15
        for i in range(threshold + 2):
            await execute_progress_note(
                input_data=ProgressNoteInput(step_id=f"s{i}", description=f"步骤{i}"),
                task_id="task-merge",
                artifact_store=store,
                conn=conn,
                merge_threshold=threshold,
            )

        artifacts = await store.list_artifacts_for_task("task-merge")
        # 应该有合并笔记
        merged = [a for a in artifacts if "__merged_history__" in a.name]
        assert len(merged) >= 1

        # 验证合并笔记的内容
        merged_artifact = merged[0]
        merged_content = json.loads(merged_artifact.parts[0].content)
        assert merged_content["step_id"] == "__merged_history__"
        assert "milestones" in merged_content
        assert len(merged_content["milestones"]) > 0

    async def test_merged_history_written_non_versionable(self) -> None:
        """F104 T5.2/SD-9/FR-022：__merged_history__ 合并笔记走默认 put_artifact
        （versionable=False），不进版本表/不被当版本化逻辑文件。

        负向断言：所有 name='progress-note:__merged_history__' 的 put_call 必须
        versionable=False 且 logical_file_id=None；user step 笔记才 versionable=True。
        """
        store = MockArtifactStore()
        conn = MockConn()
        threshold = 15
        for i in range(threshold + 2):
            await execute_progress_note(
                input_data=ProgressNoteInput(step_id=f"s{i}", description=f"步骤{i}"),
                task_id="task-merge-nonver",
                artifact_store=store,
                conn=conn,
                merge_threshold=threshold,
            )

        merged_calls = [
            c for c in store.put_calls
            if c["name"] == "progress-note:__merged_history__"
        ]
        assert len(merged_calls) >= 1
        for c in merged_calls:
            # SD-9：合并历史是派生汇总，明确排除在版本化之外
            assert c["versionable"] is False
            assert c["logical_file_id"] is None

        # 反向对照：user step 笔记仍 versionable=True（确认不是全局关版本化）
        user_calls = [
            c for c in store.put_calls
            if c["name"].startswith("progress-note:")
            and c["name"] != "progress-note:__merged_history__"
        ]
        assert user_calls
        assert all(c["versionable"] is True for c in user_calls)


class TestProgressNoteInput:
    """T033: 输入模型验证。"""

    def test_valid_input(self) -> None:
        """合法输入通过验证。"""
        note = ProgressNoteInput(
            step_id="step_1",
            description="完成了数据收集",
            status="completed",
            key_decisions=["选择了方案 A"],
            next_steps=["开始分析"],
        )
        assert note.step_id == "step_1"
        assert note.status == "completed"

    def test_minimal_input(self) -> None:
        """最小输入（只有必填字段）通过验证。"""
        note = ProgressNoteInput(
            step_id="s1",
            description="做了事情",
        )
        assert note.status == "completed"  # 默认值
        assert note.key_decisions == []
        assert note.next_steps == []

    def test_empty_step_id_rejected(self) -> None:
        """空 step_id 被拒绝。"""
        with pytest.raises(Exception):  # Pydantic ValidationError
            ProgressNoteInput(step_id="", description="test")

    def test_empty_description_rejected(self) -> None:
        """空 description 被拒绝。"""
        with pytest.raises(Exception):
            ProgressNoteInput(step_id="s1", description="")

    def test_invalid_status_rejected(self) -> None:
        """非法 status 值被拒绝。"""
        with pytest.raises(Exception):
            ProgressNoteInput(step_id="s1", description="test", status="unknown")


class TestProgressNoteOutput:
    """T033: 输出模型验证。"""

    def test_output_model(self) -> None:
        """输出模型正确实例化。"""
        output = ProgressNoteOutput(note_id="pn-xxx", persisted=True)
        assert output.note_id == "pn-xxx"
        assert output.persisted is True

    def test_output_serialization(self) -> None:
        """输出模型可序列化。"""
        output = ProgressNoteOutput(note_id="pn-test", persisted=False)
        data = output.model_dump()
        assert data == {"note_id": "pn-test", "persisted": False}
