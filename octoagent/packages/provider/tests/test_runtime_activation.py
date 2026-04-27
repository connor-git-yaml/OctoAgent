from __future__ import annotations

from pathlib import Path

import pytest

from octoagent.provider.dx.runtime_activation import (
    RuntimeActivationError,
    RuntimeActivationService,
)


def test_build_compose_up_command_returns_deprecation_message(tmp_path: Path) -> None:
    """Feature 081 P3：build_compose_up_command 不再生成 docker compose 命令，
    改成返回 deprecation 提示文本（实际 LiteLLM Proxy 已退役）。
    """
    service = RuntimeActivationService(tmp_path)
    command = service.build_compose_up_command()
    assert "Feature 081" in command
    assert "已退役" in command


@pytest.mark.asyncio
async def test_start_proxy_raises_deprecation_error(tmp_path: Path) -> None:
    """Feature 081 P3：start_proxy 直接抛 RuntimeActivationError 提示用户 migrate。"""
    service = RuntimeActivationService(tmp_path)
    with pytest.raises(RuntimeActivationError, match="LiteLLM Proxy 已退役"):
        await service.start_proxy()


def test_resolve_proxy_url_returns_empty_string(tmp_path: Path) -> None:
    """Feature 081 P3：resolve_proxy_url 不再有真实 URL，返回空字符串。"""
    service = RuntimeActivationService(tmp_path)
    assert service.resolve_proxy_url() == ""


def test_resolve_source_root_no_compose_required(tmp_path: Path) -> None:
    """Feature 081 P3：缺失 docker-compose.litellm.yml 不再抛错。"""
    service = RuntimeActivationService(tmp_path)
    # 不抛错；返回 instance root 兜底
    result = service.resolve_source_root()
    assert isinstance(result, Path)
