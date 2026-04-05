"""Feature 067: Session 驱动统一记忆提取服务。

从 AgentSession 的新增 turns 中提取记忆并通过 propose-validate-commit 写入 SoR。
单次 LLM 调用提取 facts / solutions / entities / tom 四类记忆。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from octoagent.core.models.agent_context import (
    AgentSession,
    AgentSessionKind,
    AgentSessionTurn,
    AgentSessionTurnKind,
    MemoryNamespaceKind,
)
from octoagent.core.store.agent_context_store import SqliteAgentContextStore
from octoagent.memory import (
    SENSITIVE_PARTITIONS,
    EvidenceRef,
    MemoryMaintenanceCommand,
    MemoryMaintenanceCommandKind,
    MemoryPartition,
    MemoryService,
    WriteAction,
)
from octoagent.provider.dx.llm_common import LlmServiceProtocol, parse_llm_json_array

log = structlog.get_logger()

_PARTITION_MAP: dict[str, MemoryPartition] = {
    "work": MemoryPartition.WORK,
    "core": MemoryPartition.CORE,
    "profile": MemoryPartition.PROFILE,
    "chat": MemoryPartition.CHAT,
    "health": MemoryPartition.HEALTH,
    "solution": MemoryPartition.SOLUTION,
    "personal": MemoryPartition.PROFILE,  # LLM 常见别名
    "finance": MemoryPartition.FINANCE,
}

# 单次提取最多处理的 turn 数量（防止大量积压 turns 超出 LLM context window）
_MAX_TURNS_PER_EXTRACTION = 50

# Session kinds 白名单 -- 仅这些 kind 触发记忆提取
_EXTRACTABLE_SESSION_KINDS = frozenset({
    AgentSessionKind.MAIN_BOOTSTRAP,
    AgentSessionKind.BUTLER_MAIN,  # 历史兼容
    AgentSessionKind.WORKER_INTERNAL,
    AgentSessionKind.DIRECT_WORKER,
})

# LLM 提取 prompt
_EXTRACTION_SYSTEM_PROMPT = """\
你是一个记忆管理助手。从对话中提取以下类型的长期记忆：

1. facts — 用户偏好、个人事实、项目决策、关键结论
2. solutions — 问题-解决方案对（问题描述 + 解决步骤 + 适用条件）
3. entities — 人物、组织、项目等实体及其关系
4. tom — Theory of Mind 推理（用户的隐含需求、情绪状态、沟通风格）

输出 JSON 数组:
[
  {
    "type": "fact|solution|entity|tom",
    "subject_key": "主题/子主题",
    "content": "完整陈述句",
    "confidence": 0.8,
    "action": "add|update",
    "partition": "work|core|profile|chat|health|finance|solution",
    "problem": "（仅 solution）问题描述",
    "solution": "（仅 solution）解决方案",
    "context": "（仅 solution）适用条件",
    "entity_name": "（仅 entity）实体名称",
    "entity_type": "（仅 entity）person|org|project",
    "relations": [{"target": "...", "relation": "..."}],
    "inference": "（仅 tom）推理内容",
    "supporting_evidence": ["（仅 tom）证据"]
  }
]

