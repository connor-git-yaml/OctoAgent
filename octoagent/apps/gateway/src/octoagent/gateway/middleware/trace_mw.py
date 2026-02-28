"""TraceMiddleware -- 对齐 spec FR-M0-OB-3

为任务操作绑定 trace_id，贯穿任务生命周期日志。
trace_id 从请求体或路径参数中提取 task_id 生成。
"""

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class TraceMiddleware(BaseHTTPMiddleware):
    """任务级追踪中间件 -- 为任务操作绑定 trace_id"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # 从路径中提取 task_id（如果有）
        path = request.url.path
        trace_id = None

        if "/tasks/" in path:
            # 从 /api/tasks/{task_id} 或 /api/stream/task/{task_id} 提取
            parts = path.split("/")
            for i, part in enumerate(parts):
                if part in ("tasks", "task") and i + 1 < len(parts):
                    task_id = parts[i + 1]
                    # 排除子路由如 /cancel
                    if len(task_id) >= 20:  # ULID 长度
                        trace_id = f"trace-{task_id}"
                        break

        if trace_id:
            structlog.contextvars.bind_contextvars(trace_id=trace_id)

        return await call_next(request)
