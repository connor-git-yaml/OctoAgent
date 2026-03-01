"""回调服务器单元测试 -- T009

验证:
- 成功回调返回 CallbackResult
- 超时抛出 OAuthFlowError
- 无效路径返回 404
- 缺少参数返回 400
- state 不匹配返回 400
对齐 FR-003
"""

from __future__ import annotations

import asyncio

import pytest

from octoagent.provider.auth.callback_server import CallbackResult, wait_for_callback
from octoagent.provider.exceptions import OAuthFlowError


async def _send_http_request(port: int, request_line: str) -> str:
    """发送原始 HTTP 请求并返回响应"""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"{request_line}\r\nHost: localhost\r\n\r\n".encode())
    await writer.drain()
    response = await reader.read(4096)
    writer.close()
    await writer.wait_closed()
    return response.decode("utf-8", errors="replace")


class TestCallbackServerSuccess:
    """成功回调场景"""

    async def test_valid_callback_returns_result(self) -> None:
        """有效回调返回 CallbackResult"""
        port = 18901
        expected_state = "test-state-abc"

        async def send_callback():
            await asyncio.sleep(0.1)  # 等待服务器启动
            response = await _send_http_request(
                port,
                f"GET /auth/callback?code=auth-code-123&state={expected_state} HTTP/1.1",
            )
            assert "200" in response
            assert "授权成功" in response

        result_task = asyncio.create_task(
            wait_for_callback(
                port=port,
                expected_state=expected_state,
                timeout=5.0,
            )
        )
        sender_task = asyncio.create_task(send_callback())

        result = await result_task
        await sender_task

        assert isinstance(result, CallbackResult)
        assert result.code == "auth-code-123"
        assert result.state == expected_state

    async def test_callback_result_frozen(self) -> None:
        """CallbackResult 是 frozen dataclass"""
        result = CallbackResult(code="c", state="s")
        with pytest.raises(AttributeError):
            result.code = "new"  # type: ignore[misc]


class TestCallbackServerTimeout:
    """超时场景"""

    async def test_timeout_raises_oauth_flow_error(self) -> None:
        """超时抛出 OAuthFlowError"""
        with pytest.raises(OAuthFlowError, match="超时"):
            await wait_for_callback(
                port=18902,
                expected_state="timeout-state",
                timeout=0.5,
            )


class TestCallbackServerInvalidRequests:
    """无效请求场景"""

    async def test_wrong_path_returns_404(self) -> None:
        """非 /auth/callback 路径返回 404"""
        port = 18903
        expected_state = "state-404"

        async def send_wrong_path():
            await asyncio.sleep(0.1)
            response = await _send_http_request(
                port,
                "GET /wrong/path HTTP/1.1",
            )
            assert "404" in response

        # 发送错误路径请求后，超时关闭
        server_task = asyncio.create_task(
            wait_for_callback(
                port=port,
                expected_state=expected_state,
                timeout=1.0,
            )
        )
        sender_task = asyncio.create_task(send_wrong_path())

        await sender_task
        with pytest.raises(OAuthFlowError, match="超时"):
            await server_task

    async def test_missing_code_returns_400(self) -> None:
        """缺少 code 参数返回 400"""
        port = 18904
        expected_state = "state-400-code"

        async def send_no_code():
            await asyncio.sleep(0.1)
            response = await _send_http_request(
                port,
                f"GET /auth/callback?state={expected_state} HTTP/1.1",
            )
            assert "400" in response

        server_task = asyncio.create_task(
            wait_for_callback(
                port=port,
                expected_state=expected_state,
                timeout=1.0,
            )
        )
        sender_task = asyncio.create_task(send_no_code())

        await sender_task
        with pytest.raises(OAuthFlowError, match="超时"):
            await server_task

    async def test_missing_state_returns_400(self) -> None:
        """缺少 state 参数返回 400"""
        port = 18905

        async def send_no_state():
            await asyncio.sleep(0.1)
            response = await _send_http_request(
                port,
                "GET /auth/callback?code=test-code HTTP/1.1",
            )
            assert "400" in response

        server_task = asyncio.create_task(
            wait_for_callback(
                port=port,
                expected_state="expected",
                timeout=1.0,
            )
        )
        sender_task = asyncio.create_task(send_no_state())

        await sender_task
        with pytest.raises(OAuthFlowError, match="超时"):
            await server_task

    async def test_state_mismatch_returns_400(self) -> None:
        """state 不匹配返回 400"""
        port = 18906

        async def send_wrong_state():
            await asyncio.sleep(0.1)
            response = await _send_http_request(
                port,
                "GET /auth/callback?code=test-code&state=wrong-state HTTP/1.1",
            )
            assert "400" in response
            assert "state" in response.lower() or "CSRF" in response

        server_task = asyncio.create_task(
            wait_for_callback(
                port=port,
                expected_state="correct-state",
                timeout=1.0,
            )
        )
        sender_task = asyncio.create_task(send_wrong_state())

        await sender_task
        with pytest.raises(OAuthFlowError, match="超时"):
            await server_task
