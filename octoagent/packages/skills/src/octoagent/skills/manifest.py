"""SkillManifest 定义。"""

from __future__ import annotations

from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
    ContextBudgetPolicy,
    LoopGuardPolicy,
    RetryPolicy,
    SkillManifestModel,
)

logger = structlog.get_logger(__name__)


class SkillManifest(SkillManifestModel):
    """Skill 声明模型。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    input_model: type[BaseModel] = Field(description="输入模型类型")
    output_model: type[BaseModel] = Field(description="输出模型类型")

    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    loop_guard: LoopGuardPolicy = Field(default_factory=LoopGuardPolicy)
    context_budget: ContextBudgetPolicy = Field(default_factory=ContextBudgetPolicy)

    @field_validator("input_model", "output_model")
    @classmethod
    def _validate_model_type(cls, value: type[BaseModel]) -> type[BaseModel]:
        if not issubclass(value, BaseModel):
            raise ValueError("input_model/output_model 必须是 BaseModel 子类")
        return value

    def load_description(self) -> str | None:
        """加载 Skill 描述。

        优先返回 `description`；若为空且配置了 `description_md`，尝试读取文件。
        文件缺失时仅记录警告并返回 None。
        """
        if self.description:
            return self.description

        if not self.description_md:
            return None

        path = Path(self.description_md)
        if not path.exists():
            logger.warning(
                "skill_description_missing",
                skill_id=self.skill_id,
                description_md=self.description_md,
            )
            return None

        content = path.read_text(encoding="utf-8").strip()
        return content or None
