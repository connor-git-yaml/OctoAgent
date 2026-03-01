"""本地 OAuth 回调服务器 -- 对齐 contracts/auth-oauth-pkce-api.md SS4, FR-003

使用 asyncio.start_server 实现轻量 HTTP 服务器，
在 OAuth 流程期间监听本地端口接收授权回调。

安全约束:
- 仅绑定 127.0.0.1（不绑定 0.0.0.0）
- 收到第一个有效回调后立即关闭
- 默认 300s 超时后自动关闭
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import structlog

from ..exceptions import OAuthFlowError

log = structlog.get_logger()

# 成功回调后返回的 HTML 页面
_SUCCESS_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>OctoAgent OAuth</title></head>
<body style="font-family:system-ui;text-align:center;padding:60px">
<h2>授权成功</h2>
<p>可以关闭此窗口并返回终端。</p>
</body>
</html>"""

# 错误页面模板
_ERROR_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>OctoAgent OAuth</title></head>
<body style="font-family:system-ui;text-align:center;padding:60px">
<h2>授权失败</h2>
<p>{message}</p>
</body>
</html>"""


@dataclass(frozen=True, slots=True)
class CallbackResult:
    """OAuth 回调结果"""

    code: str  # 授权码
    state: str  # state 参数（用于 CSRF 验证）


def _build_http_response(status_code: int, status_text: str, body: str) -> bytes:
    """构建简单的 HTTP 响应"""
    body_bytes = body.encode("utf-8")
    headers = (
        f"HTTP/1.1 {status_code} {status_text}\r\n"
        f"Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )
    return headers.encode("ascii") + body_bytes


async def wait_for_callback(
    port: int = 1455,
    path: str = "/auth/callback",
    expected_state: str = "",
    timeout: float = 300.0,
) -> CallbackResult:
    """启动临时 HTTP 服务器等待 OAuth callback

    实现要求:
    - 仅绑定 127.0.0.1（不绑定 0.0.0.0）
    - 验证 callback 中的 state 参数与 expected_state 一致
    - 收到第一个有效 callback 后立即关闭服务器
    - 超时（默认 5 分钟）后自动关闭
    - 返回 HTML 页面告知用户授权结果

    HTTP 响应规则:
    - 非 /auth/callback 路径 -> HTTP 404
    - 缺少 code 或 state 参数 -> HTTP 400
    - state 不匹配 -> HTTP 400
    - 成功 -> HTTP 200 + 成功提示 HTML

    Args:
        port: 监听端口（默认 1455）
        path: 回调路径
        expected_state: 预期的 state 参数值
        timeout: 超时时间（秒）

    Returns:
        CallbackResult 包含 code 和 state

    Raises:
        OAuthFlowError: 超时
        OSError: 端口绑定失败（EADDRINUSE）
    """
    result_future: asyncio.Future[CallbackResult] = asyncio.get_event_loop().create_future()

    async def handle_connection(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """处理单个 HTTP 连接"""
        try:
            # 读取 HTTP 请求行
            request_line = await asyncio.wait_for(
                reader.readline(), timeout=10.0
            )
            if not request_line:
                return

            request_str = request_line.decode("ascii", errors="replace").strip()
            # 读取剩余 headers（丢弃）
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line == b"\r\n" or line == b"\n" or not line:
                    break

            # 解析请求方法和路径
            parts = request_str.split(" ")
            if len(parts) < 2:
                writer.write(_build_http_response(400, "Bad Request", "无效请求"))
                await writer.drain()
                return

            request_path = parts[1]
            parsed = urlparse(request_path)

            # 路由: 非回调路径 -> 404
            if parsed.path != path:
                writer.write(
                    _build_http_response(404, "Not Found", "未找到页面")
                )
                await writer.drain()
                return

            # 解析查询参数
            query_params = parse_qs(parsed.query)
            code_values = query_params.get("code", [])
            state_values = query_params.get("state", [])

            # 缺少 code 或 state -> 400
            if not code_values or not state_values:
                error_msg = "缺少必需的 code 或 state 参数"
                writer.write(
                    _build_http_response(
                        400,
                        "Bad Request",
                        _ERROR_HTML.format(message=error_msg),
                    )
                )
                await writer.drain()
                log.warning("oauth_callback_missing_params", path=request_path)
                return

            code = code_values[0]
            state = state_values[0]

            # state 不匹配 -> 400
            if expected_state and state != expected_state:
                error_msg = "state 参数不匹配，可能存在 CSRF 风险，请重新授权"
                writer.write(
                    _build_http_response(
                        400,
                        "Bad Request",
                        _ERROR_HTML.format(message=error_msg),
                    )
                )
                await writer.drain()
                log.warning("oauth_callback_state_mismatch")
                return

            # 成功 -> 200 + 设置结果
            writer.write(
                _build_http_response(200, "OK", _SUCCESS_HTML)
            )
            await writer.drain()

            if not result_future.done():
                result_future.set_result(CallbackResult(code=code, state=state))

        except TimeoutError:
            pass
        except Exception as exc:
            log.debug("oauth_callback_handler_error", error=str(exc))
        finally:
            writer.close()
            with _suppress_connection_error():
                await writer.wait_closed()

    # 启动服务器（仅绑定 127.0.0.1）
    # 注意: 端口占用时会抛出 OSError，由调用方捕获并降级
    server = await asyncio.start_server(
        handle_connection,
        host="127.0.0.1",
        port=port,
    )

    log.debug("oauth_callback_server_started", port=port, path=path)

    try:
        # 等待结果或超时
        result = await asyncio.wait_for(result_future, timeout=timeout)
        return result
    except TimeoutError as exc:
        raise OAuthFlowError(
            f"OAuth 回调超时（{timeout}s），请重试",
            provider="",
        ) from exc
    finally:
        server.close()
        await server.wait_closed()
        log.debug("oauth_callback_server_closed", port=port)


class _suppress_connection_error:
    """上下文管理器：静默 ConnectionError（客户端提前断开连接时）"""

    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type: type | None, *_: object) -> bool:
        return exc_type is not None and issubclass(exc_type, (ConnectionError, OSError))
