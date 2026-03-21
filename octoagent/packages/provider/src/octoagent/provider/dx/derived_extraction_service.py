"""Feature 065 Phase 2: Derived Memory 自动提取服务 (US-4)。

从 Consolidate 新产出的 SoR 记录中，通过 LLM 提取
entity/relation/category 类型的 DerivedMemoryRecord，
写入 SQLite derived_memory 表。

提取为 best-effort，任何失败都不抛异常，只记录到 result.errors。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.memory import MemoryPartition, SqliteMemoryStore
from octoagent.memory.models.integration import DerivedMemoryRecord

from .consolidation_service import CommittedSorInfo, DerivedExtractionResult
from .llm_common import LlmServiceProtocol, parse_llm_json_array, resolve_default_model_alias

_log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------


_EXTRACTION_SYSTEM_PROMPT = """\
你是一个知识图谱提取助手。你的任务是从一组结构化事实记录中提取命名实体、实体关系和分类标签。

## 提取规则

1. **entity（命名实体）**: 人名、地名、组织名、工具名、技术名、品牌名等
2. **relation（实体关系）**: 实体之间的关系，格式为 source-relation-target
3. **category（分类标签）**: 信息所属的主题分类

## 输出格式

输出 JSON 数组：
[
  {
    "derived_type": "entity" | "relation" | "category",
    "subject_key": "标识符",
    "summary": "简短描述",
    "confidence": 0.0-1.0,
    "payload": { ... },
    "source_memory_ids": ["mem-id"]
  }
]

payload 结构：
- entity: {"entity_type": "person|location|organization|tool|technology|other", "name": "实体名称"}
- relation: {"source": "主体", "relation": "关系动词", "target": "客体", "time": "时间（可选）"}
- category: {"category": "分类标签名"}

如果无可提取内容，输出 []。
"""


_EXTRACTION_USER_PROMPT_TEMPLATE = """\
以下是最新整理的事实记录，请提取命名实体、关系和分类标签：

{sor_entries}
"""


# ---------------------------------------------------------------------------
# DerivedExtractionService
# ---------------------------------------------------------------------------


class DerivedExtractionService:
    """从 SoR 中自动提取 entity/relation/category 派生记录。"""

    def __init__(
        self,
        memory_store: SqliteMemoryStore,
        llm_service: LlmServiceProtocol | None,
        project_root: Path,
    ) -> None:
        self._memory_store = memory_store
        self._llm_service = llm_service
        self._project_root = project_root

    async def extract_from_sors(
        self,
        *,
        scope_id: str,
        partition: MemoryPartition,
        committed_sors: list[CommittedSorInfo],
        model_alias: str = "",
    ) -> DerivedExtractionResult:
        """从一批刚 commit 的 SoR 中提取 Derived Memory。

        best-effort: 任何失败都不抛异常，只记录到 result.errors。
        """
        result = DerivedExtractionResult(scope_id=scope_id)

        # 1. 空 SoR -> 直接返回
        if not committed_sors:
            return result

        # 2. LLM 不可用 -> 记录错误返回
        if self._llm_service is None:
            result.errors.append("LLM 服务未配置")
            return result

        # 3. 构建 prompt
        sor_entries = self._format_sors(committed_sors)
        user_content = _EXTRACTION_USER_PROMPT_TEMPLATE.format(sor_entries=sor_entries)
        messages = [
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        # 4. 调用 LLM
        resolved_alias = model_alias or self._resolve_default_model_alias()
        try:
            llm_result = await self._llm_service.call(
                messages,
                model_alias=resolved_alias,
            )
            response_text = llm_result.content.strip()
        except Exception as exc:
            _log.warning(
                "derived_extraction_failed",
                scope_id=scope_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            result.errors.append(f"LLM 调用失败: {exc}")
            return result

        # 5. 解析 JSON
        items = self._parse_response(response_text)
        if items is None:
            _log.warning(
                "derived_extraction_failed",
                scope_id=scope_id,
                error_type="ParseError",
                error=response_text[:200],
            )
            result.errors.append("LLM 输出格式错误，无法解析为 JSON 数组")
            return result

        if not items:
            return result

        # 6. 构建 DerivedMemoryRecord 列表
        timestamp_ms = int(time.time() * 1000)
        # 收集所有 SoR 的 source_fragment_ids 作为 derived 的 source_fragment_refs
        all_fragment_refs: list[str] = []
        for sor in committed_sors:
            all_fragment_refs.extend(sor.source_fragment_ids)

        records: list[DerivedMemoryRecord] = []
        for idx, item in enumerate(items):
            derived_type = item.get("derived_type", "").strip()
            subject_key = item.get("subject_key", "").strip()
            summary = item.get("summary", "").strip()

            if not derived_type or not subject_key:
                result.skipped += 1
                continue

            confidence = float(item.get("confidence", 0.7))
            payload = item.get("payload", {})
            source_memory_ids = item.get("source_memory_ids", [])
            # 将 source_memory_ids 写入 payload 供追溯
            if source_memory_ids and isinstance(payload, dict):
                payload["source_memory_ids"] = source_memory_ids

            derived_id = f"derived:consolidate:{scope_id}:{timestamp_ms}:{idx}:{derived_type}"

            records.append(
                DerivedMemoryRecord(
                    derived_id=derived_id,
                    scope_id=scope_id,
                    partition=partition,
                    derived_type=derived_type,
                    subject_key=subject_key,
                    summary=summary,
                    payload=payload,
                    confidence=confidence,
                    source_fragment_refs=all_fragment_refs,
                    source_artifact_refs=[],
                    proposal_ref="",
                    created_at=datetime.now(UTC),
                )
            )

        # 7. 写入 derived 记录
        if records:
            try:
                written = await self._memory_store.upsert_derived_records(scope_id, records)
                result.extracted = written
            except Exception as exc:
                _log.warning(
                    "derived_extraction_failed",
                    scope_id=scope_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                result.errors.append(f"Derived 记录写入失败: {exc}")

        _log.info(
            "derived_extraction_complete",
            scope_id=scope_id,
            extracted=result.extracted,
            skipped=result.skipped,
            error_count=len(result.errors),
        )
        return result

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _format_sors(committed_sors: list[CommittedSorInfo]) -> str:
        """将 SoR 列表格式化为 LLM 输入文本。"""
        lines: list[str] = []
        for sor in committed_sors:
            lines.append(
                f"[{sor.memory_id}] ({sor.partition.value}) {sor.subject_key}: {sor.content}"
            )
        return "\n".join(lines)

    @staticmethod
    def _parse_response(text: str) -> list[dict[str, Any]] | None:
        return parse_llm_json_array(text)

    def _resolve_default_model_alias(self) -> str:
        return resolve_default_model_alias(self._project_root)
