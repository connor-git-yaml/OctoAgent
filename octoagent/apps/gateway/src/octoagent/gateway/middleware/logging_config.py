"""structlog 配置模块 -- 对齐 spec FR-M0-OB-1/4 + F129 FR-D/FR-E

dev 模式：pretty print 可读输出
json 模式：结构化 JSON 输出
Logfire APM：LOGFIRE_SEND_TO_LOGFIRE 环境变量控制，false 时降级为纯本地日志。

F129 常驻服务地基新增（`.specify/features/129-service-foundation/` DP-6）：

- **日志落盘（FR-D1/D2）**：``RotatingFileHandler`` → ``<log_dir>/octoagent.log``
  （size 轮转，默认 10MB × 5，env ``OCTOAGENT_LOG_MAX_BYTES`` /
  ``OCTOAGENT_LOG_BACKUP_COUNT`` 可覆盖）。目录解析链：
  ``OCTOAGENT_LOG_DIR``（显式）→ ``$OCTOAGENT_PROJECT_ROOT/logs``（托管实例，
  run-octo-home.sh 必设）→ 都缺省则**不落盘**（前台 dev / hermetic 单测走
  StreamHandler baseline，绝不隐式写用户 ``~/.octoagent``）。
  FileHandler 是进程内 handler，不受 Popen DEVNULL 重定向影响——这是
  「日志不再随终端消失」的核心机制（FR-D2）。
- **脱敏（FR-E1）**：``_RedactingProcessorFormatter`` 在**最终字符串层**跑
  ``redact_sensitive_text()``，对所有 handler（Stream + File）一处覆盖，
  且连 stdlib 拼接的 exception 文本一并脱敏（比 processor 层覆盖面更大）。
- **崩溃兜底（FR-D3）**：``sys.excepthook`` 链式包装（未捕获异常经 logger
  落盘，自动脱敏）+ ``faulthandler.enable(file=...)``（C 层崩溃线程栈 dump
  到 ``octoagent-crash.log``；dump 内容为栈帧/函数名，无 secret 值面）。
- **不阻塞主流程（FR-D5，Constitution #6）**：file sink 构造失败（权限/磁盘）
  只 warning 降级 StreamHandler；emit 期故障由 logging 框架 handleError 吞掉。
"""

import contextlib
import faulthandler
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Any

import structlog

from .log_redaction import redact_sensitive_text

_LOGFIRE_INITIALIZED = False

#: 进程内日志文件名。与 provider dx ``service_manager.PROCESS_LOG_FILE`` 是
#: 同一契约（`octo logs` / status last_error_line 都读它）——改名须两处同步。
PROCESS_LOG_FILE_NAME = "octoagent.log"

#: faulthandler 崩溃栈 dump 文件（与主日志分离：faulthandler 直写 fd，
#: 与 RotatingFileHandler 的轮转 rename 同文件会互相破坏）。
CRASH_LOG_FILE_NAME = "octoagent-crash.log"

_DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
_DEFAULT_LOG_BACKUP_COUNT = 5

#: 崩溃钩子只装一次（setup_logging 会被多次调用，如多个 create_app），
#: 防 excepthook 无限嵌套包装。
_CRASH_HOOKS_INSTALLED = False
#: faulthandler 需要 fd 存活，模块级持有防 GC 关闭。
_CRASH_FILE_HANDLE: IO[str] | None = None


class _RedactingProcessorFormatter(structlog.stdlib.ProcessorFormatter):
    """最终字符串层脱敏 formatter（FR-E1，Hermes RedactingFormatter 范式）。

    在 ``format()`` 产出最终字符串后统一跑脱敏——无论 renderer 是 Console
    还是 JSON、无论 exception 文本由 structlog 还是 stdlib 拼接，落到任何
    handler 的字节都已脱敏。``redact_sensitive_text`` 内部保证不抛。
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_text(super().format(record))


class _SecureRotatingFileHandler(RotatingFileHandler):
    """0600 权限的轮转 handler（FR-D5：日志文件属敏感，仅 owner 可读写）。

    ``_open()`` 在初建与每次 rollover 后都会被调用，新文件都收紧权限；
    轮转备份（.1/.2/...）由 rename 产生，继承原文件权限。
    """

    def _open(self) -> IO[str]:
        stream = super()._open()
        # chmod 失败不阻塞日志（#6）
        with contextlib.suppress(OSError):
            os.chmod(self.baseFilename, 0o600)
        return stream


def _env_int(name: str, default: int) -> int:
    """解析正整数 env；缺失/非法/非正值一律回落默认（不抛，#6）。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _resolve_log_dir() -> Path | None:
    """解析日志目录（FR-D1）。

    1. ``OCTOAGENT_LOG_DIR``：显式覆盖（测试 / 非标准布局）。
    2. ``$OCTOAGENT_PROJECT_ROOT/logs``：托管实例主路径——run-octo-home.sh
       恒 export 该变量（service / `octo restart` 全走它），与
       service_manager 的 ``ServiceManager.log_dir`` 同源同值。
    3. 都未设 → None（不落盘）：前台 dev 有终端 stdout；hermetic 单测
       不得隐式写用户 ``~/.octoagent``（spec 偏离已在 completion-report
       归档：FR-D1 字面默认 ``~/.octoagent/logs``，实现收窄为 env 驱动）。
    """
    explicit = os.environ.get("OCTOAGENT_LOG_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    project_root = os.environ.get("OCTOAGENT_PROJECT_ROOT", "").strip()
    if project_root:
        return Path(project_root).expanduser() / "logs"
    return None


def _build_file_handler(formatter: logging.Formatter) -> logging.Handler | None:
    """构造落盘 handler；任何失败返回 None 降级 StreamHandler（FR-D5，#6）。"""
    log_dir = _resolve_log_dir()
    if log_dir is None:
        return None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        # 目录 0700：日志属敏感（脱敏非万能，FR-E5），仅 owner 可进
        with contextlib.suppress(OSError):
            os.chmod(log_dir, 0o700)
        handler = _SecureRotatingFileHandler(
            log_dir / PROCESS_LOG_FILE_NAME,
            maxBytes=_env_int("OCTOAGENT_LOG_MAX_BYTES", _DEFAULT_LOG_MAX_BYTES),
            backupCount=_env_int("OCTOAGENT_LOG_BACKUP_COUNT", _DEFAULT_LOG_BACKUP_COUNT),
            encoding="utf-8",
        )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "日志落盘初始化失败，降级为仅 stdout（Constitution #6）: %s: %s",
            type(exc).__name__,
            exc,
        )
        return None
    handler.setFormatter(formatter)
    return handler


