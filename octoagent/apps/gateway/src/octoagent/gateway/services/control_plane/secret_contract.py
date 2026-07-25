"""F149 control-plane write-only secret mutation 与读模型去值合同。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_PLACEHOLDERS = frozenset(
    {
        "**********",
        "••••••••••",
        "[REDACTED]",
        "<REDACTED>",
        "REDACTED",
    }
)


class SecretMutation(BaseModel):
    """Web 编辑面只接收显式 keep / replace / remove，不回传旧值。"""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["keep", "replace", "remove"]
    value: str | None = Field(default=None)

    @model_validator(mode="after")
    def validate_mode_value(self) -> SecretMutation:
        value = self.value
        if self.mode == "replace":
            if value is None or not value.strip() or value.strip() in _PLACEHOLDERS:
                raise ValueError("replace 需要非占位 secret value")
            return self
        if value is not None:
            raise ValueError(f"{self.mode} 不允许携带 value")
        return self


def apply_secret_mutations(
    existing: Mapping[str, str],
    mutations: Mapping[str, object],
) -> dict[str, str]:
    """把三态 mutation 应用到现有值；任何 schema 漂移都 fail closed。"""

    resolved = {
        str(name).strip(): str(value) for name, value in existing.items() if str(name).strip()
    }
    for raw_name, raw_mutation in mutations.items():
        name = str(raw_name).strip()
        if not name:
            raise ValueError("secret name 不能为空")
        mutation = SecretMutation.model_validate(raw_mutation)
        if mutation.mode == "keep":
            continue
        if mutation.mode == "remove":
            resolved.pop(name, None)
            continue
        assert mutation.value is not None
        resolved[name] = mutation.value
    return resolved


def validate_initial_secret_values(values: Mapping[str, object]) -> dict[str, str]:
    """校验首次创建时的一次性 secret 值；不得把占位符当作真实凭证。"""

    resolved: dict[str, str] = {}
    for raw_name, raw_value in values.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_value, str):
            raise ValueError("首次 secret 的 name/value 不符合合同")
        value = raw_value.strip()
        if not value or value in _PLACEHOLDERS:
            raise ValueError("首次 secret 不得为空或使用占位符")
        resolved[name] = raw_value
    return resolved


def secret_field_summaries(existing: Mapping[str, str]) -> list[dict[str, object]]:
    """仅公开字段名与是否已配置，不公开值或可逆摘要。"""

    return [
        {
            "name": name,
            "configured": bool(str(existing[name])),
            "redacted_summary": "已配置" if str(existing[name]) else "未配置",
        }
        for name in sorted(str(key).strip() for key in existing if str(key).strip())
    ]
