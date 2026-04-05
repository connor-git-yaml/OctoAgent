"""Schema Reflection 测试 -- US1 Tool Contract Declaration

验证 reflect_tool_schema() 从函数签名自动生成 ToolMeta：
- 基础类型（str/int/float/bool）
- Optional/Union
- list/dict
- 嵌套 BaseModel
- docstring 解析（Google 格式）
- async/sync 检测
- EC-1（无类型注解拒绝）
- EC-5（零参数函数）
"""

import pytest
from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.exceptions import SchemaReflectionError
from octoagent.tooling.models import SideEffectLevel, ToolMeta
from octoagent.tooling.schema import reflect_tool_schema
from pydantic import BaseModel


class TestBasicTypes:
    """基础类型 Schema 反射"""

    def test_str_param(self) -> None:
        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        async def echo(text: str) -> str:
            """回显输入文本。

            Args:
                text: 要回显的文本
            """
            return text

        meta = reflect_tool_schema(echo)
        assert isinstance(meta, ToolMeta)
        assert meta.name == "echo"
        assert "text" in meta.parameters_json_schema["properties"]
        assert meta.parameters_json_schema["properties"]["text"]["type"] == "string"

    def test_int_param(self) -> None:
        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        async def add(a: int, b: int) -> int:
            """两数相加。

            Args:
                a: 第一个数
                b: 第二个数
            """
            return a + b

        meta = reflect_tool_schema(add)
        props = meta.parameters_json_schema["properties"]
        assert props["a"]["type"] == "integer"
        assert props["b"]["type"] == "integer"
        assert set(meta.parameters_json_schema["required"]) == {"a", "b"}

    def test_float_param(self) -> None:
        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        async def scale(value: float, factor: float) -> float:
            """缩放数值。

            Args:
                value: 原始值
                factor: 缩放因子
            """
            return value * factor

        meta = reflect_tool_schema(scale)
        props = meta.parameters_json_schema["properties"]
        assert props["value"]["type"] == "number"
        assert props["factor"]["type"] == "number"

    def test_bool_param(self) -> None:
        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        async def toggle(enabled: bool) -> str:
            """切换开关。

            Args:
                enabled: 是否开启
            """
            return "on" if enabled else "off"

        meta = reflect_tool_schema(toggle)
        assert meta.parameters_json_schema["properties"]["enabled"]["type"] == "boolean"


class TestComplexTypes:
    """复杂类型 Schema 反射"""

    def test_optional_param(self) -> None:
        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        async def greet(name: str, title: str | None = None) -> str:
            """打招呼。

            Args:
                name: 姓名
                title: 头衔
            """
            return f"Hello {title or ''} {name}"

        meta = reflect_tool_schema(greet)
        props = meta.parameters_json_schema["properties"]
        assert "name" in props
        assert "title" in props
        # name 是 required，title 不是
        assert "name" in meta.parameters_json_schema["required"]

    def test_list_param(self) -> None:
        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        async def summarize(items: list[str]) -> str:
            """汇总列表。

            Args:
                items: 条目列表
            """
            return ", ".join(items)

        meta = reflect_tool_schema(summarize)
        items_schema = meta.parameters_json_schema["properties"]["items"]
        assert items_schema["type"] == "array"

    def test_dict_param(self) -> None:
        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        async def process(data: dict[str, int]) -> str:
            """处理字典。

            Args:
                data: 键值对
            """
            return str(data)

        meta = reflect_tool_schema(process)
        data_schema = meta.parameters_json_schema["properties"]["data"]
        assert data_schema["type"] == "object"

    def test_nested_basemodel(self) -> None:
        """嵌套 BaseModel 参数

        注意：pydantic-ai 对单个 BaseModel 参数会展开（flatten）其字段，
        因此 schema 中直接包含 BaseModel 的字段而非嵌套引用。
        """

        class FileSpec(BaseModel):
            path: str
            content: str

        @tool_contract(
            side_effect_level=SideEffectLevel.REVERSIBLE,

            tool_group="filesystem",
        )
        async def write_file(spec: FileSpec) -> str:
            """写入文件。

            Args:
                spec: 文件规范
            """
            return "ok"

        meta = reflect_tool_schema(write_file)
        props = meta.parameters_json_schema["properties"]
        # pydantic-ai 展开单 BaseModel 参数，字段直接出现在顶层
        assert "path" in props
        assert "content" in props


class TestDocstringParsing:
    """Docstring 解析"""

    def test_google_format_description(self) -> None:
        """从 Google 格式 docstring 提取描述"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        async def echo(text: str) -> str:
            """回显输入文本。

            Args:
                text: 要回显的文本
            """
            return text

        meta = reflect_tool_schema(echo)
        assert meta.description == "回显输入文本。"

    def test_no_docstring(self) -> None:
        """没有 docstring 时描述为空字符串"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        async def no_doc(x: str) -> str:
            return x

        meta = reflect_tool_schema(no_doc)
        assert meta.description == ""


class TestAsyncSyncDetection:
    """async/sync 检测"""

    def test_async_function(self) -> None:
        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        async def async_tool(x: str) -> str:
            """异步工具。

            Args:
                x: 输入
            """
            return x

        meta = reflect_tool_schema(async_tool)
        assert meta.is_async is True

    def test_sync_function(self) -> None:
        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        def sync_tool(x: str) -> str:
            """同步工具。

            Args:
                x: 输入
            """
            return x

        meta = reflect_tool_schema(sync_tool)
        assert meta.is_async is False


class TestEdgeCases:
    """边界场景"""

    def test_ec1_no_type_annotation_rejected(self) -> None:
        """EC-1: 无类型注解参数拒绝注册"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        def bad_tool(x):  # noqa: ANN001
            """没有类型注解的工具。"""
            return x

        with pytest.raises(SchemaReflectionError, match="x"):
            reflect_tool_schema(bad_tool)

    def test_ec5_zero_params(self) -> None:
        """EC-5: 零参数函数正常注册"""

        @tool_contract(
            side_effect_level=SideEffectLevel.NONE,

            tool_group="system",
        )
        async def status() -> str:
            """获取系统状态。"""
            return "ok"

        meta = reflect_tool_schema(status)
        assert meta.name == "status"
        assert meta.parameters_json_schema["properties"] == {}

    def test_no_decorator_rejected(self) -> None:
        """未附加 @tool_contract 装饰器的函数被拒绝"""

        async def plain_func(x: str) -> str:
            """普通函数"""
            return x

        with pytest.raises(SchemaReflectionError, match="tool_contract"):
            reflect_tool_schema(plain_func)

    def test_metadata_passthrough(self) -> None:
        """装饰器元数据正确传递到 ToolMeta"""

        @tool_contract(
            side_effect_level=SideEffectLevel.IRREVERSIBLE,

            tool_group="filesystem",
            version="2.0.0",
            timeout_seconds=30.0,
            output_truncate_threshold=1000,
        )
        async def write_file(path: str, content: str) -> str:
            """写入文件。

            Args:
                path: 目标路径
                content: 文件内容
            """
            return "ok"

        meta = reflect_tool_schema(write_file)
        assert meta.side_effect_level == SideEffectLevel.IRREVERSIBLE

        assert meta.tool_group == "filesystem"
        assert meta.version == "2.0.0"
        assert meta.timeout_seconds == 30.0
        assert meta.output_truncate_threshold == 1000
