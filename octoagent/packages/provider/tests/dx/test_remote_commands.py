"""F130 Phase E：`octo remote` CLI 测试（spec [@test] FR-B / AC-1/AC-7）。

Hermetic：tailscale helper（probe/serve）+ config load/save 全经 monkeypatch
注入 stub，零真实 tailscale 调用 / 零真实 yaml 写入（照 F129
test_service_commands.py CliRunner + monkeypatch 范式）。
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from octoagent.provider.dx import remote_commands
from octoagent.provider.dx.remote_commands import remote_group
from octoagent.provider.dx.tailscale_helper import (
    TailscaleProbeResult,
    TailscaleServeResult,
    TailscaleState,
)


class _FakeConfig:
    """最小 config stub：只暴露 front_door.mode（可读可写）。"""

    class _FrontDoor:
        def __init__(self, mode: str) -> None:
            self.mode = mode

    def __init__(self, mode: str = "loopback") -> None:
        self.front_door = _FakeConfig._FrontDoor(mode)


def _patch_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key in ("OCTOAGENT_FRONTDOOR_MODE", "OCTOAGENT_FRONTDOOR_TOKEN", "OCTOAGENT_PORT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _patch_probe(monkeypatch: pytest.MonkeyPatch, probe: TailscaleProbeResult) -> None:
    monkeypatch.setattr(
        remote_commands, "probe_tailscale_status", lambda: probe
    )


def _patch_config(
    monkeypatch: pytest.MonkeyPatch,
    cfg: _FakeConfig | None,
    saved: list[tuple[object, str]],
) -> None:
    from pathlib import Path

    monkeypatch.setattr(
        remote_commands, "resolve_instance_root", lambda: Path("/fake/instance")
    )
    # _load_config_and_root 内 lazy import config_wizard.load_config
    import octoagent.gateway.services.config.config_wizard as cw

    monkeypatch.setattr(cw, "load_config", lambda _root: cfg)

    def _fake_save(config: object, _root: object) -> None:
        saved.append((config, config.front_door.mode))

    monkeypatch.setattr(cw, "save_config", _fake_save)


_READY = TailscaleProbeResult(
    supported=True,
    state=TailscaleState.READY,
    dns_name="macmini.tail1234.ts.net",
    ipv4="100.1.2.3",
)


class TestRemoteEnable:
    def test_enable_ready_switches_bearer_and_serves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        cfg = _FakeConfig(mode="loopback")
        saved: list = []
        _patch_config(monkeypatch, cfg, saved)
        serve_calls: list = []

        def _fake_serve(port: int, **kwargs: object) -> TailscaleServeResult:
            serve_calls.append((port, kwargs))
            return TailscaleServeResult(
                ok=True, published_url="https://macmini.tail1234.ts.net/"
            )

        monkeypatch.setattr(remote_commands, "enable_tailscale_serve", _fake_serve)

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 0
        # 切到 bearer 写了 yaml
        assert saved and saved[0][1] == "bearer"
        # 调了 serve
        assert serve_calls
        # 输出手机 URL
        assert "https://macmini.tail1234.ts.net/" in result.output

    def test_enable_not_installed_no_config_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_env(monkeypatch)
        _patch_probe(
            monkeypatch,
            TailscaleProbeResult(supported=True, state=TailscaleState.NOT_INSTALLED),
        )
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(), saved)

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 1
        assert saved == []  # 未就绪绝不改配置
        assert "tailscale.com/download" in result.output

    def test_enable_installed_not_ready_gives_guidance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_env(monkeypatch)
        _patch_probe(
            monkeypatch,
            TailscaleProbeResult(
                supported=True,
                state=TailscaleState.INSTALLED_NOT_READY,
                detail="未登录",
            ),
        )
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(), saved)

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 1
        assert saved == []
        assert "tailscale up" in result.output

    def test_enable_dry_run_no_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), saved)
        serve_calls: list = []
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: serve_calls.append((a, k)),
        )

        result = CliRunner().invoke(remote_group, ["enable", "--dry-run"])
        assert result.exit_code == 0
        assert saved == []  # dry-run 不写 yaml
        assert serve_calls == []  # dry-run 不跑 serve
        assert "dry-run" in result.output
        assert "bearer" in result.output

    def test_enable_idempotent_already_bearer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已是 bearer → 不重复写 yaml（幂等），仍跑 serve。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(mode="bearer"), saved)
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(
                ok=True, published_url="https://macmini.tail1234.ts.net/"
            ),
        )

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 0
        assert saved == []  # 已 bearer 不重复写
        assert "幂等" in result.output

    def test_enable_prompts_token_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """token 未设 → 提示设 token（走 .env，不写 config）。"""
        _patch_env(monkeypatch)  # 不设 OCTOAGENT_FRONTDOOR_TOKEN
        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(
                ok=True, published_url="https://macmini.tail1234.ts.net/"
            ),
        )

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 0
        assert "OCTOAGENT_FRONTDOOR_TOKEN" in result.output
        assert ".env" in result.output

    def test_enable_serve_failure_reports_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(
                ok=False,
                error_code="https_required",
                hint="去 admin console 启用 HTTPS",
            ),
        )

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 1
        assert "https_required" in result.output


class TestRemoteDisable:
    def test_disable_switches_loopback_and_resets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_env(monkeypatch)
        cfg = _FakeConfig(mode="bearer")
        saved: list = []
        _patch_config(monkeypatch, cfg, saved)
        reset_calls: list = []
        monkeypatch.setattr(
            remote_commands,
            "disable_tailscale_serve",
            lambda *a, **k: reset_calls.append(1) or TailscaleServeResult(ok=True),
        )

        result = CliRunner().invoke(remote_group, ["disable"])
        assert result.exit_code == 0
        assert saved and saved[0][1] == "loopback"
        assert reset_calls

    def test_disable_dry_run_no_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_env(monkeypatch)
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(mode="bearer"), saved)
        reset_calls: list = []
        monkeypatch.setattr(
            remote_commands,
            "disable_tailscale_serve",
            lambda *a, **k: reset_calls.append(1),
        )

        result = CliRunner().invoke(remote_group, ["disable", "--dry-run"])
        assert result.exit_code == 0
        assert saved == []
        assert reset_calls == []
        assert "dry-run" in result.output


class TestRemoteStatus:
    def test_status_ready_bearer_shows_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_env(monkeypatch, OCTOAGENT_HOST="127.0.0.1")
        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _FakeConfig(mode="bearer"), [])

        result = CliRunner().invoke(remote_group, ["status"])
        assert result.exit_code == 0
        assert "https://macmini.tail1234.ts.net/" in result.output
        assert "安全" in result.output

    def test_status_ready_but_loopback_warns_bearer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """就绪但 mode=loopback → 提示切 bearer（serve+loopback 会拒，AC-2）。"""
        _patch_env(monkeypatch, OCTOAGENT_HOST="127.0.0.1")
        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])

        result = CliRunner().invoke(remote_group, ["status"])
        assert result.exit_code == 0
        assert "octo remote enable" in result.output
        assert "X-Forwarded" in result.output

    def test_status_naked_exposure_flagged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """0.0.0.0 + loopback → status 标危险（裸奔）。"""
        _patch_env(monkeypatch, OCTOAGENT_HOST="0.0.0.0")
        _patch_probe(
            monkeypatch,
            TailscaleProbeResult(supported=True, state=TailscaleState.NOT_INSTALLED),
        )
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])

        result = CliRunner().invoke(remote_group, ["status"])
        assert result.exit_code == 0
        assert "裸奔" in result.output
