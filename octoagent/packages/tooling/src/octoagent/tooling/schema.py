"""Schema Reflection -- Feature 004 Tool Contract

对齐 spec FR-003/005, plan.md M3。
从函数签名 + type hints + docstring 自动生成 ToolMeta。
隔离 Pydantic AI _function_schema 依赖（Adapter 模式）。
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from typing import Any

from pydantic.json_schema import GenerateJsonSchema
from pydantic_ai._function_schema import function_schema

from .exceptions import SchemaReflectionError
from .models import ToolMeta, ToolTier


def _enforce_write_result_contract(handler: Callable[..., Any], produces_write: bool) -> None:
    """检查 produces_write=True 工具的 return type 必须是 WriteResult 子类（FR-2.4）。

    关键：必须用 typing.get_type_hints(func, include_extras=True) 解析 return annotation，
    因为 14/15 个 builtin_tools 启用了 from __future__ import annotations，
    导致 inspect.signature().return_annotation 是字符串而不是真实类型。

    不抛出时：produces_write=False，直接返回（豁免 browser.* / terminal.exec / tts.speak 等）。
    抛出时：SchemaReflectionError（复用现有异常，防 F13 回归）。

    Args:
        handler: 被检查的函数。
        produces_write: 是否声明为写入工具。
    """
    if not produces_write:
        return

    # 延迟导入避免循环依赖（WriteResult 在 octoagent.core，不在 tooling 包）
    try:
        from octoagent.core.models.tool_results import WriteResult  # type: ignore[import]
    except ImportError:
        # 单元测试环境可能没有 core 包，允许降级（只记录警告，不阻断）
        return

    try:
        hints = typing.get_type_hints(handler, include_extras=True)
    except Exception as exc:
        raise SchemaReflectionError(
            f"{handler.__name__}: 解析类型注解失败（get_type_hints error）: {exc}"
        ) from exc

    return_type = hints.get("return")

    # 检查 return type 是否是 WriteResult 或其子类
    is_valid = (
        isinstance(return_type, type)
        and issubclass(return_type, WriteResult)
    )

    if not is_valid:
        raise SchemaReflectionError(
            f"{handler.__name__}: produces_write=True 要求 return type 是 WriteResult 子类，"
            f"实际为 {return_type!r}。"
            "请将 return type 改为 WriteResult 或其子类（如 FilesystemWriteTextResult）。"
        )


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

    # 步骤 1.5: WriteResult 契约检查（FR-2.4）
    produces_write: bool = tool_meta_dict.get("metadata", {}).get("produces_write", False)
    _enforce_write_result_contract(func, produces_write)

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
