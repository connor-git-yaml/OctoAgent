"""Feature 065 Phase 3: 用户画像自动生成服务 (US-9)。

从 SoR (partition in [core, profile, work]) + Derived (entity, relation, tom)
聚合生成结构化画像，写入 SoR (partition=profile)。

画像维度：
- 基本信息 / 工作领域 / 技术偏好 / 个人偏好 / 常用工具 / 近期关注

best-effort: 任何失败都不抛异常，只记录到 result.errors。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from octoagent.memory import (
    EvidenceRef,
    MemoryPartition,
    MemoryService,
    SqliteMemoryStore,
    WriteAction,
)
from octoagent.memory.models.integration import DerivedMemoryQuery

from .llm_common import LlmServiceProtocol, resolve_default_model_alias

_log = structlog.get_logger()

# 数据不足阈值
_MIN_SOR_COUNT = 5
_MIN_DERIVED_COUNT = 3


# ---------------------------------------------------------------------------
# 返回值数据模型
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProfileGenerateResult:
    """画像生成结果。"""

    scope_id: str
    dimensions_generated: int = 0
    dimensions_updated: int = 0
    skipped: bool = False
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

_PROFILE_SYSTEM_PROMPT = """\
你是一个用户画像生成助手。基于用户的记忆记录和派生知识，生成一份结构化的用户画像。

## 画像维度

为以下每个维度生成**详细的多段描述**，深入覆盖该维度的各个子方面：

1. **基本信息**: 姓名、职业、所在地、家庭背景、生活阶段等
2. **工作领域**: 主要从事的工作、所属行业、关注的技术方向、职业目标、团队角色
3. **技术偏好**: 编程语言、框架、工具链的偏好，技术理念、架构风格、编码习惯
4. **个人偏好**: 饮食、生活习惯、兴趣爱好、消费偏好、品牌忠诚
5. **常用工具**: 经常使用的软件、平台、服务，工作流和效率工具
6. **近期关注**: 最近在关注或处理的事情，近期计划和目标
7. **人际关系**: 重要的家人、朋友、同事，关系特征和互动模式
8. **健康与运动**: 运动习惯、身体状况、健康目标、饮食偏好

## 规则

- 只输出有依据的内容，不要凭空编造
- 如果某个维度没有足够信息，输出 null
- 每个维度可以包含多个段落，深入覆盖不同子方面，不要人为限制长度
- 随着信息积累，每个维度应逐步丰富——新版本的信息密度不应低于旧版本
- 如果已有画像内容且新信息与之矛盾，以新信息为准
- 每个维度的描述应该是**完整的自然语言段落**，而非关键词列表

## 输出格式

```json
{
  "基本信息": "描述或null",
  "工作领域": "描述或null",
  "技术偏好": "描述或null",
  "个人偏好": "描述或null",
  "常用工具": "描述或null",
  "近期关注": "描述或null",
  "人际关系": "描述或null",
  "健康与运动": "描述或null"
}
```

T066: 输出格式保持 `string | null`，与下游消费方完全兼容。
"""

_PROFILE_USER_PROMPT_TEMPLATE = """\
以下是用户的记忆记录和派生知识，请生成用户画像：

## 事实记录（SoR）

{sor_entries}

## 派生知识（Derived）

{derived_entries}

## 已有画像（参考）

