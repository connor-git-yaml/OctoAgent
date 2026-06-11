"""F119 e2e_live：F123 出站 SSRF 预检端到端补全。

集成 review 缺口：F123 有 url_safety 单测但无 e2e_live——工具入口（web.fetch /
_fetch_browser_page）真接了 SSRF 校验吗？bootstrap 后经 broker 真路径传入私网 URL
真被拦吗？302 逐跳 re-validate 机制真生效吗？

设计原则：
1. 真跑 OctoHarness bootstrap → 真 app.state.capability_pack_service（pack_service）
2. 预检在发包前（_fetch_browser_page 首行 async_ensure_url_safe）→ 拦私网无需真网络
3. DNS 解析 seam（_resolve_host）monkeypatch 注入私网 IP，hermetic
4. 每个 case ≥ 2 独立断言点（参数化覆盖多类内网地址）

AC 绑定（spec §3）：
- AC-123-1 → test_ssrf_fetch_browser_page_blocks_internal
- AC-123-2 → test_ssrf_web_fetch_via_broker_blocked
- AC-123-3 → test_ssrf_hostname_resolving_to_private_blocked
- AC-123-4 → test_ssrf_redirect_hook_revalidates_each_hop
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest


pytestmark = [pytest.mark.e2e_full, pytest.mark.e2e_live]


# 参数化内网地址：覆盖云元数据 / loopback / 私网 / CGNAT / IPv6 loopback。
_INTERNAL_URLS = [
    "http://169.254.169.254/latest/meta-data/",  # AWS/GCP 云元数据（永久拦）
    "http://127.0.0.1:8080/admin",  # loopback（永久拦）
    "http://[::1]/",  # IPv6 loopback（永久拦）
    "http://10.0.0.5/internal",  # RFC1918 私网（开关关闭拦）
    "http://100.64.1.1/cgnat",  # CGNAT 100.64/10（开关关闭拦）
]


@pytest.fixture
async def bootstrapped_harness(octo_harness_e2e: dict[str, Any]) -> dict[str, Any]:
    """真跑 OctoHarness.bootstrap → 拿 app.state.capability_pack_service + tool_broker。"""
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
    return {"harness": harness, "app": app, "project_root": project_root}


# ---------------------------------------------------------------------------
# AC-123-1：_fetch_browser_page 拦各类内网地址
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", _INTERNAL_URLS)
async def test_ssrf_fetch_browser_page_blocks_internal(
    bootstrapped_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    """AC-123-1：pack_service._fetch_browser_page(内网url) 抛 UnsafeUrlError（发包前预检）。

    断言（≥ 2 独立点）：
    1. 抛 UnsafeUrlError（预检命中，未真发包）
    2. 异常信息含"拒绝"（结构化错误，非网络错误）
    """
    from octoagent.gateway.harness.url_safety import UnsafeUrlError

    # 确保私网/CGNAT 也被拦（开关默认关闭，显式 delenv 防宿主 env 污染）
    monkeypatch.delenv("OCTOAGENT_ALLOW_PRIVATE_URLS", raising=False)

    app = bootstrapped_harness["app"]
    pack_service = app.state.capability_pack_service

    with pytest.raises(UnsafeUrlError) as exc_info:
        await pack_service._fetch_browser_page(url, timeout_seconds=5.0)

    assert "拒绝" in str(exc_info.value), (
        f"AC-123-1: SSRF 拦截应给结构化错误（含'拒绝'），实际 {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# AC-123-2：web.fetch 经 broker 真路径被拦
# ---------------------------------------------------------------------------


async def test_ssrf_web_fetch_via_broker_blocked(
    bootstrapped_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-123-2：tool_broker.execute("web.fetch", 私网) → ToolResult.is_error。

    断言（≥ 2 独立点）：
    1. web.fetch 真注册到 bootstrap 后的 broker（工具入口存在）
    2. broker.execute 私网 url → is_error=True + error 含"拒绝"（入口真接 SSRF 校验）
    """
    from octoagent.tooling.models import ExecutionContext, PermissionPreset

    monkeypatch.delenv("OCTOAGENT_ALLOW_PRIVATE_URLS", raising=False)

    app = bootstrapped_harness["app"]
    tool_broker = app.state.tool_broker

    assert "web.fetch" in tool_broker._registry, (
        "AC-123-2: web.fetch 应在 bootstrap 后注册到 broker（capability pack startup）"
    )

    ctx = ExecutionContext(
        task_id="_e2e_ssrf_broker_task",
        trace_id="_e2e_ssrf_broker_task",
        caller="e2e_ssrf",
        permission_preset=PermissionPreset.FULL,
    )
    result = await tool_broker.execute(
        tool_name="web.fetch",
        args={"url": "http://169.254.169.254/latest/meta-data/"},
        context=ctx,
    )

    assert result.is_error is True, (
        f"AC-123-2: web.fetch 私网 url 应 is_error=True，实际 error={result.error}"
    )
    assert "拒绝" in (result.error or ""), (
        f"AC-123-2: error 应来自 SSRF 拦截（含'拒绝'），实际 {result.error!r}"
    )


