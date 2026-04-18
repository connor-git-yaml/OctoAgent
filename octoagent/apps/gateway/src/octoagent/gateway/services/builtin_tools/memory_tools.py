"""memory_tools：记忆读写工具（6 个）。

工具列表：
- memory.read
- memory.browse
- memory.search
- memory.citations
- memory.recall
- memory.write
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from octoagent.memory import (
    EvidenceRef,
    MemoryAccessPolicy,
    MemoryLayer,
    MemoryPartition,
    MemoryRecallHookOptions,
    MemoryRecallPostFilterMode,
    MemoryRecallRerankMode,
    MemoryRecallResult,
    SqliteMemoryStore,
    WriteAction,
)
from octoagent.gateway.services.memory.memory_retrieval_profile import (
    apply_retrieval_profile_to_hook_options,
)
from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract

from ._deps import (
    ToolDeps,
    current_parent,
    resolve_memory_scope_ids,
    resolve_runtime_project_context,
)

_log = structlog.get_logger()

_VALID_PARTITIONS = {"core", "profile", "work", "health", "finance", "chat"}


async def register(broker, deps: ToolDeps) -> None:
    """注册所有记忆工具。"""
    from octoagent.gateway.services.memory.memory_console_service import MemoryConsoleService
    from octoagent.gateway.services.memory.memory_runtime_service import MemoryRuntimeService
    from ..agent_context import (
        build_default_memory_recall_hook_options,
    )

    @tool_contract(
        name="memory.read",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="memory",
        tags=["memory", "subject", "history"],
        manifest_ref="builtin://memory.read",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def memory_read(
        subject_key: str,
        scope_id: str = "",
        project_id: str = "",
    ) -> str:
        """读取指定 subject 的 current/history。"""

        project, workspace, _task = await resolve_runtime_project_context(
            deps,
            project_id=project_id,
        )
        document = await deps.memory_console_service.get_memory_subject_history(
            subject_key=subject_key,
            project_id=project.project_id if project is not None else "",
            scope_id=scope_id or None,
        )
        return json.dumps(document.model_dump(mode="json"), ensure_ascii=False)

    @tool_contract(
        name="memory.browse",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="memory",
        tags=["memory", "browse", "directory"],
        manifest_ref="builtin://memory.browse",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def memory_browse(
        prefix: str = "",
        partition: str = "",
        scope_id: str = "",
        group_by: str = "partition",
        offset: int = 0,
        limit: int = 20,
        project_id: str = "",
    ) -> str:
        """按 subject_key 前缀、partition、scope 等维度浏览记忆目录，获取分组统计和概览。"""

        project, workspace, _task = await resolve_runtime_project_context(
            deps,
            project_id=project_id,
        )
        result = await deps.memory_console_service.browse_memory(
            project_id=project.project_id if project is not None else "",
            scope_id=scope_id or "",
            prefix=prefix,
            partition=partition,
            group_by=group_by,
            offset=offset,
            limit=max(1, min(limit, 100)),
        )
        return json.dumps(result, ensure_ascii=False)

    @tool_contract(
        name="memory.search",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="memory",
        tags=["memory", "search", "records"],
        manifest_ref="builtin://memory.search",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def memory_search(
        query: str,
        scope_id: str = "",
        partition: str = "",
        layer: str = "",
        project_id: str = "",
        limit: int = 10,
        derived_type: str = "",
        status: str = "",
        updated_after: str = "",
        updated_before: str = "",
    ) -> str:
        """按 query / scope / partition / layer / derived_type / status / 时间范围搜索 Memory。

        新增可选参数：
        - derived_type: 按派生类型筛选（profile/tom/entity/relation）
        - status: 按 SoR 状态筛选（current/archived/superseded）
        - updated_after: 更新时间下界（ISO 8601）
        - updated_before: 更新时间上界（ISO 8601）
        所有新参数均为可选，默认空字符串，向后兼容。
        """

        project, workspace, _task = await resolve_runtime_project_context(
            deps,
            project_id=project_id,
        )
        document = await deps.memory_console_service.get_memory_console(
            project_id=project.project_id if project is not None else "",
            scope_id=scope_id or None,
            partition=MemoryPartition(partition) if partition else None,
            layer=MemoryLayer(layer) if layer else None,
            query=query,
            include_history=bool(status and status != "current"),
            include_vault_refs=True,
            limit=max(1, min(limit, 50)),
            derived_type=derived_type,
            status=status,
            updated_after=updated_after,
            updated_before=updated_before,
        )
        return json.dumps(document.model_dump(mode="json"), ensure_ascii=False)

    @tool_contract(
        name="memory.citations",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="memory",
        tags=["memory", "citations", "evidence"],
        manifest_ref="builtin://memory.citations",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def memory_citations(
        subject_key: str,
        scope_id: str = "",
        project_id: str = "",
    ) -> str:
        """读取 subject 的证据链引用。"""

        project, workspace, _task = await resolve_runtime_project_context(
            deps,
            project_id=project_id,
        )
        document = await deps.memory_console_service.get_memory_subject_history(
            subject_key=subject_key,
            project_id=project.project_id if project is not None else "",
            scope_id=scope_id or None,
        )
        citations = []
        if document.current_record is not None:
            citations.extend(document.current_record.evidence_refs)
        for record in document.history:
            citations.extend(record.evidence_refs)
        return json.dumps(
            {
                "subject_key": subject_key,
                "scope_id": document.scope_id,
                "citations": citations,
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="memory.recall",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="memory",
        tags=["memory", "recall", "context"],
        manifest_ref="builtin://memory.recall",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def memory_recall(
        query: str,
        scope_id: str = "",
        project_id: str = "",
        limit: int = 4,
        allow_vault: bool = False,
        post_filter_mode: MemoryRecallPostFilterMode = (
            MemoryRecallPostFilterMode.KEYWORD_OVERLAP
        ),
        rerank_mode: MemoryRecallRerankMode = MemoryRecallRerankMode.HEURISTIC,
        subject_hint: str = "",
        focus_terms: list[str] | None = None,
    ) -> str:
        """生成结构化 recall pack。

        返回 query 扩展、命中、citation、backend truth 与 hook trace。
        """

        project, workspace, task = await resolve_runtime_project_context(
            deps,
            project_id=project_id,
        )
        memory_service = await deps.memory_runtime_service.memory_service_for_scope(
            project=project,
        )
        backend_status = await memory_service.get_backend_status()
        retrieval_profile = await deps.memory_runtime_service.retrieval_profile_for_scope(
            project=project,
            backend_status=backend_status,
        )
        scope_ids = await resolve_memory_scope_ids(
            deps,
            task=task,
            project=project,
            explicit_scope_id=scope_id,
        )
        if not scope_ids:
            empty = MemoryRecallResult(
                query=query.strip(),
                expanded_queries=[],
                scope_ids=[],
                hits=[],
                backend_status=backend_status,
                degraded_reasons=["memory_scope_unresolved"],
            )
            return json.dumps(empty.model_dump(mode="json"), ensure_ascii=False)
        bounded_limit = max(1, min(limit, 8))
        hook_options = apply_retrieval_profile_to_hook_options(
            build_default_memory_recall_hook_options(
                subject_hint=subject_hint,
            ).model_copy(
                update={
                    "post_filter_mode": post_filter_mode,
                    "rerank_mode": rerank_mode,
                    "focus_terms": list(focus_terms or []),
                }
            ),
            retrieval_profile,
        )
        recall = await memory_service.recall_memory(
            scope_ids=scope_ids[:4],
            query=query,
            policy=MemoryAccessPolicy(allow_vault=allow_vault),
            per_scope_limit=min(4, bounded_limit),
            max_hits=bounded_limit,
            hook_options=MemoryRecallHookOptions.model_validate(hook_options),
        )
        return json.dumps(recall.model_dump(mode="json"), ensure_ascii=False)

    @tool_contract(
        name="memory.write",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="memory",
        tags=["memory", "write", "persist"],
        manifest_ref="builtin://memory.write",
        metadata={
            "entrypoints": ["agent_runtime", "web"],
        },
    )
    async def memory_write(
        subject_key: str,
        content: str,
        partition: str = "work",
        evidence_refs: list[dict[str, str]] | None = None,
        scope_id: str = "",
        project_id: str = "",
    ) -> str:
        """将重要信息持久化为长期记忆（SoR 记录）。

        当用户透露偏好、事实、决策或其他值得长期记住的信息时，
        调用此工具保存。系统会自动判断是新增还是更新已有记忆。

        Args:
            subject_key: 记忆主题标识，用 `/` 分层（如"用户偏好/编程语言"）
            content: 记忆内容，完整的陈述句
            partition: 业务分区 (core/profile/work/health/finance/chat)，默认 work
            evidence_refs: 证据引用列表 [{"ref_id": "...", "ref_type": "message"}]
            scope_id: 可选，指定 scope
            project_id: 可选，指定 project
        """
        # 1. 参数校验
        subject_key = subject_key.strip()
        if not subject_key:
            return json.dumps(
                {"error": "MISSING_PARAM", "message": "subject_key 不能为空"},
                ensure_ascii=False,
            )
        content = content.strip()
        if not content:
            return json.dumps(
                {"error": "MISSING_PARAM", "message": "content 不能为空"},
                ensure_ascii=False,
            )
        partition = partition.strip().lower()
        if partition not in _VALID_PARTITIONS:
            return json.dumps(
                {
                    "error": "INVALID_PARTITION",
                    "message": f"无效的 partition 值 '{partition}'，有效值为: {', '.join(sorted(_VALID_PARTITIONS))}",
                },
                ensure_ascii=False,
            )

        # 2. 解析 project/workspace/scope context
        try:
            project, workspace, task = await resolve_runtime_project_context(
                deps,
                project_id=project_id,
            )
            scope_ids = await resolve_memory_scope_ids(
                deps,
                task=task,
                project=project,
                explicit_scope_id=scope_id,
            )
        except Exception as exc:
            return json.dumps(
                {"error": "SCOPE_UNRESOLVED", "message": f"无法解析 memory scope: {exc}"},
                ensure_ascii=False,
            )

        if not scope_ids:
            return json.dumps(
                {"error": "SCOPE_UNRESOLVED", "message": "无法解析 memory scope，请确认 project 和 workspace 配置"},
                ensure_ascii=False,
            )

        resolved_scope_id = scope_ids[0]

        # 3. 获取 MemoryService
        try:
            memory_service = await deps.memory_runtime_service.memory_service_for_scope(
                project=project,
            )
        except Exception as exc:
            return json.dumps(
                {"error": "INTERNAL_ERROR", "message": f"获取 memory 服务失败: {exc}"},
                ensure_ascii=False,
            )

        # 4. 查询是否已存在 SoR → 决定 ADD 或 UPDATE
        memory_store = SqliteMemoryStore(deps.stores.conn)
        try:
            existing = await memory_store.get_current_sor(resolved_scope_id, subject_key)
        except Exception:
            existing = None

        if existing is not None:
            action = WriteAction.UPDATE
            expected_version = existing.version
            action_label = "update"
        else:
            action = WriteAction.ADD
            expected_version = None
            action_label = "add"

        # 5. 构建 evidence_refs
        refs: list[EvidenceRef] = []
        if evidence_refs:
            for ref_dict in evidence_refs:
                refs.append(
                    EvidenceRef(
                        ref_id=str(ref_dict.get("ref_id", "")).strip(),
                        ref_type=str(ref_dict.get("ref_type", "message")).strip(),
                        snippet=str(ref_dict.get("snippet", "")).strip() or None,
                    )
                )
        # 自动追加当前 task_id 作为证据
        try:
            _, ctx, current_task = await current_parent(deps)
            if current_task is not None:
                refs.append(
                    EvidenceRef(
                        ref_id=current_task.task_id,
                        ref_type="task",
                    )
                )
        except Exception:
            pass
        # 确保至少有一个 evidence_ref
        if not refs:
            refs.append(EvidenceRef(ref_id="memory.write", ref_type="tool"))

        # 6. 治理流程: propose_write -> validate_proposal -> commit_memory
        mem_partition = MemoryPartition(partition)
        try:
            proposal = await memory_service.propose_write(
                scope_id=resolved_scope_id,
                partition=mem_partition,
                action=action,
                subject_key=subject_key,
                content=content,
                rationale="memory.write tool",
                confidence=1.0,
                evidence_refs=refs,
                expected_version=expected_version,
                metadata={"source": "memory.write"},
            )
        except Exception as exc:
            return json.dumps(
                {"error": "PROPOSE_FAILED", "message": f"记忆写入提案失败: {exc}"},
                ensure_ascii=False,
            )

        try:
            validation = await memory_service.validate_proposal(proposal.proposal_id)
        except Exception as exc:
            return json.dumps(
                {"error": "VALIDATE_FAILED", "message": f"记忆写入验证失败: {exc}"},
                ensure_ascii=False,
            )

        if not validation.accepted:
            _log.info(
                "memory_rejected",
                subject_key=subject_key,
                errors=validation.errors,
                scope_id=resolved_scope_id,
                action=action_label,
            )
            return json.dumps(
                {
                    "status": "rejected",
                    "action": action_label,
                    "subject_key": subject_key,
                    "errors": validation.errors,
                    "scope_id": resolved_scope_id,
                },
                ensure_ascii=False,
            )

        try:
            commit_result = await memory_service.commit_memory(proposal.proposal_id)
        except Exception as exc:
            return json.dumps(
                {"error": "COMMIT_FAILED", "message": f"记忆写入失败: {exc}"},
                ensure_ascii=False,
            )

        _log.info(
            "memory_committed",
            action=action_label,
            subject_key=subject_key,
            memory_id=commit_result.memory_id,
            version=commit_result.version,
            scope_id=resolved_scope_id,
            partition=partition,
        )

        return json.dumps(
            {
                "status": "committed",
                "action": action_label,
                "subject_key": subject_key,
                "memory_id": commit_result.memory_id,
                "version": commit_result.version,
                "scope_id": resolved_scope_id,
                "partition": partition,
            },
            ensure_ascii=False,
        )

    for handler in (
        memory_read,
        memory_browse,
        memory_search,
        memory_citations,
        memory_recall,
        memory_write,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)
