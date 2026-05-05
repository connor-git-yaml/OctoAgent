"""F087 e2e_live helpers/factories.py（T-P2-8 主 fixture + T-P2-12 helper 复制）。

提供：

1. ``octo_harness_e2e`` fixture：注入 4 DI 钩子（``credential_store`` /
   ``llm_adapter`` / ``mcp_servers_dir`` / ``data_dir``）+ ProviderRouter
   timeout 120s + max_steps=10。
2. ``_build_real_user_profile_handler`` / ``_ensure_audit_task`` /
   ``_insert_turn_events`` 复制版本（T-P2-12 双源共存——P5 删除旧位置）。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fixture：octo_harness_e2e（T-P2-8）
# ---------------------------------------------------------------------------


@pytest.fixture
async def octo_harness_e2e(
    tmp_path: Path,
    real_codex_credential_store: Any,  # 由 fixtures_real_credentials 提供
) -> AsyncIterator[Any]:
    """OctoHarness e2e fixture：注入 4 DI 钩子 + ProviderRouter 120s timeout。

    布置：
      - ``data_dir`` = ``tmp_path / "data"``（隔离宿主 SQLite / artifacts）
      - ``mcp_servers_dir`` = ``tmp_path / "mcp-servers"``（隔离 ~/.octoagent/mcp-servers）
      - ``credential_store`` = e2e tmp 副本（隔离宿主 auth-profiles.json）
      - ``llm_adapter`` = None → 走默认 ProviderRouterMessageAdapter
      - ``ProviderRouter(timeout_s=120.0)`` 由 OctoHarness 内部已构造，timeout
        由 alias_registry / provider config 控制；e2e 不强行覆盖 router timeout
        （改在 alias config 注入），保留代码路径自然。
      - ``max_steps=10`` 由 task_runner 默认值控制；e2e 不需要单独 override。

    F087 P2 阶段不实际触发 ``OctoHarness.bootstrap()``——
    P3 起每个 case 自行决定 bootstrap 时机；本 fixture 仅提供构造好的 harness 实例。
    """
    from fastapi import FastAPI

    from octoagent.gateway.harness.octo_harness import OctoHarness

    # e2e 实例 root 在 tmp 下，绝不动宿主
    e2e_root = tmp_path / "octoagent_e2e_root"
    data_dir = e2e_root / "data"
    mcp_servers_dir = e2e_root / "mcp-servers"
    project_root = e2e_root  # OctoHarness 内 project_root 就是 instance root

    # 必要骨架目录（OctoHarness bootstrap 内部还会按需 mkdir，这里仅保证 root 存在）
    project_root.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    mcp_servers_dir.mkdir(parents=True, exist_ok=True)
    (project_root / "behavior" / "system").mkdir(parents=True, exist_ok=True)

    harness = OctoHarness(
        project_root=project_root,
        credential_store=real_codex_credential_store,
        llm_adapter=None,  # 走默认 ProviderRouterMessageAdapter
        mcp_servers_dir=mcp_servers_dir,
        data_dir=data_dir,
    )

    app = FastAPI()
    yield {"harness": harness, "app": app, "project_root": project_root}

    # teardown：调 shutdown 释放资源
    # F089 Codex adversarial review Finding #1 (high) 闭环：
    # 旧实现 ``except: pass`` / 中间版 ``except: log.error`` 都吞掉
    # ``harness.shutdown(app)`` 异常——这条路径覆盖 mcp_installer /
    # mcp_registry / task_runner / DB 连接等所有生产 shutdown 子步骤，
    # 任一失败都应让 e2e 真 fail，而不是靠日志兜底。
    # ``OctoHarness.shutdown`` 自身用 ``hasattr(app.state, ...)`` 守卫，
    # 未 bootstrap 时各分支 no-op 安全完成、不抛异常，因此**不需要**
    # 额外 try/except 容忍"未 bootstrap"——直接 await，错误自然上抛。
    await harness.shutdown(app)


# ---------------------------------------------------------------------------
# T-P2-12: 旧 helper 复制版本（双源共存到 P5 T-P5-1 删除旧位置）
# ---------------------------------------------------------------------------
# 旧位置：apps/gateway/tests/e2e/test_acceptance_scenarios.py
#
# 复制原因：避免 T-P3-6（旧 acceptance 仍跑）+ T-P5-1（删旧文件）时序冲突。
# 复制后 P3/P4 新 case 用本文件版本；旧 acceptance 仍用旧位置；P5 一并清理。


async def _ensure_audit_task(sg: Any, task_id: str) -> None:
    """确保审计 task 存在（外键约束）。从旧 acceptance_scenarios 复制。"""
    from datetime import datetime, timezone

    from octoagent.core.models.task import RequesterInfo, Task, TaskPointers

    try:
        existing = await sg.task_store.get_task(task_id)
        if existing is not None:
            return
    except Exception:
        pass
    now = datetime.now(timezone.utc)
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        title=f"E2E 审计占位 task ({task_id})",
        trace_id=task_id,
        requester=RequesterInfo(channel="system", sender_id="system"),
        pointers=TaskPointers(),
    )
    await sg.task_store.create_task(task)


async def _insert_turn_events(conn: Any, turns: list[dict[str, Any]]) -> None:
    """向 events 表写入模拟 turn 事件。从旧 acceptance_scenarios 复制。"""
    import time
    from datetime import datetime, timezone

    from ulid import ULID

    now = datetime.now(timezone.utc)
    try:
        await conn.execute(
            """
            INSERT OR IGNORE INTO tasks (task_id, created_at, updated_at, title, status, trace_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "_e2e_turns_task",
                now.isoformat(),
                now.isoformat(),
                "e2e turns test task",
                "running",
                "_e2e_turns_task",
            ),
        )
    except Exception:
        pass

    for i, turn in enumerate(turns):
        payload_str = json.dumps(turn.get("payload", {}))
        await conn.execute(
            """
            INSERT OR IGNORE INTO events (event_id, task_id, task_seq, ts, type, actor, payload, trace_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(ULID()),
                "_e2e_turns_task",
                i + 100 + int(time.time() * 1000) % 100000,
                now.isoformat(),
                "TASK_USER_MESSAGE",
                "USER",
                payload_str,
                "_e2e_turns_task",
            ),
        )
    await conn.commit()


async def _build_real_user_profile_handler(
    store_group: Any,
    tmp_path: Path,
) -> tuple[Any, Any, Path]:
    """构造真实 user_profile.update handler（F38/F39 修复路径）。

    从旧 acceptance_scenarios 复制；P5 T-P5-1 删除旧位置时本文件保留。
    """
    from unittest.mock import MagicMock

    from octoagent.gateway.harness.snapshot_store import SnapshotStore
    from octoagent.gateway.services.builtin_tools import user_profile_tools
    from octoagent.gateway.services.builtin_tools._deps import ToolDeps

    user_md = tmp_path / "behavior" / "system" / "USER.md"
    user_md.parent.mkdir(parents=True, exist_ok=True)
    if not user_md.exists():
        user_md.write_text("", encoding="utf-8")

    snap_store = SnapshotStore(conn=store_group.conn)
    await snap_store.load_snapshot(
        session_id="e2e-real-handler",
        files={"USER.md": user_md},
    )

    deps = ToolDeps(
        project_root=tmp_path,
        stores=store_group,
        tool_broker=MagicMock(),
        tool_index=MagicMock(),
        skill_discovery=MagicMock(),
        memory_console_service=MagicMock(),
        memory_runtime_service=MagicMock(),
        _snapshot_store=snap_store,
    )

    captured_handlers: dict[str, Any] = {}

    class _CaptureBroker:
        async def try_register(self, meta: Any, handler: Any) -> None:
            captured_handlers[meta.name] = handler

    await user_profile_tools.register(_CaptureBroker(), deps)
    handler = captured_handlers.get("user_profile.update")
    assert handler is not None, "user_profile.update handler 应已注册"
    return handler, snap_store, user_md


async def submit_message_with_control_metadata(
    *,
    app: Any,
    text: str,
    control_metadata: dict[str, Any],
    thread_id: str = "e2e-thread",
    sender_id: str = "owner",
    sender_name: str = "Owner",
) -> str:
    """提交消息时注入 trusted control_metadata（如 force_tool_choice）。

    e2e 专用。绕开 ``/api/message`` HTTP route——该 route 故意不接 control_metadata
    （trust boundary：禁止外部 HTTP 直接控 LLM 行为）。本 helper 直调
    ``TaskService.create_task`` 内部入口，把 control_metadata 写进 USER_MESSAGE
    event payload；后续 ``merge_control_metadata`` 会按 turn 生命周期合并到
    ``execution_context.metadata``，``ProviderModelClient`` 从中读
    ``force_tool_choice`` 透传给 ProviderRouter。

    适用：F087 域 #7（强制 graph_pipeline）/ 域 #8b（强制 delegate_task）等
    需要消除 LLM 决策不确定性的 e2e。

    Args:
        app: FastAPI 实例（需 ``app.state.store_group`` / ``app.state.sse_hub`` /
             ``app.state.task_runner``）。
        text: 用户消息文本。
        control_metadata: trusted control 字典，如
            ``{"force_tool_choice": {"type": "function", "function": {"name": "graph_pipeline"}}}``。
            未在 ``connection_metadata.CONTROL_METADATA_KEYS`` 白名单的 key 会被
            ``normalize_control_metadata`` 静默丢弃。
        thread_id / sender_id / sender_name: 消息属性。

    Returns:
        新建 task 的 task_id。
    """
    import uuid

    from octoagent.core.models.message import NormalizedMessage
    from octoagent.gateway.services.task_service import TaskService

    msg = NormalizedMessage(
        channel="web",
        thread_id=thread_id,
        sender_id=sender_id,
        sender_name=sender_name,
        text=text,
        idempotency_key=f"e2e-fc-{uuid.uuid4().hex[:8]}",
        control_metadata=control_metadata,
    )

    sg = app.state.store_group
    sse_hub = app.state.sse_hub
    service = TaskService(sg, sse_hub)
    task_id, _ = await service.create_task(msg)

    task_runner = getattr(app.state, "task_runner", None)
    if task_runner is not None:
        await task_runner.enqueue(task_id, msg.text)

    return task_id


def copy_local_instance_template(template_root: Path, dst_root: Path) -> None:
    """把 ``tests/fixtures/local-instance/`` 模板复制到 e2e tmp dst_root。

    供 e2e fixture 在 bootstrap 前调用，给 dst_root 注入 USER.md / MEMORY.md /
    octoagent.yaml 初始内容。
    """
    dst_behavior = dst_root / "behavior" / "system"
    dst_behavior.mkdir(parents=True, exist_ok=True)

    src_behavior = template_root / "behavior" / "system"
    if (src_behavior / "USER.md.template").exists():
        shutil.copy(src_behavior / "USER.md.template", dst_behavior / "USER.md")
    if (src_behavior / "MEMORY.md.template").exists():
        shutil.copy(src_behavior / "MEMORY.md.template", dst_behavior / "MEMORY.md")

    if (template_root / "octoagent.yaml.template").exists():
        shutil.copy(template_root / "octoagent.yaml.template", dst_root / "octoagent.yaml")


__all__ = [
    "octo_harness_e2e",
    "_build_real_user_profile_handler",
    "_ensure_audit_task",
    "_insert_turn_events",
    "copy_local_instance_template",
    "submit_message_with_control_metadata",
]
