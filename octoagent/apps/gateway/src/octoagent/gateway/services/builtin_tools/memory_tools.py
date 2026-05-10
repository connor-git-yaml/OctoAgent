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
from datetime import datetime, timezone
from typing import Any

import structlog
from ulid import ULID

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
from pydantic import BaseModel

from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract
from octoagent.gateway.harness.tool_registry import ToolEntry
from octoagent.gateway.harness.tool_registry import register as _registry_register
from octoagent.core.models import MemoryNamespaceKind
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.core.models.tool_results import MemoryWriteResult

from ..execution_context import get_current_execution_context
from ._deps import (
    ToolDeps,
    current_parent,
    resolve_memory_scope_ids,
    resolve_worker_default_scope_id,
    WorkerMemoryNamespaceNotResolved,
    resolve_runtime_project_context,
)

_log = structlog.get_logger()

# 各工具 entrypoints 声明（Feature 084 D1 根治）
_TOOL_ENTRYPOINTS: dict[str, frozenset[str]] = {
    "memory.read":      frozenset({"agent_runtime", "web"}),
    "memory.browse":    frozenset({"agent_runtime", "web"}),
    "memory.search":    frozenset({"agent_runtime", "web"}),
    "memory.citations": frozenset({"agent_runtime", "web"}),
    "memory.recall":    frozenset({"agent_runtime", "web"}),
    "memory.write":     frozenset({"agent_runtime", "web"}),
}

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
        try:
            exec_ctx = get_current_execution_context()
            actor_id = exec_ctx.worker_id or exec_ctx.agent_session_id or exec_ctx.session_id
            actor_label = exec_ctx.runtime_kind or ""
        except RuntimeError:
            actor_id = ""
            actor_label = ""
        recall = await memory_service.recall_memory(
            scope_ids=scope_ids[:4],
            query=query,
            policy=MemoryAccessPolicy(
                allow_vault=allow_vault,
                actor_id=actor_id,
                actor_label=actor_label,
                project_id=project.project_id if project is not None else "",
            ),
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
        produces_write=True,
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
    ) -> MemoryWriteResult:
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
            return MemoryWriteResult(
                status="rejected",
                target="memory_store",
                preview="subject_key 不能为空",
                reason="MISSING_PARAM: subject_key 不能为空",
                memory_id="",
                version=0,
                action="create",
                scope_id="",
            )
        content = content.strip()
        if not content:
            return MemoryWriteResult(
                status="rejected",
                target="memory_store",
                preview="content 不能为空",
                reason="MISSING_PARAM: content 不能为空",
                memory_id="",
                version=0,
                action="create",
                scope_id="",
            )
        partition = partition.strip().lower()
        if partition not in _VALID_PARTITIONS:
            return MemoryWriteResult(
                status="rejected",
                target="memory_store",
                preview=f"无效的 partition 值 '{partition}'",
                reason=f"INVALID_PARTITION: 有效值为 {', '.join(sorted(_VALID_PARTITIONS))}",
                memory_id="",
                version=0,
                action="create",
                scope_id="",
            )

        # 2. 解析 project/workspace/scope context
        # F094 B4 (Codex plan HIGH-1 + Phase B HIGH-1 闭环): scope 解析三段式
        # - 调用方显式传 scope_id：走 resolve_memory_scope_ids 白名单（兼容显式
        #   PROJECT_SHARED 写）；同时 captured 对应 namespace_id/kind 给 audit emit
        # - 未传 scope_id + agent_runtime_id 非空：fail closed，必须解析到
        #   AGENT_PRIVATE namespace；解析失败 → 立即返回 SCOPE_UNRESOLVED rejected
        #   （NFR-3 显式失败，不静默降级到 PROJECT_SHARED——避免 worker 私有 fact
        #   被错误写到共享 scope）
        # - 未传 scope_id + agent_runtime_id 空（legacy / 无上下文调用）：走 baseline
        #   resolve_memory_scope_ids
        captured_namespace_id = ""
        captured_namespace_kind = ""
        try:
            project, workspace, task = await resolve_runtime_project_context(
                deps,
                project_id=project_id,
            )

            resolved_scope_id = ""
            scope_ids: list[str] = []
            explicit_scope_provided = bool(scope_id.strip())

            try:
                exec_ctx = get_current_execution_context()
                agent_runtime_id_val = (exec_ctx.agent_runtime_id or "").strip()
            except RuntimeError:
                agent_runtime_id_val = ""

            if not explicit_scope_provided and agent_runtime_id_val:
                # F097 Phase F P1 闭环（α 语义）：subagent 默认 memory.write 路径
                # 优先复用 caller AGENT_PRIVATE namespace 的 scope_id（与 _ensure_memory_namespaces
                # α 路径一致）。subagent runtime 自身没有 AGENT_PRIVATE namespace（Phase F TF.2
                # α 语义不为 subagent 创建），因此原 worker default 路径会 SCOPE_UNRESOLVED 拒绝。
                subagent_caller_scope_resolved = False
                if task is not None:
                    try:
                        # 从 task 最新 USER_MESSAGE event 反序列化 SubagentDelegation
                        events = await deps.stores.event_store.get_events_for_task(
                            task.task_id
                        )
                        from octoagent.core.models import EventType as _EventType, SubagentDelegation as _SD

                        for event in reversed(events):
                            if event.type is not _EventType.USER_MESSAGE:
                                continue
                            cm = (event.payload or {}).get("control_metadata", {}) or {}
                            raw_d = cm.get("subagent_delegation")
                            if not raw_d:
                                continue
                            delegation = (
                                _SD.model_validate_json(raw_d)
                                if isinstance(raw_d, str)
                                else _SD.model_validate(raw_d)
                            )
                            caller_ns_ids = list(delegation.caller_memory_namespace_ids or [])
                            if not caller_ns_ids:
                                break  # 无 caller namespace，走 degrade
                            caller_ns = (
                                await deps.stores.agent_context_store.get_memory_namespace(
                                    caller_ns_ids[0]
                                )
                            )
                            if caller_ns is not None and caller_ns.memory_scope_ids:
                                resolved_scope_id = caller_ns.memory_scope_ids[0]
                                scope_ids = [resolved_scope_id]
                                captured_namespace_id = caller_ns.namespace_id
                                captured_namespace_kind = caller_ns.kind.value
                                subagent_caller_scope_resolved = True
                            break
                    except Exception:
                        # 失败降级到原 worker default 路径
                        pass
                if subagent_caller_scope_resolved:
                    pass  # subagent 走 caller scope，跳过 worker default 解析
                else:
                    # F094 B4 HIGH-1 fail-closed 路径：必须解析到 AGENT_PRIVATE
                    try:
                        worker_scope = await resolve_worker_default_scope_id(
                            deps,
                            project_id=(project.project_id if project is not None else ""),
                            agent_runtime_id=agent_runtime_id_val,
                        )
                        resolved_scope_id = worker_scope
                        scope_ids = [worker_scope]
                        # captured namespace 信息 给 emit 复用（避免 commit 后反查 race）
                        matched_namespaces = (
                            await deps.stores.agent_context_store.list_memory_namespaces(
                                project_id=(
                                    project.project_id if project is not None else None
                                ),
                                agent_runtime_id=agent_runtime_id_val,
                                kind=MemoryNamespaceKind.AGENT_PRIVATE,
                            )
                        )
                        if matched_namespaces:
                            captured_namespace_id = matched_namespaces[0].namespace_id
                            captured_namespace_kind = matched_namespaces[0].kind.value
                    except WorkerMemoryNamespaceNotResolved as exc:
                        return MemoryWriteResult(
                            status="rejected",
                            target="memory_store",
                            preview=f"无法解析 worker private namespace: {exc}",
                            reason=(
                                f"SCOPE_UNRESOLVED: F094 NFR-3 fail-closed: "
                                f"agent_runtime_id={agent_runtime_id_val} 但未找到"
                                f" active AGENT_PRIVATE namespace；上游 dispatch 路径"
                                f" 应该已经创建过——请检查 agent_context.resolve 与"
                                f" memory_namespaces 表数据一致性。原始异常: {exc}"
                            ),
                            memory_id="",
                            version=0,
                            action="create",
                            scope_id="",
                        )
            else:
                # 显式 scope_id 或 legacy 无 agent_runtime_id 路径走 baseline
                scope_ids = await resolve_memory_scope_ids(
                    deps,
                    task=task,
                    project=project,
                    explicit_scope_id=scope_id,
                )
        except Exception as exc:
            return MemoryWriteResult(
                status="rejected",
                target="memory_store",
                preview=f"无法解析 memory scope: {exc}",
                reason=f"SCOPE_UNRESOLVED: {exc}",
                memory_id="",
                version=0,
                action="create",
                scope_id="",
            )

        if not scope_ids:
            return MemoryWriteResult(
                status="rejected",
                target="memory_store",
                preview="无法解析 memory scope",
                reason="SCOPE_UNRESOLVED: 请确认 project 和 workspace 配置",
                memory_id="",
                version=0,
                action="create",
                scope_id="",
            )

        if not resolved_scope_id:
            resolved_scope_id = scope_ids[0]

        # F094 B6 (Codex Phase B MED-3 闭环): 显式 scope_id 路径或 legacy 路径
        # 也尝试解析对应 namespace，避免 audit emit 缺字段。失败时留空（degraded）。
        if not captured_namespace_id and resolved_scope_id:
            try:
                lookup_runtime = agent_runtime_id_val or None
                ns_candidates = (
                    await deps.stores.agent_context_store.list_memory_namespaces(
                        project_id=(
                            project.project_id if project is not None else None
                        ),
                        agent_runtime_id=lookup_runtime,
                    )
                )
                for ns in ns_candidates:
                    if resolved_scope_id in ns.memory_scope_ids:
                        captured_namespace_id = ns.namespace_id
                        captured_namespace_kind = ns.kind.value
                        break
                # 显式 PROJECT_SHARED 路径下 lookup_runtime 可能为空，仍能通过
                # project_id + scope_id contains 匹配到 PROJECT_SHARED namespace
                if not captured_namespace_id:
                    ns_project_wide = (
                        await deps.stores.agent_context_store.list_memory_namespaces(
                            project_id=(
                                project.project_id if project is not None else None
                            ),
                        )
                    )
                    for ns in ns_project_wide:
                        if resolved_scope_id in ns.memory_scope_ids:
                            captured_namespace_id = ns.namespace_id
                            captured_namespace_kind = ns.kind.value
                            break
            except Exception as ns_exc:
                _log.warning(
                    "memory_write_namespace_lookup_failed",
                    error=str(ns_exc),
                )

        # 3. 获取 MemoryService
        try:
            memory_service = await deps.memory_runtime_service.memory_service_for_scope(
                project=project,
            )
        except Exception as exc:
            return MemoryWriteResult(
                status="rejected",
                target="memory_store",
                preview=f"获取 memory 服务失败: {exc}",
                reason=f"INTERNAL_ERROR: {exc}",
                memory_id="",
                version=0,
                action="create",
                scope_id=resolved_scope_id,
            )

        # 4. 查询是否已存在 SoR → 决定 ADD 或 UPDATE
        memory_store = SqliteMemoryStore(deps.stores.conn)
        try:
            existing = await memory_store.get_current_sor(resolved_scope_id, subject_key)
        except Exception:
            existing = None

        if existing is not None:
            write_action = WriteAction.UPDATE
            expected_version = existing.version
            action_label = "update"
        else:
            write_action = WriteAction.ADD
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
                action=write_action,
                subject_key=subject_key,
                content=content,
                rationale="memory.write tool",
                confidence=1.0,
                evidence_refs=refs,
                expected_version=expected_version,
                metadata={"source": "memory.write"},
            )
        except Exception as exc:
            return MemoryWriteResult(
                status="rejected",
                target="memory_store",
                preview=f"记忆写入提案失败: {exc}",
                reason=f"PROPOSE_FAILED: {exc}",
                memory_id="",
                version=0,
                action="create",
                scope_id=resolved_scope_id,
            )

        try:
            validation = await memory_service.validate_proposal(proposal.proposal_id)
        except Exception as exc:
            return MemoryWriteResult(
                status="rejected",
                target="memory_store",
                preview=f"记忆写入验证失败: {exc}",
                reason=f"VALIDATE_FAILED: {exc}",
                memory_id="",
                version=0,
                action="create",
                scope_id=resolved_scope_id,
            )

        if not validation.accepted:
            _log.info(
                "memory_rejected",
                subject_key=subject_key,
                errors=validation.errors,
                scope_id=resolved_scope_id,
                action=action_label,
            )
            return MemoryWriteResult(
                status="rejected",
                target="memory_store",
                preview=f"记忆写入被拒绝: {', '.join(str(e) for e in validation.errors)}",
                reason=f"VALIDATION_REJECTED: {validation.errors}",
                memory_id="",
                version=0,
                action="create",
                scope_id=resolved_scope_id,
            )

        try:
            commit_result = await memory_service.commit_memory(proposal.proposal_id)
        except Exception as exc:
            return MemoryWriteResult(
                status="rejected",
                target="memory_store",
                preview=f"记忆写入失败: {exc}",
                reason=f"COMMIT_FAILED: {exc}",
                memory_id="",
                version=0,
                action="create",
                scope_id=resolved_scope_id,
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

        # F094 B6 (Codex plan MED-4 闭环): memory.write commit 成功后**新增** emit
        # MEMORY_ENTRY_ADDED 事件——baseline 该事件主要由 user_profile.update /
        # memory_candidates.promote emit；F094 让 memory.write 也成为可审计来源，
        # payload 含 worker / agent 维度（namespace_kind / namespace_id /
        # agent_runtime_id / agent_session_id）让 F096 audit 可订阅。
        # 失败不阻断主路径（memory write 已成功）；emit 异常仅 log。
        try:
            exec_ctx_for_event = None
            try:
                exec_ctx_for_event = get_current_execution_context()
            except RuntimeError:
                pass
            audit_task_id = (
                (exec_ctx_for_event.task_id if exec_ctx_for_event else "")
                or "_memory_write_audit"
            )
            agent_runtime_id_for_event = (
                exec_ctx_for_event.agent_runtime_id
                if exec_ctx_for_event
                else ""
            )
            agent_session_id_for_event = (
                exec_ctx_for_event.agent_session_id
                if exec_ctx_for_event
                else ""
            )
            # F094 B6 (Codex Phase B MED-3 闭环): 直接用 scope 解析阶段 captured
            # 的 namespace 信息（捕获时机在 commit 前，避免 commit-后反查 race +
            # archive race）。captured_namespace_kind/_id 在 scope 解析时已填充。
            namespace_kind_value = captured_namespace_kind
            namespace_id_value = captured_namespace_id
            event_store = deps.stores.event_store
            task_seq = await event_store.get_next_task_seq(audit_task_id)
            event = Event(
                event_id=str(ULID()),
                task_id=audit_task_id,
                task_seq=task_seq,
                ts=datetime.now(timezone.utc),
                type=EventType.MEMORY_ENTRY_ADDED,
                actor=ActorType.SYSTEM,
                payload={
                    "tool": "memory.write",
                    "action": action_label,
                    "memory_id": commit_result.memory_id,
                    "version": commit_result.version,
                    "scope_id": resolved_scope_id,
                    "subject_key": subject_key,
                    "partition": partition,
                    # F094 B6 新字段：worker / agent 维度
                    "agent_runtime_id": agent_runtime_id_for_event,
                    "agent_session_id": agent_session_id_for_event,
                    "namespace_kind": namespace_kind_value,
                    "namespace_id": namespace_id_value,
                },
                trace_id=audit_task_id,
            )
            await event_store.append_event_committed(event, update_task_pointer=False)
        except Exception as event_exc:
            _log.warning(
                "memory_write_event_emit_failed",
                error=str(event_exc),
                memory_id=commit_result.memory_id,
            )

        return MemoryWriteResult(
            status="written",
            target="memory_store",
            preview=f"{action_label}: {subject_key} → {content[:100]}",
            memory_id=commit_result.memory_id,
            version=commit_result.version,
            action="create" if action_label == "add" else "update",
            scope_id=resolved_scope_id,
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

    # 向 ToolRegistry 注册 ToolEntry（Feature 084 T013 — entrypoints 迁移）
    for _name, _handler, _sel in (
        ("memory.read",      memory_read,      SideEffectLevel.NONE),
        ("memory.browse",    memory_browse,    SideEffectLevel.NONE),
        ("memory.search",    memory_search,    SideEffectLevel.NONE),
        ("memory.citations", memory_citations, SideEffectLevel.NONE),
        ("memory.recall",    memory_recall,    SideEffectLevel.NONE),
        ("memory.write",     memory_write,     SideEffectLevel.REVERSIBLE),
    ):
        _registry_register(ToolEntry(
            name=_name,
            entrypoints=_TOOL_ENTRYPOINTS[_name],
            toolset="core",
            handler=_handler,
            schema=BaseModel,
            side_effect_level=_sel,
        ))
