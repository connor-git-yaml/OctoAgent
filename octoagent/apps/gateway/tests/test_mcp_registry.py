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
from octoagent.core.models import BuiltinToolAvailabilityStatus
from octoagent.gateway.services.mcp_registry import (
    McpRegistryService,
    McpToolRecord,
)


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


def _seed_tool_record(
    registry: McpRegistryService, *, registered_name: str, server_name: str
) -> None:
    registry._tool_records[registered_name] = McpToolRecord(
        registered_name=registered_name,
        server_name=server_name,
        source_tool_name=registered_name.rsplit(".", 1)[-1],
        availability=BuiltinToolAvailabilityStatus.AVAILABLE,
    )


def test_list_tools_server_name_filter_is_slug_tolerant(tmp_path: Path) -> None:
    """LLM 常把带连字符的 server_name 写成下划线（或反过来），过滤不能精确字符串匹配。"""
    registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=_StubToolBroker(),  # type: ignore[arg-type]
        config_path=tmp_path / "mcp-servers.json",
    )
    _seed_tool_record(
        registry,
        registered_name="mcp.openrouter_perplexity.ask_model",
        server_name="openrouter-perplexity",
    )

    # 严格匹配：连字符命中
    hit_dashed = registry.list_tools(server_name="openrouter-perplexity")
    assert [t.registered_name for t in hit_dashed] == ["mcp.openrouter_perplexity.ask_model"]

    # slug 容错：下划线也应命中
    hit_underscored = registry.list_tools(server_name="openrouter_perplexity")
    assert [t.registered_name for t in hit_underscored] == ["mcp.openrouter_perplexity.ask_model"]

    # 其他大小写 / 混合分隔符也应命中（slugify 会归一化）
    hit_mixed = registry.list_tools(server_name="OpenRouter-Perplexity")
    assert len(hit_mixed) == 1

    # 不相关的 server_name 仍然返回空
    assert registry.list_tools(server_name="other-server") == []


def test_list_tools_empty_filter_returns_all(tmp_path: Path) -> None:
    registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=_StubToolBroker(),  # type: ignore[arg-type]
        config_path=tmp_path / "mcp-servers.json",
    )
    _seed_tool_record(
        registry,
        registered_name="mcp.alpha.one",
        server_name="alpha",
    )
    _seed_tool_record(
        registry,
        registered_name="mcp.beta.two",
        server_name="beta",
    )
    names = {t.registered_name for t in registry.list_tools()}
    assert names == {"mcp.alpha.one", "mcp.beta.two"}


class _StubSessionPool:
    """记录 close 调用 + 维护 known_server_names；不真启子进程。"""

    def __init__(self, initial_names: set[str]) -> None:
        self._names = set(initial_names)
        self.close_calls: list[str] = []

    def known_server_names(self) -> set[str]:
        return set(self._names)

    async def close(self, server_name: str) -> None:
        self.close_calls.append(server_name)
        self._names.discard(server_name)

    async def open(self, server_name: str, config) -> None:  # noqa: ANN001
        # 不应被调用：测试用 disabled / 空 configs 走不到 enabled-loop
        raise AssertionError(f"unexpected open() in stale-close test: {server_name}")

    async def close_all(self) -> None:  # pragma: no cover - 未触发
        for name in list(self._names):
            await self.close(name)


@pytest.mark.asyncio
async def test_refresh_closes_session_pool_entries_for_deleted_servers(
    tmp_path: Path,
) -> None:
    """删除 config 后 refresh 必须关闭 pool 里残留的 stale entry（修资源泄漏）。

    Bug 复现路径：
    1. configs 中有 alpha + beta，pool 中亦有；
    2. delete_config('alpha')；mcp-servers.json 不再含 alpha；
    3. refresh() 跑 _refresh_locked —— 旧实现只在 enabled-loop 中处理 disabled
       config 的 close，对完全删除的 alpha 不 enumerate，alpha 在 pool 中残留。

    本测试覆盖：refresh 末尾的 diff-close 段调用 pool.close('alpha')。
    """
    config_path = tmp_path / "mcp-servers.json"
    config_path.write_text(
        json.dumps({"servers": [{"name": "beta", "command": "/bin/echo"}]}),
        encoding="utf-8",
    )
    pool = _StubSessionPool(initial_names={"alpha", "beta"})

    from octoagent.gateway.services.mcp_registry import McpRegistryService

    registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=_StubToolBroker(),  # type: ignore[arg-type]
        config_path=config_path,
        session_pool=pool,
    )

    # 用 server_configs 覆盖避免触发 enabled-loop 内的 _session_pool.open
    # （要真启子进程，不在本单测 scope 内）；override 设为空 list = "configs
    # 不再有任何 server" 的极端 case。
    registry._server_configs_override = []  # type: ignore[attr-defined]

    await registry.refresh()

    # alpha + beta 都不再在 configs 中（override = []），diff-close 应同时关掉两个
    assert sorted(pool.close_calls) == ["alpha", "beta"]
    assert pool.known_server_names() == set()


