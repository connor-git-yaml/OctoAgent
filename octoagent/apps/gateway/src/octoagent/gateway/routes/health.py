"""健康检查路由 -- 对齐 contracts/rest-api.md §6, §7 + Feature 002 gateway-changes.md §5

GET /health: Liveness 检查，永远返回 200。
GET /ready: Readiness 检查，包含 SQLite 连通性、artifacts_dir、磁盘空间。
         Feature 002: 新增 profile 查询参数，支持 LiteLLM Proxy 健康检查。
"""

import shutil
from pathlib import Path

import structlog
from fastapi import APIRouter, Query, Request
from starlette.responses import JSONResponse

log = structlog.get_logger()

router = APIRouter()


@router.get("/health")
async def health():
    """Liveness 检查 -- 永远返回 200"""
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    request: Request,
    profile: str | None = Query(
        default=None,
        description="检查配置文件：core（默认）仅核心检查；llm/full 包含 LiteLLM Proxy 健康检查",
    ),
):
    """Readiness 检查 -- 验证核心依赖可用性

    Feature 002 扩展：新增 profile 查询参数。

    profile 参数:
        - None / "core": 仅核心检查（M0 行为），litellm_proxy="skipped"
        - "llm": 核心检查 + LiteLLM Proxy 真实健康检查
        - "full": 等同于 "llm"（未来可包含更多检查）

    检查项：
    1. sqlite: 数据库连通性
    2. artifacts_dir: artifacts 目录可访问性
    3. disk_space_mb: 磁盘剩余空间
    4. litellm_proxy: 根据 profile 决定是否探测 Proxy
    """
    # 确定实际 profile（None 默认为 core）
    effective_profile = profile or "core"

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

    # 4. LiteLLM Proxy 健康检查（Feature 002）
    if effective_profile in ("llm", "full"):
        # 仅在有 litellm_client 时真实探测
        litellm_client = getattr(request.app.state, "litellm_client", None)
        if litellm_client is not None:
            try:
                proxy_healthy = await litellm_client.health_check()
                if proxy_healthy:
                    checks["litellm_proxy"] = "ok"
                else:
                    checks["litellm_proxy"] = "unreachable"
                    all_ok = False
            except Exception as e:
                log.warning("health_check_error", error=str(e))
                checks["litellm_proxy"] = "unreachable"
                all_ok = False
        else:
            # Echo 模式：无 litellm_client，跳过探测
            checks["litellm_proxy"] = "skipped"
    else:
        # profile=core 或默认：不探测 Proxy
        checks["litellm_proxy"] = "skipped"

    status_code = 200 if all_ok else 503
    status_text = "ready" if all_ok else "not_ready"

    return JSONResponse(
        status_code=status_code,
        content={
            "status": status_text,
            "profile": effective_profile,
            "checks": checks,
        },
    )
