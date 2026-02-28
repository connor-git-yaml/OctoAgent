"""健康检查路由 -- 对齐 contracts/rest-api.md §6, §7

GET /health: Liveness 检查，永远返回 200。
GET /ready: Readiness 检查，包含 SQLite 连通性、artifacts_dir、磁盘空间。
"""

import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

router = APIRouter()


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
    4. litellm_proxy: M0 固定返回 skipped
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
        checks["sqlite"] = f"error: {str(e)}"
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
        checks["artifacts_dir"] = f"error: {str(e)}"
        all_ok = False

    # 3. 磁盘空间检查
    try:
        disk_usage = shutil.disk_usage("/")
        disk_space_mb = disk_usage.free // (1024 * 1024)
        checks["disk_space_mb"] = disk_space_mb
    except Exception:
        checks["disk_space_mb"] = 0
        all_ok = False

    # 4. LiteLLM Proxy（M0 固定 skipped）
    checks["litellm_proxy"] = "skipped"

    status_code = 200 if all_ok else 503
    status_text = "ready" if all_ok else "not_ready"

    return JSONResponse(
        status_code=status_code,
        content={
            "status": status_text,
            "profile": "core",
            "checks": checks,
        },
    )