规则:
- 只提取值得长期记忆的信息，跳过纯问答和闲聊
- 按语义边界拆分，每条记忆应独立且完整
- subject_key 使用 "主题/子主题" 格式，便于后续去重
- confidence 取值 0.5~1.0，表示信息的确定性
- 无值得记忆的内容时输出 []
"""


@dataclass(slots=True)
class ExtractionItem:
    """LLM 提取的单条记忆项。"""

    type: str  # "fact" | "solution" | "entity" | "tom"
    subject_key: str
    content: str
    confidence: float = 0.8
    action: str = "add"  # "add" | "update" | "merge" | "replace"
    partition: str = "work"
    # solution 特有字段
    problem: str = ""
    solution: str = ""
    context: str = ""
    # entity 特有字段
    entity_name: str = ""
    entity_type: str = ""
    relations: list[dict[str, str]] = field(default_factory=list)
    # tom 特有字段
    inference: str = ""
    supporting_evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SessionExtractionResult:
    """单次 Session 记忆提取的结果摘要。"""

    session_id: str
    scope_id: str
    turns_processed: int = 0
    new_cursor_seq: int = 0
    facts_committed: int = 0
    solutions_committed: int = 0
    entities_committed: int = 0
    tom_committed: int = 0
    fragments_created: int = 0
    skipped_reason: str = ""
    errors: list[str] = field(default_factory=list)


class SessionMemoryExtractor:
    """Session 驱动的统一记忆提取服务。

    单一入口，在 record_response_context 末尾 fire-and-forget 触发。
    """

    def __init__(
        self,
        agent_context_store: SqliteAgentContextStore,
        memory_service_factory: Any,  # callable(project) -> MemoryService
        llm_service: LlmServiceProtocol | None = None,
        project_root: Path | None = None,
        llm_service_resolver: Any | None = None,  # callable() -> LlmServiceProtocol | None
    ) -> None:
        self._agent_context_store = agent_context_store
        self._memory_service_factory = memory_service_factory
        self._llm_service = llm_service
        self._llm_service_resolver = llm_service_resolver
        self._project_root = project_root or Path.cwd()
        # per-Session asyncio.Lock 字典
        self._session_locks: dict[str, asyncio.Lock] = {}
        # 从配置读取记忆加工 model alias，对齐 Settings 页面"记忆加工"字段
        try:
            from octoagent.provider.dx.llm_common import resolve_default_model_alias

            self._model_alias = resolve_default_model_alias(project_root)
        except Exception:
            self._model_alias = "main"

    async def extract_and_commit(
        self,
        *,
        agent_session: AgentSession,
        project: Any | None,
    ) -> SessionExtractionResult:
        """从 Session 的新增 turns 中提取记忆并写入 SoR。

        全流程：
        1. 检查 session kind 白名单
        2. try-lock: 如果该 session 已有正在进行的提取，跳过
        3. 查询 turn_seq > memory_cursor_seq 的新增 turns
        4. 无新 turn 时直接返回
        5. 构建提取输入（压缩 tool calls）
        6. 调用 LLM 提取（single call, fast alias）
        7. 解析 LLM 输出为 ExtractionItem[]
        8. 通过 propose-validate-commit 写入 SoR
        9. 创建溯源 Fragment 并关联 SoR
        10. 更新 memory_cursor_seq

        内部捕获所有异常——LLM 不可用时静默降级，不影响调用方。
        """
        session_id = agent_session.agent_session_id
        result = SessionExtractionResult(session_id=session_id, scope_id="")

        try:
            # 1. 检查 session kind 白名单
            if agent_session.kind not in _EXTRACTABLE_SESSION_KINDS:
                result.skipped_reason = "unsupported_session_kind"
                log.debug(
                    "session_memory_extraction_skipped",
                    session_id=session_id,
                    reason=result.skipped_reason,
                )
                return result

            # 2. 检查 LLM 服务可用性（动态获取，避免 hot-reload 类变量丢失）
            llm_service = self._llm_service
            if llm_service is None and self._llm_service_resolver is not None:
                llm_service = self._llm_service_resolver()
                if llm_service is not None:
                    self._llm_service = llm_service  # 缓存成功获取的实例
            if llm_service is None:
                result.skipped_reason = "llm_unavailable"
                log.info(
                    "session_memory_extraction_skipped",
                    session_id=session_id,
                    reason=result.skipped_reason,
                )
                return result

            # 3. try-lock: 非阻塞获取 per-session 锁
            lock = self._session_locks.setdefault(session_id, asyncio.Lock())
            if lock.locked():
                result.skipped_reason = "extraction_in_progress"
                log.debug(
                    "session_memory_extraction_skipped",
                    session_id=session_id,
                    reason=result.skipped_reason,
                )
                return result

            async with lock:
                return await self._extract_under_lock(
                    agent_session=agent_session,
                    project=project,
                    result=result,
                )

        except Exception as exc:
            log.warning(
                "session_memory_extraction_failed",
                session_id=session_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            result.errors.append(f"{type(exc).__name__}: {exc}")
            return result

    async def _extract_under_lock(
        self,
        *,
        agent_session: AgentSession,
        project: Any | None,
        result: SessionExtractionResult,
    ) -> SessionExtractionResult:
        """在持有 session lock 的情况下执行提取。"""
        session_id = agent_session.agent_session_id
        cursor_before = agent_session.memory_cursor_seq

        # 4. 查询新增 turns（截断到最大限制，避免超出 LLM context window）
        all_new_turns = await self._agent_context_store.list_turns_after_seq(
            session_id, after_seq=cursor_before
        )
        if not all_new_turns:
            result.skipped_reason = "no_new_turns"
            log.debug(
                "session_memory_extraction_skipped",
                session_id=session_id,
                reason=result.skipped_reason,
            )
            return result

        # 截断：只取最近 N 个 turn，剩余的下次再处理
        new_turns = all_new_turns[-_MAX_TURNS_PER_EXTRACTION:]
        if len(all_new_turns) > _MAX_TURNS_PER_EXTRACTION:
            log.info(
                "session_memory_extraction_truncated",
                session_id=session_id,
                total_pending=len(all_new_turns),
                processing=len(new_turns),
            )

        # 5. 推导 scope_id
        scope_id = await self._resolve_scope_id(
            agent_session=agent_session,
            project=project,
        )
        if not scope_id:
            result.skipped_reason = "no_scope"
            log.info(
                "session_memory_extraction_skipped",
                session_id=session_id,
                reason=result.skipped_reason,
            )
            return result

        result.scope_id = scope_id

        # 合成 scope 自动注册到 namespace，使 UI 可发现
        if scope_id.startswith("memory/auto/") and agent_session.project_id:
            await self._ensure_auto_scope_registered(scope_id, agent_session.project_id)

        result.turns_processed = len(new_turns)
        max_turn_seq = max(t.turn_seq for t in new_turns)

        log.info(
            "session_memory_extraction_started",
            session_id=session_id,
            scope_id=scope_id,
            cursor_before=cursor_before,
            new_turns_count=len(new_turns),
        )

        # 6. 构建提取输入
        extraction_input = self._build_extraction_input(new_turns)

        # 7. 调用 LLM
        assert self._llm_service is not None
        try:
            llm_response = await self._llm_service.call(
                [
                    {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"以下是最近的对话内容（{len(new_turns)} 轮）：\n\n{extraction_input}",
                    },
                ],
                model_alias=self._model_alias,
                # 关闭 thinking 模式——Qwen3 等模型开启 thinking 时 content 为空，
                # JSON 输出会跑到 reasoning_content 里导致提取失败
                extra_body={"enable_thinking": False},
            )
        except Exception as exc:
            log.error(
                "session_memory_extraction_llm_failed",
                session_id=session_id,
                scope_id=scope_id,
                model_alias=self._model_alias,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            result.errors.append(f"llm_failed: {type(exc).__name__}: {exc}")
            # LLM 失败也推进 cursor，防止同一批 turns 反复重试导致永久卡住
            await self._agent_context_store.update_memory_cursor(session_id, max_turn_seq)
            result.new_cursor_seq = max_turn_seq
            return result

        # 提取文本内容（兼容 thinking 模型——content 可能为空，JSON 在 reasoning_content 里）
        raw_text = ""
        if isinstance(llm_response, str):
            raw_text = llm_response
        elif hasattr(llm_response, "content"):
            raw_text = str(llm_response.content) if llm_response.content else ""
            # Qwen3 等 thinking 模型：content 为空时尝试从 reasoning_content 提取
            if not raw_text and hasattr(llm_response, "reasoning_content"):
                raw_text = str(llm_response.reasoning_content) if llm_response.reasoning_content else ""
        elif hasattr(llm_response, "choices"):
            choices = llm_response.choices
            if choices and hasattr(choices[0], "message"):
                msg = choices[0].message
                raw_text = str(msg.content or "")
                # 同上：thinking 模型 fallback
                if not raw_text and hasattr(msg, "reasoning_content"):
                    raw_text = str(msg.reasoning_content or "")
                if not raw_text:
                    # provider_specific_fields fallback
                    psf = getattr(msg, "provider_specific_fields", None) or {}
                    if isinstance(psf, dict):
                        raw_text = str(psf.get("reasoning_content", "") or "")

        # 8. 解析输出
        items = self._parse_extraction_output(raw_text)
        if items is None:
            log.warning(
                "session_memory_extraction_parse_failed",
                session_id=session_id,
                scope_id=scope_id,
                response_preview=raw_text[:200] if raw_text else "(empty)",
            )
            result.errors.append("parse_failed")
            # parse 失败也推进 cursor，防止永久卡住
            await self._agent_context_store.update_memory_cursor(session_id, max_turn_seq)
            result.new_cursor_seq = max_turn_seq
            return result

        # 空结果 -- LLM 判断无值得记忆的内容
        if not items:
            result.new_cursor_seq = max_turn_seq
            await self._agent_context_store.update_memory_cursor(session_id, max_turn_seq)
            log.info(
                "session_memory_extraction_completed",
                session_id=session_id,
                scope_id=scope_id,
                cursor_after=max_turn_seq,
                facts=0,
                solutions=0,
                entities=0,
                tom=0,
                fragments=0,
            )
            return result

        # 9. 通过 propose-validate-commit 写入 SoR
        memory_service = await self._memory_service_factory(
            project=project,
        )
        committed_sor_ids: list[str] = []
        facts, solutions, entities, tom = await self._commit_extractions(
            items=items,
            scope_id=scope_id,
            memory_service=memory_service,
            committed_sor_ids=committed_sor_ids,
            errors=result.errors,
        )
        result.facts_committed = facts
        result.solutions_committed = solutions
        result.entities_committed = entities
        result.tom_committed = tom

        # 10. 创建溯源 Fragment
        if committed_sor_ids:
            try:
                from ulid import ULID
                frag_run = await memory_service.run_memory_maintenance(
                    MemoryMaintenanceCommand(
                        command_id=str(ULID()),
                        kind=MemoryMaintenanceCommandKind.FLUSH,
                        scope_id=scope_id,
                        partition=MemoryPartition.WORK,
                        reason="session_memory_extraction_evidence",
                        requested_by=f"session_memory_extractor:{session_id}",
                        idempotency_key=f"session_extract:{session_id}:cursor:{max_turn_seq}",
                        summary=extraction_input[:2000],
                        evidence_refs=[
                            EvidenceRef(
                                ref_id=session_id,
                                ref_type="agent_session",
                                snippet=f"turns {cursor_before+1}-{max_turn_seq}",
                            ),
                        ],
                        metadata={
                            "source": "session_memory_extractor",
                            "session_id": session_id,
                            "scope_id": scope_id,
                            "evidence_for_sor_ids": committed_sor_ids,
                            "cursor_before": cursor_before,
                            "cursor_after": max_turn_seq,
                        },
                    )
                )
                result.fragments_created = len(frag_run.fragment_refs)
            except Exception as exc:
                log.warning(
                    "session_memory_extraction_fragment_failed",
                    session_id=session_id,
                    error=str(exc),
                )
                result.errors.append(f"fragment_failed: {exc}")

        # 11. 更新 cursor -- 仅在所有写入成功后推进
        result.new_cursor_seq = max_turn_seq
        await self._agent_context_store.update_memory_cursor(session_id, max_turn_seq)

        log.info(
            "session_memory_extraction_completed",
            session_id=session_id,
            scope_id=scope_id,
            cursor_after=max_turn_seq,
            facts=facts,
            solutions=solutions,
            entities=entities,
            tom=tom,
            fragments=result.fragments_created,
        )

        return result

    @staticmethod
    def _build_extraction_input(turns: list[AgentSessionTurn]) -> str:
        """将 turns 格式化为文本，Tool Call 类型 turn 压缩为摘要。"""
        lines: list[str] = []
        for turn in turns:
            if turn.kind in (
                AgentSessionTurnKind.TOOL_CALL,
                AgentSessionTurnKind.TOOL_RESULT,
            ):
                # 压缩 tool call/result
                tool_name = turn.tool_name or "unknown_tool"
                summary = turn.summary.strip() if turn.summary else "(no summary)"
                lines.append(f"[Tool: {tool_name}] {summary}")
            elif turn.kind == AgentSessionTurnKind.CONTEXT_SUMMARY:
                # 上下文摘要 -- 保留但标记来源
                lines.append(f"[Context Summary] {turn.summary or ''}")
            else:
                # user / assistant 消息
                role = turn.role or turn.kind.value
                content = turn.summary or ""
                lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    @staticmethod
    def _parse_extraction_output(raw_response: str) -> list[ExtractionItem] | None:
        """解析 LLM 输出 JSON 数组为 ExtractionItem 列表。

        Returns:
            ExtractionItem 列表，格式异常时返回 None。
        """
        parsed = parse_llm_json_array(raw_response)
        if parsed is None:
            return None

        items: list[ExtractionItem] = []
        for raw in parsed:
            if not isinstance(raw, dict):
                continue
            item_type = str(raw.get("type", "")).strip()
            subject_key = str(raw.get("subject_key", "")).strip()
            content = str(raw.get("content", "")).strip()
            if not item_type or not content:
                continue

            items.append(
                ExtractionItem(
                    type=item_type,
                    subject_key=subject_key or content[:50],
                    content=content,
                    confidence=float(raw.get("confidence", 0.8)),
                    action=str(raw.get("action", "add")),
                    partition=str(raw.get("partition", "work")),
                    problem=str(raw.get("problem", "")),
                    solution=str(raw.get("solution", "")),
                    context=str(raw.get("context", "")),
                    entity_name=str(raw.get("entity_name", "")),
                    entity_type=str(raw.get("entity_type", "")),
                    relations=raw.get("relations", []) or [],
                    inference=str(raw.get("inference", "")),
                    supporting_evidence=raw.get("supporting_evidence", []) or [],
                )
            )
        return items

    async def _commit_extractions(
        self,
        *,
        items: list[ExtractionItem],
        scope_id: str,
        memory_service: MemoryService,
        committed_sor_ids: list[str],
        errors: list[str],
    ) -> tuple[int, int, int, int]:
        """逐条通过 propose-validate-commit 写入 SoR。

        Returns:
            (facts, solutions, entities, tom) 各类型提交计数。
        """
        counts = {"fact": 0, "solution": 0, "entity": 0, "tom": 0}

        for item in items:
            try:
                partition = _PARTITION_MAP.get(item.partition, MemoryPartition.WORK)

                # 将 action 字符串映射为 WriteAction 枚举
                action_map = {
                    "add": WriteAction.ADD,
                    "update": WriteAction.UPDATE,
                    "merge": WriteAction.MERGE,
                    "replace": WriteAction.UPDATE,
                }
                write_action = action_map.get(item.action, WriteAction.ADD)

                # 构建记忆内容
                content = item.content
                if item.type == "solution" and item.problem:
                    content = f"问题: {item.problem}\n解决方案: {item.solution or item.content}\n条件: {item.context}" if item.solution else content
                elif item.type == "entity" and item.entity_name:
                    relations_str = ", ".join(
                        f"{r.get('target', '')}({r.get('relation', '')})"
                        for r in item.relations
                        if r.get("target")
                    )
                    content = f"{item.entity_name} ({item.entity_type}): {item.content}"
                    if relations_str:
                        content += f" [关系: {relations_str}]"
                elif item.type == "tom" and item.inference:
                    content = f"推理: {item.inference}\n证据: {', '.join(item.supporting_evidence)}" if item.supporting_evidence else f"推理: {item.inference}"

                # 快速路径：低风险自动提取使用 fast_commit
                if (
                    write_action == WriteAction.ADD
                    and item.confidence >= 0.75
                    and partition not in SENSITIVE_PARTITIONS
                ):
                    commit_result = await memory_service.fast_commit(
                        scope_id=scope_id,
                        partition=partition,
                        action=write_action,
                        subject_key=item.subject_key,
                        content=content,
                        confidence=item.confidence,
                        evidence_refs=[],
                        metadata={
                            "source": "session_memory_extractor",
                            "extraction_type": item.type,
                        },
                    )
                    if commit_result.sor_id:
                        committed_sor_ids.append(commit_result.sor_id)
                    counts[item.type] = counts.get(item.type, 0) + 1
                else:
                    # 完整治理路径
                    proposal = await memory_service.propose_write(
                        scope_id=scope_id,
                        partition=partition,
                        action=write_action,
                        subject_key=item.subject_key,
                        content=content,
                        rationale="session_memory_extraction",
                        confidence=item.confidence,
                        evidence_refs=[],
                        metadata={
                            "source": "session_memory_extractor",
                            "extraction_type": item.type,
                        },
                    )

                    # validate
                    validation = await memory_service.validate_proposal(proposal.proposal_id)

                    if validation.accepted:
                        # commit
                        commit_result = await memory_service.commit_memory(proposal.proposal_id)
                        if commit_result.sor_id:
                            committed_sor_ids.append(commit_result.sor_id)
                        counts[item.type] = counts.get(item.type, 0) + 1
                    else:
                        # ADD 被拒（已存在）时尝试 UPDATE
                        if write_action == WriteAction.ADD and any(
                            "已存在" in e or "current" in e for e in validation.errors
                        ):
                            try:
                                update_proposal = await memory_service.propose_write(
                                    scope_id=scope_id,
                                    partition=partition,
                                    action=WriteAction.UPDATE,
                                    subject_key=item.subject_key,
                                    content=content,
                                    rationale="session_memory_extraction (fallback update)",
                                    confidence=item.confidence,
                                    evidence_refs=[],
                                    metadata={
                                        "source": "session_memory_extractor",
                                        "extraction_type": item.type,
                                        "fallback": "add_to_update",
                                    },
                                )
                                update_validation = await memory_service.validate_proposal(
                                    update_proposal.proposal_id
                                )
                                if update_validation.accepted:
                                    commit_result = await memory_service.commit_memory(
                                        update_proposal.proposal_id
                                    )
                                    if commit_result.sor_id:
                                        committed_sor_ids.append(commit_result.sor_id)
                                    counts[item.type] = counts.get(item.type, 0) + 1
                            except Exception:
                                pass  # 降级场景，不阻塞其他条目

            except Exception as exc:
                errors.append(f"commit_failed[{item.subject_key}]: {type(exc).__name__}: {exc}")

        return counts.get("fact", 0), counts.get("solution", 0), counts.get("entity", 0), counts.get("tom", 0)

    async def _ensure_auto_scope_registered(
        self, scope_id: str, project_id: str
    ) -> None:
        """确保合成 scope 在 memory_namespaces 中有注册记录，使 UI 能发现它。"""
        namespace_id = f"memory_namespace:auto|project:{project_id}"
        try:
            existing = await self._agent_context_store.get_memory_namespace(namespace_id)
            if existing is not None:
                return
            # 注册新 namespace
            from octoagent.core.models.agent_context import MemoryNamespace

            ns = MemoryNamespace(
                namespace_id=namespace_id,
                project_id=project_id,
                agent_runtime_id="",
                kind=MemoryNamespaceKind.PROJECT_SHARED,
                memory_scope_ids=[scope_id],
            )
            await self._agent_context_store.save_memory_namespace(ns)
            log.info("auto_scope_registered", scope_id=scope_id, project_id=project_id)
        except Exception as exc:
            log.debug("auto_scope_registration_failed", error=str(exc))

    async def _resolve_scope_id(
        self,
        *,
        agent_session: AgentSession,
        project: Any | None,
    ) -> str | None:
        """从 AgentSession 推导目标 scope_id。

        复用 AgentContextService 中的 namespace -> scope 计算逻辑。
        """
        # 尝试通过最近的 context_frame 获取 namespace
        if agent_session.last_context_frame_id:
            frame = await self._agent_context_store.get_context_frame(
                agent_session.last_context_frame_id
            )
            if frame is not None:
                for namespace_id in frame.memory_namespace_ids:
                    namespace = await self._agent_context_store.get_memory_namespace(
                        namespace_id
                    )
                    if namespace is not None and namespace.kind is MemoryNamespaceKind.PROJECT_SHARED:
                        # 优先使用 runtime_private scope
                        for scope_id in namespace.memory_scope_ids:
                            if "/runtime:" in scope_id:
                                return scope_id
                        # 其次 session_private
                        for scope_id in namespace.memory_scope_ids:
                            if "/session:" in scope_id:
                                return scope_id
                        # 最后 namespace primary
                        if namespace.memory_scope_ids:
                            return namespace.memory_scope_ids[0]

        # 降级 1: 尝试通过 project_id 查找 namespace
        if agent_session.project_id:
            namespaces = await self._agent_context_store.list_memory_namespaces(
                project_id=agent_session.project_id,
                kind=MemoryNamespaceKind.PROJECT_SHARED,
            )
            for ns in namespaces:
                if ns.memory_scope_ids:
                    return ns.memory_scope_ids[0]

        # 降级 2: 扫描所有 project_shared namespace（新 project 可能还没创建 namespace）
        try:
            all_namespaces = await self._agent_context_store.list_memory_namespaces(
                kind=MemoryNamespaceKind.PROJECT_SHARED,
            )
            for ns in all_namespaces:
                for scope_id in ns.memory_scope_ids:
                    if "/runtime:" in scope_id:
                        return scope_id
                if ns.memory_scope_ids:
                    return ns.memory_scope_ids[0]
        except Exception:
            pass

        # 降级 3: 合成一个基于 project_id 的 scope_id
        if agent_session.project_id:
            return f"memory/auto/{agent_session.project_id}"

        return None
