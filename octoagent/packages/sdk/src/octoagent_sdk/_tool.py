"""@tool 装饰器 — 从函数签名自动生成工具 JSON Schema。"""

from __future__ import annotations

import inspect
import json
from typing import Any, Callable, get_type_hints


class ToolSpec:
    """工具元数据，由 @tool 装饰器生成。"""

    def __init__(
        self,
        fn: Callable,
        *,
        name: str = "",
        description: str = "",
    ) -> None:
        self.fn = fn
        self.name = name or fn.__name__
        self.description = description or (fn.__doc__ or "").strip().split("\n")[0]
        self.is_async = inspect.iscoroutinefunction(fn)
        self._schema: dict[str, Any] | None = None

    @property
    def json_schema(self) -> dict[str, Any]:
        """生成 OpenAI function calling 格式的 JSON Schema。"""
        if self._schema is not None:
            return self._schema

        hints = get_type_hints(self.fn)
        sig = inspect.signature(self.fn)
        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls", "ctx", "context"):
                continue
            param_type = hints.get(param_name, str)
            json_type = _python_type_to_json(param_type)
            properties[param_name] = {"type": json_type}

            # 从 docstring 提取参数描述（简单实现）
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        self._schema = {
            "type": "object",
            "properties": properties,
            "required": required,
        }
        return self._schema

    def to_openai_tool(self) -> dict[str, Any]:
        """转为 OpenAI tools 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.json_schema,
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> str:
        """执行工具函数，返回字符串结果。"""
        if self.is_async:
            result = await self.fn(**arguments)
        else:
            result = self.fn(**arguments)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)


def tool(fn: Callable | None = None, *, name: str = "", description: str = ""):
    """@tool 装饰器 — 将函数标记为 Agent 可调用的工具。

    用法：
        @tool
        async def search(query: str) -> str:
            \"\"\"搜索网页\"\"\"
            return "结果..."

        @tool(name="web_search", description="搜索互联网")
        async def search(query: str) -> str:
            return "结果..."
    """
    if fn is not None:
        # @tool 不带参数
        spec = ToolSpec(fn)
        spec._original_fn = fn  # type: ignore
        return spec

    # @tool(...) 带参数
    def decorator(f: Callable) -> ToolSpec:
        spec = ToolSpec(f, name=name, description=description)
        spec._original_fn = f  # type: ignore
        return spec

    return decorator


def _python_type_to_json(py_type: type) -> str:
    """Python 类型到 JSON Schema 类型的简单映射。"""
    mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }
    return mapping.get(py_type, "string")
