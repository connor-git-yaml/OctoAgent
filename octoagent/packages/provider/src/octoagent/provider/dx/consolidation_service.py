"""Feature 065: 独立的 ConsolidationService -- 将 Fragment 整合为 SoR 事实记录。

从 MemoryConsoleService._consolidate_scope 提取的核心逻辑，
三个入口共享：管理台手动 / Flush 后异步 / Scheduler 定期。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.memory import (
    EvidenceRef,
    FragmentRecord,
    MemoryPartition,
    MemoryService,
    SqliteMemoryStore,
    WriteAction,
)

from .llm_common import LlmServiceProtocol, parse_llm_json_array, resolve_default_model_alias

# 单次 consolidate 操作的 Fragment 批量上限
_MAX_FRAGMENTS_PER_BATCH: int = 200

_log = structlog.get_logger()


# ---------------------------------------------------------------------------
# 返回值数据模型
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CommittedSorInfo:
    """Consolidate 产出的 SoR 摘要信息，供 derived 提取用。"""

    memory_id: str
    subject_key: str
    content: str
    partition: MemoryPartition
    source_fragment_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DerivedExtractionResult:
    """Derived 提取结果。"""

    scope_id: str
    extracted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConsolidationScopeResult:
    """单个 scope 的 consolidate 结果。"""

    scope_id: str
    consolidated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    derived_extracted: int = 0  # Phase 2: Derived 记录提取数
    tom_extracted: int = 0  # Phase 3: ToM 推理产出数


@dataclass(slots=True)
class ConsolidationBatchResult:
    """批量 consolidate 的汇总结果。"""

    results: list[ConsolidationScopeResult] = field(default_factory=list)
    total_consolidated: int = 0
    total_skipped: int = 0
    all_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ConsolidationService
# ---------------------------------------------------------------------------

_CONSOLIDATE_SYSTEM_PROMPT = """\
你是一个记忆整理助手。你的任务是从一组对话摘要片段中提取出持久有价值的结构化事实。

## 提取维度（全生活域覆盖）

你应主动识别并提取以下维度的信息，不限于工作和技术：

- **人物关系**：家人、朋友、同事、合作伙伴等（姓名、称呼、关系、特征）
- **家庭事件**：生日、纪念日、搬家、婚丧嫁娶等重要事件
- **情感状态**：用户当前或长期的情绪倾向、心理状态、压力来源
- **健康信息**：运动习惯、饮食偏好、身体状况、就医记录
- **消费习惯**：购物偏好、品牌忠诚、预算范围、消费决策
- **技术选型**：编程语言、框架、工具链、架构偏好
- **项目决策**：技术方案、设计选择、里程碑、优先级
- **生活习惯**：作息、通勤方式、居住地、日常安排
- **兴趣爱好**：运动、音乐、阅读、游戏、旅行等
- **日程安排**：重要计划、截止日期、定期活动
- **职业信息**：职位、公司、行业、职业目标
- **学习记录**：正在学习的技能、课程、认证计划

当对话不包含某些维度时，不要强制生成空 SoR——只提取实际出现的信息。

## 信息主体归因规则

**关键规则**：当对话中 A 提到关于 B 的信息时，subject_key 的信息主体应指向 B，而非 A。

例如：
- "我妈妈喜欢园艺" → subject_key 应为 `妈妈/兴趣爱好`，而非 `用户偏好/妈妈`
- "同事张三擅长 Go 语言" → subject_key 应为 `人物/张三/技术能力`
- "女朋友下周生日" → subject_key 应为 `人物/女朋友/生日`

## 提取规则

1. **提取事实，不是操作记录**
   - 保留：用户偏好、项目决策、人物关系、重要结论、知识点、生活事实
   - 过滤：纯操作性内容（"修了一个 bug"、"运行了测试"）、临时状态（"正在等待回复"）

2. **subject_key 命名规范**
   - 用 `/` 分层，如 `用户偏好/编程语言`、`项目/OctoAgent/架构决策`、`人物/妈妈/兴趣爱好`
   - 简短明确，同一主题的事实用相同 key

