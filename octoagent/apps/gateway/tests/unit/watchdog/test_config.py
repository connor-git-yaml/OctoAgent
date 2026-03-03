"""WatchdogConfig 单元测试 -- Feature 011 T016 + T040

覆盖默认值/env 覆盖/无效值回退/no_progress_threshold_seconds 计算（T016）
以及 US5 专属验收测试场景（T040）。
"""

import os

import pytest

from octoagent.gateway.services.watchdog.config import WatchdogConfig


class TestWatchdogConfigDefaults:
    """默认值测试（T016）"""

    def test_default_values(self):
        """无配置时使用默认值（FR-017）"""
        config = WatchdogConfig()
        assert config.scan_interval_seconds == 15
        assert config.no_progress_cycles == 3
        assert config.cooldown_seconds == 60
        assert config.failure_window_seconds == 300
        assert config.repeated_failure_threshold == 3

    def test_no_progress_threshold_seconds_calculation(self):
        """no_progress_threshold_seconds 属性计算正确（FR-017）"""
        config = WatchdogConfig()
        # 默认：3 cycles × 15s = 45s
        assert config.no_progress_threshold_seconds == 45

    def test_custom_threshold_calculation(self):
        """自定义参数的 threshold 计算"""
        config = WatchdogConfig(scan_interval_seconds=10, no_progress_cycles=5)
        assert config.no_progress_threshold_seconds == 50

    def test_explicit_valid_values(self):
        """显式赋值有效正整数可正常创建"""
        config = WatchdogConfig(
            scan_interval_seconds=30,
            no_progress_cycles=2,
            cooldown_seconds=120,
            failure_window_seconds=600,
            repeated_failure_threshold=5,
        )
        assert config.scan_interval_seconds == 30
        assert config.no_progress_cycles == 2
        assert config.cooldown_seconds == 120


class TestWatchdogConfigInvalidValues:
    """无效值回退测试（T016 + T040）"""

    def test_zero_value_falls_back_to_default(self):
        """零值回退到默认值（FR-018）"""
        config = WatchdogConfig(scan_interval_seconds=0)
        assert config.scan_interval_seconds == 15  # 回退默认

    def test_negative_value_falls_back_to_default(self):
        """负数回退到默认值（FR-018）"""
        config = WatchdogConfig(no_progress_cycles=-1)
        assert config.no_progress_cycles == 3  # 回退默认

    def test_invalid_cooldown_falls_back(self):
        """cooldown_seconds 无效值回退"""
        config = WatchdogConfig(cooldown_seconds=0)
        assert config.cooldown_seconds == 60

    def test_invalid_failure_window_falls_back(self):
        """failure_window_seconds 无效值回退"""
        config = WatchdogConfig(failure_window_seconds=-100)
        assert config.failure_window_seconds == 300

    def test_invalid_repeated_failure_threshold_falls_back(self):
        """repeated_failure_threshold 无效值回退"""
        config = WatchdogConfig(repeated_failure_threshold=0)
        assert config.repeated_failure_threshold == 3

    def test_invalid_value_does_not_affect_startup(self):
        """无效值不影响系统启动（FR-018 核心要求）"""
        # 全部传入无效值，不应抛出异常，全部回退到默认
        config = WatchdogConfig(
            scan_interval_seconds=0,
            no_progress_cycles=-5,
            cooldown_seconds=-1,
            failure_window_seconds=0,
            repeated_failure_threshold=-3,
        )
        assert config.scan_interval_seconds == 15
        assert config.no_progress_cycles == 3
        assert config.cooldown_seconds == 60
        assert config.failure_window_seconds == 300
        assert config.repeated_failure_threshold == 3


