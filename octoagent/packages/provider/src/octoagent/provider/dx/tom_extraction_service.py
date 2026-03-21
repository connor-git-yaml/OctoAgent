"""Feature 065 Phase 3: Theory of Mind 推理服务 (US-7)。

从 Consolidate 新产出的 SoR 记录中，通过 LLM 推断用户的：
- 意图/目标 (intent)
- 偏好/倾向 (preference)
- 知识水平 (knowledge_level)
- 情绪状态 (emotional_state)

生成 derived_type="tom" 的 DerivedMemoryRecord。

best-effort: 任何失败都不抛异常，只记录到 result.errors。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from octoagent.memory import MemoryPartition, SqliteMemoryStore
from octoagent.memory.models.integration import DerivedMemoryRecord

from .consolidation_service import CommittedSorInfo
from .llm_common import LlmServiceProtocol, parse_llm_json_array, resolve_default_model_alias

_log = structlog.get_logger()

# ToM 合法维度
_VALID_TOM_DIMENSIONS = frozenset({"intent", "preference", "knowledge_level", "emotional_state"})


# ---------------------------------------------------------------------------
# 返回值数据模型
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ToMExtractionResult:
    """ToM 推理结果。"""

    scope_id: str
    extracted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

_TOM_SYSTEM_PROMPT = """\
你是一个用户心智模型分析助手。你的任务是从一组已整理的事实记录中推断用户的心智状态。

## 推断维度

1. **intent（意图/目标）**: 用户当前关注什么、想要达成什么
   - 例如："Connor 近期关注 OctoAgent 的 Memory 系统优化"
2. **preference（偏好/倾向）**: 用户在某方面的持续偏好
   - 例如："Connor 偏好使用 Python + SQLite 的轻量方案"
3. **knowledge_level（知识水平）**: 用户在某领域的熟练程度
   - 例如："Connor 在分布式系统领域是高级工程师水平"
4. **emotional_state（情绪倾向）**: 用户对某事物的态度或情绪
   - 例如："Connor 对当前 Memory 系统的检索质量不太满意"

## 推断规则

- 只输出有合理依据的推断，不要凭空猜测
- confidence 反映推断的确信程度：
  - 0.9+: 多次直接表达的偏好或意图
  - 0.7-0.8: 从行为模式中推断
  - 0.5-0.6: 单次间接信号
- 如果无法做出任何有意义的推断，输出 []

## 输出格式

```json
[
  {
    "derived_type": "tom",
    "tom_dimension": "intent|preference|knowledge_level|emotional_state",
    "subject_key": "ToM/维度/主题",
    "summary": "推断描述",
    "confidence": 0.8,
    "payload": {
      "dimension": "intent",
      "domain": "领域",
      "evidence": "支持此推断的简要证据"
    },
    "source_memory_ids": ["mem-id"]
  }
]
```
"""

_TOM_USER_PROMPT_TEMPLATE = """\
以下是最新整理的事实记录，请推断用户的意图、偏好、知识水平和情绪倾向：

{sor_entries}
"""


# ---------------------------------------------------------------------------
# ToMExtractionService
# ---------------------------------------------------------------------------


class ToMExtractionService:
    """Theory of Mind 推理服务 -- 从 SoR 事实推断用户心智状态。"""

    def __init__(
        self,
        memory_store: SqliteMemoryStore,
        llm_service: LlmServiceProtocol | None,
        project_root: Path,
    ) -> None:
        self._memory_store = memory_store
        self._llm_service = llm_service
        self._project_root = project_root

    async def extract_tom(
        self,
        *,
        scope_id: str,
        partition: MemoryPartition,
        committed_sors: list[CommittedSorInfo],
        model_alias: str = "",
    ) -> ToMExtractionResult:
        """从一批 SoR 中推理 Theory of Mind 记录。

        best-effort: 任何失败都不抛异常，只记录到 result.errors。
        """
        result = ToMExtractionResult(scope_id=scope_id)

        # 1. 空 SoR -> 直接返回
        if not committed_sors:
            return result

        # 2. LLM 不可用 -> 记录错误返回
        if self._llm_service is None:
            result.errors.append("LLM 服务未配置")
            return result

        # 3. 构建 prompt
        sor_entries = self._format_sors(committed_sors)
        user_content = _TOM_USER_PROMPT_TEMPLATE.format(sor_entries=sor_entries)
        messages = [
            {"role": "system", "content": _TOM_SYSTEM_PROMPT},
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
                "tom_extraction_failed",
                scope_id=scope_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            result.errors.append(f"LLM 调用失败: {exc}")
            return result

        # 5. 解析 JSON
        items = parse_llm_json_array(response_text)
        if items is None:
            _log.warning(
                "tom_extraction_parse_failed",
                scope_id=scope_id,
                response=response_text[:200],
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
            tom_dimension = item.get("tom_dimension", "").strip()
            subject_key = item.get("subject_key", "").strip()
            summary = item.get("summary", "").strip()

            # 验证 tom_dimension 合法
            if tom_dimension not in _VALID_TOM_DIMENSIONS:
                result.skipped += 1
                continue

            if not subject_key or not summary:
                result.skipped += 1
                continue

            confidence = float(item.get("confidence", 0.7))
            confidence = max(0.0, min(1.0, confidence))

            payload = item.get("payload", {})
            if isinstance(payload, dict):
                # 确保 payload 中包含 tom_dimension
                payload["tom_dimension"] = tom_dimension
                # 写入 source_memory_ids 供追溯
                source_memory_ids = item.get("source_memory_ids", [])
                if source_memory_ids:
                    payload["source_memory_ids"] = source_memory_ids

            derived_id = f"derived:tom:{scope_id}:{timestamp_ms}:{idx}"

            records.append(
                DerivedMemoryRecord(
                    derived_id=derived_id,
                    scope_id=scope_id,
                    partition=partition,
                    derived_type="tom",
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
                    "tom_extraction_write_failed",
                    scope_id=scope_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                result.errors.append(f"ToM 记录写入失败: {exc}")

        _log.info(
            "tom_extraction_complete",
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

    def _resolve_default_model_alias(self) -> str:
        return resolve_default_model_alias(self._project_root)