3. **去重合并**
   - 多个片段包含相同或相近信息时，合并为一条更完整的表述

4. **confidence 评估**
   - 1.0：直接明确陈述的事实
   - 0.7-0.9：多次提及、可靠推断
   - 0.5-0.6：仅出现一次或推断较弱

5. **写入策略（action 字段）**
   对于每条事实，你可以指定以下策略之一：
   - `"add"`: 新增全新事实（默认，subject_key 不存在时）
   - `"update"`: 更新已有事实（subject_key 已存在，信息有补充或修正）
   - `"merge"`: 合并多条高度相关的已有事实为一条综合记忆。需额外提供 `merge_source_ids`（被合并的已有 SoR 的 subject_key 列表）。例如三条关于编程语言偏好的记忆合并为一条综合表述
   - `"replace"`: 替换已有事实（新信息与旧信息语义矛盾，如"搬到了新城市"替换"住在旧城市"）
   不确定时默认使用 `"add"` 或 `"update"`。

6. **输出格式**
   必须输出一个 JSON 数组，不要输出其他内容：
   ```json
   [
     {
       "subject_key": "主题/子主题",
       "content": "完整的陈述句",
       "confidence": 0.8,
       "source_fragment_ids": ["frag-id-1", "frag-id-2"],
       "action": "add",
       "merge_source_ids": []
     }
   ]
   ```
   - `action` 字段可选，默认为 `"add"` 或 `"update"`（由系统根据 subject_key 是否存在判断）
   - `merge_source_ids` 仅在 `action="merge"` 时需要提供
   如果没有可提取的有价值事实，输出空数组 `[]`。
"""


# T058: Solution 检测 prompt
_SOLUTION_EXTRACTION_PROMPT = """\
你是一个解决方案识别助手。你的任务是从一组已整理的事实记录中识别"问题-解决方案"模式。

## 规则

1. **识别 problem-solution 模式**
   - 寻找描述了"遇到问题 + 找到解决方案"的记录
   - 典型场景：调试错误、配置问题、性能优化、流程改进
   - 只提取已确认有效的解决方案，不要提取正在探索中的尝试

2. **输出格式**
   输出 JSON 数组，每条包含：
   ```json
   [
     {
       "problem": "问题的清晰描述",
       "solution": "解决步骤或方法",
       "context": "适用条件和限制（什么情况下该方案有效）",
       "subject_key": "solution/问题领域/简短标题",
       "source_subject_keys": ["来源的 subject_key"]
     }
   ]
   ```

3. **不强制生成**
   - 如果事实记录中没有明显的 problem-solution 模式，输出空数组 `[]`
   - 不要将普通偏好或决策强行包装为 solution
