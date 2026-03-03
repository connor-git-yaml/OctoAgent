"""LoggingMiddleware -- 对齐 spec FR-M0-OB-2

为每个 HTTP 请求生成 request_id/trace_id/span_id，绑定到 structlog contextvars。
"""

import re

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from ulid import ULID

_TASK_PATH_PATTERN = re.compile(r"/(?:tasks|task)/([0-9A-HJKMNP-TV-Z]{26})(?:/|$)")


class LoggingMiddleware(BaseHTTPMiddleware):
    """请求级日志中间件 -- 为每个请求生成 request_id"""

    def _resolve_trace_id(self, request: Request, request_id: str) -> str:
        """优先复用已有 trace_id，否则按请求生成。"""
        header_trace_id = request.headers.get("x-trace-id")
        if header_trace_id:
            return header_trace_id

        state_trace_id = getattr(request.state, "trace_id", None)
        if isinstance(state_trace_id, str) and state_trace_id:
            return state_trace_id

        path_match = _TASK_PATH_PATTERN.search(request.url.path)
        if path_match:
            return f"trace-{path_match.group(1)}"

        return f"trace-{request_id}"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = str(ULID())
        trace_id = self._resolve_trace_id(request, request_id)
        span_id = str(ULID())

        # 绑定 request/trace/span 到 structlog context
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            trace_id=trace_id,
            span_id=span_id,
            method=request.method,
            path=request.url.path,
        )

        log = structlog.get_logger()
        await log.ainfo("request_started")

        response = await call_next(request)

        await log.ainfo(
            "request_completed",
            status_code=response.status_code,
        )

        # 在响应头中返回关键 trace 上下文
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Span-ID"] = span_id
        return response
