"""Import domain service —— 聊天导入 Workbench 相关 action / document。

从 control_plane.py 提取的方法：
  action handlers:
    - _handle_import_source_detect   -> import.source.detect
    - _handle_import_mapping_save    -> import.mapping.save
    - _handle_import_preview         -> import.preview
    - _handle_import_run             -> import.run
    - _handle_import_resume          -> import.resume
    - _handle_import_report_inspect  -> import.report.inspect
  document getters:
    - get_import_workbench
    - get_import_source
    - get_import_run
"""

from __future__ import annotations

from typing import Any

import structlog
from octoagent.core.models import (
    ActionRequestEnvelope,
    ActionResultEnvelope,
)
from octoagent.provider.dx.chat_import_service import ChatImportService
from octoagent.provider.dx.import_workbench_models import (
    ImportRunDocument,
    ImportSourceDocument,
    ImportWorkbenchDocument,
)
from octoagent.provider.dx.import_workbench_service import (
    ImportWorkbenchError,
    ImportWorkbenchService,
)

from ._base import ControlPlaneActionError, ControlPlaneContext, DomainServiceBase

log = structlog.get_logger()


class ImportDomainService(DomainServiceBase):
    """聊天导入 Workbench 的 action handler 和 document getter。"""

    def __init__(self, ctx: ControlPlaneContext) -> None:
        super().__init__(ctx)
        self._import_workbench_service: ImportWorkbenchService = (
            ctx.import_workbench_service
            or ImportWorkbenchService(
                ctx.project_root,
                surface="web",
                store_group=ctx.store_group,
            )
        )

    # ------------------------------------------------------------------
    # 路由注册
    # ------------------------------------------------------------------

    def action_routes(self) -> dict[str, Any]:
        return {
            "import.source.detect": self._handle_import_source_detect,
            "import.mapping.save": self._handle_import_mapping_save,
            "import.preview": self._handle_import_preview,
            "import.run": self._handle_import_run,
            "import.resume": self._handle_import_resume,
            "import.report.inspect": self._handle_import_report_inspect,
        }

    def document_routes(self) -> dict[str, Any]:
        return {
            "import_workbench": self.get_import_workbench,
            "import_source": self.get_import_source,
            "import_run": self.get_import_run,
        }

    # ------------------------------------------------------------------
    # Document getters
    # ------------------------------------------------------------------

    async def get_import_workbench(
        self,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,  # deprecated, ignored
    ) -> ImportWorkbenchDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        resolved_project_id = project_id or (
            selected_project.project_id if selected_project is not None else None
        )
        return await self._import_workbench_service.get_workbench(
            project_id=resolved_project_id,
        )

    async def get_import_source(self, source_id: str) -> ImportSourceDocument:
        return await self._get_import_source_in_scope(source_id)

    async def get_import_run(self, run_id: str) -> ImportRunDocument:
        return await self._get_import_run_in_scope(run_id)

    # ------------------------------------------------------------------
    # 内部 scope 查询
    # ------------------------------------------------------------------

    async def _get_import_source_in_scope(self, source_id: str) -> ImportSourceDocument:
        return await self._import_workbench_service.get_source(source_id)

    async def _get_import_run_in_scope(self, run_id: str) -> ImportRunDocument:
        return await self._import_workbench_service.get_run(run_id)

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    async def _handle_import_source_detect(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        source_type = str(request.params.get("source_type", "")).strip().lower()
        input_path = str(request.params.get("input_path", "")).strip()
        media_root = str(request.params.get("media_root", "")).strip() or None
        format_hint = str(request.params.get("format_hint", "")).strip() or None
        if not source_type:
            raise ControlPlaneActionError("IMPORT_SOURCE_INVALID", "source_type 不能为空")
        if not input_path:
            raise ControlPlaneActionError("INPUT_PATH_REQUIRED", "input_path 不能为空")
        try:
            document = await self._import_workbench_service.detect_source(
                source_type=source_type,
                input_path=input_path,
                media_root=media_root,
                format_hint=format_hint,
            )
        except ImportWorkbenchError as exc:
            raise ControlPlaneActionError(exc.code, exc.message) from exc
        return self._completed_result(
            request=request,
            code="IMPORT_SOURCE_DETECTED",
            message="已识别导入源",
            data=document.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("import_workbench", "imports:workbench"),
                self._resource_ref("import_source", document.resource_id),
            ],
        )

    async def _handle_import_mapping_save(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        source_id = str(request.params.get("source_id", "")).strip()
        if not source_id:
            raise ControlPlaneActionError("IMPORT_SOURCE_INVALID", "source_id 不能为空")
        raw_conversation_mappings = request.params.get("conversation_mappings")
        raw_sender_mappings = request.params.get("sender_mappings")
        conversation_mappings = (
            list(raw_conversation_mappings) if isinstance(raw_conversation_mappings, list) else None
        )
        sender_mappings = (
            list(raw_sender_mappings) if isinstance(raw_sender_mappings, list) else None
        )
        try:
            await self.get_import_source(source_id)
            profile = await self._import_workbench_service.save_mapping(
                source_id=source_id,
                conversation_mappings=conversation_mappings,
                sender_mappings=sender_mappings,
                attachment_policy=str(
                    request.params.get("attachment_policy", "artifact-first")
                ).strip()
                or "artifact-first",
                memu_policy=str(request.params.get("memu_policy", "best-effort")).strip()
                or "best-effort",
            )
            source = await self.get_import_source(source_id)
        except ImportWorkbenchError as exc:
            raise ControlPlaneActionError(exc.code, exc.message) from exc
        return self._completed_result(
            request=request,
            code="IMPORT_MAPPING_SAVED",
            message="导入 mapping 已保存",
            data=profile.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("import_workbench", "imports:workbench"),
                self._resource_ref("import_source", source.resource_id),
            ],
        )

    async def _handle_import_preview(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        source_id = str(request.params.get("source_id", "")).strip()
        if not source_id:
            raise ControlPlaneActionError("IMPORT_SOURCE_INVALID", "source_id 不能为空")
        mapping_id = str(request.params.get("mapping_id", "")).strip() or None
        try:
            await self.get_import_source(source_id)
            document = await self._import_workbench_service.preview(
                source_id=source_id,
                mapping_id=mapping_id,
            )
        except ImportWorkbenchError as exc:
            raise ControlPlaneActionError(exc.code, exc.message) from exc
        return self._completed_result(
            request=request,
            code="IMPORT_PREVIEW_READY",
            message="已生成导入预览",
            data=document.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("import_workbench", "imports:workbench"),
                self._resource_ref("import_run", document.resource_id),
            ],
        )

    async def _handle_import_run(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        source_id = str(request.params.get("source_id", "")).strip()
        mapping_id = str(request.params.get("mapping_id", "")).strip() or None
        if source_id:
            try:
                await self.get_import_source(source_id)
                document = await self._import_workbench_service.run(
                    source_id=source_id,
                    mapping_id=mapping_id,
                    resume=bool(request.params.get("resume", False)),
                )
            except ImportWorkbenchError as exc:
                raise ControlPlaneActionError(exc.code, exc.message) from exc
            return self._completed_result(
                request=request,
                code="IMPORT_RUN_COMPLETED",
                message="导入执行完成",
                data=document.model_dump(mode="json"),
                resource_refs=[
                    self._resource_ref("import_workbench", "imports:workbench"),
                    self._resource_ref("import_run", document.resource_id),
                ],
            )

        # 降级路径：直接调用 ChatImportService（无 source_id 时）
        input_path = str(request.params.get("input_path", "")).strip()
        if not input_path:
            raise ControlPlaneActionError("INPUT_PATH_REQUIRED", "input_path 不能为空")
        report = await ChatImportService(
            self._ctx.project_root, store_group=self._stores
        ).import_chats(
            input_path=input_path,
            source_format=str(request.params.get("source_format", "normalized-jsonl")),
            source_id=(str(request.params.get("source_id", "")).strip() or None),
            channel=(str(request.params.get("channel", "")).strip() or None),
            thread_id=(str(request.params.get("thread_id", "")).strip() or None),
            dry_run=bool(request.params.get("dry_run", False)),
            resume=bool(request.params.get("resume", False)),
        )
        return self._completed_result(
            request=request,
            code="IMPORT_COMPLETED",
            message="聊天导入已完成",
            data=report.model_dump(mode="json"),
            resource_refs=[self._resource_ref("diagnostics_summary", "diagnostics:runtime")],
        )

    async def _handle_import_resume(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        resume_id = str(request.params.get("resume_id", "")).strip()
        if not resume_id:
            raise ControlPlaneActionError("IMPORT_RESUME_BLOCKED", "resume_id 不能为空")
        try:
            await self.get_import_source(resume_id.removeprefix("resume:"))
            document = await self._import_workbench_service.resume(resume_id=resume_id)
        except ImportWorkbenchError as exc:
            raise ControlPlaneActionError(exc.code, exc.message) from exc
        return self._completed_result(
            request=request,
            code="IMPORT_RESUME_COMPLETED",
            message="已恢复导入",
            data=document.model_dump(mode="json"),
            resource_refs=[
                self._resource_ref("import_workbench", "imports:workbench"),
                self._resource_ref("import_run", document.resource_id),
            ],
        )

    async def _handle_import_report_inspect(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        run_id = str(request.params.get("run_id", "")).strip()
        if not run_id:
            raise ControlPlaneActionError("IMPORT_REPORT_NOT_FOUND", "run_id 不能为空")
        try:
            document = await self.get_import_run(run_id)
        except ImportWorkbenchError as exc:
            raise ControlPlaneActionError(exc.code, exc.message) from exc
        return self._completed_result(
            request=request,
            code="IMPORT_REPORT_READY",
            message="已加载导入报告",
            data=document.model_dump(mode="json"),
            resource_refs=[self._resource_ref("import_run", document.resource_id)],
        )
