"""Feature 078 Phase 3 —— OAuth refresh 硬超时。

验证：
- refresh_access_token 超时时抛 OAuthRefreshTimeoutError
- subprocess 卡住 > timeout_s 会被 subprocess.run timeout 强制终止
- TokenRefreshCoordinator.refresh_if_needed timeout 参数生效，hang 任务不阻塞
"""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from octoagent.provider.auth.oauth_flows import refresh_access_token
from octoagent.provider.exceptions import OAuthFlowError, OAuthRefreshTimeoutError
from octoagent.provider.refresh_coordinator import TokenRefreshCoordinator


# ──────────────────────── refresh_access_token 超时 ──────────────────────


@pytest.mark.asyncio
async def test_refresh_access_token_raises_timeout_when_subprocess_hangs() -> None:
    """subprocess 挂起 > timeout_s → OAuthRefreshTimeoutError。"""
    # 模拟 subprocess.run 抛 TimeoutExpired
    def _hanging_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 15.0))

    with patch("subprocess.run", side_effect=_hanging_run):
        with pytest.raises(OAuthRefreshTimeoutError) as ei:
            await refresh_access_token(
                token_endpoint="https://auth.example.com/oauth/token",
                refresh_token="rt",
                client_id="cid",
                timeout_s=5.0,
            )
    # 异常信息应提到超时秒数
    assert "5" in str(ei.value) or "超时" in str(ei.value)


@pytest.mark.asyncio
async def test_refresh_access_token_raises_timeout_on_curl_exit_28() -> None:
    """curl 以 exit code 28（--max-time 触发）退出 → OAuthRefreshTimeoutError。"""
    fake_result = MagicMock()
    fake_result.returncode = 28
    fake_result.stderr = "curl: (28) Operation timed out after 5000 milliseconds"
    fake_result.stdout = ""

    with patch("subprocess.run", return_value=fake_result):
        with pytest.raises(OAuthRefreshTimeoutError):
            await refresh_access_token(
                token_endpoint="https://auth.example.com/oauth/token",
                refresh_token="rt",
                client_id="cid",
                timeout_s=5.0,
            )


@pytest.mark.asyncio
async def test_refresh_access_token_normal_path_preserves_return() -> None:
    """正常返回时（curl exit 0 + 200），响应被正常解析。"""
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stderr = ""
    # body + "\n200"
    fake_result.stdout = (
        '{"access_token":"new-at","refresh_token":"new-rt","expires_in":28800,"token_type":"Bearer"}\n200'
    )

    with patch("subprocess.run", return_value=fake_result):
        resp = await refresh_access_token(
            token_endpoint="https://auth.example.com/oauth/token",
            refresh_token="rt",
            client_id="cid",
        )
    assert resp.access_token.get_secret_value() == "new-at"
    assert resp.expires_in == 28800


# ────────────────────── TokenRefreshCoordinator 超时 ──────────────────────


@pytest.mark.asyncio
async def test_coordinator_timeout_returns_none_when_refresh_fn_hangs() -> None:
    """refresh_fn 永不返回 → coord 超时 → 返回 None，不阻塞。"""
    coord = TokenRefreshCoordinator()

    async def _never_returns() -> str:
        await asyncio.sleep(10)  # 远大于 timeout
        return "never"

    result = await coord.refresh_if_needed(
        provider_id="test-provider",
        refresh_fn=_never_returns,
        timeout_s=0.5,
    )
    assert result is None


@pytest.mark.asyncio
async def test_coordinator_timeout_releases_lock_for_next_caller() -> None:
    """超时后应释放 lock，下次调用能正常进入（不会一直死锁）。"""
    coord = TokenRefreshCoordinator()

    # 第一次 hang → 超时
    async def _hang() -> str:
        await asyncio.sleep(10)
        return "x"

    r1 = await coord.refresh_if_needed(
        provider_id="p1", refresh_fn=_hang, timeout_s=0.3,
    )
    assert r1 is None

    # 第二次正常 → 必须能拿到 lock
    async def _ok() -> str:
        return "ok-token"

    r2 = await coord.refresh_if_needed(
        provider_id="p1", refresh_fn=_ok, timeout_s=2.0,
    )
    assert r2 == "ok-token"


@pytest.mark.asyncio
async def test_coordinator_does_not_timeout_on_fast_refresh() -> None:
    """正常短时刷新不受 timeout 影响。"""
    coord = TokenRefreshCoordinator()

    async def _fast() -> str:
        await asyncio.sleep(0.01)
        return "fast-token"

    r = await coord.refresh_if_needed(
        provider_id="p2", refresh_fn=_fast, timeout_s=2.0,
    )
    assert r == "fast-token"
