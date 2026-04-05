"""Memory Console 导入/导出子服务。

包含 inspect_export、verify_restore 等导入/导出相关操作。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from octoagent.memory import (
    SENSITIVE_PARTITIONS,
    VaultAccessGrantStatus,
)
from ulid import ULID

from ._memory_console_base import (
    MemoryConsoleBase,
    MemoryPermissionDecision,
)


class MemoryExportService:
    """Memory 导入/导出——inspect export、verify restore。"""

    def __init__(self, base: MemoryConsoleBase) -> None:
        self._base = base

    async def inspect_export(
        self,
        *,
        active_project_id: str = "",
        project_id: str = "",
        scope_ids: list[str] | None = None,
        include_history: bool = False,
        include_vault_refs: bool = False,
    ) -> tuple[str, dict[str, Any], MemoryPermissionDecision]:
        context = await self._base.resolve_context(
            active_project_id=active_project_id,
            project_id=project_id,
        )
        decision = self._base.decide_project_scope_action(
            action_id="memory.export.inspect",
            actor_id="system:memory-export",
            context=context,
            required_scope_id=(scope_ids or [None])[0],
            bypass_actor_check=True,
        )
        if not decision.allowed:
            return "MEMORY_EXPORT_INSPECTION_NOT_ALLOWED", {}, decision
        selected_scope_ids = scope_ids or context.selected_scope_ids
        scope_decision = self._base.decide_scope_list_bound(
            action_id="memory.export.inspect",
            context=context,
            scope_ids=selected_scope_ids,
        )
        if scope_decision is not None:
            return "MEMORY_EXPORT_INSPECTION_NOT_ALLOWED", {}, scope_decision
        counts = {
            "fragments": 0,
            "sor_current": 0,
            "sor_history": 0,
            "vault_refs": 0,
            "proposals": 0,
        }
        sensitive_partitions: set[str] = set()
        for scope in selected_scope_ids:
            fragments = await self._base._memory_store.list_fragments(scope, limit=200)
            counts["fragments"] += len(fragments)
            sor_records = await self._base._memory_store.search_sor(
                scope,
                include_history=include_history,
                limit=200,
            )
            for sor in sor_records:
                if sor.status == "current":
                    counts["sor_current"] += 1
                else:
                    counts["sor_history"] += 1
                if sor.partition in SENSITIVE_PARTITIONS:
                    sensitive_partitions.add(sor.partition.value)
            if include_vault_refs:
                vault_records = await self._base._memory_store.search_vault(scope, limit=200)
                counts["vault_refs"] += len(vault_records)
                for vault in vault_records:
                    if vault.partition in SENSITIVE_PARTITIONS:
                        sensitive_partitions.add(vault.partition.value)
        counts["proposals"] = len(
            await self._base._memory.list_proposals(scope_ids=selected_scope_ids, limit=200)
        )
        payload = {
            "inspection_id": str(ULID()),
            "counts": counts,
            "sensitive_partitions": sorted(sensitive_partitions),
            "warnings": context.warnings,
            "blocking_issues": context.blocking_issues,
            "export_refs": [
                {
                    "project_id": context.project.project_id,
                    "scope_id": scope_id,
                }
                for scope_id in selected_scope_ids
            ],
        }
        code = (
            "MEMORY_EXPORT_INSPECTION_BLOCKED"
            if payload["blocking_issues"]
            else "MEMORY_EXPORT_INSPECTION_READY"
        )
        return code, payload, decision

    async def verify_restore(
        self,
        *,
        actor_id: str,
        active_project_id: str = "",
        project_id: str = "",
        snapshot_ref: str,
        target_scope_mode: str = "current_project",
        scope_ids: list[str] | None = None,
    ) -> tuple[str, dict[str, Any], MemoryPermissionDecision]:
        context = await self._base.resolve_context(
            active_project_id=active_project_id,
            project_id=project_id,
        )
        permission = self._base.decide_operator_only(
            action_id="memory.restore.verify",
            actor_id=actor_id,
            context=context,
        )
        if not permission.allowed:
            return "MEMORY_RESTORE_VERIFICATION_NOT_ALLOWED", {}, permission

        snapshot_path = Path(snapshot_ref).expanduser()
        if not snapshot_path.is_absolute():
            snapshot_path = (self._base._project_root / snapshot_path).resolve()
        else:
            snapshot_path = snapshot_path.resolve()

        warnings: list[str] = list(context.warnings)
        blocking_issues: list[str] = list(context.blocking_issues)
        schema_ok = False
        snapshot_payload: dict[str, Any] = {}
        if not snapshot_path.exists():
            blocking_issues.append(f"snapshot 不存在: {snapshot_path}")
        elif snapshot_path.suffix.lower() == ".json":
            snapshot_payload, schema_ok, parse_warning = self._load_memory_snapshot_json(
                snapshot_path
            )
            if parse_warning:
                warnings.append(parse_warning)
        elif snapshot_path.suffix.lower() == ".zip":
            warnings.append("bundle 校验仅做 manifest/entries 检查，未发现专用 memory snapshot。")
            schema_ok = self._bundle_contains_memory_refs(snapshot_path)
            if not schema_ok:
                blocking_issues.append("bundle 未包含可识别的 memory snapshot/manifest。")
        else:
            blocking_issues.append("仅支持 .json 或 .zip 的 memory snapshot/bundle 校验。")

        snapshot_scope_ids = self._snapshot_scope_ids(snapshot_payload)
        target_scopes = scope_ids or snapshot_scope_ids or context.selected_scope_ids
        scope_conflicts: list[str] = []
        if target_scope_mode == "current_project":
            bound_scope_ids = set(context.scope_bindings.keys())
            for scope in target_scopes:
                if scope not in bound_scope_ids:
                    scope_conflicts.append(f"scope 未绑定到当前 project: {scope}")

        subject_conflicts: list[str] = []
        grant_conflicts: list[str] = []
        for item in snapshot_payload.get("records", []):
            if item.get("layer") != "sor" or item.get("status") != "current":
                continue
            item_scope_id = str(item.get("scope_id", ""))
            item_subject = str(item.get("subject_key", ""))
            if not item_scope_id or not item_subject:
                continue
            current = await self._base._memory_store.get_current_sor(item_scope_id, item_subject)
            if current is not None:
                subject_conflicts.append(
                    f"{item_scope_id}:{item_subject} 已存在 current version={current.version}"
                )
        for item in snapshot_payload.get("grants", []):
            item_scope_id = str(item.get("scope_id", ""))
            item_subject = str(item.get("subject_key", ""))
            item_actor_id = str(item.get("granted_to_actor_id", ""))
            if not item_scope_id or not item_actor_id:
                continue
            existing = await self._base._memory.list_vault_access_grants(
                project_id=context.project.project_id,
                scope_ids=[item_scope_id],
                subject_key=item_subject or None,
                actor_id=item_actor_id,
                statuses=[VaultAccessGrantStatus.ACTIVE],
                limit=10,
            )
            if existing:
                grant_conflicts.append(
                    f"{item_actor_id}:{item_scope_id}:{item_subject or '*'} 已存在 active grant"
                )

        payload = {
            "verification_id": str(ULID()),
            "schema_ok": schema_ok,
            "subject_conflicts": subject_conflicts,
            "grant_conflicts": grant_conflicts,
            "scope_conflicts": scope_conflicts,
            "warnings": warnings,
            "blocking_issues": blocking_issues,
        }
        code = (
            "MEMORY_RESTORE_VERIFICATION_BLOCKED"
            if (
                not schema_ok
                or subject_conflicts
                or grant_conflicts
                or scope_conflicts
                or blocking_issues
            )
            else "MEMORY_RESTORE_VERIFICATION_READY"
        )
        return code, payload, permission

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _load_memory_snapshot_json(self, snapshot_path: Path) -> tuple[dict[str, Any], bool, str]:
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {}, False, f"snapshot 解析失败: {exc}"
        if not isinstance(payload, dict):
            return {}, False, "snapshot 顶层必须是 object。"
        schema_ok = any(key in payload for key in ("records", "manifest", "grants"))
        if "records" not in payload:
            payload["records"] = []
        if "grants" not in payload:
            payload["grants"] = []
        return payload, schema_ok, ""

    def _snapshot_scope_ids(self, snapshot_payload: dict[str, Any]) -> list[str]:
        scope_ids: set[str] = set()
        raw_scope_ids = snapshot_payload.get("scope_ids")
        if isinstance(raw_scope_ids, list):
            scope_ids.update(str(item).strip() for item in raw_scope_ids if str(item).strip())
        manifest = snapshot_payload.get("manifest")
        if isinstance(manifest, dict):
            manifest_scopes = manifest.get("scopes")
            if isinstance(manifest_scopes, list):
                scope_ids.update(
                    str(item).strip() for item in manifest_scopes if str(item).strip()
                )
        for collection_key in ("records", "grants"):
            collection = snapshot_payload.get(collection_key)
            if not isinstance(collection, list):
                continue
            for item in collection:
                if not isinstance(item, dict):
                    continue
                scope_id = str(item.get("scope_id", "")).strip()
                if scope_id:
                    scope_ids.add(scope_id)
        return sorted(scope_ids)

    def _bundle_contains_memory_refs(self, bundle_path: Path) -> bool:
        try:
            with zipfile.ZipFile(bundle_path) as archive:
                names = set(archive.namelist())
        except Exception:
            return False
        return any("memory" in name for name in names)
