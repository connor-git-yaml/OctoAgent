"""Migration 117: WorkerProfile → AgentProfile 完全合并（架构债 D2）。

用户拍板（2026-06-13）：决策 A = **A1 彻底物理合并**；决策 B = **本次全改名**。

把 ``worker_profiles`` + ``worker_profile_revisions`` 两表的数据合并进统一的
``agent_profiles``（kind=worker 行携带工具字段）+ 新 ``agent_profile_revisions``，
塌缩 ``agent_runtimes.worker_profile_id``，重命名 ``works.requested_worker_profile_id``，
最后 DROP 两张旧表。

⚠ **不可逆**：apply 会 DROP ``worker_profiles`` / ``worker_profile_revisions`` 与
``agent_runtimes.worker_profile_id`` 列。``run_rollback`` 仅释放幂等键（删 audit 行），
**不还原已 DROP 的表/列**——真迁移前必须先备份（VACUUM INTO / F022 backup）。

⚠ **schema-defensive**：实测托管实例 ``~/.octoagent`` 的 ``agent_profiles`` **无 kind 列**
（schema 落后于 F090 kind ALTER），worker 镜像仅靠 ``metadata.source_kind=
"worker_profile_mirror"`` 标识。本迁移全程 PRAGMA 驱动，不假设 code-schema == instance-schema。

三段式（与 F094 migrate-094 / config migrate 模板对齐）::

    octo ... migrate-117 --dry-run     # 只读，报 rows/conflicts/irreversible-points
    octo ... migrate-117 --apply       # 单事务真实迁移 + 写 audit 行（幂等短路）
    octo ... migrate-117 --rollback <run_id>   # 删 audit 行（仅释放幂等键）

或直接::

    python -m octoagent.core.store.migrations.migration_117_profile_merge \\
        <db_path> [--dry-run | --apply | --rollback <run_id>]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

_IDEMPOTENCY_KEY = "octoagent.core.migration.117.profile_merge.v1"

# F117 从 worker_profiles 吸收进 agent_profiles 的 9 列（default 与 worker_profiles DDL 一致）
_WORKER_COLUMNS: list[tuple[str, str]] = [
    ("summary", "TEXT NOT NULL DEFAULT ''"),
    ("default_tool_groups", "TEXT NOT NULL DEFAULT '[]'"),
    ("selected_tools", "TEXT NOT NULL DEFAULT '[]'"),
    ("runtime_kinds", "TEXT NOT NULL DEFAULT '[]'"),
    ("status", "TEXT NOT NULL DEFAULT 'active'"),
    ("origin_kind", "TEXT NOT NULL DEFAULT 'custom'"),
    ("draft_revision", "INTEGER NOT NULL DEFAULT 0"),
    ("active_revision", "INTEGER NOT NULL DEFAULT 0"),
    ("archived_at", "TEXT"),
]

_AUDIT_TABLE = "f117_profile_merge_runs"


# ───────────────────────── helpers (read-only) ─────────────────────────


async def _table_exists(conn: aiosqlite.Connection, table_name: str) -> bool:
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return (await cursor.fetchone()) is not None


async def _table_columns(conn: aiosqlite.Connection, table_name: str) -> set[str]:
    cursor = await conn.execute(f"PRAGMA table_info({table_name})")
    rows = await cursor.fetchall()
    return {str(row[1]) for row in rows}


async def _scalar(conn: aiosqlite.Connection, sql: str, params: tuple = ()) -> int:
    cursor = await conn.execute(sql, params)
    row = await cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def _detect_worker_mirror_profile_ids(conn: aiosqlite.Connection) -> set[str]:
    """检出 agent_profiles 中属于 worker 镜像的 profile_id（3 法并集，schema-defensive）。

    1. 同 id：profile_id ∈ worker_profiles.profile_id（worker_profile_ops / entity_ensure 约定）
    2. metadata 标记：json_extract(metadata,'$.source_kind')='worker_profile_mirror'
    3. kind 列（若存在）：kind='worker'
    （agent-profile-{wid} 前缀镜像单独由 _detect_prefix_mirrors 处理）
    """
    ids: set[str] = set()
    if await _table_exists(conn, "worker_profiles"):
        cursor = await conn.execute(
            "SELECT a.profile_id FROM agent_profiles a "
            "JOIN worker_profiles w ON a.profile_id = w.profile_id"
        )
        ids.update(str(r[0]) for r in await cursor.fetchall())
    cursor = await conn.execute(
        "SELECT profile_id FROM agent_profiles "
        "WHERE json_extract(metadata,'$.source_kind')='worker_profile_mirror'"
    )
    ids.update(str(r[0]) for r in await cursor.fetchall())
    if "kind" in await _table_columns(conn, "agent_profiles"):
        cursor = await conn.execute(
            "SELECT profile_id FROM agent_profiles WHERE kind='worker'"
        )
        ids.update(str(r[0]) for r in await cursor.fetchall())
    return ids


async def _detect_prefix_mirrors(conn: aiosqlite.Connection) -> list[tuple[str, str]]:
    """检出 agent-profile-{wid} 前缀镜像：返回 (agent_profile_id, derived_worker_id)。

    仅当 'agent-profile-{wid}' 去前缀后命中 worker_profiles.profile_id 才算前缀镜像
    （区别于独立 main profile 如 agent-profile-project-default）。
    """
    if not await _table_exists(conn, "worker_profiles"):
        return []
    cursor = await conn.execute(
        "SELECT profile_id FROM agent_profiles WHERE profile_id LIKE 'agent-profile-%'"
    )
    candidates = [str(r[0]) for r in await cursor.fetchall()]
    cursor = await conn.execute("SELECT profile_id FROM worker_profiles")
    worker_ids = {str(r[0]) for r in await cursor.fetchall()}
    out: list[tuple[str, str]] = []
    for apid in candidates:
        derived = apid[len("agent-profile-") :]
        if derived in worker_ids:
            out.append((apid, derived))
    return out


async def _conflicts(conn: aiosqlite.Connection) -> list[str]:
    """真实冲突：worker_profiles.profile_id 命中一个 NON-mirror agent_profiles 行
    （即 metadata 无 worker_profile_mirror 标记且非同名预期镜像）——理论不应发生。
    """
    if not await _table_exists(conn, "worker_profiles"):
        return []
    cursor = await conn.execute(
        "SELECT w.profile_id FROM worker_profiles w "
        "JOIN agent_profiles a ON a.profile_id = w.profile_id "
        "WHERE COALESCE(json_extract(a.metadata,'$.source_kind'),'') "
        "      != 'worker_profile_mirror'"
    )
    rows = [str(r[0]) for r in await cursor.fetchall()]
    # 同名但无 mirror 标记 → 仍可安全 UPDATE 合并（同 profile_id），仅作 INFO 提示，
    # 不视为阻断性冲突。返回供 report 标注。
    return rows


# ───────────────────────── audit helpers ─────────────────────────


async def _ensure_audit_table(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_AUDIT_TABLE} ("
        " run_id TEXT PRIMARY KEY,"
        " idempotency_key TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'completed',"
        " requested_by TEXT NOT NULL DEFAULT '',"
        " metadata TEXT NOT NULL DEFAULT '{}',"
        " started_at TEXT NOT NULL,"
        " finished_at TEXT NOT NULL"
        ")"
    )


async def _existing_run_id(conn: aiosqlite.Connection) -> str | None:
    if not await _table_exists(conn, _AUDIT_TABLE):
        return None
    cursor = await conn.execute(
        f"SELECT run_id FROM {_AUDIT_TABLE} WHERE idempotency_key=?",
        (_IDEMPOTENCY_KEY,),
    )
    row = await cursor.fetchone()
    return str(row[0]) if row else None


# ───────────────────────── dry-run (只读) ─────────────────────────


async def run_dry_run(db_path: str) -> dict[str, object]:
    """只读扫描，报告 apply 将做什么 + 冲突 + 不可逆点。不写库。"""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row

        worker_tbl = await _table_exists(conn, "worker_profiles")
        rev_tbl = await _table_exists(conn, "worker_profile_revisions")

        result: dict[str, object] = {
            "command": "migrate-117",
            "mode": "dry-run",
            "idempotency_key": _IDEMPOTENCY_KEY,
        }

        existing = await _existing_run_id(conn)
        if existing is not None:
            result["already_applied_run_id"] = existing
        if not worker_tbl and not rev_tbl:
            result["status"] = "already_merged"
            result["note"] = "worker_profiles / worker_profile_revisions 已不存在——迁移已应用或库已是合并后 schema"
            return result

        agent_cols = await _table_columns(conn, "agent_profiles")
        kind_present = "kind" in agent_cols
        missing_worker_cols = [c for c, _ in _WORKER_COLUMNS if c not in agent_cols]

        mirror_ids = await _detect_worker_mirror_profile_ids(conn)
        prefix_mirrors = await _detect_prefix_mirrors(conn)
        conflicts_same_id = await _conflicts(conn)

        worker_count = await _scalar(conn, "SELECT COUNT(*) FROM worker_profiles") if worker_tbl else 0
        rev_count = await _scalar(conn, "SELECT COUNT(*) FROM worker_profile_revisions") if rev_tbl else 0
        # worker_profiles 行：已有同 id 镜像（UPDATE 合并）vs 无镜像（INSERT 新行）
        worker_with_mirror = (
            await _scalar(
                conn,
                "SELECT COUNT(*) FROM worker_profiles w "
                "WHERE EXISTS (SELECT 1 FROM agent_profiles a WHERE a.profile_id=w.profile_id)",
            )
            if worker_tbl
            else 0
        )
        worker_without_mirror = worker_count - worker_with_mirror

        rt_cols = await _table_columns(conn, "agent_runtimes")
        has_wpid = "worker_profile_id" in rt_cols
        runtimes_with_wpid = (
            await _scalar(conn, "SELECT COUNT(*) FROM agent_runtimes WHERE worker_profile_id!=''")
            if has_wpid
            else 0
        )
        orphan_worker_runtimes = (
            await _scalar(
                conn,
                "SELECT COUNT(*) FROM agent_runtimes "
                "WHERE role='worker' AND agent_profile_id='' AND worker_profile_id!=''",
            )
            if has_wpid
            else 0
        )
        mismatch_worker_runtimes = (
            await _scalar(
                conn,
                "SELECT COUNT(*) FROM agent_runtimes "
                "WHERE role='worker' AND agent_profile_id!='' "
                "AND worker_profile_id!='' AND agent_profile_id!=worker_profile_id",
            )
            if has_wpid
            else 0
        )

        works_cols = await _table_columns(conn, "works") if await _table_exists(conn, "works") else set()
        works_total = await _scalar(conn, "SELECT COUNT(*) FROM works") if works_cols else 0
        works_with_req = (
            await _scalar(conn, "SELECT COUNT(*) FROM works WHERE requested_worker_profile_id!=''")
            if "requested_worker_profile_id" in works_cols
            else 0
        )

        prefix_ids = {apid for apid, _ in prefix_mirrors}
        projects_prefix_refs = (
            await _scalar(
                conn,
                "SELECT COUNT(*) FROM projects WHERE default_agent_profile_id IN (%s)"
                % (",".join("?" * len(prefix_ids))),
                tuple(prefix_ids),
            )
            if prefix_ids and await _table_exists(conn, "projects")
            else 0
        )

        result.update(
            {
                "status": "pending",
                "schema": {
                    "agent_profiles_kind_column_present": kind_present,
                    "agent_profiles_missing_worker_columns": missing_worker_cols,
                    "worker_detection": "metadata.source_kind"
                    if not kind_present
                    else "kind_column+metadata",
                },
                "plan": {
                    "worker_profiles_to_merge": worker_count,
                    "worker_rows_update_into_existing_mirror": worker_with_mirror,
                    "worker_rows_insert_as_new": worker_without_mirror,
                    "mirror_agent_profile_rows_detected": len(mirror_ids),
                    "prefix_mirror_rows_to_reconcile": len(prefix_mirrors),
                    "revisions_to_rekey_to_agent_profile_revisions": rev_count,
                    "agent_runtimes_with_worker_profile_id": runtimes_with_wpid,
                    "orphan_worker_runtimes_to_backfill_agent_profile_id": orphan_worker_runtimes,
                    "mismatched_worker_runtimes_to_reconcile_id": mismatch_worker_runtimes,
                    "works_total_rows": works_total,
                    "works_rows_with_requested_worker_profile_id": works_with_req,
                    "projects_referencing_prefix_mirror": projects_prefix_refs,
                },
                "conflicts": {
                    "same_id_without_mirror_marker": conflicts_same_id,
                    "note": "同 profile_id 但 metadata 无 worker_profile_mirror 标记——仍可安全 UPDATE 合并（非阻断），仅提示",
                },
                "irreversible_points": [
                    "DROP TABLE worker_profiles",
                    "DROP TABLE worker_profile_revisions",
                    "ALTER TABLE agent_runtimes DROP COLUMN worker_profile_id（表 rebuild / DROP COLUMN）",
                    "rollback 仅删 audit 行释放幂等键，不还原已 DROP 的表/列——apply 前必须备份",
                ],
                "prefix_mirror_detail": [
                    {"agent_profile_id": apid, "canonical_worker_id": wid}
                    for apid, wid in prefix_mirrors
                ],
            }
        )
        return result


# ───────────────────────── apply (真实迁移，单事务) ─────────────────────────


async def run_apply(
    db_path: str, *, requested_by: str = "octo migrate-117"
) -> dict[str, object]:
    """单事务执行 WorkerProfile→AgentProfile 合并 + DROP 旧表 + 写 audit 行。

    ⚠ 不可逆。调用方应在 apply 前完成 DB 备份（CLI 层 --yes 确认 + 备份提示）。
    幂等：worker_profiles 表已不存在 → skipped。
    """
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=OFF")

        if not await _table_exists(conn, "worker_profiles"):
            await _ensure_audit_table(conn)
            existing = await _existing_run_id(conn)
            await conn.commit()
            return {
                "command": "migrate-117",
                "mode": "apply",
                "status": "skipped",
                "reason": "worker_profiles already dropped (already merged)",
                "run_id": existing or "",
                "idempotency_key": _IDEMPOTENCY_KEY,
            }

        await _ensure_audit_table(conn)
        existing = await _existing_run_id(conn)
        if existing is not None:
            return {
                "command": "migrate-117",
                "mode": "apply",
                "status": "skipped",
                "reason": "idempotency_short_circuit",
                "run_id": existing,
                "idempotency_key": _IDEMPOTENCY_KEY,
            }

        agent_cols = await _table_columns(conn, "agent_profiles")
        prefix_mirrors = await _detect_prefix_mirrors(conn)
        worker_count = await _scalar(conn, "SELECT COUNT(*) FROM worker_profiles")
        rev_count = await _scalar(conn, "SELECT COUNT(*) FROM worker_profile_revisions")

        await conn.execute("BEGIN")
        try:
            # 1. 确保 agent_profiles 有 kind + resource_limits + 9 worker 列
            if "kind" not in agent_cols:
                await conn.execute(
                    "ALTER TABLE agent_profiles ADD COLUMN kind TEXT NOT NULL DEFAULT 'main'"
                )
            if "resource_limits" not in agent_cols:
                await conn.execute(
                    "ALTER TABLE agent_profiles ADD COLUMN resource_limits TEXT NOT NULL DEFAULT '{}'"
                )
            for col, decl in _WORKER_COLUMNS:
                if col not in agent_cols:
                    await conn.execute(
                        f"ALTER TABLE agent_profiles ADD COLUMN {col} {decl}"
                    )

            # 2. 前缀镜像 reconcile：把 agent-profile-{wid} 的运行时字段并入 {wid}，
            #    重写引用，删前缀行（canonical = worker id）
            for apid, wid in prefix_mirrors:
                await conn.execute(
                    "UPDATE projects SET default_agent_profile_id=? "
                    "WHERE default_agent_profile_id=?",
                    (wid, apid),
                )
                await conn.execute(
                    "UPDATE agent_runtimes SET agent_profile_id=? WHERE agent_profile_id=?",
                    (wid, apid),
                )
                # 若 {wid} 行不存在（worker 行有但 agent 行只有前缀），把前缀行改名为 {wid}
                exists_wid = await _scalar(
                    conn, "SELECT COUNT(*) FROM agent_profiles WHERE profile_id=?", (wid,)
                )
                if exists_wid == 0:
                    await conn.execute(
                        "UPDATE agent_profiles SET profile_id=?, kind='worker' WHERE profile_id=?",
                        (wid, apid),
                    )
                else:
                    await conn.execute(
                        "DELETE FROM agent_profiles WHERE profile_id=?", (apid,)
                    )

            # 3. merge worker_profiles → agent_profiles
            cursor = await conn.execute("SELECT * FROM worker_profiles")
            worker_rows = await cursor.fetchall()
            for w in worker_rows:
                wd = dict(w)
                pid = wd["profile_id"]
                version = max(int(wd.get("active_revision") or 0), int(wd.get("draft_revision") or 0), 1)
                exists = await _scalar(
                    conn, "SELECT COUNT(*) FROM agent_profiles WHERE profile_id=?", (pid,)
                )
                if exists:
                    # UPDATE 既有镜像行：填工具字段 + status/origin/revisions + kind=worker。
                    # F117 Wave 2bc（Codex HIGH-4）：一并刷新 name/scope/project_id/model_alias/
                    # tool_profile/persona_summary——存量镜像可能 stale（旧代码改 worker 名/模型不同步
                    # 镜像），而 resolve_worker_binding 现从镜像读这些字段。metadata：合并既有镜像
                    # metadata（保 agent 侧 memory_recall 等）+ overlay worker metadata（capability_
                    # provider_selection 等）+ 确保 source_kind 标记，闭合 dropped-fallback 缺口。
                    cur = await conn.execute(
                        "SELECT metadata FROM agent_profiles WHERE profile_id=?", (pid,)
                    )
                    row_meta = await cur.fetchone()
                    try:
                        existing_meta = json.loads(row_meta[0]) if row_meta and row_meta[0] else {}
                    except (ValueError, TypeError):
                        existing_meta = {}
                    try:
                        worker_meta = json.loads(wd.get("metadata") or "{}")
                    except (ValueError, TypeError):
                        worker_meta = {}
                    merged_meta = {**existing_meta, **worker_meta}
                    merged_meta.setdefault("source_kind", "worker_profile_mirror")
                    merged_meta.setdefault("source_worker_profile_id", pid)
                    await conn.execute(
                        "UPDATE agent_profiles SET "
                        " kind='worker', name=?, scope=?, project_id=?, persona_summary=?,"
                        " model_alias=?, tool_profile=?, metadata=?,"
                        " summary=?, default_tool_groups=?, selected_tools=?,"
                        " runtime_kinds=?, status=?, origin_kind=?, draft_revision=?,"
                        " active_revision=?, archived_at=?, resource_limits=?,"
                        " version=MAX(version, ?) "
                        "WHERE profile_id=?",
                        (
                            wd.get("name", pid),
                            wd.get("scope", "project"),
                            wd.get("project_id", ""),
                            wd.get("summary", ""),
                            wd.get("model_alias", "main"),
                            wd.get("tool_profile", "minimal"),
                            json.dumps(merged_meta, ensure_ascii=False),
                            wd.get("summary", ""),
                            wd.get("default_tool_groups", "[]"),
                            wd.get("selected_tools", "[]"),
                            wd.get("runtime_kinds", "[]"),
                            wd.get("status", "active"),
                            wd.get("origin_kind", "custom"),
                            int(wd.get("draft_revision") or 0),
                            int(wd.get("active_revision") or 0),
                            wd.get("archived_at"),
                            wd.get("resource_limits", "{}"),
                            version,
                            pid,
                        ),
                    )
                else:
                    # INSERT 新 kind=worker 行（agent-only 字段取默认）
                    await conn.execute(
                        "INSERT INTO agent_profiles ("
                        " profile_id, scope, project_id, name, kind, persona_summary,"
                        " instruction_overlays, model_alias, tool_profile, policy_refs,"
                        " memory_access_policy, context_budget_policy, bootstrap_template_ids,"
                        " metadata, version, resource_limits, summary, default_tool_groups,"
                        " selected_tools, runtime_kinds, status, origin_kind, draft_revision,"
                        " active_revision, archived_at, created_at, updated_at"
                        ") VALUES (?,?,?,?, 'worker', '', '[]', ?, ?, '[]', '{}', '{}', '[]',"
                        " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            pid,
                            wd.get("scope", "project"),
                            wd.get("project_id", ""),
                            wd.get("name", pid),
                            wd.get("model_alias", "main"),
                            wd.get("tool_profile", "minimal"),
                            wd.get("metadata", "{}"),
                            version,
                            wd.get("resource_limits", "{}"),
                            wd.get("summary", ""),
                            wd.get("default_tool_groups", "[]"),
                            wd.get("selected_tools", "[]"),
                            wd.get("runtime_kinds", "[]"),
                            wd.get("status", "active"),
                            wd.get("origin_kind", "custom"),
                            int(wd.get("draft_revision") or 0),
                            int(wd.get("active_revision") or 0),
                            wd.get("archived_at"),
                            wd.get("created_at"),
                            wd.get("updated_at"),
                        ),
                    )

            # 4. agent_profile_revisions 建表 + 复制（re-key FK→agent_profiles）
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS agent_profile_revisions ("
                " revision_id TEXT PRIMARY KEY,"
                " profile_id TEXT NOT NULL,"
                " revision INTEGER NOT NULL,"
                " change_summary TEXT NOT NULL DEFAULT '',"
                " snapshot_payload TEXT NOT NULL DEFAULT '{}',"
                " created_by TEXT NOT NULL DEFAULT '',"
                " created_at TEXT NOT NULL,"
                " FOREIGN KEY (profile_id) REFERENCES agent_profiles(profile_id),"
                " UNIQUE(profile_id, revision)"
                ")"
            )
            await conn.execute(
                "INSERT INTO agent_profile_revisions "
                "(revision_id, profile_id, revision, change_summary, snapshot_payload, created_by, created_at) "
                "SELECT revision_id, profile_id, revision, change_summary, snapshot_payload, created_by, created_at "
                "FROM worker_profile_revisions"
            )

            # 5. agent_runtimes：backfill orphan + reconcile mismatch + 塌缩 worker_profile_id
            if "worker_profile_id" in await _table_columns(conn, "agent_runtimes"):
                # orphan: 空 agent_profile_id 用 worker_profile_id 兜底
                await conn.execute(
                    "UPDATE agent_runtimes SET agent_profile_id=worker_profile_id "
                    "WHERE role='worker' AND agent_profile_id='' AND worker_profile_id!=''"
                )
                # mismatch: canonical = worker id（同 §3 规则）
                await conn.execute(
                    "UPDATE agent_runtimes SET agent_profile_id=worker_profile_id "
                    "WHERE role='worker' AND worker_profile_id!='' "
                    "AND agent_profile_id!=worker_profile_id"
                )
                await conn.execute(
                    "DROP INDEX IF EXISTS idx_agent_runtimes_active_worker_unique"
                )
                await conn.execute("ALTER TABLE agent_runtimes DROP COLUMN worker_profile_id")
                await conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runtimes_active_worker_unique "
                    "ON agent_runtimes(project_id, agent_profile_id) "
                    "WHERE status='active' AND role='worker' "
                    "AND agent_profile_id!='' AND agent_runtime_id NOT LIKE 'subagent-%'"
                )

            # 6. works：rename 列
            works_cols = await _table_columns(conn, "works")
            if "requested_worker_profile_id" in works_cols:
                await conn.execute("DROP INDEX IF EXISTS idx_works_requested_worker_profile")
                await conn.execute(
                    "ALTER TABLE works RENAME COLUMN requested_worker_profile_id "
                    "TO requested_agent_profile_id"
                )
                if "requested_worker_profile_version" in works_cols:
                    await conn.execute(
                        "ALTER TABLE works RENAME COLUMN requested_worker_profile_version "
                        "TO requested_agent_profile_version"
                    )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_works_requested_agent_profile "
                    "ON works(requested_agent_profile_id, requested_agent_profile_version)"
                )

            # 7. DROP 旧表 + 旧索引
            await conn.execute("DROP INDEX IF EXISTS idx_worker_profiles_scope_project")
            await conn.execute("DROP INDEX IF EXISTS idx_worker_profile_revisions_profile_created")
            await conn.execute("DROP TABLE IF EXISTS worker_profile_revisions")
            await conn.execute("DROP TABLE IF EXISTS worker_profiles")

            # 8. audit 行
            run_id = str(uuid4())
            now_iso = datetime.now(tz=UTC).isoformat()
            metadata_blob = json.dumps(
                {
                    "migration": "117_profile_merge",
                    "worker_profiles_merged": worker_count,
                    "revisions_rekeyed": rev_count,
                    "prefix_mirrors_reconciled": len(prefix_mirrors),
                },
                ensure_ascii=False,
            )
            await conn.execute(
                f"INSERT INTO {_AUDIT_TABLE} "
                "(run_id, idempotency_key, status, requested_by, metadata, started_at, finished_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (run_id, _IDEMPOTENCY_KEY, "completed", requested_by, metadata_blob, now_iso, now_iso),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

        return {
            "command": "migrate-117",
            "mode": "apply",
            "status": "succeeded",
            "run_id": run_id,
            "worker_profiles_merged": worker_count,
            "revisions_rekeyed": rev_count,
            "idempotency_key": _IDEMPOTENCY_KEY,
        }


async def run_rollback(db_path: str, run_id: str) -> dict[str, object]:
    """删指定 run_id 的 audit 行（释放幂等键）。

    ⚠ 不还原已 DROP 的 worker_profiles / worker_profile_revisions / worker_profile_id 列。
    真正回滚需从 apply 前的 DB 备份恢复。
    """
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        if not await _table_exists(conn, _AUDIT_TABLE):
            return {
                "command": "migrate-117",
                "mode": "rollback",
                "status": "not_found",
                "run_id": run_id,
                "reason": "no audit table",
            }
        cursor = await conn.execute(
            f"SELECT run_id FROM {_AUDIT_TABLE} WHERE run_id=? AND idempotency_key=?",
            (run_id, _IDEMPOTENCY_KEY),
        )
        if (await cursor.fetchone()) is None:
            return {
                "command": "migrate-117",
                "mode": "rollback",
                "status": "not_found",
                "run_id": run_id,
            }
        await conn.execute(
            f"DELETE FROM {_AUDIT_TABLE} WHERE run_id=? AND idempotency_key=?",
            (run_id, _IDEMPOTENCY_KEY),
        )
        await conn.commit()
        return {
            "command": "migrate-117",
            "mode": "rollback",
            "status": "succeeded",
            "run_id": run_id,
            "warning": "audit 行已删除释放幂等键；已 DROP 的表/列不会还原，需从备份恢复",
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migration 117: WorkerProfile→AgentProfile 完全合并（不可逆）",
    )
    parser.add_argument("db_path", help="SQLite 数据库文件路径")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="仅扫描，不写库")
    group.add_argument("--apply", action="store_true", help="单事务真实迁移 + DROP 旧表（不可逆）")
    group.add_argument("--rollback", metavar="RUN_ID", help="删 audit 行释放幂等键")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"[错误] 数据库文件不存在: {db_path}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        result = asyncio.run(run_dry_run(str(db_path)))
    elif args.apply:
        result = asyncio.run(run_apply(str(db_path)))
    else:
        result = asyncio.run(run_rollback(str(db_path), args.rollback))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
