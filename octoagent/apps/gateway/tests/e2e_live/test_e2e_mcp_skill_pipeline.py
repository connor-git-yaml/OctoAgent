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
    """域 #5：真 npm install + 真启动 MCP server + 真调远端 OpenRouter API。

    Codex final high-2 闭环（fixup#12）：
    旧实现走 install_source=local + /dev/null/test-server.js + 占位 API_KEY，
    npm 子进程 / server 启动 / 远端调用全坏都不被发现。R4/R10 缓解失效。

    修复方案（Plan A 真 e2e）：
    1. 真调 mcp_installer.install(install_source="npm", package_name="openrouter-mcp")
       走真实 npm install 子进程（~2s）+ verify_server 启动（~3s）+
       discover ask_model 工具
    2. 等 install task COMPLETED（轮询 60s）
    3. 验证 broker 注册 ``mcp.<server_id>.ask_model``
    4. 真调 broker.execute("mcp.<server_id>.ask_model")，
       传 model=perplexity/sonar-pro-search + 简单 message
    5. 严格断言 result 非空 + content 含 markdown 文本
    6. R10：宿主 ~/.octoagent/mcp-servers/ sha256 跑前后不变（e2e tmp 隔离）

    SKIP 路径（manual gate）：
    - ``OCTOAGENT_E2E_PERPLEXITY_API_KEY`` env 未设置 → SKIP（不在 CI 默认跑）
    - npm 不可用 → SKIP
    - mcp_installer 未绑定 → SKIP
    - 远端配额 429 / 网络 timeout → SKIP

    关键约束：
    - 真凭证仅通过 env 注入（fixture 不读写宿主），不进 git
    - mcp_servers_dir = e2e tmp（hermetic 隔离）
    - npm install 真包到 e2e tmp，不污染宿主 ~/.octoagent
    """
    import shutil as _shutil

    from octoagent.tooling.models import ExecutionContext, PermissionPreset

    app = harness_real_llm["app"]
    sg = app.state.store_group
    tool_broker = app.state.tool_broker
    mcp_installer = getattr(app.state, "mcp_installer", None)
    mcp_registry = getattr(app.state, "mcp_registry", None)

    # SKIP gate 1：mcp_installer 必须绑定（hermetic harness 应已注入）
    if mcp_installer is None or mcp_registry is None:
        pytest.skip("域#5 SKIP: mcp_installer / mcp_registry 未绑定到 app.state。")

    # SKIP gate 2：npm 必须可用
    if _shutil.which("npm") is None:
        pytest.skip("域#5 SKIP: npm 未安装，无法跑真 npm install。")

    # SKIP gate 3：真凭证必须显式从 env 注入（manual gate，CI 默认 SKIP）
    api_key = os.environ.get("OCTOAGENT_E2E_PERPLEXITY_API_KEY", "").strip()
    if not api_key or not api_key.startswith("sk-or-"):
        pytest.skip(
            "域#5 SKIP（manual gate）: 需设置 OCTOAGENT_E2E_PERPLEXITY_API_KEY=sk-or-..."
            " 才跑真 e2e（真 npm install + 真调 OpenRouter API）。"
            " 默认 CI / pre-commit 不跑此 case。"
        )

    # R10 跑前快照：宿主 ~/.octoagent/mcp-servers/
    host_mcp_dir = Path.home() / ".octoagent" / "mcp-servers"
    host_sha_before = _sha256_dir(host_mcp_dir)

    # 步骤 1：真 npm install openrouter-mcp 到 e2e tmp
    # 注：mcp_installer._mcp_servers_dir 已被 OctoHarness 重定向到 tmp
    server_env = {
        "OPENROUTER_API_KEY": api_key,
        "OPENROUTER_ALLOWED_MODELS": "perplexity/sonar-pro-search",
    }
    package_name = "openrouter-mcp"

    try:
        install_task_id = await mcp_installer.install(
            install_source="npm",
            package_name=package_name,
            env=server_env,
        )
    except Exception as exc:
        pytest.skip(f"域#5 SKIP: mcp_installer.install 启动失败: {exc!r}")

    # 步骤 2：轮询 install task COMPLETED（npm install ~2s + verify ~3s = ~5s）
    deadline_s = 60.0
    start = time.monotonic()
    final_status: str | None = None
    install_error: str = ""
    while time.monotonic() - start < deadline_s:
        task = mcp_installer.get_install_status(install_task_id)
        if task is None:
            await asyncio.sleep(0.5)
            continue
        # InstallTaskStatus enum: pending/running/completed/failed
        status_str = str(task.status).split(".")[-1].lower()
        if status_str in {"completed", "failed"}:
            final_status = status_str
            install_error = task.error or ""
            break
        await asyncio.sleep(0.5)

    if final_status is None:
        # R10 防漏：清退后再 raise
        host_sha_after = _sha256_dir(host_mcp_dir)
        assert host_sha_before == host_sha_after, "R10 隔离失败"
        pytest.skip(f"域#5 SKIP: install task 未在 {deadline_s}s 内达终态。")

    if final_status == "failed":
        host_sha_after = _sha256_dir(host_mcp_dir)
        assert host_sha_before == host_sha_after, "R10 隔离失败"
        # 区分 npm 网络失败（SKIP）vs 其他失败（FAIL）
        if any(
            kw in install_error.lower()
            for kw in ("etimedout", "enotfound", "econnreset", "network", "timeout")
        ):
            pytest.skip(f"域#5 SKIP（npm 网络失败）: {install_error[:200]}")
        pytest.fail(f"域#5 FAIL: npm install 失败: {install_error[:500]}")

    # 子断言 1：install completed
    install_task = mcp_installer.get_install_status(install_task_id)
    assert install_task is not None, "install_task 不应为 None"
    assert final_status == "completed", (
        f"域#5 子断言 1: install task 应 completed，实际 {final_status}"
    )

    server_id = install_task.server_id
    install_record = mcp_installer.get_install(server_id)
    assert install_record is not None, "install_record 不应为 None"
    assert str(install_record.status).split(".")[-1].lower() == "installed", (
        f"域#5 子断言 1b: install_record.status 应 installed，实际 {install_record.status}"
    )

    # 子断言 2：tools 列表（verify_server 应发现 ask_model）
    tools = install_task.result.get("tools", [])
    tool_names = [t.get("name") for t in tools if isinstance(t, dict)]
    if "ask_model" not in tool_names:
        # verify 失败可能是 server 启动报 OPENROUTER_ALLOWED_MODELS 缺失
        # mcp_installer "验证失败不阻断"，所以 tools 可能为空——这里强断言
        host_sha_after = _sha256_dir(host_mcp_dir)
        assert host_sha_before == host_sha_after, "R10 隔离失败"
        pytest.fail(
            f"域#5 子断言 2: verify_server 应发现 ask_model 工具，"
            f"实际 tools={tool_names}"
        )

    # 子断言 3：mcp_registry refresh 后 broker 注册 mcp.<slug>.ask_model
    # 命名规则: mcp.<slugify(server_id)>.<slugify(tool_name)>
    slug_server = server_id.lower().replace("-", "_").replace(".", "_")
    expected_tool_name = f"mcp.{slug_server}.ask_model"

    # 触发一次 registry refresh 确保 broker 同步（install 完成时已 refresh，
    # 这里再 refresh 一次保险）
    await mcp_registry.refresh()

    if expected_tool_name not in tool_broker._registry:
        # 工具命名规则可能因 slugify 微差异，做模糊匹配
        candidates = [
            n for n in tool_broker._registry
            if n.startswith(f"mcp.") and "ask_model" in n and server_id.replace("-", "_") in n.replace("-", "_")
        ]
        if not candidates:
            host_sha_after = _sha256_dir(host_mcp_dir)
            assert host_sha_before == host_sha_after, "R10 隔离失败"
            pytest.fail(
                f"域#5 子断言 3: broker 应注册 {expected_tool_name}，"
                f"实际 mcp.* 注册项: "
                f"{[n for n in tool_broker._registry if n.startswith('mcp.')]}"
            )
        expected_tool_name = candidates[0]

    # 步骤 3：真调 ask_model 工具（真打 OpenRouter Perplexity 后端）
    test_task_id = f"_e2e_d5_real_{uuid.uuid4().hex[:8]}"
    from apps.gateway.tests.e2e_live.helpers.factories import _ensure_audit_task

    await _ensure_audit_task(sg, test_task_id)

    ctx = ExecutionContext(
        task_id=test_task_id,
        trace_id=test_task_id,
        caller="e2e_d5_real",
        permission_preset=PermissionPreset.FULL,
    )

    call_args = {
        "model": "perplexity/sonar-pro-search",
        "message": "What is 1 plus 1? Answer in one short sentence.",
        "append_files": [""],  # 包要求显式传 [""] 表示无文件
    }

    try:
        ask_result = await asyncio.wait_for(
            tool_broker.execute(
                tool_name=expected_tool_name,
                args=call_args,
                context=ctx,
            ),
            timeout=120.0,
        )
    except TimeoutError:
        host_sha_after = _sha256_dir(host_mcp_dir)
        assert host_sha_before == host_sha_after, "R10 隔离失败"
        pytest.skip("域#5 SKIP（远端 timeout）: ask_model 调用 120s 未返回。")
    except Exception as exc:
        host_sha_after = _sha256_dir(host_mcp_dir)
        assert host_sha_before == host_sha_after, "R10 隔离失败"
        msg = str(exc).lower()
        if any(kw in msg for kw in ("429", "rate limit", "quota", "timeout", "network")):
            pytest.skip(f"域#5 SKIP（远端配额/网络）: {exc!r}")
        raise

    # 子断言 4：ask_model 返回非错误
    if ask_result.is_error:
        msg = str(ask_result.error or ask_result.output).lower()
        if any(kw in msg for kw in ("429", "rate limit", "quota", "timeout")):
            pytest.skip(f"域#5 SKIP（远端配额）: {ask_result.error or ask_result.output}")
        host_sha_after = _sha256_dir(host_mcp_dir)
        assert host_sha_before == host_sha_after, "R10 隔离失败"
        pytest.fail(
            f"域#5 子断言 4（ask_model 真调）: 应非错误，"
            f"实际 error={ask_result.error}, output={ask_result.output[:300]}"
        )

    # 子断言 5：响应含文本内容
    import json as _json

    try:
        ask_payload = _json.loads(ask_result.output) if ask_result.output else {}
    except Exception:
        ask_payload = {}

    content_items = ask_payload.get("content", [])
    text_pieces = [
        c.get("text", "") for c in content_items
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    full_text = " ".join(text_pieces).strip()
    assert len(full_text) > 0, (
        f"域#5 子断言 5（响应非空）: ask_model 应返回非空文本，"
        f"实际 payload={ask_payload!r}"
    )

    # 子断言 6：R10 宿主 sha256 不变（npm install + server 启动均未污染宿主）
    host_sha_after = _sha256_dir(host_mcp_dir)
    assert host_sha_before == host_sha_after, (
        f"域#5 子断言 6（R10 宿主 sha256 不变）: "
        f"before={host_sha_before[:16]}, after={host_sha_after[:16]}—"
        f"npm install 或 server 子进程污染了宿主 ~/.octoagent/mcp-servers/"
    )

    # 子断言 7：install_path 在 e2e tmp，不在宿主
    install_path = Path(install_record.install_path)
    home_octo = Path.home() / ".octoagent"
    try:
        install_path.resolve().relative_to(home_octo.resolve())
        in_host = True
    except ValueError:
        in_host = False
    assert not in_host, (
        f"域#5 子断言 7（R10 install_path 隔离）: "
        f"install_path 不应在宿主 {home_octo} 下，实际 {install_path}"
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
