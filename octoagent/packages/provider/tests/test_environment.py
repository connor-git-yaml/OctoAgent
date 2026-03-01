"""环境检测单元测试 -- T012

验证:
- SSH 环境检测（SSH_CLIENT/SSH_TTY）
- 容器检测（CODESPACES/CLOUD_SHELL）
- Linux 无 GUI 检测（无 DISPLAY 且非 WSL）
- --manual-oauth 强制手动模式
- use_manual_mode 属性计算逻辑
对齐 FR-002
"""

from __future__ import annotations

import sys

import pytest

from octoagent.provider.auth.environment import (
    EnvironmentContext,
    detect_environment,
)


class TestEnvironmentContext:
    """EnvironmentContext dataclass 行为"""

    def test_frozen(self) -> None:
        """EnvironmentContext 是 frozen dataclass"""
        ctx = EnvironmentContext(
            is_remote=False,
            can_open_browser=True,
            force_manual=False,
            detection_details="test",
        )
        with pytest.raises(AttributeError):
            ctx.is_remote = True  # type: ignore[misc]

    def test_use_manual_mode_local(self) -> None:
        """本地环境不使用手动模式"""
        ctx = EnvironmentContext(
            is_remote=False,
            can_open_browser=True,
            force_manual=False,
            detection_details="local",
        )
        assert ctx.use_manual_mode is False

    def test_use_manual_mode_remote(self) -> None:
        """远程环境使用手动模式"""
        ctx = EnvironmentContext(
            is_remote=True,
            can_open_browser=False,
            force_manual=False,
            detection_details="SSH",
        )
        assert ctx.use_manual_mode is True

    def test_use_manual_mode_force(self) -> None:
        """force_manual=True 强制使用手动模式"""
        ctx = EnvironmentContext(
            is_remote=False,
            can_open_browser=True,
            force_manual=True,
            detection_details="forced",
        )
        assert ctx.use_manual_mode is True

    def test_use_manual_mode_no_browser(self) -> None:
        """不能打开浏览器时使用手动模式"""
        ctx = EnvironmentContext(
            is_remote=False,
            can_open_browser=False,
            force_manual=False,
            detection_details="no browser",
        )
        assert ctx.use_manual_mode is True


class TestDetectEnvironmentSSH:
    """SSH 环境检测"""

    def test_ssh_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SSH_CLIENT 存在时检测为远程"""
        monkeypatch.setenv("SSH_CLIENT", "192.168.1.1 12345 22")
        monkeypatch.delenv("SSH_TTY", raising=False)
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.delenv("REMOTE_CONTAINERS", raising=False)
        monkeypatch.delenv("CODESPACES", raising=False)
        monkeypatch.delenv("CLOUD_SHELL", raising=False)
        ctx = detect_environment()
        assert ctx.is_remote is True
        assert "SSH" in ctx.detection_details

    def test_ssh_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SSH_TTY 存在时检测为远程"""
        monkeypatch.delenv("SSH_CLIENT", raising=False)
        monkeypatch.setenv("SSH_TTY", "/dev/pts/0")
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.delenv("REMOTE_CONTAINERS", raising=False)
        monkeypatch.delenv("CODESPACES", raising=False)
        monkeypatch.delenv("CLOUD_SHELL", raising=False)
        ctx = detect_environment()
        assert ctx.is_remote is True


class TestDetectEnvironmentContainer:
    """容器/云开发环境检测"""

    def test_codespaces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CODESPACES 环境变量存在"""
        monkeypatch.delenv("SSH_CLIENT", raising=False)
        monkeypatch.delenv("SSH_TTY", raising=False)
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.delenv("REMOTE_CONTAINERS", raising=False)
        monkeypatch.setenv("CODESPACES", "true")
        monkeypatch.delenv("CLOUD_SHELL", raising=False)
        ctx = detect_environment()
        assert ctx.is_remote is True
        assert "容器" in ctx.detection_details or "云" in ctx.detection_details

    def test_cloud_shell(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLOUD_SHELL 环境变量存在"""
        monkeypatch.delenv("SSH_CLIENT", raising=False)
        monkeypatch.delenv("SSH_TTY", raising=False)
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.delenv("REMOTE_CONTAINERS", raising=False)
        monkeypatch.delenv("CODESPACES", raising=False)
        monkeypatch.setenv("CLOUD_SHELL", "true")
        ctx = detect_environment()
        assert ctx.is_remote is True


class TestDetectEnvironmentForceManual:
    """--manual-oauth 强制手动模式"""

    def test_force_manual(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """force_manual=True 设置 force_manual 和 use_manual_mode"""
        monkeypatch.delenv("SSH_CLIENT", raising=False)
        monkeypatch.delenv("SSH_TTY", raising=False)
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.delenv("REMOTE_CONTAINERS", raising=False)
        monkeypatch.delenv("CODESPACES", raising=False)
        monkeypatch.delenv("CLOUD_SHELL", raising=False)
        ctx = detect_environment(force_manual=True)
        assert ctx.force_manual is True
        assert ctx.use_manual_mode is True
        assert "强制手动" in ctx.detection_details


class TestDetectEnvironmentLocal:
    """本地桌面环境"""

    @pytest.mark.skipif(
        sys.platform == "linux",
        reason="Linux 环境可能无 DISPLAY 导致误判",
    )
    def test_local_desktop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 SSH、非容器、非 Linux 无 GUI 环境为本地"""
        monkeypatch.delenv("SSH_CLIENT", raising=False)
        monkeypatch.delenv("SSH_TTY", raising=False)
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.delenv("REMOTE_CONTAINERS", raising=False)
        monkeypatch.delenv("CODESPACES", raising=False)
        monkeypatch.delenv("CLOUD_SHELL", raising=False)
        ctx = detect_environment()
        assert ctx.is_remote is False
        assert "本地" in ctx.detection_details
