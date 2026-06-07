"""Session 记忆提取 fire-and-forget task 注册进 harness shutdown drain 集合的单测。

F103d 真跑暴露的 shutdown "no active connection" 竞态根因修复：
record_response_context 的 fire-and-forget 提取 task 之前未注册进
app.state.background_tasks，shutdown 关 DB 连接前不会 drain 它 → 在途提取落库命中
已关闭连接 + 最后一轮提取永久丢失。本套件锁定 AgentContextService
._spawn_session_memory_extraction 的注册 / 自动移除 / cancel-safe done 回调行为。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_context import AgentContextService


@pytest.fixture(autouse=True)
def _restore_shared_background_tasks():
    """复位类级单例，避免跨测试 / 跨文件污染。"""
    saved = AgentContextService._shared_background_tasks
    yield
    AgentContextService._shared_background_tasks = saved


@pytest.fixture
async def make_service(tmp_path):
    """工厂 fixture：构造 AgentContextService，测试结束统一关闭 store_group 连接。"""
    created = []
    idx = 0

    async def _factory():
        nonlocal idx
        idx += 1
        store_group = await create_store_group(
            str(tmp_path / f"sm-{idx}.db"), str(tmp_path / "art")
        )
        created.append(store_group)
        return AgentContextService(store_group, project_root=tmp_path)

    yield _factory
    for sg in created:
        await sg.close()


def _stub_extractor(coro_factory):
    """返回 extractor stub，其 extract_and_commit(**kw) 返回 coro_factory() 协程。"""
    ext = MagicMock()
    ext.extract_and_commit = lambda **kw: coro_factory()
    return ext


def test_set_background_tasks_injects_shared_set():
    sentinel: set = set()
    AgentContextService.set_background_tasks(sentinel)
    assert AgentContextService._shared_background_tasks is sentinel


@pytest.mark.asyncio
async def test_spawn_registers_task_then_auto_discards(make_service):
    """在途时 task 注册进 drain 集合；完成后经 done 回调自动移除（不无界增长）。"""
    svc = await make_service()
    bg: set = set()
    AgentContextService.set_background_tasks(bg)

    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow():
        started.set()
        await release.wait()
        return MagicMock()

    svc.get_session_memory_extractor = lambda: _stub_extractor(_slow)  # type: ignore[method-assign]

    task = svc._spawn_session_memory_extraction(agent_session=MagicMock(), project=None)
    assert task is not None
    await started.wait()
    # 在途：已注册进 shutdown drain 集合
    assert task in bg

    release.set()
    await task
    # 完成：discard 回调已移除
    assert task not in bg


@pytest.mark.asyncio
async def test_spawn_done_callback_survives_cancellation(make_service):
    """drain 超时会 cancel task：done 回调先判 cancelled，不得因 t.exception() raise。"""
    svc = await make_service()
    bg: set = set()
    AgentContextService.set_background_tasks(bg)

    started = asyncio.Event()

    async def _hang():
        started.set()
        await asyncio.Event().wait()  # 永远挂起，直到被 cancel

    svc.get_session_memory_extractor = lambda: _stub_extractor(_hang)  # type: ignore[method-assign]

    task = svc._spawn_session_memory_extraction(agent_session=MagicMock(), project=None)
    assert task is not None
    await started.wait()
    assert task in bg

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # done 回调未因 cancelled 崩，且已从集合移除
    assert task not in bg


@pytest.mark.asyncio
async def test_spawn_without_injected_set_degrades_gracefully(make_service):
    """未注入 background_tasks（_shared_background_tasks=None）→ 仍 spawn，不崩。"""
    svc = await make_service()
    AgentContextService.set_background_tasks(None)

    release = asyncio.Event()

    async def _quick():
        await release.wait()
        return MagicMock()

    svc.get_session_memory_extractor = lambda: _stub_extractor(_quick)  # type: ignore[method-assign]

    task = svc._spawn_session_memory_extraction(agent_session=MagicMock(), project=None)
    assert task is not None
    release.set()
    await task  # 不抛即可


@pytest.mark.asyncio
async def test_spawn_returns_none_when_extractor_unavailable(make_service):
    """extractor 不可用 → 返回 None，不创建 task、不崩。"""
    svc = await make_service()
    AgentContextService.set_background_tasks(set())
    svc.get_session_memory_extractor = lambda: None  # type: ignore[method-assign]

    task = svc._spawn_session_memory_extraction(agent_session=MagicMock(), project=None)
    assert task is None
