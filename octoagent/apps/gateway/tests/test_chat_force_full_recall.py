"""F101 Phase A：force_full_recall producer 测试（Codex per-Phase A review 修订版）。

Codex review finding 修复：
- H-A1：不再 patch _enqueue_or_run，改为验证真实持久化链路。
  路径：chat_control_metadata → NormalizedMessage.control_metadata →
        USER_MESSAGE event.control_metadata →
        TaskService.get_latest_user_metadata → orchestrator metadata["force_full_recall"]
- H-A2：ENV OCTOAGENT_LONG_PROMPT_THRESHOLD 覆盖测试（_resolve_long_prompt_threshold helper）
- M-A1：orchestrator 链路集成测试（spy OrchestratorService.dispatch，断言 metadata 含 force_full_recall=True）
- L-A1：删除死字段 should_trigger 参数化 case（test_cross_language_triggering），
        改用动态 len() 计算或合并到全触发矩阵

覆盖范围：
- AC-D1：新对话路径，长 prompt → NormalizedMessage.control_metadata 含 force_full_recall=True
- AC-D2：短 prompt → 不含 force_full_recall
- AC-D3：续对话路径，长 prompt → append_user_message control_metadata 含 force_full_recall=True
- FR-D3 边界：len == threshold 不触发，len == threshold + 1 触发
- H-A2：ENV 覆盖 threshold（500 / 非法值 fallback 2000）
- M-A1：orchestrator.dispatch metadata["force_full_recall"] == True（链路集成）
- A-5b：≥ 5 个跨语言 case（中/英/代码/JSON/混合）全部触发
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import Project
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.gateway.services.task_service import TaskService
from octoagent.gateway.routes.chat import LONG_PROMPT_THRESHOLD, _resolve_long_prompt_threshold


# -----------------------------------------------------------------------
# 消息构造工具
# -----------------------------------------------------------------------

def _make_long_message(length: int, char: str = "a") -> str:
    """生成长度为 length 的消息（重复 char）。"""
    return char * length


def _make_unicode_message(length: int, char: str = "测") -> str:
    """生成长度为 length 的中文 Unicode 消息。"""
    return char * length


# -----------------------------------------------------------------------
# fixtures
# -----------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_app(tmp_path: Path):
    """测试用 FastAPI 应用，注入 task_runner（标准路径）。"""
    from fastapi import FastAPI
    from octoagent.gateway.routes import chat, tasks

    app = FastAPI()
    app.include_router(chat.router)
    app.include_router(tasks.router)

    store_group = await create_store_group(
        str(tmp_path / "f101-force-recall.db"),
        str(tmp_path / "artifacts"),
    )
    sse_hub = SSEHub()
    llm_service = LLMService()
    task_runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=llm_service,
        timeout_seconds=60,
        monitor_interval_seconds=0.05,
    )
    await task_runner.startup()

    app.state.store_group = store_group
    app.state.sse_hub = sse_hub
    app.state.llm_service = llm_service
    app.state.task_runner = task_runner
    app.state.project_root = tmp_path

    yield app

    await task_runner.shutdown()
    await store_group.close()


@pytest_asyncio.fixture
async def client(test_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac


# -----------------------------------------------------------------------
# 辅助：spy service.create_task，捕获 NormalizedMessage.control_metadata
# -----------------------------------------------------------------------

class CapturedControlMetadata:
    """捕获 TaskService.create_task / append_user_message 被调用时的 control_metadata。"""

    def __init__(self) -> None:
        self.create_task_msgs: list[NormalizedMessage] = []
        self.append_control_metas: list[dict[str, Any]] = []

    @property
    def last_create_task_control_metadata(self) -> dict[str, Any] | None:
        if not self.create_task_msgs:
            return None
        return self.create_task_msgs[-1].control_metadata

    @property
    def last_append_control_metadata(self) -> dict[str, Any] | None:
        if not self.append_control_metas:
            return None
        return self.append_control_metas[-1]


# -----------------------------------------------------------------------
# TestNewChatControlMetadata — 新对话路径，NormalizedMessage.control_metadata（H-A1 修复路径）
# -----------------------------------------------------------------------

class TestNewChatControlMetadata:
    """H-A1 修复路径验证：新对话路径，force_full_recall 写入 NormalizedMessage.control_metadata。

    不再 patch _enqueue_or_run（Codex H-A1 指出的盲点）。
    改为 spy service.create_task，断言传入的 NormalizedMessage.control_metadata 含 force_full_recall=True。
    这直接验证 chat_control_metadata → NormalizedMessage.control_metadata 的传递链路。
    """

    async def test_long_message_sets_force_full_recall_in_control_metadata(
        self, client: AsyncClient, test_app
    ) -> None:
        """AC-D1（H-A1 修复路径）：新对话路径，len > threshold → NormalizedMessage.control_metadata
        含 force_full_recall=True。
        """
        long_msg = _make_long_message(LONG_PROMPT_THRESHOLD + 1)
        captured = CapturedControlMetadata()
        orig_create_task = TaskService.create_task

        async def spy_create_task(self, message: NormalizedMessage):
            captured.create_task_msgs.append(message)
            return await orig_create_task(self, message)

        with patch.object(TaskService, "create_task", spy_create_task):
            resp = await client.post(
                "/api/chat/send",
                json={"message": long_msg},
            )

        assert resp.status_code == 200
        cm = captured.last_create_task_control_metadata
        assert cm is not None, "NormalizedMessage.control_metadata 未被捕获"
        assert cm.get("force_full_recall") is True, (
            f"期望 NormalizedMessage.control_metadata['force_full_recall'] == True，"
            f"实际得到 {cm}"
        )

    async def test_short_message_does_not_set_force_full_recall(
        self, client: AsyncClient, test_app
    ) -> None:
        """AC-D2：新对话路径，len <= threshold → NormalizedMessage.control_metadata
        不含 force_full_recall（baseline 不变）。
        """
        short_msg = _make_long_message(LONG_PROMPT_THRESHOLD)  # 恰好等于阈值（不超过）
        captured = CapturedControlMetadata()
        orig_create_task = TaskService.create_task

        async def spy_create_task(self, message: NormalizedMessage):
            captured.create_task_msgs.append(message)
            return await orig_create_task(self, message)

        with patch.object(TaskService, "create_task", spy_create_task):
            resp = await client.post(
                "/api/chat/send",
                json={"message": short_msg},
            )

        assert resp.status_code == 200
        cm = captured.last_create_task_control_metadata
        assert "force_full_recall" not in (cm or {}), (
            f"短 prompt（len={LONG_PROMPT_THRESHOLD}）不应写入 force_full_recall，"
            f"实际 control_metadata={cm}"
        )

    async def test_boundary_exactly_threshold_does_not_trigger(
        self, client: AsyncClient, test_app
    ) -> None:
        """FR-D3 边界：len == threshold → 不触发（FR-D3 `> threshold`，不是 `>=`）。"""
        msg = _make_long_message(LONG_PROMPT_THRESHOLD)
        captured = CapturedControlMetadata()
        orig_create_task = TaskService.create_task

        async def spy_create_task(self, message: NormalizedMessage):
            captured.create_task_msgs.append(message)
            return await orig_create_task(self, message)

        with patch.object(TaskService, "create_task", spy_create_task):
            resp = await client.post("/api/chat/send", json={"message": msg})

        assert resp.status_code == 200
        cm = captured.last_create_task_control_metadata or {}
        assert "force_full_recall" not in cm, (
            f"len == threshold 不应触发，实际 control_metadata={cm}"
        )

    async def test_boundary_one_over_threshold_triggers(
        self, client: AsyncClient, test_app
    ) -> None:
        """FR-D3 边界：len == threshold + 1 → 触发。"""
        msg = _make_long_message(LONG_PROMPT_THRESHOLD + 1)
        captured = CapturedControlMetadata()
        orig_create_task = TaskService.create_task

        async def spy_create_task(self, message: NormalizedMessage):
            captured.create_task_msgs.append(message)
            return await orig_create_task(self, message)

        with patch.object(TaskService, "create_task", spy_create_task):
            resp = await client.post("/api/chat/send", json={"message": msg})

        assert resp.status_code == 200
        cm = captured.last_create_task_control_metadata
        assert cm is not None
        assert cm.get("force_full_recall") is True, (
            f"len == threshold+1 应触发，实际 control_metadata={cm}"
        )


# -----------------------------------------------------------------------
# TestContinueChatControlMetadata — 续对话路径（AC-D3）
# -----------------------------------------------------------------------

class TestContinueChatControlMetadata:
    """H-A1 修复路径验证（续对话）：force_full_recall 写入 append_user_message control_metadata。

    spy service.append_user_message，断言 control_metadata 含 force_full_recall=True（AC-D3）。
    """

    @pytest_asyncio.fixture
    async def existing_task_id(self, test_app) -> str:
        """预先创建一个 Task，用于续对话路径测试。"""
        import uuid

        thread_id = f"thread-continue-{uuid.uuid4().hex[:8]}"
        task_service = TaskService(test_app.state.store_group, test_app.state.sse_hub)
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                channel="web",
                thread_id=thread_id,
                scope_id=f"chat:web:{thread_id}",
                sender_id="owner",
                sender_name="Owner",
                text="续对话测试初始消息",
                idempotency_key=f"continue-test-{thread_id}",
            )
        )
        assert created is True
        return task_id

    async def test_long_message_in_continue_sets_force_full_recall(
        self, client: AsyncClient, test_app, existing_task_id: str
    ) -> None:
        """AC-D3：续对话路径，len > threshold → append_user_message control_metadata 含 force_full_recall=True。"""
        long_msg = _make_long_message(LONG_PROMPT_THRESHOLD + 100)
        captured = CapturedControlMetadata()
        orig_append = TaskService.append_user_message

        async def spy_append(self, task_id, text, *, control_metadata=None, **kwargs):
            captured.append_control_metas.append(control_metadata or {})
            return await orig_append(self, task_id, text, control_metadata=control_metadata, **kwargs)

        with patch.object(TaskService, "append_user_message", spy_append):
            resp = await client.post(
                "/api/chat/send",
                json={"message": long_msg, "task_id": existing_task_id},
            )

        assert resp.status_code == 200
        cm = captured.last_append_control_metadata
        assert cm is not None, "续对话路径 control_metadata 未被捕获"
        assert cm.get("force_full_recall") is True, (
            f"续对话路径长 prompt 期望 force_full_recall=True，实际 {cm}"
        )

    async def test_short_message_in_continue_does_not_set_force_full_recall(
        self, client: AsyncClient, test_app, existing_task_id: str
    ) -> None:
        """续对话路径，短 prompt 不写入 force_full_recall（baseline 不变）。"""
        short_msg = "继续处理这个任务"
        captured = CapturedControlMetadata()
        orig_append = TaskService.append_user_message

        async def spy_append(self, task_id, text, *, control_metadata=None, **kwargs):
            captured.append_control_metas.append(control_metadata or {})
            return await orig_append(self, task_id, text, control_metadata=control_metadata, **kwargs)

        with patch.object(TaskService, "append_user_message", spy_append):
            resp = await client.post(
                "/api/chat/send",
                json={"message": short_msg, "task_id": existing_task_id},
            )

        assert resp.status_code == 200
        cm = captured.last_append_control_metadata
        assert "force_full_recall" not in (cm or {}), (
            f"短 prompt 不应写入 force_full_recall，实际 control_metadata={cm}"
        )


# -----------------------------------------------------------------------
# TestUserMessageEventPersistence — USER_MESSAGE event 持久化链路验证
# -----------------------------------------------------------------------

class TestUserMessageEventPersistence:
    """验证 force_full_recall 经 USER_MESSAGE event.control_metadata 持久化后
    能通过 TaskService.get_latest_user_metadata 读回。

    这是 H-A1 修复的核心链路：
    chat_control_metadata → NormalizedMessage.control_metadata → EVENT 持久化
    → get_latest_user_metadata → metadata["force_full_recall"]

    关键前提：force_full_recall 已加入 TURN_SCOPED_CONTROL_KEYS 白名单（connection_metadata.py），
    否则 normalize_control_metadata 会过滤掉它。
    """

    async def test_force_full_recall_persisted_in_user_message_event(
        self, client: AsyncClient, test_app
    ) -> None:
        """长 prompt → USER_MESSAGE event.control_metadata 持久化 force_full_recall=True
        → get_latest_user_metadata 可读回。
        """
        long_msg = _make_long_message(LONG_PROMPT_THRESHOLD + 1)

        resp = await client.post(
            "/api/chat/send",
            json={"message": long_msg},
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        # 通过 get_latest_user_metadata 读取持久化的 control_metadata
        task_service = TaskService(test_app.state.store_group, test_app.state.sse_hub)
        meta = await task_service.get_latest_user_metadata(task_id)

        assert meta.get("force_full_recall") is True, (
            f"force_full_recall 应通过 USER_MESSAGE event 持久化并通过 "
            f"get_latest_user_metadata 读回，实际 meta={meta}"
        )

    async def test_short_message_force_full_recall_not_in_event(
        self, client: AsyncClient, test_app
    ) -> None:
        """短 prompt → USER_MESSAGE event 不含 force_full_recall → get_latest_user_metadata 无该键。"""
        short_msg = _make_long_message(LONG_PROMPT_THRESHOLD)

        resp = await client.post(
            "/api/chat/send",
            json={"message": short_msg},
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        task_service = TaskService(test_app.state.store_group, test_app.state.sse_hub)
        meta = await task_service.get_latest_user_metadata(task_id)

        assert "force_full_recall" not in meta, (
            f"短 prompt 不应在 get_latest_user_metadata 中包含 force_full_recall，"
            f"实际 meta={meta}"
        )

    async def test_continue_chat_force_full_recall_persisted(
        self, client: AsyncClient, test_app
    ) -> None:
        """续对话路径：长 prompt → append_user_message 事件持久化 force_full_recall=True。"""
        import uuid

        # 先创建初始对话
        thread_id = f"thread-persist-{uuid.uuid4().hex[:8]}"
        task_service = TaskService(test_app.state.store_group, test_app.state.sse_hub)
        task_id, created = await task_service.create_task(
            NormalizedMessage(
                channel="web",
                thread_id=thread_id,
                scope_id=f"chat:web:{thread_id}",
                sender_id="owner",
                sender_name="Owner",
                text="初始消息",
                idempotency_key=f"persist-test-{thread_id}",
            )
        )
        assert created is True

        # 续对话发长 prompt
        long_msg = _make_long_message(LONG_PROMPT_THRESHOLD + 50)
        resp = await client.post(
            "/api/chat/send",
            json={"message": long_msg, "task_id": task_id},
        )
        assert resp.status_code == 200

        # 验证持久化
        meta = await task_service.get_latest_user_metadata(task_id)
        assert meta.get("force_full_recall") is True, (
            f"续对话路径 force_full_recall 应通过 get_latest_user_metadata 读回，"
            f"实际 meta={meta}"
        )


# -----------------------------------------------------------------------
# TestOrchestratorMetadataLinkage — M-A1 orchestrator 链路集成测试
# -----------------------------------------------------------------------

class TestOrchestratorMetadataLinkage:
    """M-A1 orchestrator 链路集成测试：
    spy OrchestratorService.dispatch，断言收到的 metadata 含 force_full_recall=True。

    不 patch _enqueue_or_run，测试完整路径：
    chat POST → service.create_task → task_runner.enqueue → task_runner._run_job
    → get_latest_user_metadata → orchestrator.dispatch(metadata=...)

    spy dispatch 在捕获 metadata 后抛 _SpyStopDispatch 中止执行（避免构造 WorkerResult 等复杂 mock）。
    task_runner._run_job 捕获异常后标记任务 FAILED，不影响本测试断言。
    """

    async def test_orchestrator_dispatch_receives_force_full_recall(
        self, client: AsyncClient, test_app
    ) -> None:
        """M-A1：长 prompt 经完整链路后，orchestrator.dispatch 收到 metadata['force_full_recall'] == True。

        使用 patch.object start/stop（不用 with 块），确保 patch 在异步 task_runner._run_job 执行时仍有效。
        """
        from octoagent.gateway.services.orchestrator import OrchestratorService

        long_msg = _make_long_message(LONG_PROMPT_THRESHOLD + 1)
        captured_metadata: list[dict[str, Any]] = []

        class _SpyStopDispatch(Exception):
            """spy dispatch 捕获 metadata 后立即中止执行的哨兵异常。"""

        async def spy_dispatch(self, task_id, user_text, *, metadata=None, **kwargs):
            captured_metadata.append(dict(metadata or {}))
            # 抛哨兵异常中止 LLM 执行（task_runner._run_job 会捕获并标记 task FAILED）
            raise _SpyStopDispatch("spy: captured metadata, stopping dispatch")

        # 在 post 之前启动 patch，保持到 _run_job 异步执行完毕再停
        patcher = patch.object(OrchestratorService, "dispatch", spy_dispatch)
        patcher.start()
        try:
            resp = await client.post(
                "/api/chat/send",
                json={"message": long_msg},
            )
            assert resp.status_code == 200

            # 等待 task_runner 异步执行 _run_job（从 enqueue 到 dispatch 调用的耗时）
            for _ in range(40):
                await asyncio.sleep(0.05)
                if captured_metadata:
                    break
        finally:
            patcher.stop()

        assert captured_metadata, (
            "orchestrator.dispatch 未被调用——task_runner 可能未触发或等待超时（2s）"
        )
        last_meta = captured_metadata[-1]
        assert last_meta.get("force_full_recall") is True, (
            f"orchestrator.dispatch metadata 应含 force_full_recall=True，"
            f"实际 metadata={last_meta}"
        )

    async def test_orchestrator_dispatch_no_force_full_recall_for_short_msg(
        self, client: AsyncClient, test_app
    ) -> None:
        """M-A1 基线：短 prompt 时 orchestrator.dispatch metadata 不含 force_full_recall。"""
        from octoagent.gateway.services.orchestrator import OrchestratorService

        short_msg = _make_long_message(LONG_PROMPT_THRESHOLD // 2)
        captured_metadata: list[dict[str, Any]] = []

        class _SpyStopDispatch(Exception):
            pass

        async def spy_dispatch(self, task_id, user_text, *, metadata=None, **kwargs):
            captured_metadata.append(dict(metadata or {}))
            raise _SpyStopDispatch("spy: captured metadata, stopping dispatch")

        patcher = patch.object(OrchestratorService, "dispatch", spy_dispatch)
        patcher.start()
        try:
            resp = await client.post(
                "/api/chat/send",
                json={"message": short_msg},
            )
            assert resp.status_code == 200

            for _ in range(40):
                await asyncio.sleep(0.05)
                if captured_metadata:
                    break
        finally:
            patcher.stop()

        if captured_metadata:
            last_meta = captured_metadata[-1]
            assert not last_meta.get("force_full_recall"), (
                f"短 prompt 时 orchestrator.dispatch metadata 不应含 force_full_recall=True，"
                f"实际 metadata={last_meta}"
            )


# -----------------------------------------------------------------------
# TestEnvThresholdOverride — H-A2 ENV 覆盖测试
# -----------------------------------------------------------------------

class TestEnvThresholdOverride:
    """H-A2 修复验证：_resolve_long_prompt_threshold() 优先 ENV OCTOAGENT_LONG_PROMPT_THRESHOLD。"""

    def test_default_threshold_is_2000(self, monkeypatch) -> None:
        """无 ENV 时默认 2000。"""
        monkeypatch.delenv("OCTOAGENT_LONG_PROMPT_THRESHOLD", raising=False)
        assert _resolve_long_prompt_threshold() == 2000

    def test_env_override_to_500(self, monkeypatch) -> None:
        """ENV=500 → threshold 变为 500。"""
        monkeypatch.setenv("OCTOAGENT_LONG_PROMPT_THRESHOLD", "500")
        assert _resolve_long_prompt_threshold() == 500

    def test_env_override_custom_value(self, monkeypatch) -> None:
        """ENV 任意合法正整数。"""
        monkeypatch.setenv("OCTOAGENT_LONG_PROMPT_THRESHOLD", "1234")
        assert _resolve_long_prompt_threshold() == 1234

    def test_env_non_integer_fallback(self, monkeypatch) -> None:
        """ENV 非整数（'abc'）→ fallback 2000。"""
        monkeypatch.setenv("OCTOAGENT_LONG_PROMPT_THRESHOLD", "abc")
        assert _resolve_long_prompt_threshold() == 2000

    def test_env_negative_value_fallback(self, monkeypatch) -> None:
        """ENV 负数（'-100'）→ fallback 2000（非正数不合法）。"""
        monkeypatch.setenv("OCTOAGENT_LONG_PROMPT_THRESHOLD", "-100")
        assert _resolve_long_prompt_threshold() == 2000

    def test_env_zero_fallback(self, monkeypatch) -> None:
        """ENV 零（'0'）→ fallback 2000（非正数）。"""
        monkeypatch.setenv("OCTOAGENT_LONG_PROMPT_THRESHOLD", "0")
        assert _resolve_long_prompt_threshold() == 2000

    def test_env_empty_string_fallback(self, monkeypatch) -> None:
        """ENV 空字符串 → fallback 2000。"""
        monkeypatch.setenv("OCTOAGENT_LONG_PROMPT_THRESHOLD", "")
        assert _resolve_long_prompt_threshold() == 2000

    async def test_env_500_boundary_triggers_at_501_via_spy(
        self, client: AsyncClient, test_app, monkeypatch
    ) -> None:
        """ENV=500 → 501 字符触发，500 字符不触发（通过 spy create_task 验证）。"""
        monkeypatch.setenv("OCTOAGENT_LONG_PROMPT_THRESHOLD", "500")

        # 501 字符应触发
        msg_501 = "x" * 501
        captured = CapturedControlMetadata()
        orig_create_task = TaskService.create_task

        async def spy_create_task(self, message: NormalizedMessage):
            captured.create_task_msgs.append(message)
            return await orig_create_task(self, message)

        with patch.object(TaskService, "create_task", spy_create_task):
            resp = await client.post("/api/chat/send", json={"message": msg_501})
        assert resp.status_code == 200
        cm_501 = captured.last_create_task_control_metadata
        assert cm_501 is not None and cm_501.get("force_full_recall") is True, (
            f"ENV=500 时 501 字符应触发，实际 control_metadata={cm_501}"
        )

        # 500 字符不应触发
        msg_500 = "x" * 500
        captured2 = CapturedControlMetadata()

        async def spy_create_task2(self, message: NormalizedMessage):
            captured2.create_task_msgs.append(message)
            return await orig_create_task(self, message)

        with patch.object(TaskService, "create_task", spy_create_task2):
            resp2 = await client.post("/api/chat/send", json={"message": msg_500})
        assert resp2.status_code == 200
        cm_500 = captured2.last_create_task_control_metadata or {}
        assert "force_full_recall" not in cm_500, (
            f"ENV=500 时恰好 500 字符不应触发，实际 control_metadata={cm_500}"
        )


# -----------------------------------------------------------------------
# TestCrossLanguageMatrix — A-5b 跨语言矩阵
# -----------------------------------------------------------------------

class TestCrossLanguageMatrix:
    """A-5b 跨语言测试矩阵：≥ 5 类输入（中/英/代码/JSON/混合），len > 2000 均触发。

    L-A1 修复：删除旧 test_cross_language_triggering 中的死字段（should_trigger 参数）
    和占位 case（代码块实际长度 1213 但注释写"应触发"）。
    改为统一使用动态构造 len > LONG_PROMPT_THRESHOLD 的消息，全部断言触发。
    """

    @pytest.mark.parametrize(
        "description,message",
        [
            ("2001 中文字符（测）", "测" * (LONG_PROMPT_THRESHOLD + 1)),
            ("2001 英文字符（a）", "a" * (LONG_PROMPT_THRESHOLD + 1)),
            (
                "2001 字符代码块（含换行/backticks）",
                "```python\n" + "x = 1\n" * ((LONG_PROMPT_THRESHOLD // 6) + 1) + "```",
            ),
            (
                "2001 字符 JSON",
                json.dumps({"data": "x" * (LONG_PROMPT_THRESHOLD + 50)}),
            ),
            (
                "短中文 + 长 stack trace（总 > 2000）",
                "错误发生了：\n" + "  at func() in file.py line 1\n" * 70,
            ),
        ],
    )
    async def test_all_cross_language_inputs_trigger_force_full_recall(
        self,
        client: AsyncClient,
        test_app,
        description: str,
        message: str,
    ) -> None:
        """A-5b 验收：≥ 5 个跨语言 case，len > 2000 均触发 force_full_recall=True。

        通过 spy create_task 验证 NormalizedMessage.control_metadata（H-A1 修复路径）。
        """
        # 前置检查：确保测试数据本身就超过阈值
        assert len(message) > LONG_PROMPT_THRESHOLD, (
            f"[{description}] 测试数据长度不足，len={len(message)}"
        )

        captured = CapturedControlMetadata()
        orig_create_task = TaskService.create_task

        async def spy_create_task(self, msg: NormalizedMessage):
            captured.create_task_msgs.append(msg)
            return await orig_create_task(self, msg)

        with patch.object(TaskService, "create_task", spy_create_task):
            resp = await client.post("/api/chat/send", json={"message": message})

        assert resp.status_code == 200
        cm = captured.last_create_task_control_metadata
        assert cm is not None, f"[{description}] NormalizedMessage.control_metadata 未被捕获"
        assert cm.get("force_full_recall") is True, (
            f"[{description}] len={len(message)} 期望 force_full_recall=True，"
            f"实际 control_metadata={cm}"
        )


# -----------------------------------------------------------------------
# TestIsRecallPlannerSkipIntegration — AC-D1 第二层验证（无网络）
# -----------------------------------------------------------------------

class TestIsRecallPlannerSkipIntegration:
    """AC-D1 第二层验证：runtime_context.force_full_recall=True → is_recall_planner_skip=False。

    直接测试 runtime_control.is_recall_planner_skip 逻辑，不跑 LLM。
    """

    def test_force_full_recall_true_makes_recall_planner_skip_return_false(self) -> None:
        """直接验证 is_recall_planner_skip 逻辑（runtime_control.py:106-124）。"""
        from octoagent.core.models import RuntimeControlContext
        from octoagent.gateway.services.runtime_control import is_recall_planner_skip

        # force_full_recall=True，无论 delegation_mode / recall_planner_mode 如何
        for delegation_mode in ("main_inline", "worker_inline", "main_delegate", "subagent", "unspecified"):
            for recall_planner_mode in ("auto", "skip", "full"):
                ctx = RuntimeControlContext(
                    task_id="task-test",
                    force_full_recall=True,
                    delegation_mode=delegation_mode,
                    recall_planner_mode=recall_planner_mode,
                )
                result = is_recall_planner_skip(ctx, {})
                assert result is False, (
                    f"force_full_recall=True 时 is_recall_planner_skip 应返回 False，"
                    f"delegation_mode={delegation_mode}, recall_planner_mode={recall_planner_mode}，"
                    f"实际返回 {result}"
                )

    def test_force_full_recall_false_baseline_not_affected(self) -> None:
        """force_full_recall=False（默认）不影响 baseline 行为。"""
        from octoagent.core.models import RuntimeControlContext
        from octoagent.gateway.services.runtime_control import is_recall_planner_skip

        # baseline：main_inline + skip → should return True
        ctx = RuntimeControlContext(
            task_id="task-test",
            force_full_recall=False,
            delegation_mode="main_inline",
            recall_planner_mode="skip",
        )
        result = is_recall_planner_skip(ctx, {})
        assert result is True, (
            f"baseline（force_full_recall=False, main_inline, skip）应返回 True，实际 {result}"
        )
