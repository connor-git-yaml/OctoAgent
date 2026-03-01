"""环境检测模块 -- 对齐 contracts/auth-oauth-pkce-api.md SS3, FR-002

检测当前运行环境的 OAuth 交互能力，决定使用自动浏览器模式还是手动粘贴模式。
检测维度:
1. SSH 环境（SSH_CLIENT, SSH_TTY, SSH_CONNECTION）
2. 容器/云开发环境（REMOTE_CONTAINERS, CODESPACES, CLOUD_SHELL）
3. Linux 无图形界面（无 DISPLAY 和 WAYLAND_DISPLAY，且非 WSL）
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EnvironmentContext:
    """运行环境上下文 -- 描述当前环境的 OAuth 交互能力

    用于决定 OAuth 流程的交互模式（自动打开浏览器 vs 手动粘贴 URL）。
    """

    is_remote: bool  # 是否为远程环境（SSH/容器/云开发）
    can_open_browser: bool  # 是否可以自动打开浏览器
    force_manual: bool  # 是否强制手动模式（--manual-oauth flag）
    detection_details: str  # 检测详情（用于日志/诊断）

    @property
    def use_manual_mode(self) -> bool:
        """是否应使用手动粘贴模式"""
        return self.force_manual or self.is_remote or not self.can_open_browser


def _is_ssh() -> bool:
    """检测是否在 SSH 环境中"""
    ssh_vars = ("SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION")
    return any(os.environ.get(var) for var in ssh_vars)


def _is_container() -> bool:
    """检测是否在容器/云开发环境中"""
    container_vars = ("REMOTE_CONTAINERS", "CODESPACES", "CLOUD_SHELL")
    return any(os.environ.get(var) for var in container_vars)


def _is_linux_no_gui() -> bool:
    """检测是否为 Linux 无图形界面环境（排除 WSL）"""
    if sys.platform != "linux":
        return False

    # WSL 环境有浏览器能力（通过 Windows 宿主）
    if "microsoft" in os.uname().release.lower():
        return False

    has_display = bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )
    return not has_display


def detect_environment(force_manual: bool = False) -> EnvironmentContext:
    """检测当前运行环境

    检测维度:
    1. SSH 环境（SSH_CLIENT, SSH_TTY, SSH_CONNECTION 环境变量）
    2. 容器/云开发环境（REMOTE_CONTAINERS, CODESPACES, CLOUD_SHELL）
    3. Linux 无图形界面（无 DISPLAY 和 WAYLAND_DISPLAY，且非 WSL）

    Args:
        force_manual: 是否强制手动模式（--manual-oauth CLI flag）

    Returns:
        EnvironmentContext 实例
    """
    details_parts: list[str] = []

    ssh = _is_ssh()
    if ssh:
        details_parts.append("SSH 环境")

    container = _is_container()
    if container:
        details_parts.append("容器/云开发环境")

    linux_no_gui = _is_linux_no_gui()
    if linux_no_gui:
        details_parts.append("Linux 无 GUI")

    is_remote = ssh or container or linux_no_gui

    # 浏览器打开能力：非远程环境 + 可用的 webbrowser 模块
    browser_available = _can_open_browser_impl()
    can_browser = not is_remote and browser_available

    if force_manual:
        details_parts.append("强制手动模式")

    if not details_parts:
        details_parts.append("本地桌面环境")

    return EnvironmentContext(
        is_remote=is_remote,
        can_open_browser=can_browser,
        force_manual=force_manual,
        detection_details="; ".join(details_parts),
    )


def _can_open_browser_impl() -> bool:
    """检测 webbrowser 模块是否可用"""
    try:
        import webbrowser

        # 尝试获取浏览器，不实际打开
        browser = webbrowser.get()
        return browser is not None
    except Exception:
        return False


def is_remote_environment() -> bool:
    """检测是否处于远程/无浏览器环境

    等价于 detect_environment().is_remote
    """
    return detect_environment().is_remote


def can_open_browser() -> bool:
    """检测是否可以自动打开浏览器

    使用 webbrowser 模块检测，捕获异常返回 False。
    """
    return _can_open_browser_impl() and not is_remote_environment()
