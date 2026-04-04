"""029 WeChat Import + Multi-source Import Workbench 服务。"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
from octoagent.core.models import (
    ControlPlaneCapability,
    ControlPlaneDegradedState,
    Project,
    ProjectBinding,
    ProjectBindingType,
)
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.memory import (
    ChatImportProcessor,
    ImportConversationMapping,
    ImportedChatMessage,
    ImportInputRef,
    ImportMappingProfile,
    ImportReport,
    ImportSourceFormat,
    ImportSourceType,
    SqliteChatImportStore,
    derive_import_source_id,
    verify_chat_import_tables,
)
from octoagent.memory.imports.source_adapters import (
    ImportSourceAdapter,
    NormalizedJsonlImportAdapter,
    WeChatImportAdapter,
)
from pydantic import BaseModel, Field
from ulid import ULID

from .backup_service import resolve_artifacts_dir, resolve_db_path, resolve_project_root
from .chat_import_service import ChatImportService
from .import_mapping_store import ImportMappingStore
from .import_source_store import ImportSourceStore
from .import_workbench_models import (
    ImportMemoryEffectSummary,
    ImportResumeEntry,
    ImportRunDocument,
    ImportRunStatus,
    ImportSourceDocument,
    ImportWorkbenchDocument,
    ImportWorkbenchSummary,
)
from .project_selector import ProjectSelectorService

_IMPORT_MAPPING_RUN_ID = "feature-029-import-workbench"
_SCOPE_PATTERN = re.compile(r"^chat:(?P<channel>[a-z0-9_-]+):(?P<thread>.+)$", re.IGNORECASE)


class ImportWorkbenchError(RuntimeError):
    """结构化 import workbench 错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _GroupPreview(BaseModel):
    scope_id: str
    conversation_key: str
    report: ImportReport
    dedupe_details: list[dict[str, Any]] = Field(default_factory=list)