def _install_crash_hooks(log_dir: Path) -> None:
    """崩溃 traceback 落盘（FR-D3）：sys.excepthook 链式包装 + faulthandler。

    幂等（模块级 sentinel）；仅在落盘启用时安装——无 file sink 时崩溃输出
    仍走 stderr（service 层 StandardErrorPath 兜底，DP-6 层 2）。
    """
    global _CRASH_HOOKS_INSTALLED, _CRASH_FILE_HANDLE
    if _CRASH_HOOKS_INSTALLED:
        return

    try:
        crash_path = log_dir / CRASH_LOG_FILE_NAME
        crash_handle = crash_path.open("a", encoding="utf-8")
        with contextlib.suppress(OSError):  # pragma: no cover
            os.chmod(crash_path, 0o600)
        faulthandler.enable(file=crash_handle)
        _CRASH_FILE_HANDLE = crash_handle
    except Exception:  # noqa: S110 - faulthandler 失败不阻塞主流程（#6）
        pass

    previous_hook = sys.excepthook

    def _logging_excepthook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: Any,
    ) -> None:
        # 崩溃路径上日志故障不再级联（#6）
        with contextlib.suppress(Exception):
            logging.getLogger("octoagent.crash").critical(
                "uncaught_exception",
                exc_info=(exc_type, exc_value, exc_tb),
            )
        previous_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _logging_excepthook
    _CRASH_HOOKS_INSTALLED = True


def setup_logging() -> None:
    """初始化 structlog 配置

    根据 OCTOAGENT_LOG_FORMAT 环境变量选择渲染模式：
    - "json": 结构化 JSON 输出（生产环境）
    - "dev" (默认): pretty print 可读输出

    F129：如落盘目录可解析（见 ``_resolve_log_dir``），额外挂
    RotatingFileHandler + 崩溃钩子；StreamHandler 恒保留（FR-D1）。
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

    # 配置标准库 logging（F129 FR-E1：formatter 层统一脱敏，Stream + File 共用）
    formatter = _RedactingProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # F129 FR-D1/D2/D3：日志落盘 + 崩溃钩子（目录不可解析则保持 baseline）
    file_handler = _build_file_handler(formatter)
    if file_handler is not None:
        root_logger.addHandler(file_handler)
        _install_crash_hooks(Path(file_handler.baseFilename).parent)


def setup_logfire() -> None:
    """Logfire 可选初始化

    LOGFIRE_SEND_TO_LOGFIRE 环境变量控制：
    - "true": 启用 Logfire APM（需要 LOGFIRE_TOKEN）
    - "false" (默认): 降级为纯本地日志
    """
    global _LOGFIRE_INITIALIZED

    if _LOGFIRE_INITIALIZED:
        return

    send_to_logfire = os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "false").lower()
    if send_to_logfire != "true":
        return

    try:
        import logfire

        logfire.configure()
        logfire.instrument_fastapi()

        # Feature 012: HTTP 客户端链路也纳入 trace（默认开启，可显式关闭）
        capture_httpx = os.environ.get("LOGFIRE_CAPTURE_HTTPX", "true").lower() == "true"
        if capture_httpx:
            logfire.instrument_httpx(capture_all=False)

        _LOGFIRE_INITIALIZED = True
    except Exception as exc:
        # Logfire 初始化失败不影响系统运行（C6: Degrade Gracefully）
        structlog.get_logger().warning(
            "logfire_init_failed",
            error_type=type(exc).__name__,
            message="Logfire 初始化失败，降级为纯本地日志",
        )
