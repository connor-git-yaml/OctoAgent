"""Feature 037: runtime control context helpers。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from octoagent.core.models import RuntimeControlContext

RUNTIME_CONTEXT_KEY = "runtime_context"
RUNTIME_CONTEXT_JSON_KEY = "runtime_context_json"


def encode_runtime_context(context: RuntimeControlContext) -> str:
    """序列化 runtime context，供 string-only metadata 透传。"""

    return context.model_dump_json(exclude_none=True)


def decode_runtime_context(value: Any) -> RuntimeControlContext | None:
    """从 dict / JSON / model 解析 runtime context。"""

    if value is None:
        return None
    if isinstance(value, RuntimeControlContext):
        return value
    if isinstance(value, Mapping):
        try:
            return RuntimeControlContext.model_validate(dict(value))
        except Exception:
            return None
    if isinstance(value, str) and value.strip():
        try:
            return RuntimeControlContext.model_validate_json(value)
        except Exception:
            return None
    return None


def runtime_context_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> RuntimeControlContext | None:
    """从 work/dispatch metadata 中提取 runtime context。"""

    if metadata is None:
        return None
    parsed = decode_runtime_context(metadata.get(RUNTIME_CONTEXT_KEY))
    if parsed is not None:
        return parsed
    return decode_runtime_context(metadata.get(RUNTIME_CONTEXT_JSON_KEY))
