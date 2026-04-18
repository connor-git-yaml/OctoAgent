"""filesystem 工具模块。"""

from __future__ import annotations

import json
from typing import Any

from octoagent.tooling import SideEffectLevel, reflect_tool_schema, tool_contract

from ._deps import ToolDeps, resolve_instance_root, resolve_and_check_path, truncate_text


async def register(broker: Any, deps: ToolDeps) -> None:
    """注册 filesystem 工具组。"""

    @tool_contract(
        name="filesystem.list_dir",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="filesystem",
        tags=["filesystem", "directory", "list"],
        manifest_ref="builtin://filesystem.list_dir",
        path_escalation=True,
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def filesystem_list_dir(
        path: str = ".",
        max_entries: int = 50,
    ) -> str:
        """列出目录内容。默认列出当前 project workspace。"""

        instance_root, project_slug = await resolve_instance_root(deps)
        target = resolve_and_check_path(
            instance_root, path, deps.project_root.resolve(), project_slug,
        )
        if not target.exists():
            raise RuntimeError(f"path not found: {target}")
        if not target.is_dir():
            raise RuntimeError(f"path is not a directory: {target}")
        entries = []
        bounded_limit = max(1, min(max_entries, 200))
        is_inside_workspace = target == instance_root or target.is_relative_to(instance_root)
        for item in sorted(target.iterdir(), key=lambda current: (not current.is_dir(), current.name))[
            :bounded_limit
        ]:
            if is_inside_workspace:
                display_path = "." if item == instance_root else str(item.relative_to(instance_root))
            else:
                display_path = str(item)
            entries.append(
                {
                    "name": item.name,
                    "path": display_path,
                    "kind": "directory" if item.is_dir() else "file",
                }
            )
        if is_inside_workspace:
            display_target = "." if target == instance_root else str(target.relative_to(instance_root))
        else:
            display_target = str(target)
        return json.dumps(
            {
                "workspace_root": str(instance_root),
                "path": display_target,
                "entries": entries,
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="filesystem.read_text",
        side_effect_level=SideEffectLevel.NONE,
        tool_group="filesystem",
        tags=["filesystem", "file", "read"],
        manifest_ref="builtin://filesystem.read_text",
        path_escalation=True,
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def filesystem_read_text(
        path: str,
        max_chars: int = 100_000,
    ) -> str:
        """读取文本文件内容。受路径访问策略保护。"""

        instance_root, project_slug = await resolve_instance_root(deps)
        target = resolve_and_check_path(
            instance_root, path, deps.project_root.resolve(), project_slug,
        )
        if not target.exists():
            # 返回结构化的 "不存在" 响应，而非抛异常，让 Agent 更容易处理
            return json.dumps(
                {"exists": False, "path": str(target), "error": "file not found"},
                ensure_ascii=False,
            )
        if not target.is_file():
            raise RuntimeError(f"path is not a file: {target}")
        content = target.read_text(encoding="utf-8")
        # 工具层不做低阈值截断——由 LargeOutputHandler 按上下文比例统一管理
        bounded_limit = max(200, min(max_chars, 500_000))
        return json.dumps(
            {
                "workspace_root": str(instance_root),
                "path": str(target.relative_to(instance_root)),
                "content": truncate_text(content, limit=bounded_limit),
            },
            ensure_ascii=False,
        )

    @tool_contract(
        name="filesystem.write_text",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="filesystem",
        tags=["filesystem", "file", "write"],
        manifest_ref="builtin://filesystem.write_text",
        path_escalation=True,
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def filesystem_write_text(
        path: str,
        content: str,
        create_dirs: bool = True,
    ) -> str:
        """在当前 project 内创建或覆盖文本文件。受路径访问策略保护。"""

        instance_root, project_slug = await resolve_instance_root(deps)
        target = resolve_and_check_path(
            instance_root, path, deps.project_root.resolve(), project_slug,
        )
        if target.is_dir():
            raise RuntimeError(f"path is a directory, not a file: {target}")
        dirs_created = create_dirs and not target.parent.exists()
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        relative = str(target.relative_to(instance_root))
        return json.dumps(
            {
                "workspace_root": str(instance_root),
                "path": relative,
                "bytes_written": len(content.encode("utf-8")),
                "created_dirs": dirs_created,
            },
            ensure_ascii=False,
        )

    for handler in (
        filesystem_list_dir,
        filesystem_read_text,
        filesystem_write_text,
    ):
        await broker.try_register(reflect_tool_schema(handler), handler)
