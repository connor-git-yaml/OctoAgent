"""dotenv 自动加载 -- 对齐 spec FR-009, contracts/dx-cli-api.md

Gateway 启动时自动加载 .env 文件。
- .env 不存在时静默跳过（不阻塞启动）
- 语法错误仅记录 warning 日志，不阻塞启动（EC-7）
- override=False：已设置的环境变量不被覆盖
"""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger()


def load_project_dotenv(
    project_root: Path | None = None,
    override: bool = False,
) -> bool:
    """加载项目根目录的 .env 文件

    Args:
        project_root: 项目根目录路径，None 时使用当前工作目录
        override: 是否覆盖已有环境变量（默认 False）

    Returns:
        True 表示成功加载，False 表示跳过或失败
    """
    if project_root is None:
        project_root = Path.cwd()

    env_path = project_root / ".env"

    # .env 不存在时静默跳过
    if not env_path.exists():
        log.debug("dotenv_skip", path=str(env_path), reason="文件不存在")
        return False

    try:
        from dotenv import load_dotenv

        loaded = load_dotenv(
            dotenv_path=str(env_path),
            override=override,
        )
        if loaded:
            log.info("dotenv_loaded", path=str(env_path), override=override)
        else:
            log.debug("dotenv_empty", path=str(env_path))
        return loaded
    except Exception as exc:
        # 语法错误或其他异常：仅 warning，不阻塞启动（EC-7）
        log.warning(
            "dotenv_load_error",
            path=str(env_path),
            error=str(exc),
        )
        return False
