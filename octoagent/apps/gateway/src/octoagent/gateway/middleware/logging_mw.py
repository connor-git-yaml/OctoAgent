"""LoggingMiddleware -- 对齐 spec FR-M0-OB-2

为每个 HTTP 请求生成 request_id，绑定到 structlog contextvars。
"""

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from ulid import ULID


class LoggingMiddleware(BaseHTTPMiddleware):
    """请求级日志中间件 -- 为每个请求生成 request_id"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = str(ULID())

        # 绑定 request_id 到 structlog context
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
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

        # 在响应头中返回 request_id
        response.headers["X-Request-ID"] = request_id
        return response
