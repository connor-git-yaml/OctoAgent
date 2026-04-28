"""terminal 工具模块。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel

from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract
from octoagent.gateway.harness.tool_registry import ToolEntry
from octoagent.gateway.harness.tool_registry import register as _registry_register

from ._deps import ToolDeps, resolve_instance_root, resolve_and_check_path, truncate_text

# 各工具 entrypoints 声明（Feature 084 D1 根治）
_TOOL_ENTRYPOINTS: dict[str, frozenset[str]] = {
    "terminal.exec": frozenset({"agent_runtime"}),
}


async def register(broker: Any, deps: ToolDeps) -> None:
    """注册 terminal 工具组。"""

    @tool_contract(
        name="terminal.exec",
        # REVERSIBLE: 默认策略自动放行，避免 node -v/grep 等只读命令
        # 也被审批拦截。真正高危操作由 Policy Profile 的 irreversible
        # 规则或 Skill 层的 Side-effect Two-Phase 保护。
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="terminal",
        tags=["terminal", "command", "exec"],
        manifest_ref="builtin://terminal.exec",
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def terminal_exec(
        command: str,
        cwd: str = ".",
        timeout_seconds: float = 300.0,
        max_output_chars: int = 200_000,
    ) -> str:
        """在当前 project 内执行受治理终端命令。cwd 受路径访问策略保护。"""

        instance_root, project_slug = await resolve_instance_root(deps)
        working_dir = resolve_and_check_path(
            instance_root, cwd, deps.project_root.resolve(), project_slug,
        )
        if not working_dir.exists() or not working_dir.is_dir():
            raise RuntimeError(f"cwd is not a directory: {working_dir}")
        # 超时上限 600s（对齐 MCP 安装等长命令场景）
        bounded_timeout = max(1.0, min(timeout_seconds, 600.0))
        # 工具层不做低阈值截断——由 LargeOutputHandler 按上下文比例统一管理
        bounded_limit = max(200, min(max_output_chars, 500_000))
        try:
            cwd_label = "." if working_dir == instance_root else str(
                working_dir.relative_to(instance_root)
            )
        except ValueError:
            cwd_label = str(working_dir)

        # 使用 asyncio subprocess 避免阻塞事件循环
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-lc", command,
            cwd=str(working_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=bounded_timeout,
            )
        except asyncio.TimeoutError:
            # 超时后先尝试 terminate，给 2s 优雅退出，不行再 kill
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            stdout_bytes = b""
            stderr_bytes = b""
            timed_out = True

        stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace")
        payload = {
            "workspace_root": str(instance_root),
            "cwd": cwd_label,
            "command": command,
            "returncode": proc.returncode,
            "stdout": truncate_text(stdout_text, limit=bounded_limit),
            "stderr": truncate_text(stderr_text, limit=bounded_limit),
            "timed_out": timed_out,
        }
        if timed_out:
            payload["timeout_seconds"] = bounded_timeout
        return json.dumps(payload, ensure_ascii=False)

    await broker.try_register(reflect_tool_schema(terminal_exec), terminal_exec)

    # 向 ToolRegistry 注册 ToolEntry（Feature 084 T013 — entrypoints 迁移）
    _registry_register(ToolEntry(
        name="terminal.exec",
        entrypoints=_TOOL_ENTRYPOINTS["terminal.exec"],
        toolset="agent_only",
        handler=terminal_exec,
        schema=BaseModel,
        side_effect_level=SideEffectLevel.REVERSIBLE,
    ))
