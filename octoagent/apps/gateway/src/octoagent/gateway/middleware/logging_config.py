"""structlog 配置模块 -- 对齐 spec FR-M0-OB-1/4

dev 模式：pretty print 可读输出
json 模式：结构化 JSON 输出
Logfire APM：LOGFIRE_SEND_TO_LOGFIRE 环境变量控制，false 时降级为纯本地日志。
"""

import logging
import os

import structlog


def setup_logging() -> None:
    """初始化 structlog 配置

    根据 OCTOAGENT_LOG_FORMAT 环境变量选择渲染模式：
    - "json": 结构化 JSON 输出（生产环境）
    - "dev" (默认): pretty print 可读输出
    """
    log_format = os.environ.get("OCTOAGENT_LOG_FORMAT", "dev")
    log_level = os.environ.get("OCTOAGENT_LOG_LEVEL", "INFO")

    # 基础处理器链
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if log_format == "json":
        # 生产模式：JSON 输出
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        # 开发模式：pretty print
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # 配置标准库 logging
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))


def setup_logfire() -> None:
    """Logfire 可选初始化

    LOGFIRE_SEND_TO_LOGFIRE 环境变量控制：
    - "true": 启用 Logfire APM（需要 LOGFIRE_TOKEN）
    - "false" (默认): 降级为纯本地日志
    """
    send_to_logfire = os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "false").lower()
    if send_to_logfire == "true":
        try:
            import logfire

            logfire.configure()
            logfire.instrument_fastapi()
        except Exception:
            # Logfire 初始化失败不影响系统运行（C6: Degrade Gracefully）
            structlog.get_logger().warning(
                "logfire_init_failed",
                message="Logfire 初始化失败，降级为纯本地日志",
            )
