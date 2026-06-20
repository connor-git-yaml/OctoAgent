"""F126 项1：执行前 schema 校验 BeforeHook（Pydantic AI F1 抽象）。

在 ToolBroker 真正调用 handler 前，对照工具的 ``parameters_json_schema``
（ToolMeta 单一事实源，Constitution #3）做**宽松**预校验，失败时以
``proceed=False`` + 字段级结构化 ``validation_errors``（loc/msg/type）拒绝，
复用 broker 现有 before-hook 拒绝路径（emit TOOL_CALL_FAILED + 中央权限已在前），
由 runner 回灌自愈 retry loop 做精确修正。

校验策略（C1 决议「宽松校验 + 逐工具豁免」）：
- **必传字段缺失**：schema ``required`` 中的字段未出现在 args → 报 ``missing``（最高置信，永不被 coerce 掉）。
- **结构性类型错配**：仅当 object/array 容器类型与 scalar 标量类型互相错配时报错
  （如声明 object 却传标量、声明 string 却传 dict/list）。**不**校验标量↔标量
  （如 string vs integer），因工具体内常做 coerce，强校验会误拒（FR-1.5）。
- **不**拒绝多余字段（不启用 additionalProperties:false）、**不**递归深层 nested、**不**校验 format/pattern。
- ``ToolMeta.skip_arg_validation=True`` 的工具整体跳过预校验（纵深保险，机制非规则 #9）。

fail_mode 说明：本 hook 的"拒绝非法 args"语义通过正常返回 ``proceed=False`` 表达；
``fail_mode=OPEN`` 仅指**校验器自身意外崩溃时放行**（不因校验器 bug 阻断全部合法调用，
崩溃只回退到原 except 兜底路径，不劣化现状）。
"""

from __future__ import annotations

from typing import Any

import structlog

from .models import BeforeHookResult, ExecutionContext, FailMode, ToolMeta

logger = structlog.get_logger(__name__)

# JSON Schema 顶层类型 → Python 类型分类（仅用于结构性容器↔标量错配检测）
_CONTAINER_TYPES = {"object", "array"}
_SCALAR_TYPES = {"string", "integer", "number", "boolean"}


def _structural_mismatch(json_type: str, value: Any) -> bool:
    """仅检测容器↔标量的结构性错配（coerce 永不发生的粗错），其余一律放行（宽松）。

    返回 True 表示确定错配（应报错）；False 表示放行（含一切标量↔标量、union、未知 type）。
    """
    if value is None:
        # None 留给 required/nullable 语义，不在此判类型（宽松）
        return False
    if json_type == "object":
        return not isinstance(value, dict)
    if json_type == "array":
        return not isinstance(value, (list, tuple))
    if json_type in _SCALAR_TYPES:
        # 声明标量却传了容器（dict/list）= 结构性粗错；标量↔标量交给工具体内 coerce
        return isinstance(value, (dict, list))
    # 未知/union（anyOf/oneOf 无单一 type）→ 放行
    return False


class SchemaValidationHook:
    """执行前 args schema 宽松预校验（fail-closed 语义经 proceed=False 表达）。"""

    def __init__(self, *, priority: int = 900) -> None:
        # priority 较大 → 在 before-hook 链中靠后执行，确保校验的是经前序 hook
        # 可能 modified_args 之后、真正将传给 handler 的 args（避免校验过期参数）。
        self._priority = priority

    @property
    def name(self) -> str:
        return "schema_validation"

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def fail_mode(self) -> FailMode:
        # OPEN：校验器自身崩溃时放行（回退原 except 兜底），不阻断合法调用。
        return FailMode.OPEN

    async def before_execute(
        self,
        tool_meta: ToolMeta,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> BeforeHookResult:
        if tool_meta.skip_arg_validation:
            return BeforeHookResult(proceed=True)

        schema = tool_meta.parameters_json_schema or {}
        properties = schema.get("properties") or {}
        required = schema.get("required") or []

        errors: list[dict[str, Any]] = []

        # 1) 必传字段缺失（最高置信）
        for field in required:
            if field not in args:
                errors.append(
                    {"loc": [field], "msg": "Field required", "type": "missing"}
                )

        # 2) 结构性类型错配（容器↔标量粗错；宽松，跳过标量↔标量）
        for key, value in args.items():
            prop = properties.get(key)
            if not isinstance(prop, dict):
                continue  # 多余字段或无 schema 描述 → 放行（不拒多余字段）
            json_type = prop.get("type")
            if not isinstance(json_type, str):
                continue  # union/anyOf/无 type → 放行
            if _structural_mismatch(json_type, value):
                errors.append(
                    {
                        "loc": [key],
                        "msg": f"Input should be a valid {json_type}",
                        "type": f"{json_type}_type",
                    }
                )

        if not errors:
            return BeforeHookResult(proceed=True)

        reason = _format_rejection(tool_meta.name, errors)
        logger.info(
            "schema_validation_rejected",
            tool_name=tool_meta.name,
            error_count=len(errors),
            locs=[e["loc"] for e in errors],
        )
        return BeforeHookResult(
            proceed=False,
            rejection_reason=reason,
            validation_errors=errors,
        )


def _format_rejection(tool_name: str, errors: list[dict[str, Any]]) -> str:
    """生成人类可读 rejection_reason（结构化数据另走 validation_errors 字段）。"""
    parts = []
    for e in errors:
        loc = ".".join(str(x) for x in e.get("loc", []))
        parts.append(f"{loc}: {e.get('msg', '')}")
    return f"参数校验失败（{tool_name}）：" + "；".join(parts)
