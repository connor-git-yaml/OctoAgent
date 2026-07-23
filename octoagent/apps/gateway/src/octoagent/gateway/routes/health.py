"""健康检查路由 -- 对齐 contracts/rest-api.md §6, §7 + Feature 012

GET /health: Liveness 检查，永远返回 200。
GET /ready: Readiness 检查，包含 SQLite 连通性、artifacts_dir、磁盘空间。
         Feature 012: 增加 subsystems + tool registry diagnostics 摘要。
"""

import shutil
from pathlib import Path

import structlog
from fastapi import APIRouter, Request
from octoagent.gateway.services.operations.backup_service import (
    resolve_data_dir,
    resolve_project_root,
)
from octoagent.gateway.services.operations.recovery_status_store import RecoveryStatusStore
from starlette.responses import JSONResponse

log = structlog.get_logger()

router = APIRouter()


def _collect_subsystem_health(
    request: Request,
) -> tuple[dict[str, str], dict[str, dict[str, int | bool]]]:
    """收集 M1.5 关键子系统状态（不影响 core readiness 判定）。"""
    state = request.app.state
    subsystems: dict[str, str] = {}

    subsystems["orchestrator"] = (
        "ok" if getattr(state, "orchestrator", None) is not None else "unavailable"
    )

    task_runner = getattr(state, "task_runner", None)
    subsystems["worker_runtime"] = "ok" if task_runner is not None else "unavailable"

    store_group = getattr(state, "store_group", None)
    checkpoint_store = getattr(store_group, "task_job_store", None) if store_group else None
    subsystems["checkpoint"] = "ok" if checkpoint_store is not None else "unavailable"

    if task_runner is None:
        subsystems["watchdog"] = "unavailable"
    else:
        monitor_task = getattr(task_runner, "_monitor_task", None)
        if monitor_task is None:
            subsystems["watchdog"] = "unavailable"
        elif monitor_task.done():
            subsystems["watchdog"] = "degraded"
        else:
            subsystems["watchdog"] = "ok"

    tool_broker = getattr(state, "tool_broker", None)
    diagnostics_count = 0
    if tool_broker is None:
        subsystems["tool_registry"] = "unavailable"
    else:
        diagnostics = getattr(tool_broker, "registry_diagnostics", [])
        if callable(diagnostics):
            diagnostics = diagnostics()
        diagnostics_count = len(diagnostics) if diagnostics is not None else 0
        subsystems["tool_registry"] = "degraded" if diagnostics_count > 0 else "ok"

    recovery_store = RecoveryStatusStore(
        resolve_project_root(),
        data_dir=resolve_data_dir(),
    )
    recovery_summary = recovery_store.load_summary()
    if recovery_summary.ready_for_restore:
        subsystems["recovery"] = "ok"
    elif recovery_summary.latest_recovery_drill is None:
        subsystems["recovery"] = "unavailable"
    else:
        subsystems["recovery"] = "degraded"

    return subsystems, {
        "tool_registry": {"diagnostics_count": diagnostics_count},
        "recovery": {
            "ready_for_restore": recovery_summary.ready_for_restore,
        },
    }


@router.get("/health")
async def health():
    """Liveness 检查 -- 永远返回 200"""
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    """Readiness 检查 -- 验证核心依赖可用性

    检查项：
    1. sqlite: 数据库连通性
    2. artifacts_dir: artifacts 目录可访问性
    3. disk_space_mb: 磁盘剩余空间
    4. provider_route: canonical alias 可在本地解析为 Provider 路由
    """
    checks = {}
    all_ok = True

    # 1. SQLite 连通性检查
    try:
        store_group = request.app.state.store_group
        cursor = await store_group.conn.execute("SELECT 1")
        await cursor.fetchone()
        checks["sqlite"] = "ok"
    except Exception as e:
        log.warning(
            "ready_sqlite_check_failed",
            error_type=type(e).__name__,
        )
        checks["sqlite"] = "unavailable"
        all_ok = False

    # 2. Artifacts 目录检查
    try:
        store_group = request.app.state.store_group
        artifacts_dir = store_group.artifact_store._artifacts_dir
        if isinstance(artifacts_dir, (str, Path)):
            artifacts_path = Path(artifacts_dir)
            if artifacts_path.exists() and artifacts_path.is_dir():
                checks["artifacts_dir"] = "ok"
            else:
                checks["artifacts_dir"] = "error: directory does not exist"
                all_ok = False
        else:
            checks["artifacts_dir"] = "ok"
    except Exception as e:
        log.warning(
            "ready_artifacts_check_failed",
            error_type=type(e).__name__,
        )
        checks["artifacts_dir"] = "unavailable"
        all_ok = False

    # 3. 磁盘空间检查
    try:
        disk_usage = shutil.disk_usage("/")
        disk_space_mb = disk_usage.free // (1024 * 1024)
        checks["disk_space_mb"] = disk_space_mb
    except Exception:
        checks["disk_space_mb"] = 0
        all_ok = False

    # 4. Canonical Provider 路由只做本地结构解析，不触发网络或模型调用。
    provider_route_diagnostics: dict[str, str] = {}
    try:
        alias_registry = request.app.state.alias_registry
        provider_router = request.app.state.provider_router
        canonical_alias = alias_registry.resolve("main")
        resolved = provider_router.resolve_for_alias(
            canonical_alias,
            task_scope=None,
        )
        checks["provider_route"] = "ok"
        provider_route_diagnostics = {
            "alias": canonical_alias,
            "provider": resolved.provider_id,
            "model": resolved.model_name,
        }
    except Exception as e:
        log.warning(
            "ready_provider_route_check_failed",
            error_type=type(e).__name__,
        )
        checks["provider_route"] = "unavailable"
        all_ok = False

    subsystems, diagnostics = _collect_subsystem_health(request)
    diagnostics["provider_route"] = provider_route_diagnostics

    status_code = 200 if all_ok else 503
    status_text = "ready" if all_ok else "not_ready"

    return JSONResponse(
        status_code=status_code,
        content={
            "status": status_text,
            "checks": checks,
            "subsystems": subsystems,
            "diagnostics": diagnostics,
        },
    )
