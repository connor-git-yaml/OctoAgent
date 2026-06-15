"""F105 Phase D: ConversationBinding 热路径写入测试。

覆盖 spec US-3 AC-1（telegram inbound 登记）/ AC-2（touch 不新增行）
/ AC-3（store 失败降级不阻断主链）/ AC-5（H1 构造性保证）
+ FR-E3 H1 排除（CODEX-H4：direct-worker 会话不写 binding）
+ web 新会话/续聊 touch。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import AgentProfile, AgentProfileStatus
from octoagent.core.store import create_store_group
from octoagent.core.store.conversation_binding_store import (
    SqliteConversationBindingStore,
)
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner

from .test_f105_channel_adapter import _build_service, _write_config


def _telegram_update(update_id: int, message_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "text": text,
            "chat": {"id": 1001, "type": "private"},
            "from": {"id": 2002, "username": "connor"},
        },
    }


# ============================================================
# US-3 AC-1/AC-2/AC-3: telegram inbound 登记 / touch / 降级
# ============================================================


@pytest.mark.asyncio
async def test_runtime_upsert_and_touch(tmp_path: Path) -> None:
    """telegram 首条消息登记 binding；重复消息 touch 不新增行（US-3 AC-1/AC-2）。"""
    _write_config(tmp_path, dm_policy="open")
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"), str(tmp_path / "artifacts")
    )
    service, _ = _build_service(tmp_path, store_group)

    result = await service.handle_webhook_update(_telegram_update(1, 10, "first"))
    assert result.status == "accepted"

    binding = await store_group.conversation_binding_store.get("telegram", "1001")
    assert binding is not None
    assert binding.scope_id == "chat:telegram:1001"
    assert binding.project_id == ""
    assert binding.binding_kind.value == "runtime"
    first_active = binding.last_active_at

    # 第二条消息（不同 update）→ 同一行 touch
    result2 = await service.handle_webhook_update(_telegram_update(2, 11, "second"))
    assert result2.status == "accepted"
    rows = await store_group.conversation_binding_store.list_by_platform("telegram")
    assert len(rows) == 1
    assert rows[0].last_active_at >= first_active

    # duplicate 重投（同 idempotency_key）→ 仍 touch，不新增行
    result3 = await service.handle_webhook_update(_telegram_update(2, 11, "second"))
    assert result3.status == "duplicate"
    rows = await store_group.conversation_binding_store.list_by_platform("telegram")
    assert len(rows) == 1

    await store_group.close()


@pytest.mark.asyncio
async def test_binding_failure_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """binding store 写入抛异常时消息主链不受影响（US-3 AC-3，Constitution #6）。"""
    _write_config(tmp_path, dm_policy="open")
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"), str(tmp_path / "artifacts")
    )
    service, _ = _build_service(tmp_path, store_group)

    async def _boom(*args, **kwargs):
        raise RuntimeError("binding store broken")

    monkeypatch.setattr(
        store_group.conversation_binding_store, "upsert_runtime_binding", _boom
    )

    result = await service.handle_webhook_update(_telegram_update(1, 10, "hello"))
    assert result.status == "accepted"  # task 照常创建
    assert result.task_id

    task = await store_group.task_store.get_task(result.task_id)
    assert task is not None
    await store_group.close()


# ============================================================
# web 路径：新会话/续聊登记 + direct-worker 排除（CODEX-H4）
# ============================================================