@pytest.mark.asyncio
async def test_refresh_does_not_close_active_servers(tmp_path: Path) -> None:
    """diff-close 不能误关掉仍在 configs 列表中的 server。"""
    config_path = tmp_path / "mcp-servers.json"
    config_path.write_text(
        json.dumps({"servers": [{"name": "alpha", "command": "/bin/echo"}]}),
        encoding="utf-8",
    )
    pool = _StubSessionPool(initial_names={"alpha"})

    from octoagent.gateway.services.mcp_registry import McpServerConfig

    registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=_StubToolBroker(),  # type: ignore[arg-type]
        config_path=config_path,
        session_pool=pool,
        # 用 disabled config override 避免走 enabled-loop 实际 spawn
        server_configs=[
            McpServerConfig(name="alpha", command="/bin/echo", enabled=False)
        ],
    )

    await registry.refresh()

    # alpha 仍在 configs 中（虽然 disabled），不该被 diff-close 段关掉。
    # 但 enabled-loop 内的 disabled-close 会调一次 close。这里我们关心的是
    # diff-close 不重复关，且不误关任何不在 stale 集合中的 server。
    # 期望：close 只来自 disabled-close 一次。
    assert pool.close_calls == ["alpha"]


@pytest.mark.asyncio
async def test_refresh_diff_close_swallows_pool_exception(tmp_path: Path) -> None:
    """单个 stale close 抛错不能阻断后续 refresh 流程。"""

    class _FailingPool(_StubSessionPool):
        async def close(self, server_name: str) -> None:
            self.close_calls.append(server_name)
            raise RuntimeError(f"simulated close failure: {server_name}")

    pool = _FailingPool(initial_names={"alpha", "beta"})
    registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=_StubToolBroker(),  # type: ignore[arg-type]
        config_path=tmp_path / "mcp-servers.json",
        session_pool=pool,
    )
    registry._server_configs_override = []  # type: ignore[attr-defined]

    # 即使 close 抛错，refresh() 不能 propagate 异常 —— 主流程必须继续
    await registry.refresh()
    # 两个 stale server 都应尝试关闭过（即使第一个抛错，第二个仍跑）
    assert sorted(pool.close_calls) == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_refresh_skips_diff_close_when_config_load_fatal(
    tmp_path: Path,
) -> None:
    """Codex F1 high-1：fatal config 解析失败时 diff-close 必须跳过，避免误关全部 pool。

    一次手抖损坏 mcp-servers.json（非法 JSON / 错误 shape），_load_configs()
    返回空 [] 并设 last_config_error。若 diff-close 仍按"empty configs"推 stale，
    会把所有现存 server 一键全关——比 bug 本身（资源泄漏）更危险。

    应保守保留上一次的 pool 状态，等用户修好 config 再正常 refresh。
    """
    # 写入非法 JSON
    config_path = tmp_path / "mcp-servers.json"
    config_path.write_text("{not valid json", encoding="utf-8")

    pool = _StubSessionPool(initial_names={"alpha", "beta"})
    registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=_StubToolBroker(),  # type: ignore[arg-type]
        config_path=config_path,
        session_pool=pool,
    )
    # 不用 _server_configs_override —— 真走 _load_configs 路径触发 last_config_error

    await registry.refresh()

    # last_config_error 应被设
    assert registry.last_config_error != ""
    # 两个 server 在 pool 中都应保留，**未被 diff-close 误关**
    assert pool.close_calls == []
    assert pool.known_server_names() == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_refresh_partial_config_still_runs_diff_close(
    tmp_path: Path,
) -> None:
    """Codex F2 high-1：部分 item 校验失败的 partial 配置仍必须做 diff-close。

    关键场景（Codex F2 命中）：
    - mcp-servers.json 含两个 server：alpha（合法）+ broken（缺 command 字段）
    - pool 之前 track 三个 server：alpha + broken + ghost（已删）
    - refresh 期望：
      * alpha 留在 pool（合法 + 仍 enabled）
      * broken 因 item 校验失败 last_config_error 非空，但 alpha 还在 → partial 不是 fatal
      * ghost 不在 valid configs 中，必须被 diff-close 清理

    旧 guard `bool(last_config_error)` 把这种 partial 当 fatal，跳过整个 diff-close，
    ghost 资源泄漏。新 guard `bool(error) and not configs` 仅在完全无 valid config
    时才跳过。
    """
    config_path = tmp_path / "mcp-servers.json"
    config_path.write_text(
        json.dumps(
            {
                "servers": [
                    {"name": "alpha", "command": "/bin/echo"},
                    # broken：缺 command（McpServerConfig 校验失败 → 跳过这一项 +
                    # 设 last_config_error）
                    {"name": "broken"},
                ]
            }
        ),
        encoding="utf-8",
    )

    pool = _StubSessionPool(initial_names={"alpha", "broken", "ghost"})
    registry = McpRegistryService(
        project_root=tmp_path,
        tool_broker=_StubToolBroker(),  # type: ignore[arg-type]
        config_path=config_path,
        session_pool=pool,
    )

    await registry.refresh()

    # last_config_error 设了（broken 校验失败）
    assert registry.last_config_error != ""
    # 但 alpha 仍是 valid config，不是 fatal —— diff-close 必须跑
    valid_names = {c.name for c in registry._load_configs()}
    assert valid_names == {"alpha"}, (
        f"_load_configs partial 行为应过滤 broken，留 alpha：实际 {valid_names}"
    )
    # ghost（不在合法 configs 中，但在 pool 中）必须被 diff-close 清理
    assert "ghost" in pool.close_calls, (
        f"Codex F2 high-1: partial config 下 ghost 应被 diff-close 清理；"
        f"实际 close_calls={pool.close_calls}"
    )
    # broken 也不在合法 configs 中（校验失败被过滤），同样应被清理
    assert "broken" in pool.close_calls, (
        "broken 因 item 校验失败被过滤，不在 valid configs，应被 diff-close 清理"
    )
    # alpha 仍在 valid configs，不应被 diff-close 误关
    assert "alpha" not in pool.close_calls
    # 收尾：alpha 仍在 pool，broken/ghost 已清理
    assert pool.known_server_names() == {"alpha"}
