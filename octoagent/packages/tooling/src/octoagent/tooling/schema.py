"""Schema Reflection -- Feature 004 Tool Contract

对齐 spec FR-003/005, plan.md M3。
从函数签名 + type hints + docstring 自动生成 ToolMeta。
隔离 Pydantic AI _function_schema 依赖（Adapter 模式）。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from pydantic.json_schema import GenerateJsonSchema
from pydantic_ai._function_schema import function_schema

from .exceptions import SchemaReflectionError
from .models import ToolMeta, ToolTier


def reflect_tool_schema(func: Callable[..., Any]) -> ToolMeta:
    """从装饰过的函数生成 ToolMeta（单一事实源）

    流程：
    1. 检查 func 是否有 @tool_contract 装饰器元数据
    2. 检查所有参数是否有类型注解（无则拒绝 -- FR-005）
    3. 调用 pydantic_ai._function_schema.function_schema() 生成 JSON Schema
    4. 合并装饰器元数据 + 自动生成的 Schema
    5. 返回 ToolMeta

    Args:
        func: 已附加 @tool_contract 装饰器的函数

    Returns:
        ToolMeta 实例

    Raises:
        SchemaReflectionError: 缺少装饰器或类型注解
    """
    # 步骤 1: 检查装饰器元数据
    tool_meta_dict: dict[str, Any] | None = getattr(func, "_tool_meta", None)
    if tool_meta_dict is None:
        raise SchemaReflectionError(
            f"函数 '{func.__name__}' 未附加 @tool_contract 装饰器，"
            "请先使用 @tool_contract 声明工具元数据"
        )

    # 步骤 2: 检查类型注解完整性（FR-005, EC-1）
    _validate_type_annotations(func)

    # 步骤 3: 调用 pydantic_ai function_schema() 生成 JSON Schema
    try:
        fs = function_schema(
            func,
            GenerateJsonSchema,
            takes_ctx=False,
            docstring_format="google",
        )
    except Exception as e:
        raise SchemaReflectionError(f"函数 '{func.__name__}' Schema 反射失败: {e}") from e

    # 步骤 4: 合并装饰器元数据 + 自动生成的 Schema
    description = fs.description or ""
    is_async = inspect.iscoroutinefunction(func)

    # 步骤 5: 返回 ToolMeta
    return ToolMeta(
        name=tool_meta_dict["name"],
        description=description,
        parameters_json_schema=dict(fs.json_schema),
        side_effect_level=tool_meta_dict["side_effect_level"],
        tool_profile=tool_meta_dict["tool_profile"],
        tool_group=tool_meta_dict["tool_group"],
        version=tool_meta_dict["version"],
        timeout_seconds=tool_meta_dict["timeout_seconds"],
        is_async=is_async,
        output_truncate_threshold=tool_meta_dict["output_truncate_threshold"],
        tags=tool_meta_dict.get("tags", []),
        worker_types=tool_meta_dict.get("worker_types", []),
        manifest_ref=tool_meta_dict.get("manifest_ref", ""),
        metadata=tool_meta_dict.get("metadata", {}),
        tier=tool_meta_dict.get("tier", ToolTier.DEFERRED),
    )


def _validate_type_annotations(func: Callable[..., Any]) -> None:
    """验证函数所有参数都有类型注解（FR-005, EC-1）

    Args:
        func: 目标函数

    Raises:
        SchemaReflectionError: 参数缺少类型注解
    """
    sig = inspect.signature(func)
    missing_annotations: list[str] = []

    for param_name, param in sig.parameters.items():
        # 跳过 self/cls
        if param_name in ("self", "cls"):
            continue
        # 跳过 *args 和 **kwargs
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        # 检查是否有类型注解
        if param.annotation is inspect.Parameter.empty:
            missing_annotations.append(param_name)

    if missing_annotations:
        params_str = ", ".join(missing_annotations)
        raise SchemaReflectionError(
            f"函数 '{func.__name__}' 的参数 [{params_str}] 缺少类型注解。"
            "所有工具参数必须有完整的类型注解（FR-005）"
        )