"""


class ConsolidationService:
    """将未整理的 Fragment 通过 LLM 分析提取为 SoR 事实记录。

    三个入口共享此服务：
    - 管理台手动触发（通过 MemoryConsoleService.run_consolidate）
    - Flush 后异步触发（通过 TaskService._auto_consolidate_after_flush）
    - Scheduler 定时触发（通过 ControlPlaneService._handle_memory_consolidate）
    """

    def __init__(
        self,
        memory_store: SqliteMemoryStore,
        llm_service: LlmServiceProtocol | None,
        project_root: Path,
        derived_extraction_service: Any | None = None,  # Phase 2: DerivedExtractionService
        tom_extraction_service: Any | None = None,  # Phase 3: ToMExtractionService
    ) -> None:
        self._memory_store = memory_store
        self._llm_service = llm_service
        self._project_root = project_root
        self._derived_service = derived_extraction_service
        self._tom_service = tom_extraction_service

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    async def consolidate_scope(
        self,
        *,
        memory: MemoryService,
        scope_id: str,
        model_alias: str = "",
        fragment_filter: Callable[[FragmentRecord], bool] | None = None,
    ) -> ConsolidationScopeResult:
        """对单个 scope 下的未整理 Fragment 执行 LLM 整合。

        Args:
            memory: 目标 scope 的 MemoryService 实例
            scope_id: 要处理的 scope ID
            model_alias: LLM 模型别名（空字符串则读取 config 默认值 ``main``）
            fragment_filter: 可选过滤函数，用于进一步筛选要处理的 Fragment
        """
        if self._llm_service is None:
            _log.warning("consolidation_no_llm", scope_id=scope_id)
            return ConsolidationScopeResult(
                scope_id=scope_id,
                errors=["LLM 服务未配置"],
            )

        # 1. 读取所有 fragment
        fragments = await self._memory_store.list_fragments(scope_id, query=None, limit=_MAX_FRAGMENTS_PER_BATCH)
        if not fragments:
            return ConsolidationScopeResult(scope_id=scope_id)

        # 2. 排除已整理过的（metadata 中有 consolidated_at 标记）
        pending = [f for f in fragments if not f.metadata.get("consolidated_at")]

        # 3. 若提供 fragment_filter，进一步过滤
        if fragment_filter is not None:
            pending = [f for f in pending if fragment_filter(f)]

        if not pending:
            return ConsolidationScopeResult(scope_id=scope_id)

        # 4. 读取已有 SoR，供 LLM 参考去重 + UPDATE 时取 version
        existing_sor = await self._memory_store.search_sor(
            scope_id, query=None, include_history=False, limit=500,
        )
        existing_sor_map: dict[str, Any] = {s.subject_key: s for s in existing_sor}
        existing_keys = set(existing_sor_map.keys())

        # 5. 构建 LLM 请求
        fragment_texts = []
        for f in pending:
            fragment_texts.append(f"[{f.fragment_id}] ({f.partition.value}) {f.content}")
        user_content = "以下是待整理的记忆片段：\n\n" + "\n\n".join(fragment_texts)
        if existing_keys:
            user_content += "\n\n已有的事实主题（请避免重复）：\n" + "\n".join(
                f"- {k}" for k in sorted(existing_keys)
            )

        messages = [
            {"role": "system", "content": _CONSOLIDATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        # 6. 调用 LLM
        resolved_alias = model_alias or self._resolve_default_model_alias()
        try:
            result = await self._llm_service.call_with_fallback(
                messages=messages,
                model_alias=resolved_alias,
                temperature=0.3,
                max_tokens=4096,
            )
            response_text = result.content.strip()
        except Exception as exc:
            _log.warning(
                "consolidation_llm_call_failed",
                scope_id=scope_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return ConsolidationScopeResult(
                scope_id=scope_id,
                skipped=len(pending),
                errors=[f"LLM 调用失败: {exc}"],
            )

        # 7. 解析 LLM 输出
        facts = parse_llm_json_array(response_text)
        if facts is None:
            _log.warning(
                "consolidation_parse_failed",
                scope_id=scope_id,
                response=response_text[:200],
            )
            return ConsolidationScopeResult(
                scope_id=scope_id,
                skipped=len(pending),
                errors=["LLM 输出格式错误，无法解析"],
            )

        # 8. 为每个事实创建 SoR
        consolidated = 0
        skipped = 0
        errors: list[str] = []
        fragment_map = {f.fragment_id: f for f in pending}
        consolidated_fragment_ids: set[str] = set()
        committed_sors: list[CommittedSorInfo] = []  # Phase 2: 收集已 commit 的 SoR 信息

        for fact in facts:
            subject_key = fact.get("subject_key", "").strip()
            content = fact.get("content", "").strip()
            confidence = float(fact.get("confidence", 0.7))
            source_ids = fact.get("source_fragment_ids", [])

            if not subject_key or not content:
                skipped += 1
                continue

            # 构建 evidence_refs 从 source fragment
            evidence_refs = []
            for fid in source_ids:
                if fid in fragment_map:
                    evidence_refs.append(EvidenceRef(ref_id=fid, ref_type="fragment"))
                    consolidated_fragment_ids.add(fid)
            if not evidence_refs:
                # LLM 没给有效的 source_id，用第一个 pending fragment
                evidence_refs = [EvidenceRef(ref_id=pending[0].fragment_id, ref_type="fragment")]
                consolidated_fragment_ids.add(pending[0].fragment_id)

            # 推断 partition：从 source fragment 中取众数
            partitions = [fragment_map[fid].partition for fid in source_ids if fid in fragment_map]
            partition = max(set(partitions), key=partitions.count) if partitions else MemoryPartition.WORK

            # T051-T055: 判断写入策略（ADD / UPDATE / MERGE / REPLACE）
            llm_action = fact.get("action", "").strip().lower()
            merge_source_keys = fact.get("merge_source_ids", [])  # subject_key 列表

            existing_sor_for_key = existing_sor_map.get(subject_key)

            # T055: 回退安全——LLM 未输出 action 字段时使用默认逻辑
            if llm_action == "merge" and merge_source_keys:
                action = WriteAction.MERGE
                expected_version = None
                # MERGE: 收集被合并的源 SoR memory_id
                merge_source_memory_ids = []
                for src_key in merge_source_keys:
                    src_sor = existing_sor_map.get(src_key)
                    if src_sor:
                        merge_source_memory_ids.append(src_sor.memory_id)
                        # 将源 SoR 的 evidence_refs 也加入
                        for er in (src_sor.evidence_refs or []):
                            if er not in evidence_refs:
                                evidence_refs.append(er)
                metadata = {
                    "source": "consolidate",
                    "merge_source_ids": merge_source_memory_ids,
                }
            elif llm_action == "replace":
                # T053: REPLACE 复用 UPDATE 流程
                action = WriteAction.UPDATE
                expected_version = existing_sor_for_key.version if existing_sor_for_key else None
                metadata = {"source": "consolidate", "reason": "replace"}
            elif existing_sor_for_key:
                action = WriteAction.UPDATE
                expected_version = existing_sor_for_key.version
                metadata = {"source": "consolidate"}
            else:
                action = WriteAction.ADD
                expected_version = None
                metadata = {"source": "consolidate"}

            try:
                proposal = await memory.propose_write(
                    scope_id=scope_id,
                    partition=partition,
                    action=action,
                    subject_key=subject_key,
                    content=content,
                    rationale="memory consolidation",
                    confidence=confidence,
                    evidence_refs=evidence_refs,
                    expected_version=expected_version,
                    metadata=metadata,
                )
                validation = await memory.validate_proposal(proposal.proposal_id)
                if validation.accepted:
                    await memory.commit_memory(proposal.proposal_id)
                    consolidated += 1
                    existing_keys.add(subject_key)
                    # Phase 2: 收集 committed SoR 信息供 derived 提取
                    committed_sors.append(CommittedSorInfo(
                        memory_id=proposal.target_memory_id if hasattr(proposal, 'target_memory_id') else proposal.proposal_id,
                        subject_key=subject_key,
                        content=content,
                        partition=partition,
                        source_fragment_ids=[e.ref_id for e in evidence_refs if e.ref_type == "fragment"],
                    ))
                    _log.info(
                        "consolidation_fact_committed",
                        scope_id=scope_id,
                        subject_key=subject_key,
                        action=action.value,
                    )
                else:
                    skipped += 1
                    _log.info(
                        "consolidation_proposal_rejected",
                        scope_id=scope_id,
                        subject_key=subject_key,
                        errors=validation.errors,
                    )
            except Exception as exc:
                skipped += 1
                errors.append(f"写入 '{subject_key}' 失败: {exc}")
                _log.warning(
                    "consolidation_commit_failed",
                    scope_id=scope_id,
                    subject_key=subject_key,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        # 8.25 Phase 1.5: Solution 检测（T057-T059）
        solution_extracted = 0
        if committed_sors and self._llm_service is not None:
            try:
                solution_extracted = await self._extract_solutions(
                    memory=memory,
                    scope_id=scope_id,
                    committed_sors=committed_sors,
                    model_alias=resolved_alias,
                )
            except Exception as exc:
                _log.warning(
                    "consolidation_solution_extraction_failed",
                    scope_id=scope_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        # 8.5 Phase 2: Derived Memory 自动提取 (best-effort)
        derived_extracted = 0
        if self._derived_service and committed_sors:
            try:
                # 推断 partition 用于 derived 提取
                _partition = committed_sors[0].partition if committed_sors else MemoryPartition.WORK
                derived_result = await self._derived_service.extract_from_sors(
                    scope_id=scope_id,
                    partition=_partition,
                    committed_sors=committed_sors,
                    model_alias=resolved_alias,
                )
                derived_extracted = derived_result.extracted
                _log.info(
                    "consolidation_derived_extraction",
                    scope_id=scope_id,
                    extracted=derived_result.extracted,
                    errors=derived_result.errors[:3],
                )
            except Exception as exc:
                _log.warning(
                    "consolidation_derived_extraction_failed",
                    scope_id=scope_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        # 8.6 Phase 3: Theory of Mind 推理 (best-effort)
        tom_extracted = 0
        if self._tom_service and committed_sors:
            try:
                _partition_tom = committed_sors[0].partition if committed_sors else MemoryPartition.WORK
                tom_result = await self._tom_service.extract_tom(
                    scope_id=scope_id,
                    partition=_partition_tom,
                    committed_sors=committed_sors,
                    model_alias=resolved_alias,
                )
                tom_extracted = tom_result.extracted
                _log.info(
                    "consolidation_tom_extraction",
                    scope_id=scope_id,
                    extracted=tom_result.extracted,
                    errors=tom_result.errors[:3],
                )
            except Exception as exc:
                _log.warning(
                    "consolidation_tom_extraction_failed",
                    scope_id=scope_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        # 9. 标记已整理的 fragment
        now_str = datetime.now(UTC).isoformat()
        for fid in consolidated_fragment_ids:
            frag = fragment_map.get(fid)
            if frag:
                try:
                    updated_meta = {**frag.metadata, "consolidated_at": now_str}
                    await self._memory_store.update_fragment_metadata(fid, updated_meta)
                except Exception as exc:
                    _log.warning(
                        "consolidation_fragment_mark_failed",
                        fragment_id=fid,
                        error=str(exc),
                    )

        _log.info(
            "consolidation_scope_complete",
            scope_id=scope_id,
            consolidated=consolidated,
            skipped=skipped,
            error_count=len(errors),
        )
        return ConsolidationScopeResult(
            scope_id=scope_id,
            consolidated=consolidated,
            skipped=skipped,
            errors=errors,
            derived_extracted=derived_extracted,
            tom_extracted=tom_extracted,
        )

    async def consolidate_by_run_id(
        self,
        *,
        memory: MemoryService,
        scope_id: str,
        run_id: str,
        model_alias: str = "",
    ) -> ConsolidationScopeResult:
        """Flush 后即时 Consolidate -- 仅处理指定 run_id 关联的 Fragment。

        通过 fragment metadata 中的 ``maintenance_run_id`` 匹配 run_id。
        若无匹配的 Fragment，返回空结果（不处理其他 Fragment）。
        """

        def _run_id_filter(fragment: FragmentRecord) -> bool:
            meta = fragment.metadata or {}
            return meta.get("maintenance_run_id") == run_id

        return await self.consolidate_scope(
            memory=memory,
            scope_id=scope_id,
            model_alias=model_alias,
            fragment_filter=_run_id_filter,
        )

    async def consolidate_all_pending(
        self,
        *,
        memory: MemoryService,
        scope_ids: list[str],
        model_alias: str = "",
    ) -> ConsolidationBatchResult:
        """Scheduler 定期 Consolidate -- 处理所有指定 scope 下的未整理 Fragment。

        逐 scope 调用 consolidate_scope，单个 scope 失败不影响其他 scope。
        """
        results: list[ConsolidationScopeResult] = []
        total_consolidated = 0
        total_skipped = 0
        all_errors: list[str] = []

        for scope_id in scope_ids:
            try:
                result = await self.consolidate_scope(
                    memory=memory,
                    scope_id=scope_id,
                    model_alias=model_alias,
                )
                results.append(result)
                total_consolidated += result.consolidated
                total_skipped += result.skipped
                all_errors.extend(result.errors)
            except Exception as exc:
                _log.warning(
                    "consolidation_scope_failed",
                    scope_id=scope_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                results.append(
                    ConsolidationScopeResult(
                        scope_id=scope_id,
                        errors=[f"scope 处理失败: {exc}"],
                    )
                )
                all_errors.append(f"scope {scope_id} 处理失败: {exc}")

        return ConsolidationBatchResult(
            results=results,
            total_consolidated=total_consolidated,
            total_skipped=total_skipped,
            all_errors=all_errors,
        )

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    async def _extract_solutions(
        self,
        *,
        memory: MemoryService,
        scope_id: str,
        committed_sors: list[CommittedSorInfo],
        model_alias: str,
    ) -> int:
        """T057-T059: Phase 1.5 — 从已提交的 SoR 中检测 problem-solution 模式并写入 SOLUTION 分区。"""
        if not committed_sors or self._llm_service is None:
            return 0

        # heuristic 前置过滤：内容中无 problem-solution 信号词时跳过 LLM 调用
        _SOLUTION_SIGNALS = {"bug", "修复", "解决", "问题", "error", "fix", "workaround", "配置", "报错", "异常", "failed", "timeout"}
        all_text = " ".join(s.content.lower() for s in committed_sors)
        if not any(signal in all_text for signal in _SOLUTION_SIGNALS):
            return 0

        # 构建 LLM 请求
        sor_texts = []
        for sor in committed_sors:
            sor_texts.append(f"[{sor.subject_key}] {sor.content}")
        user_content = "以下是本次整理出的事实记录，请识别其中的 problem-solution 模式：\n\n" + "\n\n".join(sor_texts)

        messages = [
            {"role": "system", "content": _SOLUTION_EXTRACTION_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            result = await self._llm_service.call_with_fallback(
                messages=messages,
                model_alias=model_alias,
                temperature=0.2,
                max_tokens=2048,
            )
            response_text = result.content.strip()
        except Exception as exc:
            _log.warning("solution_extraction_llm_failed", scope_id=scope_id, error=str(exc))
            return 0

        solutions = parse_llm_json_array(response_text)
        if not solutions:
            return 0

        extracted = 0
        for sol in solutions:
            problem = sol.get("problem", "").strip()
            solution = sol.get("solution", "").strip()
            context = sol.get("context", "").strip()
            subject_key = sol.get("subject_key", "").strip()

            if not problem or not solution or not subject_key:
                continue

            # T059: 按约定格式组织 content
            content = f"问题: {problem}\n解决方案: {solution}\n上下文: {context}"

            # 构建 evidence_refs 从源 SoR
            source_keys = sol.get("source_subject_keys", [])
            evidence_refs = []
            for sk in source_keys:
                for sor in committed_sors:
                    if sor.subject_key == sk:
                        evidence_refs.append(EvidenceRef(ref_id=sor.memory_id, ref_type="sor"))
                        break
            if not evidence_refs:
                evidence_refs = [EvidenceRef(ref_id=committed_sors[0].memory_id, ref_type="sor")]

            try:
                proposal = await memory.propose_write(
                    scope_id=scope_id,
                    partition=MemoryPartition.SOLUTION,
                    action=WriteAction.ADD,
                    subject_key=subject_key,
                    content=content,
                    rationale="solution extraction",
                    confidence=0.8,
                    evidence_refs=evidence_refs,
                    expected_version=None,
                    metadata={"source": "solution_extract"},
                )
                validation = await memory.validate_proposal(proposal.proposal_id)
                if validation.accepted:
                    await memory.commit_memory(proposal.proposal_id)
                    extracted += 1
                    _log.info(
                        "solution_extracted",
                        scope_id=scope_id,
                        subject_key=subject_key,
                    )
            except Exception as exc:
                _log.warning(
                    "solution_extraction_commit_failed",
                    scope_id=scope_id,
                    subject_key=subject_key,
                    error=str(exc),
                )

        return extracted

    def _resolve_default_model_alias(self) -> str:
        return resolve_default_model_alias(self._project_root)
