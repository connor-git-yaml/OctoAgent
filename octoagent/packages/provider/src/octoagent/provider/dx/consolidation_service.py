"""Feature 065: 独立的 ConsolidationService -- 将 Fragment 整合为 SoR 事实记录。

从 MemoryConsoleService._consolidate_scope 提取的核心逻辑，
三个入口共享：管理台手动 / Flush 后异步 / Scheduler 定期。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog
from octoagent.memory import (
    EvidenceRef,
    FragmentRecord,
    MemoryPartition,
    MemoryService,
    SqliteMemoryStore,
    WriteAction,
)


@runtime_checkable
class LlmServiceProtocol(Protocol):
    """LLM 服务的最小接口契约。"""

    async def call_with_fallback(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        **kwargs: Any,
    ) -> Any: ...


# 单次 consolidate 操作的 Fragment 批量上限
# MVP 阶段设为 200，足以覆盖单用户日常积压量
# 若 Scheduler 发现仍有未处理的 Fragment，会在下次周期继续
_MAX_FRAGMENTS_PER_BATCH: int = 200

from .config_wizard import load_config

_log = structlog.get_logger()


# ---------------------------------------------------------------------------
# 返回值数据模型
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ConsolidationScopeResult:
    """单个 scope 的 consolidate 结果。"""

    scope_id: str
    consolidated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


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

## 规则

1. **提取事实，不是操作记录**
   - 保留：用户偏好、项目决策、人物关系、重要结论、知识点
   - 过滤：纯操作性内容（"修了一个 bug"、"运行了测试"）、临时状态（"正在等待回复"）

2. **subject_key 命名规范**
   - 用 `/` 分层，如 `用户偏好/编程语言`、`项目/OctoAgent/架构决策`
   - 简短明确，同一主题的事实用相同 key

3. **去重合并**
   - 多个片段包含相同或相近信息时，合并为一条更完整的表述

4. **confidence 评估**
   - 1.0：直接明确陈述的事实
   - 0.7-0.9：多次提及、可靠推断
   - 0.5-0.6：仅出现一次或推断较弱

5. **输出格式**
   必须输出一个 JSON 数组，不要输出其他内容：
   ```json
   [
     {
       "subject_key": "主题/子主题",
       "content": "完整的陈述句",
       "confidence": 0.8,
       "source_fragment_ids": ["frag-id-1", "frag-id-2"]
     }
   ]
   ```
   如果没有可提取的有价值事实，输出空数组 `[]`。
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
    ) -> None:
        self._memory_store = memory_store
        self._llm_service = llm_service
        self._project_root = project_root

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
        facts = _parse_consolidation_response(response_text)
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

            # 判断 ADD 还是 UPDATE（用已查到的 SoR，避免重复 DB 查询）
            existing_sor_for_key = existing_sor_map.get(subject_key)
            if existing_sor_for_key:
                action = WriteAction.UPDATE
                expected_version = existing_sor_for_key.version
            else:
                action = WriteAction.ADD
                expected_version = None

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
                    metadata={"source": "consolidate"},
                )
                validation = await memory.validate_proposal(proposal.proposal_id)
                if validation.accepted:
                    await memory.commit_memory(proposal.proposal_id)
                    consolidated += 1
                    existing_keys.add(subject_key)
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

    def _resolve_default_model_alias(self) -> str:
        """从项目配置中读取默认的 reasoning 模型别名。"""
        try:
            config = load_config(self._project_root)
            return (config.memory.reasoning_model_alias if config else "") or "main"
        except Exception:
            return "main"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _parse_consolidation_response(text: str) -> list[dict[str, Any]] | None:
    """从 LLM 响应中解析 JSON 数组。"""
    cleaned = text.strip()
    # 处理 markdown code block 包裹
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # 跳过首行 ``` / ```json
        start = 1
        # 去掉尾行 ```（如存在）
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        cleaned = "\n".join(lines[start:end])
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else None
    except (json.JSONDecodeError, ValueError):
        return None
