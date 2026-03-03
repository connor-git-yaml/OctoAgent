"""WatchdogConfig Pydantic 配置模型 -- Feature 011 FR-017, FR-018

支持通过 WATCHDOG_{KEY} 环境变量覆盖默认值，
无效值（负数/零）回退默认值不影响启动。
"""

import os

import structlog
from pydantic import BaseModel, Field, field_validator

log = structlog.get_logger()

# 各字段默认值常量（validator 内无法访问 Field default，用独立常量）
_DEFAULTS: dict[str, int] = {
    "scan_interval_seconds": 15,
    "no_progress_cycles": 3,
    "cooldown_seconds": 60,
    "failure_window_seconds": 300,
    "repeated_failure_threshold": 3,
}


class WatchdogConfig(BaseModel):
    """Watchdog 配置模型（FR-017 + FR-018）

    所有字段均有默认值，支持通过 WATCHDOG_{KEY} 环境变量覆盖。
    无效配置值（负数/零）回退到默认值，不影响系统启动（FR-018）。
    """

    scan_interval_seconds: int = Field(
        default=15,
        description="Watchdog 扫描周期（秒）",
    )
    no_progress_cycles: int = Field(
        default=3,
        description="无进展判定周期数（实际阈值 = cycles × interval）",
    )
    cooldown_seconds: int = Field(
        default=60,
        description="同一任务漂移告警 cooldown 时长（秒）",
    )
    failure_window_seconds: int = Field(
        default=300,
        description="重复失败统计时间窗口（秒）",
    )
    repeated_failure_threshold: int = Field(
        default=3,
        description="重复失败触发漂移的次数阈值",
    )

    @field_validator(
        "scan_interval_seconds",
        "no_progress_cycles",
        "cooldown_seconds",
        "failure_window_seconds",
        "repeated_failure_threshold",
        mode="before",
    )
    @classmethod
    def _positive_integer(cls, v: object, info) -> int:  # type: ignore[override]
        """无效值（非正整数）回退到默认值，并记录警告（FR-018）"""
        field_name: str = info.field_name
        default = _DEFAULTS.get(field_name, 1)

        # 尝试整数转换（处理字符串形式的数字）
        if isinstance(v, str):
            try:
                v = int(v)
            except ValueError:
                log.warning(
                    "watchdog_config_invalid_value",
                    field=field_name,
                    value=v,
                    fallback=default,
                )
                return default

        if not isinstance(v, int) or v <= 0:
            log.warning(
                "watchdog_config_invalid_value",
                field=field_name,
                value=v,
                fallback=default,
            )
            return default

        return v

    @property
    def no_progress_threshold_seconds(self) -> int:
        """无进展阈值（秒）= 周期数 × 扫描间隔（FR-017）"""
        return self.no_progress_cycles * self.scan_interval_seconds

    @classmethod
    def from_env(cls) -> "WatchdogConfig":
        """从环境变量加载配置，遵循 WATCHDOG_{KEY} 命名规范（FR-018）

        无效的环境变量值（非整数字符串）将由 validator 处理并回退默认值。
        """
        kwargs: dict[str, object] = {}
        env_map = {
            "WATCHDOG_SCAN_INTERVAL_SECONDS": "scan_interval_seconds",
            "WATCHDOG_NO_PROGRESS_CYCLES": "no_progress_cycles",
            "WATCHDOG_COOLDOWN_SECONDS": "cooldown_seconds",
            "WATCHDOG_FAILURE_WINDOW_SECONDS": "failure_window_seconds",
            "WATCHDOG_REPEATED_FAILURE_THRESHOLD": "repeated_failure_threshold",
        }
        for env_key, field_name in env_map.items():
            raw = os.getenv(env_key)
            if raw is not None:
                # 将原始字符串传入 validator，由 validator 统一处理类型转换和校验
                try:
                    kwargs[field_name] = int(raw)
                except ValueError:
                    # 非数字字符串：传入原始值让 validator 触发 fallback 逻辑
                    kwargs[field_name] = raw
        return cls(**kwargs)
