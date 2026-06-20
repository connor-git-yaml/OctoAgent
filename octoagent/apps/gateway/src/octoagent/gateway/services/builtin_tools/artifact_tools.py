"""F126 项3：artifact read-back 工具模块。

给 LLM 一个可调用的工具读回被卸载（LargeOutputHandler / tail eviction 占位）的
artifact 完整内容，支持字节分页。task 隔离走两道：
1. 中央权限（broker.execute check_permission，Constitution #10 单一入口）；
2. store 层 `get_artifact_content(task=<当前 task>)` 物理过滤（SQL `WHERE task_id`），
   跨 task 读回返回 None → 工具拒绝（防越权读其它 task 的 artifact）。

推翻 hooks_legacy.py 旧约束「artifact 仅审计、不供 LLM 恢复」——本工具即 LLM 恢复途径。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract
from octoagent.gateway.harness.tool_registry import ToolEntry
from octoagent.gateway.harness.tool_registry import register as _registry_register

from ._deps import ToolDeps
from ..execution_context import get_current_execution_context

# 单轮 read-back 默认返回字节数（分页，避免 read-back 自身再撑爆上下文）
_DEFAULT_LIMIT = 16_384
_MAX_LIMIT = 200_000

_TOOL_ENTRYPOINTS: dict[str, frozenset[str]] = {
    "artifact.read_content": frozenset({"agent_runtime"}),
}


def _normalize_ref(artifact_ref: str) -> str:
    """容忍 LLM 传入占位里的 `artifact:<id>` 形态，归一为裸 artifact_id。"""
    ref = artifact_ref.strip()
    if ref.startswith("artifact:"):
        ref = ref[len("artifact:"):].strip()
    return ref


async def register(broker: Any, deps: ToolDeps) -> None:
    """注册 artifact read-back 工具组。"""

    @tool_contract(
        name="artifact.read_content",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="artifact",
        tags=["artifact", "read", "readback", "offload"],
        manifest_ref="builtin://artifact.read_content",
        metadata={"entrypoints": ["agent_runtime"]},
    )
    async def artifact_read_content(
        artifact_ref: str,
        offset: int = 0,
        limit: int = _DEFAULT_LIMIT,
    ) -> str:
        """读回被卸载的 artifact 完整内容（按字节分页）。

        当工具输出/上下文被折叠为 `[已折叠，见 artifact:<id>...]` 占位时，用此工具
        恢复原始内容。仅能读回属于当前 task 的 artifact。

        Args:
            artifact_ref: artifact 引用（裸 id 或 `artifact:<id>` 形态均可）。
            offset: 起始字节偏移（默认 0）。
            limit: 返回字节数（默认 16384，上限 200000；超大内容需多次分页读取）。
        """
        ref = _normalize_ref(artifact_ref)
        if not ref:
            raise RuntimeError("artifact_ref is empty")

        context = get_current_execution_context()
        task_id = context.task_id
        if not task_id:
            # 无 task 上下文不允许 read-back（防 WHERE task_id='' 误判 + 明确失败原因）
            raise RuntimeError("no task context available for artifact read-back")

        # store 层 task 隔离（task 归属比对的权威点）：跨 task / 不存在 → None
        content = await deps.stores.artifact_store.get_artifact_content(
            ref, task=task_id
        )
        if content is None:
            raise RuntimeError(
                f"artifact not found or not accessible for current task: {ref}"
            )

        total = len(content)
        start = max(0, offset)
        bounded_limit = max(1, min(int(limit), _MAX_LIMIT))
        chunk = content[start:start + bounded_limit]
        end = start + len(chunk)
        # 字节切片可能截断多字节 UTF-8 字符 → errors="replace" 安全解码
        text = chunk.decode("utf-8", errors="replace")

        return json.dumps(
            {
                "artifact_ref": ref,
                "offset": start,
                "returned_bytes": len(chunk),
                "total_bytes": total,
                "has_more": end < total,
                "content": text,
            },
            ensure_ascii=False,
        )

    await broker.try_register(
        reflect_tool_schema(artifact_read_content), artifact_read_content
    )
    _registry_register(ToolEntry(
        name="artifact.read_content",
        entrypoints=_TOOL_ENTRYPOINTS["artifact.read_content"],
        toolset="agent_only",
        handler=artifact_read_content,
        schema=BaseModel,
        side_effect_level=SideEffectLevel.NONE,
    ))
