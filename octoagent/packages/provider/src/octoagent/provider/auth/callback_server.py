"""本地 OAuth 回调服务器 -- 对齐 contracts/auth-oauth-pkce-api.md SS4, FR-003

提供两种回调接收模式:
1. **中继模式（推荐，Gateway 上下文）**: 在 1455 端口启动持久中继服务器，
   收到回调后通过 shared future 传递给等待中的 OAuth 流程。
   服务器持久运行，用户多次发起 OAuth 不会端口冲突。
2. **独立 server 模式（降级 / CLI）**: 在独立端口启动临时 HTTP 服务器。
   仅在中继模式不可用时使用。

安全约束:
- 仅绑定 127.0.0.1（不绑定 0.0.0.0）
- 默认 300s 超时后自动取消等待
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


# ---------------------------------------------------------------------------
# 共享 pending flow（模块级单例）
# ---------------------------------------------------------------------------

@dataclass
class _PendingOAuthFlow:
    """跟踪一个正在进行的 OAuth 流程"""
    expected_state: str
    future: asyncio.Future[CallbackResult]


# 当前活跃的 OAuth 流程（同一时间只允许一个）
_pending_flow: _PendingOAuthFlow | None = None


def register_pending_flow(expected_state: str) -> asyncio.Future[CallbackResult]:
    """注册一个新的 OAuth 流程等待回调。

    如果已有正在等待的流程，自动取消旧的（新的覆盖旧的）。
    返回 Future，OAuth 流程 await 它来获取回调结果。
    """
    global _pending_flow
    if _pending_flow is not None:
        if not _pending_flow.future.done():
            _pending_flow.future.cancel()
            log.info("oauth_pending_flow_replaced", reason="新 OAuth 流程覆盖旧的")
        _pending_flow = None

    loop = asyncio.get_event_loop()
    future: asyncio.Future[CallbackResult] = loop.create_future()
    _pending_flow = _PendingOAuthFlow(expected_state=expected_state, future=future)
    log.debug("oauth_pending_flow_registered", state_prefix=expected_state[:8])
    return future


def resolve_pending_flow(code: str, state: str) -> tuple[bool, str]:
    """中继服务器或 Gateway 路由调用此函数传递回调结果。

    Returns:
        (success, message) — success=True 表示匹配成功，False 表示失败原因。
    """
    global _pending_flow
    if _pending_flow is None:
        return False, "没有正在进行的 OAuth 流程"

    if _pending_flow.expected_state != state:
        return False, "state 参数不匹配，可能存在 CSRF 风险，请重新授权"

    if _pending_flow.future.done():
        return False, "OAuth 流程已完成或已取消"

    _pending_flow.future.set_result(CallbackResult(code=code, state=state))
    log.info("oauth_pending_flow_resolved")
    return True, "授权成功"


def cancel_pending_flow() -> None:
    """取消当前挂起的 OAuth 流程。"""
    global _pending_flow
    if _pending_flow is not None and not _pending_flow.future.done():
        _pending_flow.future.cancel()
    _pending_flow = None


async def wait_for_gateway_callback(
    expected_state: str,
    timeout: float = 300.0,
) -> CallbackResult:
    """等待回调结果（通过 shared future）。

    注册一个 pending flow，由中继服务器或 Gateway 路由在收到回调时 resolve。
    调用前应确保中继服务器已通过 ensure_relay_server() 启动。

    Args:
        expected_state: 预期的 state 参数值
        timeout: 超时时间（秒）

    Returns:
        CallbackResult

    Raises:
        OAuthFlowError: 超时或被取消
    """
    future = register_pending_flow(expected_state)
    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except TimeoutError as exc:
        raise OAuthFlowError(
            f"OAuth 回调超时（{timeout}s），请重试",
            provider="",
        ) from exc
    except asyncio.CancelledError:
        raise OAuthFlowError(
            "OAuth 流程被新的授权请求取代，请重新授权",
            provider="",
        )
    finally:
        cancel_pending_flow()


# ---------------------------------------------------------------------------
# 持久中继服务器（在 1455 端口接收 OAuth 回调，转发到 shared future）
# ---------------------------------------------------------------------------

# 模块级中继服务器实例（持久运行，不随单次 OAuth 流程关闭）
_relay_server: asyncio.Server | None = None


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


async def _relay_handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """中继服务器的连接处理器：解析回调请求，调用 resolve_pending_flow()"""
    path = "/auth/callback"
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        if not request_line:
            return

        request_str = request_line.decode("ascii", errors="replace").strip()
        # 消耗剩余 headers
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if line == b"\r\n" or line == b"\n" or not line:
                break

        parts = request_str.split(" ")
        if len(parts) < 2:
            writer.write(_build_http_response(400, "Bad Request", "无效请求"))
            await writer.drain()
            return

        request_path = parts[1]
        parsed = urlparse(request_path)

        if parsed.path != path:
            writer.write(_build_http_response(404, "Not Found", "未找到页面"))
            await writer.drain()
            return

        query_params = parse_qs(parsed.query)
        code_values = query_params.get("code", [])
        state_values = query_params.get("state", [])

        if not code_values or not state_values:
            error_msg = "缺少必需的 code 或 state 参数"
            writer.write(
                _build_http_response(400, "Bad Request", _ERROR_HTML.format(message=error_msg))
            )
            await writer.drain()
            log.warning("oauth_relay_missing_params", path=request_path)
            return

        code = code_values[0]
        state = state_values[0]

        # 通过 shared future 传递回调结果
        success, message = resolve_pending_flow(code=code, state=state)

        if success:
            writer.write(_build_http_response(200, "OK", _SUCCESS_HTML))
        else:
            writer.write(
                _build_http_response(400, "Bad Request", _ERROR_HTML.format(message=message))
            )
        await writer.drain()

    except TimeoutError:
        pass
    except Exception as exc:
        log.debug("oauth_relay_handler_error", error=str(exc))
    finally:
        writer.close()
        with _suppress_connection_error():
            await writer.wait_closed()


async def ensure_relay_server(port: int = 1455) -> bool:
    """确保中继服务器在指定端口运行。

    如果服务器已在运行，直接返回 True（复用）。
    如果未运行，启动新的持久中继服务器。

    中继服务器不会在单次 OAuth 流程结束后关闭，
    而是持久运行直到进程退出。这样用户多次点击
    "连接"按钮不会因端口冲突而失败。

    Args:
        port: 监听端口（默认 1455）

    Returns:
        True 表示服务器已就绪，False 表示启动失败
    """
    global _relay_server

    # 已有运行中的服务器：检查是否还在 serving
    if _relay_server is not None:
        if _relay_server.is_serving():
            log.debug("oauth_relay_server_reused", port=port)
            return True
        # 服务器已停止，清理引用
        _relay_server = None

    try:
        _relay_server = await asyncio.start_server(
            _relay_handle_connection,
            host="127.0.0.1",
            port=port,
        )
        log.info("oauth_relay_server_started", port=port)
        return True
    except OSError as exc:
        log.warning(
            "oauth_relay_server_start_failed",
            port=port,
            error=str(exc),
        )
        return False


# ---------------------------------------------------------------------------
# 独立 server 模式（降级 / CLI 使用）
# ---------------------------------------------------------------------------

async def wait_for_callback(
    port: int = 1455,
    path: str = "/auth/callback",
    expected_state: str = "",
    timeout: float = 300.0,
) -> CallbackResult:
    """启动临时 HTTP 服务器等待 OAuth callback（独立 server 模式）

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
            request_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not request_line:
                return

            request_str = request_line.decode("ascii", errors="replace").strip()
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line == b"\r\n" or line == b"\n" or not line:
                    break

            parts = request_str.split(" ")
            if len(parts) < 2:
                writer.write(_build_http_response(400, "Bad Request", "无效请求"))
                await writer.drain()
                return

            request_path = parts[1]
            parsed = urlparse(request_path)

            if parsed.path != path:
                writer.write(_build_http_response(404, "Not Found", "未找到页面"))
                await writer.drain()
                return

            query_params = parse_qs(parsed.query)
            code_values = query_params.get("code", [])
            state_values = query_params.get("state", [])

            if not code_values or not state_values:
                error_msg = "缺少必需的 code 或 state 参数"
                writer.write(
                    _build_http_response(400, "Bad Request", _ERROR_HTML.format(message=error_msg))
                )
                await writer.drain()
                log.warning("oauth_callback_missing_params", path=request_path)
                return

            code = code_values[0]
            state = state_values[0]

            if expected_state and state != expected_state:
                error_msg = "state 参数不匹配，可能存在 CSRF 风险，请重新授权"
                writer.write(
                    _build_http_response(400, "Bad Request", _ERROR_HTML.format(message=error_msg))
                )
                await writer.drain()
                log.warning("oauth_callback_state_mismatch")
                return

            writer.write(_build_http_response(200, "OK", _SUCCESS_HTML))
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

    server = await asyncio.start_server(
        handle_connection,
        host="127.0.0.1",
        port=port,
    )

    log.debug("oauth_callback_server_started", port=port, path=path)

    try:
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
