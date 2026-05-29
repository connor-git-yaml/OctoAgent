"""benchmarks/tests/unit/test_preflight.py — preflight 自检模块测试."""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from benchmarks.runner.preflight import (
    INSTALL_COMMAND,
    REQUIRED_PACKAGES,
    _missing_packages,
    check_or_fail,
    get_required_packages,
)


class TestPreflight:
    def test_required_packages_list(self) -> None:
        """必需包列表应含 tau_bench + datasets，与 PoC §1 决策一致."""
        names = [name for name, _ in REQUIRED_PACKAGES]
        assert "tau_bench" in names
        assert "datasets" in names

    def test_get_required_packages_returns_list(self) -> None:
        """get_required_packages 返回 list (mutable copy)."""
        pkgs = get_required_packages()
        assert isinstance(pkgs, list)
        assert len(pkgs) == 2

    def test_install_command_content(self) -> None:
        """install 命令含 git URL + datasets，与 PoC 决策一致."""
        assert "tau-bench" in INSTALL_COMMAND
        assert "datasets" in INSTALL_COMMAND
        assert "uv pip install" in INSTALL_COMMAND

    def test_check_or_fail_passes_when_no_missing_packages(self) -> None:
        """模拟所有包已装 → check_or_fail 不抛.

        Codex Phase B review MED-7 修复 2026-05-29: 不再依赖真实 venv 安装状态
        (干净环境 / CI 跑 unit test 不应因 tau-bench 未装而失败).
        真实环境依赖检查放到 integration / manual preflight, 不放 unit test.
        """
        with patch(
            "benchmarks.runner.preflight._missing_packages",
            return_value=[],
        ):
            check_or_fail()  # mock 返回空 → 不抛

    def test_check_or_fail_raises_when_package_missing(self) -> None:
        """模拟缺包场景：_missing_packages 返回非空 → SystemExit(2)."""
        with patch(
            "benchmarks.runner.preflight._missing_packages",
            return_value=["tau_bench"],
        ):
            with pytest.raises(SystemExit) as excinfo:
                check_or_fail()
            assert excinfo.value.code == 2

    def test_check_or_fail_custom_exit_code(self) -> None:
        """exit_code 参数可自定义."""
        with patch(
            "benchmarks.runner.preflight._missing_packages",
            return_value=["datasets"],
        ):
            with pytest.raises(SystemExit) as excinfo:
                check_or_fail(exit_code=99)
            assert excinfo.value.code == 99

    def test_check_or_fail_prints_install_command_to_stderr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """fail-fast 时 install 命令应在 stderr 输出（用户可复制粘贴）."""
        with patch(
            "benchmarks.runner.preflight._missing_packages",
            return_value=["tau_bench"],
        ):
            with pytest.raises(SystemExit):
                check_or_fail()
        captured = capsys.readouterr()
        assert INSTALL_COMMAND in captured.err
        assert "tau_bench" in captured.err
