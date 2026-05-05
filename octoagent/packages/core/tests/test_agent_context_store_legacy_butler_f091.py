"""F091 Final Codex HIGH 闭环：legacy butler row 经 store 读取层 normalize 兜底。

场景：F091 Phase B 删除启动 migration 后，跳版本升级 / Docker volume / backup 恢复
等场景下 SQLite 仍可能有旧 butler 行。store 层 _row_to_agent_runtime / _row_to_agent_session
通过 normalize_runtime_role / normalize_session_kind 兜底，避免直接 enum constructor 抛 ValueError。

覆盖：
- 直接写入 row level legacy butler / butler_main 字符串
- 通过 store 公共 list / get API 读取，验证返回的 enum 是 normalize 后的 MAIN / MAIN_BOOTSTRAP
- 验证不报错（之前会 raise ValueError）
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from octoagent.core.models import (
    AgentRuntimeRole,
    AgentRuntimeStatus,
    AgentSessionKind,
    AgentSessionStatus,
)
from octoagent.core.store import create_store_group


async def _insert_legacy_butler_runtime_row(
    conn: aiosqlite.Connection,
    *,
    agent_runtime_id: str,
    project_id: str,
    agent_profile_id: str,
) -> None:
    """直接 SQL INSERT 一行 legacy butler role 数据（绕过 save_agent_runtime 校验）。"""
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO agent_runtimes (
            agent_runtime_id, project_id, agent_profile_id, worker_profile_id,
            role, name, persona_summary, status, permission_preset, role_card,
            metadata, created_at, updated_at, archived_at
        ) VALUES (?, ?, ?, '', 'butler', ?, ?, 'active', 'normal', '', '{}', ?, ?, NULL)
        """,
        (
            agent_runtime_id,
            project_id,
            agent_profile_id,
            "Legacy Butler Runtime",
            "legacy butler persona summary",
            now,
            now,
        ),
    )
    await conn.commit()


async def _insert_legacy_butler_session_row(
    conn: aiosqlite.Connection,
    *,
    agent_session_id: str,
    agent_runtime_id: str,
    project_id: str,
) -> None:
    """直接 SQL INSERT 一行 legacy butler_main kind session 数据。"""
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        """
        INSERT INTO agent_sessions (
            agent_session_id, agent_runtime_id, kind, status, project_id,
            surface, thread_id, legacy_session_id, alias, parent_agent_session_id,
            work_id, a2a_conversation_id, last_context_frame_id, last_recall_frame_id,
            recent_transcript, rolling_summary, metadata, created_at, updated_at, closed_at
        ) VALUES (
            ?, ?, 'butler_main', 'active', ?,
            'chat', '', '', '', '',
            '', '', '', '',
            '[]', '', '{}', ?, ?, NULL
        )
        """,
        (
            agent_session_id,
            agent_runtime_id,
            project_id,
            now,
            now,
        ),
    )
    await conn.commit()


async def test_legacy_butler_runtime_row_normalized_to_main(tmp_path: Path) -> None:
    """legacy role='butler' 行经 store 层 normalize_runtime_role 兜底返回 AgentRuntimeRole.MAIN。"""
    store_group = await create_store_group(
        str(tmp_path / "f091-butler-runtime.db"),
        str(tmp_path / "artifacts"),
    )

    await _insert_legacy_butler_runtime_row(
        store_group.conn,
        agent_runtime_id="agent-runtime-legacy",
        project_id="project-legacy",
        agent_profile_id="agent-profile-legacy",
    )

    # 通过 store 公共 API 读取——之前会 raise ValueError("'butler' is not a valid AgentRuntimeRole")
    runtime = await store_group.agent_context_store.get_agent_runtime("agent-runtime-legacy")
    assert runtime is not None
    assert runtime.role is AgentRuntimeRole.MAIN  # ← 关键：normalize 兜底
    assert runtime.status is AgentRuntimeStatus.ACTIVE  # 未受影响

    await store_group.conn.close()


async def test_legacy_butler_session_row_normalized_to_main_bootstrap(tmp_path: Path) -> None:
    """legacy kind='butler_main' 行经 store 层 normalize_session_kind 兜底返回 AgentSessionKind.MAIN_BOOTSTRAP。"""
    store_group = await create_store_group(
        str(tmp_path / "f091-butler-session.db"),
        str(tmp_path / "artifacts"),
    )

    # 先创建一个合法 runtime（butler row 已 normalize）
    await _insert_legacy_butler_runtime_row(
        store_group.conn,
        agent_runtime_id="agent-runtime-legacy-2",
        project_id="project-legacy-2",
        agent_profile_id="agent-profile-legacy-2",
    )
    await _insert_legacy_butler_session_row(
        store_group.conn,
        agent_session_id="agent-session-legacy",
        agent_runtime_id="agent-runtime-legacy-2",
        project_id="project-legacy-2",
    )

    session = await store_group.agent_context_store.get_agent_session("agent-session-legacy")
    assert session is not None
    assert session.kind is AgentSessionKind.MAIN_BOOTSTRAP  # ← 关键：normalize 兜底
    assert session.status is AgentSessionStatus.ACTIVE

    await store_group.conn.close()


async def test_canonical_main_role_unaffected(tmp_path: Path) -> None:
    """canonical role='main' 不受 normalize 影响，仍正常返回 MAIN。"""
    store_group = await create_store_group(
        str(tmp_path / "f091-canonical-main.db"),
        str(tmp_path / "artifacts"),
    )

    now = datetime.now(UTC).isoformat()
    await store_group.conn.execute(
        """
        INSERT INTO agent_runtimes (
            agent_runtime_id, project_id, agent_profile_id, worker_profile_id,
            role, name, persona_summary, status, permission_preset, role_card,
            metadata, created_at, updated_at, archived_at
        ) VALUES ('agent-runtime-canonical', 'project-canonical', 'agent-profile-canonical', '',
            'main', 'Canonical Main', 'canonical persona', 'active', 'normal', '', '{}', ?, ?, NULL)
        """,
        (now, now),
    )
    await store_group.conn.commit()

    runtime = await store_group.agent_context_store.get_agent_runtime("agent-runtime-canonical")
    assert runtime is not None
    assert runtime.role is AgentRuntimeRole.MAIN

    await store_group.conn.close()
