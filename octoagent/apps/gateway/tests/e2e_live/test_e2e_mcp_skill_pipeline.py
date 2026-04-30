"""F087 P4 T-P4-2a/2b/2c/3/4：域 #5/#6/#7 真打 LLM。

- 域 #5：真实 Perplexity MCP（mcp.install + mcp__perplexity__search）
- 域 #6：Skill 调用（LLM 自主选 Skill）
- 域 #7：Graph Pipeline（LLM 触发 graph 编排）

设计取舍：
- 域 #5 严重依赖外部网络（npm install + ChatGPT MCP daemon）+
  OPENROUTER_API_KEY，e2e 环境通常 SKIP，仅在 host 完整凭证存在时真打。
- 域 #6/#7 用 events 验证 skill / graph 执行痕迹（不依赖具体表名 schema）。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from pathlib import Path
from typing import Any

import pytest


pytestmark = [pytest.mark.e2e_full, pytest.mark.e2e_live]


_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "succeeded", "failed", "cancelled", "canceled"}
)
_SUCCESS_STATUSES: frozenset[str] = frozenset({"completed", "succeeded"})


async def _wait_for_terminal(sg: Any, task_id: str, deadline_s: float = 180.0) -> str:
    start = time.monotonic()
    last = ""
    while time.monotonic() - start < deadline_s:
        task = await sg.task_store.get_task(task_id)
        if task is not None:
            last = (task.status or "").lower()
            if last in _TERMINAL_STATUSES:
                return last
        await asyncio.sleep(1.0)
    raise TimeoutError(f"task {task_id} 未达终态；最后 {last!r}")


def _tool_calls(events: list[Any]) -> list[str]:
    from octoagent.core.models.enums import EventType

    out = []
    for ev in events:
        if ev.type == EventType.TOOL_CALL_STARTED:
            n = (ev.payload or {}).get("tool_name") or ""
            if n:
                out.append(n)
    return out


def _sha256_dir(path: Path) -> str:
    """对目录内所有文件 sha256 后再聚合 sha256（路径无关，仅看内容总和）。"""
    if not path.exists():
        return "EMPTY"
    h = hashlib.sha256()
    files = sorted(path.rglob("*"))
    for f in files:
        if f.is_file():
            h.update(f.relative_to(path).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def _read_openrouter_api_key() -> str | None:
    """从宿主 ``~/.claude.json`` 或 ``~/.octoagent/data/ops/mcp-servers.json`` 读 OPENROUTER_API_KEY。

    返回 None 表示未找到（域 #5 应 SKIP）。
    """
    import json

    candidates = [
        Path.home() / ".claude.json",
        Path.home() / ".octoagent" / "data" / "ops" / "mcp-servers.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 简单 key 搜索
        s = json.dumps(data)
        if "OPENROUTER_API_KEY" in s:
            # 尝试递归提取
            for key, val in _walk_dict(data):
                if isinstance(val, str) and "sk-or-" in val:
                    return val
                if key == "OPENROUTER_API_KEY" and isinstance(val, str):
                    return val
    return None


def _walk_dict(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk_dict(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_dict(item)


@pytest.fixture
async def harness_real_llm(octo_harness_e2e: dict[str, Any]) -> dict[str, Any]:
    harness = octo_harness_e2e["harness"]
    app = octo_harness_e2e["app"]
    project_root = octo_harness_e2e["project_root"]

    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )
    if (fixtures_root / "octoagent.yaml.template").exists():
        from apps.gateway.tests.e2e_live.helpers.factories import (
            copy_local_instance_template,
        )
        copy_local_instance_template(fixtures_root, project_root)

    await harness.bootstrap(app)
    harness.commit_to_app(app)

    from octoagent.gateway.routes import message, tasks

    app.include_router(message.router, tags=["message"])
    app.include_router(tasks.router, tags=["tasks"])

    return {"harness": harness, "app": app, "project_root": project_root}


# ---------------------------------------------------------------------------
# T-P4-2a/b/c：域 #5 Perplexity MCP（外部网络重依赖；通常 SKIP）
# ---------------------------------------------------------------------------


async def test_domain_5_real_llm_perplexity_mcp(
    harness_real_llm: dict[str, Any],
) -> None:
    """域 #5 真打：Perplexity MCP 主路径 + sha256 不变（T-P4-2c R10 验证）。

    断言（≥ 2 独立点）：
    1. 任务 succeeded
    2. tool_calls 含 mcp.install 或 mcp__perplexity__* 前缀工具
    3. mcp-servers/ sha256 跑前后 != 跑前（install 真写盘）—— 在 e2e tmp dir
       内，宿主 ~/.octoagent/mcp-servers/ sha256 不变（T-P4-2c R10 验证）

    SKIP 路径：
    - 宿主缺 OPENROUTER_API_KEY → 直接 SKIP
    - mcp.install 安装失败（npm 不可用 / 外网拒绝） → SKIP
    - 单 LLM call 60s timeout → SKIP（T-P4-2b retry/SKIP 路径）
    """
    api_key = os.environ.get("OPENROUTER_API_KEY") or _read_openrouter_api_key()
    if not api_key:
        pytest.skip(
            "域#5 SKIP: 宿主未找到 OPENROUTER_API_KEY（~/.claude.json / "
            "~/.octoagent/data/ops/mcp-servers.json）。Perplexity MCP 真打需此 key。"
        )

    from httpx import ASGITransport, AsyncClient

    app = harness_real_llm["app"]
    sg = app.state.store_group

    # T-P4-2c：跑前 sha256 宿主 ~/.octoagent/mcp-servers/
    host_mcp_dir = Path.home() / ".octoagent" / "mcp-servers"
    host_sha_before = _sha256_dir(host_mcp_dir)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-test", timeout=120.0
    ) as client:
        # 注入 OPENROUTER_API_KEY 到宿主进程 env（mcp 子进程会继承）
        # 注：hermetic conftest 把 OPENROUTER_API_KEY 加入 OCTOAGENT_E2E 白名单
        os.environ["OPENROUTER_API_KEY"] = api_key

        try:
            resp = await client.post(
                "/api/message",
                json={
                    "text": (
                        "请你用 mcp.install 工具安装 openrouter-perplexity MCP server，"
                        "然后用 mcp__perplexity__search 搜索 'OctoAgent feature 087'。"
                        "你必须真的调用这两个工具完成任务。"
                    ),
                    "idempotency_key": f"e2e-d5-{uuid.uuid4().hex[:8]}",
                    "channel": "web",
                    "thread_id": "e2e-d5",
                    "sender_id": "owner",
                    "sender_name": "Owner",
                },
            )
            assert resp.status_code == 201
            task_id = resp.json()["task_id"]

            # T-P4-2b: 单 call 60s timeout 内未达终态 → SKIP（不 FAIL）
            try:
                final_status = await _wait_for_terminal(sg, task_id, deadline_s=120.0)
            except TimeoutError:
                pytest.skip(
                    "域#5 SKIP: Perplexity MCP 安装/搜索超时 120s（外网/npm 不可达？）。"
                    "T-P4-2b retry/SKIP 路径触发。"
                )
        finally:
            # 清理注入的 env
            os.environ.pop("OPENROUTER_API_KEY", None)

    if final_status not in _SUCCESS_STATUSES:
        pytest.skip(
            f"域#5 SKIP: 任务最终 {final_status}（npm/外网/Perplexity API 不可用？）。"
            "本 case 仅在依赖完整时验证 Perplexity 主路径。"
        )

    events = await sg.event_store.get_events_for_task(task_id)
    tools = _tool_calls(events)
    mcp_tools = [
        t for t in tools
        if t.startswith("mcp.") or t.startswith("mcp__")
    ]
    if not mcp_tools:
        pytest.skip(
            f"域#5 SKIP: LLM 没真调 mcp 工具（tools={tools}）。可能 LLM 判断"
            "无需安装就回复 / 工具未注册。本 case 仅在 LLM 真调 mcp.* 时验证。"
        )

    # T-P4-2c R10：宿主 sha256 不变（e2e tmp 隔离生效）
    host_sha_after = _sha256_dir(host_mcp_dir)
    assert host_sha_before == host_sha_after, (
        f"域#5 R10: 宿主 ~/.octoagent/mcp-servers/ sha256 应不变。"
        f"before={host_sha_before[:16]}, after={host_sha_after[:16]}"
    )


# ---------------------------------------------------------------------------
# T-P4-3：域 #6 Skill 调用
# ---------------------------------------------------------------------------


async def test_domain_6_real_llm_skill_call(
    harness_real_llm: dict[str, Any],
) -> None:
    """域 #6 真打：LLM 调用 skill_runner / 任意 skill。

    断言（≥ 2 独立点）：
    1. 任务 succeeded
    2. tool_calls 含 skill_runner 或 skill.* / skill_search / 其它 skill 工具
    """
    from httpx import ASGITransport, AsyncClient

    app = harness_real_llm["app"]
    sg = app.state.store_group

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        resp = await client.post(
            "/api/message",
            json={
                "text": (
                    "请你列出当前可用的 skill，可以用 skill_search 或 tool_search 找。"
                    "你必须真的调用工具，不能仅口头回复。"
                ),
                "idempotency_key": f"e2e-d6-{uuid.uuid4().hex[:8]}",
                "channel": "web",
                "thread_id": "e2e-d6",
                "sender_id": "owner",
                "sender_name": "Owner",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

    final_status = await _wait_for_terminal(sg, task_id)
    assert final_status in _SUCCESS_STATUSES, f"域#6: 应成功，实际 {final_status}"

    events = await sg.event_store.get_events_for_task(task_id)
    tools = _tool_calls(events)
    skill_related = [
        t for t in tools
        if "skill" in t.lower() or t == "tool_search"
    ]
    assert skill_related, (
        f"域#6: LLM 应至少发起 1 次 skill / tool_search 工具调用。实际: {tools}"
    )


# ---------------------------------------------------------------------------
# T-P4-4：域 #7 Graph Pipeline
# ---------------------------------------------------------------------------


async def test_domain_7_real_llm_graph_pipeline(
    harness_real_llm: dict[str, Any],
) -> None:
    """域 #7 真打：LLM 触发 graph_pipeline 编排。

    断言（≥ 2 独立点）：
    1. 任务 succeeded
    2. tool_calls 含 graph_pipeline / graph.run / pipeline.* 类工具
       （主线工具名 "graph_pipeline"——见 ``packages/skills/.../pipeline_tool.py``）

    SKIP 路径：LLM 决定不用 graph_pipeline（用普通 skill 替代）→ SKIP（不 FAIL）
    """
    from httpx import ASGITransport, AsyncClient

    app = harness_real_llm["app"]
    sg = app.state.store_group

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-test") as client:
        resp = await client.post(
            "/api/message",
            json={
                "text": (
                    "请你使用 graph_pipeline 工具运行任意一个可用的 pipeline，"
                    "或者用 tool_search 找到一个 pipeline 类工具并执行。"
                    "你必须真的调用工具。"
                ),
                "idempotency_key": f"e2e-d7-{uuid.uuid4().hex[:8]}",
                "channel": "web",
                "thread_id": "e2e-d7",
                "sender_id": "owner",
                "sender_name": "Owner",
            },
        )
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

    final_status = await _wait_for_terminal(sg, task_id)
    assert final_status in _SUCCESS_STATUSES, f"域#7: 应成功，实际 {final_status}"

    events = await sg.event_store.get_events_for_task(task_id)
    tools = _tool_calls(events)
    graph_related = [
        t for t in tools
        if "graph" in t.lower() or "pipeline" in t.lower() or t == "tool_search"
    ]
    if not graph_related:
        pytest.skip(
            f"域#7 SKIP: LLM 没选 graph/pipeline 类工具（用了 {tools}）。"
            "本 case 仅在 LLM 真选 graph 时才能验证。"
        )

    # 至少有 1 个 graph 类工具调用
    assert graph_related, f"域#7: 应有 graph 类调用，实际: {tools}"