# ---------------------------------------------------------------------------
# AC-123-3：DNS 解析到私网也拦
# ---------------------------------------------------------------------------


async def test_ssrf_hostname_resolving_to_private_blocked(
    bootstrapped_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-123-3：hostname 解析到私网 IP → UnsafeUrlError（非仅字面量 IP）。

    断言（≥ 2 独立点）：
    1. monkeypatch _resolve_host 让公网样貌域名解析到私网 IP → 抛 UnsafeUrlError
    2. 异常信息含解析出的私网 IP（证明走的是解析后逐 IP 判定路径）
    """
    from octoagent.gateway.harness import url_safety
    from octoagent.gateway.harness.url_safety import UnsafeUrlError, ensure_url_safe

    monkeypatch.delenv("OCTOAGENT_ALLOW_PRIVATE_URLS", raising=False)
    # 模拟 DNS rebinding：看似公网域名，实际解析到内网
    monkeypatch.setattr(url_safety, "_resolve_host", lambda hostname: ["10.10.10.10"])

    with pytest.raises(UnsafeUrlError) as exc_info:
        ensure_url_safe("http://innocent-looking.example.com/data")

    assert "10.10.10.10" in str(exc_info.value), (
        f"AC-123-3: 应在解析后逐 IP 判定时拦截私网 IP，实际 {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# AC-123-4：302 逐跳 re-validate 机制
# ---------------------------------------------------------------------------


async def test_ssrf_redirect_hook_revalidates_each_hop(
    bootstrapped_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-123-4：_ssrf_request_hook 对私网 request 抛 UnsafeUrlError（每跳前校验）。

    302 重定向 re-validate 的核心机制：httpx request event-hook 在每跳出站前重跑
    SSRF 校验——公网 URL 经 302 绕进内网时，第二跳 request 被 hook 拦。

    断言（≥ 2 独立点）：
    1. hook 对内网 request 抛 UnsafeUrlError（逐跳校验机制活）
    2. hook 对公网 request 放行（不误伤，monkeypatch 解析到公网 IP）
    """
    from octoagent.gateway.harness import url_safety
    from octoagent.gateway.harness.url_safety import UnsafeUrlError
    from octoagent.gateway.services.capability_pack import _ssrf_request_hook

    monkeypatch.delenv("OCTOAGENT_ALLOW_PRIVATE_URLS", raising=False)

    # 内网跳：被拦（字面量私网 IP，无需 DNS）
    internal_req = httpx.Request("GET", "http://10.0.0.9/secret")
    with pytest.raises(UnsafeUrlError):
        await _ssrf_request_hook(internal_req)

    # 公网跳：放行（monkeypatch 解析到公网 IP，证明 hook 不误伤正常重定向）
    monkeypatch.setattr(url_safety, "_resolve_host", lambda hostname: ["93.184.216.34"])
    public_req = httpx.Request("GET", "http://example.com/page")
    await _ssrf_request_hook(public_req)  # 不应抛异常