class TestWatchdogConfigFromEnv:
    """from_env() 环境变量加载测试（T016 + T040）"""

    def test_from_env_no_env_vars_uses_defaults(self, monkeypatch):
        """无环境变量时使用默认值（T040 场景1）"""
        # 确保测试环境中不存在这些变量
        for key in [
            "WATCHDOG_SCAN_INTERVAL_SECONDS",
            "WATCHDOG_NO_PROGRESS_CYCLES",
            "WATCHDOG_COOLDOWN_SECONDS",
            "WATCHDOG_FAILURE_WINDOW_SECONDS",
            "WATCHDOG_REPEATED_FAILURE_THRESHOLD",
        ]:
            monkeypatch.delenv(key, raising=False)

        config = WatchdogConfig.from_env()
        assert config.scan_interval_seconds == 15
        assert config.no_progress_cycles == 3
        assert config.cooldown_seconds == 60
        assert config.failure_window_seconds == 300
        assert config.repeated_failure_threshold == 3

    def test_from_env_scan_interval_override(self, monkeypatch):
        """WATCHDOG_SCAN_INTERVAL_SECONDS 环境变量覆盖（T040 场景2）"""
        monkeypatch.setenv("WATCHDOG_SCAN_INTERVAL_SECONDS", "30")
        config = WatchdogConfig.from_env()
        assert config.scan_interval_seconds == 30

    def test_from_env_no_progress_cycles_override(self, monkeypatch):
        """WATCHDOG_NO_PROGRESS_CYCLES 环境变量覆盖"""
        monkeypatch.setenv("WATCHDOG_NO_PROGRESS_CYCLES", "5")
        config = WatchdogConfig.from_env()
        assert config.no_progress_cycles == 5

    def test_from_env_cooldown_override(self, monkeypatch):
        """WATCHDOG_COOLDOWN_SECONDS 环境变量覆盖"""
        monkeypatch.setenv("WATCHDOG_COOLDOWN_SECONDS", "120")
        config = WatchdogConfig.from_env()
        assert config.cooldown_seconds == 120

    def test_from_env_failure_window_override(self, monkeypatch):
        """WATCHDOG_FAILURE_WINDOW_SECONDS 环境变量覆盖"""
        monkeypatch.setenv("WATCHDOG_FAILURE_WINDOW_SECONDS", "600")
        config = WatchdogConfig.from_env()
        assert config.failure_window_seconds == 600

    def test_from_env_repeated_failure_threshold_override(self, monkeypatch):
        """WATCHDOG_REPEATED_FAILURE_THRESHOLD 环境变量覆盖"""
        monkeypatch.setenv("WATCHDOG_REPEATED_FAILURE_THRESHOLD", "5")
        config = WatchdogConfig.from_env()
        assert config.repeated_failure_threshold == 5

    def test_from_env_all_overrides(self, monkeypatch):
        """所有环境变量同时覆盖"""
        monkeypatch.setenv("WATCHDOG_SCAN_INTERVAL_SECONDS", "10")
        monkeypatch.setenv("WATCHDOG_NO_PROGRESS_CYCLES", "6")
        monkeypatch.setenv("WATCHDOG_COOLDOWN_SECONDS", "90")
        monkeypatch.setenv("WATCHDOG_FAILURE_WINDOW_SECONDS", "180")
        monkeypatch.setenv("WATCHDOG_REPEATED_FAILURE_THRESHOLD", "4")

        config = WatchdogConfig.from_env()
        assert config.scan_interval_seconds == 10
        assert config.no_progress_cycles == 6
        assert config.cooldown_seconds == 90
        assert config.failure_window_seconds == 180
        assert config.repeated_failure_threshold == 4
        # threshold 计算也随之更新
        assert config.no_progress_threshold_seconds == 60  # 6 × 10

    def test_from_env_invalid_value_falls_back(self, monkeypatch):
        """无效环境变量值回退默认值，不影响启动（T040 场景3）"""
        monkeypatch.setenv("WATCHDOG_SCAN_INTERVAL_SECONDS", "-5")
        config = WatchdogConfig.from_env()
        assert config.scan_interval_seconds == 15  # 回退默认

    def test_from_env_zero_value_falls_back(self, monkeypatch):
        """零值环境变量回退默认值"""
        monkeypatch.setenv("WATCHDOG_NO_PROGRESS_CYCLES", "0")
        config = WatchdogConfig.from_env()
        assert config.no_progress_cycles == 3

    def test_no_progress_threshold_with_env_override(self, monkeypatch):
        """env 覆盖后 no_progress_threshold_seconds 计算正确（T040 场景4）"""
        monkeypatch.setenv("WATCHDOG_SCAN_INTERVAL_SECONDS", "20")
        monkeypatch.setenv("WATCHDOG_NO_PROGRESS_CYCLES", "4")
        config = WatchdogConfig.from_env()
        assert config.no_progress_threshold_seconds == 80  # 4 × 20