{existing_profile}
"""


# ---------------------------------------------------------------------------
# ProfileGeneratorService
# ---------------------------------------------------------------------------


class ProfileGeneratorService:
    """用户画像自动生成服务。"""

    _PROFILE_DIMENSIONS: list[str] = [
        "基本信息", "工作领域", "技术偏好",
        "个人偏好", "常用工具", "近期关注",
    ]

    def __init__(
        self,
        memory_store: SqliteMemoryStore,
        llm_service: LlmServiceProtocol | None,
        project_root: Path,
    ) -> None:
        self._memory_store = memory_store
        self._llm_service = llm_service
        self._project_root = project_root

    async def generate_profile(
        self,
        *,
        memory: MemoryService,
        scope_id: str,
        model_alias: str = "",
    ) -> ProfileGenerateResult:
        """从 SoR + Derived 聚合生成用户画像。

        1. 查询相关 SoR 记录（core, profile, work 分区）
        2. 查询 Derived 记录（entity, relation, tom）
        3. 调用 LLM 生成结构化画像
        4. 逐维度通过 memory.write 治理流程写入 SoR (partition=profile)
        """
        result = ProfileGenerateResult(scope_id=scope_id)

        # 1. LLM 不可用
        if self._llm_service is None:
            result.skipped = True
            result.errors.append("LLM 服务未配置")
            return result

        # 2. 数据聚合
        try:
            sor_records = await self._memory_store.search_sor(
                scope_id, query=None, include_history=False, limit=200,
            )
            derived_records = await self._memory_store.list_derived_records(
                DerivedMemoryQuery(
                    scope_id=scope_id,
                    derived_types=["entity", "relation", "tom"],
                    limit=100,
                )
            )
            existing_profile = await self._memory_store.search_sor(
                scope_id, query="用户画像", include_history=False, limit=20,
            )
            # 进一步过滤已有画像（只保留 partition=profile 的记录）
            existing_profile = [
                s for s in existing_profile
                if s.partition == MemoryPartition.PROFILE
            ]
        except Exception as exc:
            result.errors.append(f"数据查询失败: {exc}")
            return result

        # 3. 最低数据阈值
        sor_count = len(sor_records)
        derived_count = len(derived_records)
        if sor_count < _MIN_SOR_COUNT and derived_count < _MIN_DERIVED_COUNT:
            result.skipped = True
            _log.info(
                "profile_generate_skipped",
                scope_id=scope_id,
                sor_count=sor_count,
                derived_count=derived_count,
                reason="数据不足",
            )
            return result

        # 4. 构建 LLM prompt
        sor_entries = self._format_sor_records(sor_records)
        derived_entries = self._format_derived_records(derived_records)
        existing_profile_text = self._format_existing_profile(existing_profile)

        user_content = _PROFILE_USER_PROMPT_TEMPLATE.format(
            sor_entries=sor_entries or "（无记录）",
            derived_entries=derived_entries or "（无记录）",
            existing_profile=existing_profile_text or "（无已有画像）",
        )
        messages = [
            {"role": "system", "content": _PROFILE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        # 5. 调用 LLM
        resolved_alias = model_alias or self._resolve_default_model_alias()
        try:
            llm_result = await self._llm_service.call(
                messages,
                model_alias=resolved_alias,
            )
            response_text = llm_result.content.strip()
        except Exception as exc:
            _log.warning(
                "profile_generate_failed",
                scope_id=scope_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            result.errors.append(f"LLM 调用失败: {exc}")
            return result

        # 6. 解析 LLM 输出
        profile_dict = self._parse_profile_json(response_text)
        if profile_dict is None:
            _log.warning(
                "profile_generate_parse_failed",
                scope_id=scope_id,
                response=response_text[:200],
            )
            result.errors.append("LLM 输出格式错误，无法解析为 JSON 对象")
            return result

        # 7. 构建已有画像 subject_key -> SorRecord 映射
        existing_map: dict[str, Any] = {}
        for s in existing_profile:
            existing_map[s.subject_key] = s

        # 计算 tom_count 用于 metadata
        tom_count = sum(1 for d in derived_records if d.derived_type == "tom")

        # 8. 逐维度写入
        for dimension in self._PROFILE_DIMENSIONS:
            content = profile_dict.get(dimension)
            if content is None or not str(content).strip():
                continue

            content_str = str(content).strip()
            subject_key = f"用户画像/{dimension}"
            existing_sor = existing_map.get(subject_key)

            try:
                if existing_sor:
                    # UPDATE
                    action = WriteAction.UPDATE
                    expected_version = existing_sor.version
                else:
                    # ADD
                    action = WriteAction.ADD
                    expected_version = None

                proposal = await memory.propose_write(
                    scope_id=scope_id,
                    partition=MemoryPartition.PROFILE,
                    action=action,
                    subject_key=subject_key,
                    content=content_str,
                    rationale="profile_generator 自动画像生成",
                    confidence=0.8,
                    evidence_refs=[],
                    expected_version=expected_version,
                    metadata={
                        "source": "profile_generator",
                        "generated_at": datetime.now(UTC).isoformat(),
                        "sor_count": sor_count,
                        "derived_count": derived_count,
                        "tom_count": tom_count,
                    },
                )
                validation = await memory.validate_proposal(proposal.proposal_id)
                if validation.accepted:
                    await memory.commit_memory(proposal.proposal_id)
                    if existing_sor:
                        result.dimensions_updated += 1
                    else:
                        result.dimensions_generated += 1
                else:
                    result.errors.append(
                        f"维度 '{dimension}' 验证未通过: {validation.errors}"
                    )
            except Exception as exc:
                result.errors.append(f"维度 '{dimension}' 写入失败: {exc}")
                _log.warning(
                    "profile_generate_dimension_failed",
                    scope_id=scope_id,
                    dimension=dimension,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        _log.info(
            "profile_generate_complete",
            scope_id=scope_id,
            dimensions_generated=result.dimensions_generated,
            dimensions_updated=result.dimensions_updated,
            error_count=len(result.errors),
        )
        return result

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _format_sor_records(records: list) -> str:
        lines: list[str] = []
        for r in records[:100]:  # 限制输入长度
            lines.append(f"- [{r.partition.value}] {r.subject_key}: {r.content[:200]}")
        return "\n".join(lines)

    @staticmethod
    def _format_derived_records(records: list) -> str:
        lines: list[str] = []
        for r in records[:50]:  # 限制输入长度
            lines.append(f"- [{r.derived_type}] {r.subject_key}: {r.summary[:200]}")
        return "\n".join(lines)

    @staticmethod
    def _format_existing_profile(records: list) -> str:
        lines: list[str] = []
        for r in records:
            lines.append(f"- {r.subject_key}: {r.content[:200]}")
        return "\n".join(lines)

    @staticmethod
    def _parse_profile_json(text: str) -> dict[str, str | None] | None:
        """从 LLM 响应中解析画像 JSON 对象。"""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            start = 1
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            cleaned = "\n".join(lines[start:end])
        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None

    def _resolve_default_model_alias(self) -> str:
        return resolve_default_model_alias(self._project_root)