@pytest_asyncio.fixture
async def web_app(tmp_path: Path):
    from fastapi import FastAPI
    from octoagent.gateway.routes import chat

    app = FastAPI()
    app.include_router(chat.router)

    store_group = await create_store_group(
        str(tmp_path / "web.db"), str(tmp_path / "artifacts")
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
async def web_client(web_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=web_app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_web_send_records_binding_and_continue_touches(
    web_client: AsyncClient, web_app
) -> None:
    """web 新会话登记 binding；续聊 touch 同一行。"""
    resp = await web_client.post("/api/chat/send", json={"message": "hello"})
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    store = web_app.state.store_group.conversation_binding_store
    rows = await store.list_by_platform("web")
    assert len(rows) == 1
    # conversation_id == task 的 thread_id（baseline：无显式 thread 时
    # fallback 到预生成 task-xxx id，而非 create_task 返回的 ULID）
    task = await web_app.state.store_group.task_store.get_task(task_id)
    assert task is not None
    assert rows[0].conversation_id == task.thread_id
    assert rows[0].scope_id == task.scope_id
    assert rows[0].agent_profile_id == ""  # H1：主 Agent
    first_active = rows[0].last_active_at

    # 续聊（task_id 复用路径）→ touch 同一行
    resp2 = await web_client.post(
        "/api/chat/send", json={"message": "again", "task_id": task_id}
    )
    assert resp2.status_code == 200
    rows = await store.list_by_platform("web")
    assert len(rows) == 1
    assert rows[0].last_active_at >= first_active


@pytest.mark.asyncio
async def test_project_scoped_continue_touches_same_binding(
    web_client: AsyncClient, web_app
) -> None:
    """Codex Final H1：project-scoped 会话纯 task_id 续聊不得写出空 project 第二行。

    续聊路径的 project_id 必须从 existing_task.scope_id 反解（与首条创建语义
    恒一致），否则四元组含 project_id 时会新增 (web, thread, '') 行污染 last-route。
    """
    from octoagent.core.models import Project

    await web_app.state.store_group.project_store.create_project(
        Project(project_id="proj-f105", slug="proj-f105", name="F105 测试项目")
    )
    await web_app.state.store_group.conn.commit()

    resp = await web_client.post(
        "/api/chat/send", json={"message": "hello", "project_id": "proj-f105"}
    )
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    store = web_app.state.store_group.conversation_binding_store
    rows = await store.list_by_platform("web")
    assert len(rows) == 1
    assert rows[0].project_id == "proj-f105"

    # 纯 task_id 续聊（请求不带 project_id）→ 仍 touch 同一行，不新增空 project 行
    resp2 = await web_client.post(
        "/api/chat/send", json={"message": "again", "task_id": task_id}
    )
    assert resp2.status_code == 200
    rows = await store.list_by_platform("web")
    assert len(rows) == 1
    assert rows[0].project_id == "proj-f105"


@pytest.mark.asyncio
async def test_direct_worker_session_not_bound(
    web_client: AsyncClient, web_app
) -> None:
    """CODEX-H4：用户显式选 worker 直聊的会话不写 binding（H1 不被污染）。"""
    await _save_worker_with_mirror(web_app.state.store_group.agent_context_store,
        AgentProfile(
            profile_id="worker-profile-f105",
            project_id="",
            name="测试 worker",
            summary="direct session",
            model_alias="cheap",
            status=AgentProfileStatus.ACTIVE,
        )
    )
    await web_app.state.store_group.conn.commit()

    resp = await web_client.post(
        "/api/chat/send",
        json={"message": "direct worker chat", "agent_profile_id": "worker-profile-f105"},
    )
    assert resp.status_code == 200

    rows = await web_app.state.store_group.conversation_binding_store.list_by_platform(
        "web"
    )
    assert rows == []


# ============================================================
# US-3 AC-5: H1 构造性保证
# ============================================================


def test_h1_no_agent_profile_write_path() -> None:
    """upsert 签名物理上不含 agent_profile_id（H1 构造性保证，spec D5）。"""
    signature = inspect.signature(
        SqliteConversationBindingStore.upsert_runtime_binding
    )
    assert "agent_profile_id" not in signature.parameters


@pytest.mark.asyncio
async def test_h1_all_rows_remain_main_agent(tmp_path: Path) -> None:
    """全部 v0.1 写入路径产出的行 agent_profile_id 恒 ''（US-3 AC-5）。"""
    _write_config(tmp_path, dm_policy="open")
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"), str(tmp_path / "artifacts")
    )
    service, _ = _build_service(tmp_path, store_group)
    await service.handle_webhook_update(_telegram_update(1, 10, "a"))
    await store_group.conversation_binding_store.upsert_runtime_binding(
        "web", "t-1", scope_id="chat:web:t-1"
    )

    rows = await store_group.conversation_binding_store.list_recent()
    assert len(rows) == 2
    assert all(row.agent_profile_id == "" for row in rows)
    await store_group.close()
# ── F117 测试辅助（worker 镜像播种）────────────────────────────────────
# 运行时统一读 agent_profiles(kind=worker) 镜像；生产中镜像由 publish/_sync 写。本 helper
# 把 worker 配置 AgentProfile 写成镜像（kind=worker + source_* 标记）反映生产状态。
# W4-3：WorkerProfile 类已删，入参直接是 AgentProfile（不再 save_worker_profile）。
async def _save_worker_with_mirror(store, wp: AgentProfile):
    await store.save_agent_profile(
        wp.model_copy(
            update={
                "kind": "worker",
                "persona_summary": wp.summary,
                "version": max(int(wp.active_revision or 0), int(wp.draft_revision or 0), 1),
                "metadata": {
                    **dict(wp.metadata),
                    "source_kind": "worker_profile_mirror",
                    "source_worker_profile_id": wp.profile_id,
                },
            }
        )
    )
    return wp