class ImportWorkbenchService:
    """029 的 detect / mapping / preview / run / resume / projection 聚合服务。"""

    def __init__(
        self,
        project_root: Path,
        *,
        surface: str = "web",
        store_group: StoreGroup | None = None,
        adapters: dict[str, ImportSourceAdapter] | None = None,
    ) -> None:
        self._root = resolve_project_root(project_root).resolve()
        self._db_path = resolve_db_path(self._root)
        self._artifacts_dir = resolve_artifacts_dir(self._root)
        self._surface = surface
        self._store_group = store_group
        self._source_store = ImportSourceStore(self._root)
        self._mapping_store = ImportMappingStore(self._root)
        self._processor = ChatImportProcessor()
        self._adapters = adapters or self._build_default_registry()

    async def get_workbench(
        self,
        *,
        project_id: str | None = None,
    ) -> ImportWorkbenchDocument:
        project = await self._resolve_selection(project_id)
        sources = self._source_store.list_sources(
            project_id=project.project_id,
            workspace_id=None,
        )
        recent_runs = self._source_store.list_runs(
            project_id=project.project_id,
            workspace_id=None,
            limit=12,
        )
        resume_entries = self._build_resume_entries(project, recent_runs)
        warning_count = sum(len(item.warnings) for item in sources) + sum(
            len(item.warnings) for item in recent_runs
        )
        error_count = sum(len(item.errors) for item in sources) + sum(
            len(item.errors) for item in recent_runs
        )
        return ImportWorkbenchDocument(
            active_project_id=project.project_id,
            active_workspace_id="",
            summary=ImportWorkbenchSummary(
                source_count=len(sources),
                recent_run_count=len(recent_runs),
                resume_available_count=len(resume_entries),
                warning_count=warning_count,
                error_count=error_count,
            ),
            sources=sources,
            recent_runs=recent_runs,
            resume_entries=resume_entries,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="import.source.detect",
                    label="识别导入源",
                    action_id="import.source.detect",
                ),
                ControlPlaneCapability(
                    capability_id="import.preview",
                    label="生成导入预览",
                    action_id="import.preview",
                ),
                ControlPlaneCapability(
                    capability_id="import.run",
                    label="执行导入",
                    action_id="import.run",
                ),
                ControlPlaneCapability(
                    capability_id="import.resume",
                    label="恢复导入",
                    action_id="import.resume",
                ),
            ],
        )

    async def get_source(self, source_id: str) -> ImportSourceDocument:
        document = self._source_store.get_source(source_id)
        if document is None:
            raise ImportWorkbenchError("IMPORT_SOURCE_NOT_FOUND", f"未找到 source: {source_id}")
        return document

    async def get_run(self, run_id: str) -> ImportRunDocument:
        document = self._source_store.get_run(run_id)
        if document is None:
            raise ImportWorkbenchError("IMPORT_REPORT_NOT_FOUND", f"未找到导入运行: {run_id}")
        return document

    async def detect_source(
        self,
        *,
        source_type: str,
        input_path: str,
        media_root: str | None = None,
        format_hint: str | None = None,
        project_id: str | None = None,
    ) -> ImportSourceDocument:
        project = await self._resolve_selection(project_id)
        adapter = self._require_adapter(source_type)
        input_ref = ImportInputRef(
            source_type=ImportSourceType(source_type),
            input_path=self._resolve_input_path(input_path),
            media_root=self._resolve_optional_path(media_root),
            format_hint=(format_hint or None),
        )
        detected = await adapter.detect(input_ref)
        source_id = self._build_source_id(detected.source_type, input_ref.input_path)
        latest_mapping = self._mapping_store.get_latest(
            project_id=project.project_id,
            workspace_id="",
            source_id=source_id,
        )
        latest_runs = self._source_store.list_runs(
            project_id=project.project_id,
            workspace_id=None,
            source_id=source_id,
            limit=1,
        )
        document = ImportSourceDocument(
            resource_id=f"import-source:{source_id}",
            active_project_id=project.project_id,
            active_workspace_id="",
            source_id=source_id,
            source_type=detected.source_type,
            input_ref=detected.input_ref,
            status="invalid" if detected.errors else "detected",
            detected_conversations=detected.detected_conversations,
            detected_participants=detected.detected_participants,
            attachment_roots=detected.attachment_roots,
            warnings=detected.warnings,
            errors=detected.errors,
            latest_mapping_id=latest_mapping.mapping_id if latest_mapping else None,
            latest_run_id=latest_runs[0].resource_id if latest_runs else None,
            metadata=detected.metadata,
            degraded=ControlPlaneDegradedState(
                is_degraded=bool(detected.errors),
                reasons=["source_invalid"] if detected.errors else [],
            ),
            capabilities=[
                ControlPlaneCapability(
                    capability_id="import.mapping.save",
                    label="保存 Mapping",
                    action_id="import.mapping.save",
                ),
                ControlPlaneCapability(
                    capability_id="import.preview",
                    label="生成 Preview",
                    action_id="import.preview",
                    enabled=not bool(detected.errors),
                ),
            ],
        )
        self._source_store.save_source(document)
        return document

    async def save_mapping(
        self,
        *,
        source_id: str,
        conversation_mappings: list[dict[str, Any]] | None = None,
        sender_mappings: list[dict[str, Any]] | None = None,
        attachment_policy: str = "artifact-first",
        memu_policy: str = "best-effort",
        project_id: str | None = None,
    ) -> ImportMappingProfile:
        source = await self.get_source(source_id)
        project = await self._resolve_selection(
            project_id or source.active_project_id,
        )
        profile = self._build_mapping_profile(
            source=source,
            project=project,
            conversation_mappings=conversation_mappings,
            sender_mappings=sender_mappings,
            attachment_policy=attachment_policy,
            memu_policy=memu_policy,
        )
        async with self._store_group_scope() as store_group:
            for mapping in profile.conversation_mappings:
                await self._ensure_scope_binding(
                    store_group=store_group,
                    project=project,
                    scope_id=mapping.scope_id,
                )
            await store_group.conn.commit()
        self._mapping_store.save(profile)
        updated_source = source.model_copy(
            update={
                "latest_mapping_id": profile.mapping_id,
                "updated_at": datetime.now(tz=UTC),
            }
        )
        self._source_store.save_source(updated_source)
        return profile

    async def preview(
        self,
        *,
        source_id: str,
        mapping_id: str | None = None,
    ) -> ImportRunDocument:
        source = await self.get_source(source_id)
        mapping = self._resolve_mapping(source, mapping_id)
        run_id = f"import-run:{str(ULID())}"
        if mapping is None:
            document = ImportRunDocument(
                resource_id=run_id,
                active_project_id=source.active_project_id,
                active_workspace_id=source.active_workspace_id,
                source_id=source.source_id,
                source_type=source.source_type,
                status=ImportRunStatus.ACTION_REQUIRED,
                dry_run=True,
                mapping_id=None,
                summary={"conversation_count": len(source.detected_conversations)},
                warnings=source.warnings,
                errors=["缺少有效 mapping，请先执行 import.mapping.save。"],
                degraded=ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["mapping_required"],
                ),
                capabilities=[
                    ControlPlaneCapability(
                        capability_id="import.mapping.save",
                        label="保存 Mapping",
                        action_id="import.mapping.save",
                    )
                ],
            )
            self._source_store.save_run(document)
            return document

        grouped_messages = await self._materialize_grouped_messages(source=source, mapping=mapping)
        previews: list[_GroupPreview] = []
        chat_import = ChatImportService(self._root, store_group=self._store_group)
        for scope_id, payload in grouped_messages.items():
            report = await chat_import.import_messages(
                input_label=source.input_ref.input_path,
                source_id=source.source_id,
                source_format=self._map_source_format(source.source_type),
                messages=payload["messages"],
                dry_run=True,
            )
            previews.append(
                _GroupPreview(
                    scope_id=scope_id,
                    conversation_key=payload["conversation_key"],
                    report=report,
                    dedupe_details=await self._collect_dedupe_details(
                        source_id=source.source_id,
                        scope_id=scope_id,
                        messages=payload["messages"],
                        resume=False,
                    ),
                )
            )
        document = self._build_run_document(
            run_id=run_id,
            source=source,
            mapping=mapping,
            previews=previews,
            dry_run=True,
            status=ImportRunStatus.READY_TO_RUN if previews else ImportRunStatus.ACTION_REQUIRED,
            errors=[] if previews else ["当前没有可导入的 mapped messages。"],
            metadata={"mode": "preview"},
        )
        self._source_store.save_run(document)
        self._source_store.save_source(
            source.model_copy(update={"latest_run_id": run_id, "updated_at": datetime.now(tz=UTC)})
        )
        return document

    async def run(
        self,
        *,
        source_id: str,
        mapping_id: str | None = None,
        resume: bool = False,
    ) -> ImportRunDocument:
        source = await self.get_source(source_id)
        mapping = self._resolve_mapping(source, mapping_id)
        if mapping is None:
            raise ImportWorkbenchError("IMPORT_MAPPING_REQUIRED", "缺少有效 mapping。")

        run_id = f"import-run:{str(ULID())}"
        running = ImportRunDocument(
            resource_id=run_id,
            active_project_id=source.active_project_id,
            active_workspace_id=source.active_workspace_id,
            source_id=source.source_id,
            source_type=source.source_type,
            status=ImportRunStatus.RUNNING,
            dry_run=False,
            mapping_id=mapping.mapping_id,
            summary={"resume": resume},
            warnings=[],
            errors=[],
            degraded=ControlPlaneDegradedState(),
            capabilities=[],
        )
        self._source_store.save_run(running)
        try:
            grouped_messages = await self._materialize_grouped_messages(
                source=source,
                mapping=mapping,
            )
            chat_import = ChatImportService(self._root, store_group=self._store_group)
            previews: list[_GroupPreview] = []
            errors: list[str] = []
            for scope_id, payload in grouped_messages.items():
                report = await chat_import.import_messages(
                    input_label=source.input_ref.input_path,
                    source_id=source.source_id,
                    source_format=self._map_source_format(source.source_type),
                    messages=payload["messages"],
                    dry_run=False,
                    resume=resume,
                    raise_on_failure=False,
                )
                previews.append(
                    _GroupPreview(
                        scope_id=scope_id,
                        conversation_key=payload["conversation_key"],
                        report=report,
                        dedupe_details=await self._collect_dedupe_details(
                            source_id=source.source_id,
                            scope_id=scope_id,
                            messages=payload["messages"],
                            resume=resume,
                        ),
                    )
                )
                if report.errors:
                    errors.extend(report.errors)
        except Exception as exc:
            failed = self._build_failed_run_document(
                running=running,
                error_message=str(exc),
                metadata={"resume": resume},
            )
            self._source_store.save_run(failed)
            self._source_store.save_source(
                source.model_copy(
                    update={
                        "latest_run_id": run_id,
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
            )
            raise ImportWorkbenchError(
                "IMPORT_RUN_FAILED",
                f"导入执行失败: {exc}",
            ) from exc

        status = self._derive_run_status(previews, errors)
        document = self._build_run_document(
            run_id=run_id,
            source=source,
            mapping=mapping,
            previews=previews,
            dry_run=False,
            status=status,
            errors=errors,
            metadata={"resume": resume},
        )
        self._source_store.save_run(document)
        self._source_store.save_source(
            source.model_copy(update={"latest_run_id": run_id, "updated_at": datetime.now(tz=UTC)})
        )
        return document

    async def resume(self, *, resume_id: str) -> ImportRunDocument:
        source_id = resume_id.removeprefix("resume:")
        return await self.run(source_id=source_id, resume=True)

    def inspect_report(self, run_id: str) -> ImportRunDocument:
        run = self._source_store.get_run(run_id)
        if run is None:
            raise ImportWorkbenchError("IMPORT_REPORT_NOT_FOUND", f"未找到导入报告: {run_id}")
        return run

    def _build_default_registry(self) -> dict[str, ImportSourceAdapter]:
        return {
            ImportSourceType.NORMALIZED_JSONL.value: NormalizedJsonlImportAdapter(),
            ImportSourceType.WECHAT.value: WeChatImportAdapter(),
        }

    def _require_adapter(self, source_type: str) -> ImportSourceAdapter:
        adapter = self._adapters.get(source_type)
        if adapter is None:
            raise ImportWorkbenchError(
                "IMPORT_SOURCE_UNSUPPORTED",
                f"不支持的 source type: {source_type}",
            )
        return adapter

    async def _resolve_selection(
        self,
        project_id: str | None,
    ) -> Project:
        selector = ProjectSelectorService(
            self._root,
            surface=self._surface,
            store_group=self._store_group,
        )
        if project_id:
            project, _ = await selector.resolve_project(project_id)
        else:
            project, _ = await selector.get_active_project()
        return project

    def _resolve_mapping(
        self,
        source: ImportSourceDocument,
        mapping_id: str | None,
    ) -> ImportMappingProfile | None:
        if mapping_id:
            mapping = self._mapping_store.get(mapping_id)
        elif source.latest_mapping_id:
            mapping = self._mapping_store.get(source.latest_mapping_id)
        else:
            mapping = self._mapping_store.get_latest(
                project_id=source.active_project_id,
                workspace_id=source.active_workspace_id,
                source_id=source.source_id,
            )
        if mapping is None:
            return None
        if (
            mapping.source_id != source.source_id
            or mapping.project_id != source.active_project_id
            or mapping.workspace_id != source.active_workspace_id
        ):
            raise ImportWorkbenchError(
                "IMPORT_MAPPING_MISMATCH",
                (
                    "mapping 不属于当前 source/project/workspace: "
                    f"{mapping.mapping_id}"
                ),
            )
        return mapping

    def _build_mapping_profile(
        self,
        *,
        source: ImportSourceDocument,
        project: Project,
        conversation_mappings: list[dict[str, Any]] | None,
        sender_mappings: list[dict[str, Any]] | None,
        attachment_policy: str,
        memu_policy: str,
    ) -> ImportMappingProfile:
        now = datetime.now(tz=UTC)
        if conversation_mappings:
            normalized_mappings = [
                ImportConversationMapping.model_validate(
                    {
                        **item,
                        "project_id": item.get("project_id") or project.project_id,
                        "workspace_id": item.get("workspace_id") or "",
                        "partition": item.get("partition") or "chat",
                    }
                )
                for item in conversation_mappings
            ]
        else:
            normalized_mappings = [
                ImportConversationMapping(
                    conversation_key=item.conversation_key,
                    conversation_label=item.label,
                    project_id=project.project_id,
                    workspace_id="",
                    scope_id=self._default_scope_id(source.source_type, item.conversation_key),
                    partition="chat",
                )
                for item in source.detected_conversations
            ]
        if not normalized_mappings:
            raise ImportWorkbenchError(
                "IMPORT_MAPPING_INVALID",
                "未检测到可保存的 conversation mapping。",
            )
        seen_keys: set[str] = set()
        for mapping in normalized_mappings:
            if mapping.conversation_key in seen_keys:
                raise ImportWorkbenchError(
                    "IMPORT_MAPPING_INVALID",
                    f"重复的 conversation_key: {mapping.conversation_key}",
                )
            seen_keys.add(mapping.conversation_key)
            if (
                mapping.project_id != project.project_id
                or mapping.workspace_id != ""
            ):
                raise ImportWorkbenchError(
                    "IMPORT_MAPPING_INVALID",
                    "mapping 必须落在当前 project/workspace。",
                )
            self._parse_scope(mapping.scope_id)
        sender_items = sender_mappings or []
        return ImportMappingProfile(
            mapping_id=f"mapping-{str(ULID())}",
            source_id=source.source_id,
            source_type=source.source_type,
            project_id=project.project_id,
            workspace_id="",
            conversation_mappings=normalized_mappings,
            sender_mappings=sender_items,  # type: ignore[arg-type]
            attachment_policy=attachment_policy,
            memu_policy=memu_policy,
            created_at=now,
            updated_at=now,
        )

    async def _materialize_grouped_messages(
        self,
        *,
        source: ImportSourceDocument,
        mapping: ImportMappingProfile,
    ) -> dict[str, dict[str, Any]]:
        adapter = self._require_adapter(source.source_type.value)
        mapping_by_key = {
            item.conversation_key: item for item in mapping.conversation_mappings if item.enabled
        }
        grouped: dict[str, dict[str, Any]] = {}
        async for message in adapter.materialize(source.input_ref, mapping):
            conversation_key = str(
                message.metadata.get("conversation_key") or message.thread_id or ""
            ).strip()
            mapping_item = mapping_by_key.get(conversation_key)
            if mapping_item is None:
                continue
            channel, thread_id = self._parse_scope(mapping_item.scope_id)
            updated = message.model_copy(
                update={
                    "channel": channel,
                    "thread_id": thread_id,
                    "metadata": {
                        **message.metadata,
                        "target_scope_id": mapping_item.scope_id,
                        "project_id": mapping.project_id,
                        "workspace_id": mapping.workspace_id,
                    },
                }
            )
            grouped.setdefault(
                mapping_item.scope_id,
                {
                    "conversation_key": conversation_key,
                    "messages": [],
                },
            )["messages"].append(updated)
        for payload in grouped.values():
            payload["messages"].sort(key=self._group_message_sort_key)
        return grouped

    @staticmethod
    def _group_message_sort_key(message: ImportedChatMessage) -> tuple[str, str, str, str]:
        return (
            message.timestamp.astimezone(UTC).isoformat(),
            message.source_cursor or "",
            message.source_message_id or "",
            message.sender_id,
        )

    @staticmethod
    def _build_failed_run_document(
        *,
        running: ImportRunDocument,
        error_message: str,
        metadata: dict[str, Any],
    ) -> ImportRunDocument:
        return running.model_copy(
            update={
                "status": ImportRunStatus.FAILED,
                "errors": [error_message],
                "metadata": metadata,
                "completed_at": datetime.now(tz=UTC),
                "degraded": ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["import_failed"],
                ),
            }
        )

    async def _ensure_scope_binding(
        self,
        *,
        store_group: StoreGroup,
        project: Project,
        scope_id: str,
    ) -> None:
        existing = await store_group.project_store.get_binding(
            project.project_id,
            ProjectBindingType.IMPORT_SCOPE,
            scope_id,
        )
        if existing is not None:
            return
        await store_group.project_store.create_binding(
            ProjectBinding(
                binding_id=f"binding-import-scope-{str(ULID())}",
                project_id=project.project_id,
                workspace_id="",
                binding_type=ProjectBindingType.IMPORT_SCOPE,
                binding_key=scope_id,
                binding_value=scope_id,
                source="feature_029_import_mapping",
                metadata={"surface": self._surface},
                migration_run_id=_IMPORT_MAPPING_RUN_ID,
            )
        )

    async def _collect_dedupe_details(
        self,
        *,
        source_id: str,
        scope_id: str,
        messages: list[ImportedChatMessage],
        resume: bool,
    ) -> list[dict[str, Any]]:
        details: list[dict[str, Any]] = []
        cursor_value = ""
        boundary_hit = not resume
        seen_keys: set[str] = set()
        async with self._import_state_scope() as state:
            cursor = None if state is None else await state.get_cursor(source_id, scope_id)
            if cursor is not None:
                cursor_value = cursor.cursor_value
            for message in messages:
                message_key = self._processor.build_message_key(message)
                if resume and not boundary_hit:
                    if cursor_value and message.source_cursor == cursor_value:
                        boundary_hit = True
                        details.append(
                            {
                                "reason": "resume_boundary",
                                "source_cursor": cursor_value,
                                "scope_id": scope_id,
                            }
                        )
                    continue
                reason = ""
                if message_key in seen_keys:
                    reason = "duplicate_in_input"
                elif state is not None and await state.has_dedupe_entry(
                    source_id, scope_id, message_key
                ):
                    reason = "duplicate_in_history"
                if reason:
                    details.append(
                        {
                            "reason": reason,
                            "scope_id": scope_id,
                            "source_message_id": message.source_message_id or "",
                            "source_cursor": message.source_cursor or "",
                            "message_key": message_key,
                            "preview": message.text[:80],
                        }
                    )
                    continue
                seen_keys.add(message_key)
        return details[:100]

    @asynccontextmanager
    async def _import_state_scope(self):
        if self._store_group is not None:
            if await verify_chat_import_tables(self._store_group.conn):
                yield SqliteChatImportStore(self._store_group.conn)
            else:
                yield None
            return
        if not self._db_path.exists():
            yield None
            return
        conn = await aiosqlite.connect(f"file:{self._db_path}?mode=ro", uri=True)
        conn.row_factory = aiosqlite.Row
        try:
            if not await verify_chat_import_tables(conn):
                yield None
            else:
                yield SqliteChatImportStore(conn)
        finally:
            if conn.in_transaction:
                await conn.rollback()
            await conn.close()

    def _build_run_document(
        self,
        *,
        run_id: str,
        source: ImportSourceDocument,
        mapping: ImportMappingProfile,
        previews: list[_GroupPreview],
        dry_run: bool,
        status: ImportRunStatus,
        errors: list[str],
        metadata: dict[str, Any],
    ) -> ImportRunDocument:
        warnings: list[str] = []
        artifact_refs: list[str] = []
        report_refs: list[str] = []
        dedupe_details: list[dict[str, Any]] = []
        cursor_payload: dict[str, Any] = {"scopes": {}}
        imported_count = 0
        duplicate_count = 0
        window_count = 0
        proposal_count = 0
        committed_count = 0
        attachment_count = 0
        attachment_artifact_count = 0
        attachment_fragment_count = 0
        memu_sync_count = 0
        memu_degraded_count = 0

        for preview in previews:
            report = preview.report
            warnings.extend(report.warnings)
            errors.extend(report.errors)
            artifact_refs.extend(report.artifact_refs)
            report_refs.append(report.report_id)
            dedupe_details.extend(preview.dedupe_details)
            if report.cursor is not None:
                cursor_payload["scopes"][preview.scope_id] = report.cursor.model_dump(mode="json")
            imported_count += report.summary.imported_count
            duplicate_count += report.summary.duplicate_count
            window_count += report.summary.window_count
            proposal_count += report.summary.proposal_count
            committed_count += report.summary.committed_count
            attachment_count += report.summary.attachment_count
            attachment_artifact_count += report.summary.attachment_artifact_count
            attachment_fragment_count += report.summary.attachment_fragment_count
            memu_sync_count += report.summary.memu_sync_count
            memu_degraded_count += report.summary.memu_degraded_count

        unique_artifacts = sorted(set(artifact_refs))
        unique_errors = list(dict.fromkeys(item for item in errors if item))
        unique_warnings = list(dict.fromkeys(item for item in warnings if item))
        resume_ref = f"resume:{source.source_id}" if previews else ""
        return ImportRunDocument(
            resource_id=run_id,
            active_project_id=source.active_project_id,
            active_workspace_id=source.active_workspace_id,
            source_id=source.source_id,
            source_type=source.source_type,
            status=status,
            dry_run=dry_run,
            mapping_id=mapping.mapping_id,
            summary={
                "conversation_count": len(mapping.conversation_mappings),
                "scope_count": len(previews),
                "imported_count": imported_count,
                "duplicate_count": duplicate_count,
                "window_count": window_count,
                "proposal_count": proposal_count,
                "committed_count": committed_count,
                "attachment_count": attachment_count,
                "attachment_artifact_count": attachment_artifact_count,
                "attachment_fragment_count": attachment_fragment_count,
            },
            warnings=unique_warnings,
            errors=unique_errors,
            dedupe_details=dedupe_details[:200],
            cursor=cursor_payload,
            artifact_refs=unique_artifacts,
            memory_effects=ImportMemoryEffectSummary(
                fragment_count=window_count + attachment_fragment_count,
                proposal_count=proposal_count,
                committed_count=committed_count,
                vault_ref_count=0,
                memu_sync_count=memu_sync_count,
                memu_degraded_count=memu_degraded_count,
            ),
            report_refs=report_refs,
            resume_ref=resume_ref,
            metadata=metadata,
            completed_at=datetime.now(tz=UTC),
            degraded=ControlPlaneDegradedState(
                is_degraded=status in {ImportRunStatus.FAILED, ImportRunStatus.RESUME_AVAILABLE},
                reasons=(
                    ["import_failed"]
                    if status in {ImportRunStatus.FAILED, ImportRunStatus.RESUME_AVAILABLE}
                    else []
                ),
            ),
            capabilities=[
                ControlPlaneCapability(
                    capability_id="import.report.inspect",
                    label="查看导入报告",
                    action_id="import.report.inspect",
                ),
                ControlPlaneCapability(
                    capability_id="import.resume",
                    label="继续导入",
                    action_id="import.resume",
                    enabled=bool(resume_ref),
                ),
            ],
        )

    def _derive_run_status(
        self,
        previews: list[_GroupPreview],
        errors: list[str],
    ) -> ImportRunStatus:
        if not previews:
            return ImportRunStatus.FAILED
        succeeded = sum(1 for item in previews if not item.report.errors)
        warning_count = sum(len(item.report.warnings) for item in previews)
        if errors and succeeded == 0:
            return ImportRunStatus.FAILED
        if errors:
            return ImportRunStatus.RESUME_AVAILABLE
        if warning_count:
            return ImportRunStatus.PARTIAL_SUCCESS
        return ImportRunStatus.COMPLETED

    def _build_resume_entries(
        self,
        project: Project,
        recent_runs: list[ImportRunDocument],
    ) -> list[ImportResumeEntry]:
        by_source: dict[str, ImportRunDocument] = {}
        for run in recent_runs:
            by_source.setdefault(run.source_id, run)
        entries: list[ImportResumeEntry] = []
        for source_id, run in by_source.items():
            if run.status not in {
                ImportRunStatus.RESUME_AVAILABLE,
                ImportRunStatus.FAILED,
                ImportRunStatus.PARTIAL_SUCCESS,
            }:
                continue
            scopes = run.cursor.get("scopes", {}) if isinstance(run.cursor, dict) else {}
            scope_id = next(iter(scopes.keys()), "")
            last_cursor = ""
            last_batch_id = ""
            if scope_id:
                scope_payload = scopes.get(scope_id, {})
                last_cursor = str(scope_payload.get("cursor_value", "") or "")
            if run.report_refs:
                last_batch_id = run.report_refs[-1]
            entries.append(
                ImportResumeEntry(
                    resume_id=f"resume:{source_id}",
                    source_id=source_id,
                    source_type=run.source_type,
                    project_id=project.project_id,
                    workspace_id="",
                    scope_id=scope_id,
                    last_cursor=last_cursor,
                    last_batch_id=last_batch_id,
                    state="ready" if run.resume_ref else "blocked",
                    blocking_reason="" if run.resume_ref else "缺少可恢复位点",
                    updated_at=run.updated_at,
                )
            )
        entries.sort(key=lambda item: item.updated_at, reverse=True)
        return entries

    def _default_scope_id(self, source_type: ImportSourceType, conversation_key: str) -> str:
        if source_type is ImportSourceType.WECHAT:
            slug = self._slugify(conversation_key)
            return f"chat:wechat_import:{slug}"
        slug = self._slugify(conversation_key)
        return f"chat:import:{slug}"

    @staticmethod
    def _slugify(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "import-thread"

    def _build_source_id(self, source_type: ImportSourceType, input_path: str) -> str:
        return f"{source_type.value}-{derive_import_source_id(input_path)}"

    def _resolve_input_path(self, value: str) -> str:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (self._root / path).resolve()
        return str(path.resolve())

    def _resolve_optional_path(self, value: str | None) -> str | None:
        if not value:
            return None
        return self._resolve_input_path(value)

    @staticmethod
    def _map_source_format(source_type: ImportSourceType) -> str:
        return (
            ImportSourceFormat.WECHAT.value
            if source_type is ImportSourceType.WECHAT
            else ImportSourceFormat.NORMALIZED_JSONL.value
        )

    @staticmethod
    def _parse_scope(scope_id: str) -> tuple[str, str]:
        match = _SCOPE_PATTERN.match(scope_id.strip())
        if match is None:
            raise ImportWorkbenchError(
                "IMPORT_MAPPING_INVALID",
                f"scope_id 必须是 chat:<channel>:<thread> 形式: {scope_id}",
            )
        return match.group("channel"), match.group("thread")

    @asynccontextmanager
    async def _store_group_scope(self):
        if self._store_group is not None:
            yield self._store_group
            return
        store_group = await create_store_group(
            str(self._db_path),
            self._artifacts_dir,
        )
        try:
            yield store_group
        finally:
            await store_group.conn.close()
