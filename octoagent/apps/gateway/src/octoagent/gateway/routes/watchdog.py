"""Watchdog 路由 -- Feature 011 FR-014

GET /api/tasks/journal: Task Journal 健康状态分组视图

注意：此路由必须在 /api/tasks/{task_id} 之前注册，
避免 FastAPI 将 "journal" 识别为 task_id 路径参数（contracts/rest-api.md 明确要求）。
"""

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from octoagent.gateway.services.task_journal import TaskJournalService
from octoagent.gateway.services.watchdog.config import WatchdogConfig

log = structlog.get_logger()

router = APIRouter()


@router.get("/api/tasks/journal")
async def get_task_journal(request: Request) -> JSONResponse:
    """返回当前所有非终态任务的健康状态分组视图（FR-014）

    实时聚合（Query-time Projection）：每次请求动态从 TaskStore + EventStore 聚合。
    分组固定为四类：running / stalled / drifted / waiting_approval。
    task_status 使用内部完整 TaskStatus（不映射为 A2A 状态，Constitution 原则 14）。

    若 EventStore 不可用，返回 503 降级响应 JOURNAL_DEGRADED。
    """
    try:
        store_group = request.app.state.store_group
        journal_service = TaskJournalService(store_group=store_group)

        # 从 app state 获取 watchdog config（若已注册），否则使用默认配置
        watchdog_config = getattr(request.app.state, "watchdog_config", None)
        if watchdog_config is None:
            watchdog_config = WatchdogConfig.from_env()

        journal = await journal_service.get_journal(config=watchdog_config)

        return JSONResponse(
            status_code=200,
            content=journal.model_dump(),
        )

    except Exception as exc:
        log.warning(
            "task_journal_degraded",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        # EventStore 不可用时返回降级响应（contracts/rest-api.md 错误响应规范）
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "JOURNAL_DEGRADED",
                    "message": "Task Journal is temporarily unavailable due to store error",
                    "generated_at": datetime.now(UTC).isoformat(),
                }
            },
        )
