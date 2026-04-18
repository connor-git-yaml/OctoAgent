"""McpRegistryService._load_configs schema 解析测试。

覆盖三种合法顶层 schema：
- list[{...}]
- {"servers": [{...}]}
- Claude Code 风格 {"<name>": {...}}（Agent 写错常见 schema，兼容识别）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from octoagent.core.models.event import Event
from octoagent.gateway.services.mcp_registry import McpRegistryService


class _StubEventStore:
    """ToolBroker 需要 event_store 但 _load_configs 本身用不到。"""

    async def append_event(self, event: Event) -> None:  # pragma: no cover - stub
        pass

    async def get_next_task_seq(self, task_id: str) -> int:  # pragma: no cover - stub
        return 1


class _StubToolBroker:
    """McpRegistryService 要求 tool_broker，但 _load_configs 路径不使用。"""


def _make_registry(tmp_path: Path, payload: object) -> McpRegistryService:
    config_path = tmp_path / "mcp-servers.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return McpRegistryService(
        project_root=tmp_path,
        tool_broker=_StubToolBroker(),  # type: ignore[arg-type]
        config_path=config_path,
    )


def test_load_configs_accepts_servers_wrapper(tmp_path: Path) -> None:
    """标准格式 {"servers": [...]}。"""
    registry = _make_registry(
        tmp_path,
        {
            "servers": [
                {"name": "alpha", "command": "/bin/echo", "args": ["hi"]},
            ]
        },
    )
    configs = registry._load_configs()
    assert [c.name for c in configs] == ["alpha"]
    assert registry.last_config_error == ""


def test_load_configs_accepts_top_level_list(tmp_path: Path) -> None:
    """顶层 list 格式。"""
    registry = _make_registry(
        tmp_path,
        [
            {"name": "beta", "command": "/bin/echo"},
            {"name": "gamma", "command": "/bin/echo"},
        ],
    )
    configs = registry._load_configs()
    assert [c.name for c in configs] == ["beta", "gamma"]
    assert registry.last_config_error == ""


def test_load_configs_accepts_claude_code_flat_dict(tmp_path: Path) -> None:
    """Feature 078: Agent 容易写出的 Claude Code 风格 {name → config}，兼容识别。"""
    registry = _make_registry(
        tmp_path,
        {
            "openrouter-perplexity": {
                "command": "npx",
                "args": ["-y", "openrouter-mcp"],
                "env": {"OPENROUTER_API_KEY": "sk-or-xxx"},
                "enabled": True,
            },
            "fs-server": {
                "command": "node",
                "args": ["fs.js"],
            },
        },
    )
    configs = registry._load_configs()
    by_name = {c.name: c for c in configs}
    assert set(by_name.keys()) == {"openrouter-perplexity", "fs-server"}
    assert by_name["openrouter-perplexity"].args == ["-y", "openrouter-mcp"]
    assert by_name["openrouter-perplexity"].env == {"OPENROUTER_API_KEY": "sk-or-xxx"}
    assert by_name["openrouter-perplexity"].enabled is True
    assert registry.last_config_error == ""


def test_load_configs_outer_key_wins_over_inner_name(tmp_path: Path) -> None:
    """Claude Code 风格里 config 如果冗余带了 name，外层 key 优先（反映用户 intent）。"""
    registry = _make_registry(
        tmp_path,
        {
            "real-name": {
                "name": "stale-name",  # 冲突的旧 name，应被外层 key 覆盖
                "command": "/bin/echo",
            }
        },
    )
    configs = registry._load_configs()
    assert len(configs) == 1
    assert configs[0].name == "real-name"


def test_load_configs_rejects_unknown_dict_shape(tmp_path: Path) -> None:
    """既不是 servers wrapper 也不是 Claude Code 风格，应报错。"""
    registry = _make_registry(
        tmp_path,
        {"random_key": "some_value", "nested": {"no_command_here": True}},
    )
    configs = registry._load_configs()
    assert configs == []
    assert "Claude Code-style" in registry.last_config_error
    assert "servers" in registry.last_config_error


def test_load_configs_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """配置文件不存在时返回空列表且无 error。"""
    registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=_StubToolBroker(),  # type: ignore[arg-type]
        config_path=tmp_path / "does-not-exist.json",
    )
    assert registry._load_configs() == []
    assert registry.last_config_error == ""


def test_load_configs_json_parse_error(tmp_path: Path) -> None:
    """JSON 格式本身错误（非法语法）时 last_config_error 记录异常。"""
    config_path = tmp_path / "mcp-servers.json"
    config_path.write_text("{not valid json", encoding="utf-8")
    registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=_StubToolBroker(),  # type: ignore[arg-type]
        config_path=config_path,
    )
    assert registry._load_configs() == []
    assert "JSONDecodeError" in registry.last_config_error
