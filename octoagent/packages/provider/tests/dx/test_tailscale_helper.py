"""F130 Phase A：tailscale_helper 三态 + serve 编排 + 红线（spec [@test] FR-A）。

Hermetic：exec 全经 DI ``FakeCommandRunner`` 注入，**零真实 tailscale 调用**
（照 F129 sleep_probe / openclaw ``server-tailscale.test.ts`` mock exec 范式）。
红线机械断言：probe 只跑只读 ``status``；serve 失败路径不出现 ``sudo`` token。
"""

from __future__ import annotations

import pytest
from octoagent.provider.dx.service_manager import CommandOutcome
from octoagent.provider.dx.tailscale_helper import (
    TailscaleServeResult,
    TailscaleState,
    disable_tailscale_serve,
    enable_tailscale_serve,
    find_tailscale_binary,
    probe_tailscale_status,
)

_FAKE_BINARY = "/opt/fake/tailscale"


class FakeCommandRunner:
    """记录全部命令的 stub —— 红线机械断言 + argv 断言共用。"""

    def __init__(self, outputs: dict[str, CommandOutcome]) -> None:
        self.commands: list[list[str]] = []
        self._outputs = outputs

    def __call__(self, command: list[str], timeout_s: float) -> CommandOutcome:
        self.commands.append(list(command))
        # 键用去掉 binary 路径后的 argv（测试关注 tailscale 子命令）
        key = " ".join(command[1:])
        return self._outputs.get(key, CommandOutcome(1, "", "unknown-command"))


_READY_STATUS = """{
  "Self": {
    "DNSName": "macmini.tail1234.ts.net.",
    "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1"]
  }
}"""

#: Tailscale CLI 有时在 JSON 前后打印非 JSON 行（noisy）。
_NOISY_READY_STATUS = (
    "Warning: some transient notice line\n"
    + _READY_STATUS
    + "\ntrailing junk not json\n"
)

_NOT_READY_NO_SELF = '{"Version": "1.80.0"}'


# ---------------------------------------------------------------------------
# FR-A1：binary 定位
# ---------------------------------------------------------------------------


class TestFindBinary:
    def test_find_binary_via_which(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "octoagent.provider.dx.tailscale_helper.shutil.which",
            lambda name: "/usr/bin/tailscale" if name == "tailscale" else None,
        )
        assert find_tailscale_binary() == "/usr/bin/tailscale"

    def test_find_binary_macos_app_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "octoagent.provider.dx.tailscale_helper.shutil.which", lambda _name: None
        )
        monkeypatch.setattr(
            "octoagent.provider.dx.tailscale_helper.Path.exists", lambda _self: True
        )
        assert find_tailscale_binary() is not None

    def test_find_binary_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "octoagent.provider.dx.tailscale_helper.shutil.which", lambda _name: None
        )
        monkeypatch.setattr(
            "octoagent.provider.dx.tailscale_helper.Path.exists", lambda _self: False
        )
        assert find_tailscale_binary() is None


# ---------------------------------------------------------------------------
# FR-A2：三态探测
# ---------------------------------------------------------------------------


class TestProbeStatus:
    def test_probe_status_ready(self) -> None:
        runner = FakeCommandRunner(
            {"status --json": CommandOutcome(0, _READY_STATUS)}
        )
        result = probe_tailscale_status(runner, binary=_FAKE_BINARY)
        assert result.state == TailscaleState.READY
        assert result.dns_name == "macmini.tail1234.ts.net"  # 去尾点
        assert result.ipv4 == "100.101.102.103"  # 跳过 IPv6

    def test_probe_status_noisy_json_still_parses(self) -> None:
        runner = FakeCommandRunner(
            {"status --json": CommandOutcome(0, _NOISY_READY_STATUS)}
        )
        result = probe_tailscale_status(runner, binary=_FAKE_BINARY)
        assert result.state == TailscaleState.READY
        assert result.dns_name == "macmini.tail1234.ts.net"

    def test_probe_status_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "octoagent.provider.dx.tailscale_helper.find_tailscale_binary",
            lambda: None,
        )
        runner = FakeCommandRunner({})
        result = probe_tailscale_status(runner)
        assert result.state == TailscaleState.NOT_INSTALLED
        assert runner.commands == []  # 未装不跑任何子进程

    def test_probe_status_command_fails_not_ready(self) -> None:
        runner = FakeCommandRunner(
            {"status --json": CommandOutcome(1, "", "not logged in")}
        )
        result = probe_tailscale_status(runner, binary=_FAKE_BINARY)
        assert result.state == TailscaleState.INSTALLED_NOT_READY

    def test_probe_status_no_self_node_not_ready(self) -> None:
        runner = FakeCommandRunner(
            {"status --json": CommandOutcome(0, _NOT_READY_NO_SELF)}
        )
        result = probe_tailscale_status(runner, binary=_FAKE_BINARY)
        assert result.state == TailscaleState.INSTALLED_NOT_READY

    def test_probe_status_unparseable_json_not_ready(self) -> None:
        runner = FakeCommandRunner(
            {"status --json": CommandOutcome(0, "totally not json")}
        )
        result = probe_tailscale_status(runner, binary=_FAKE_BINARY)
        assert result.state == TailscaleState.INSTALLED_NOT_READY

    def test_probe_status_no_dns_name_not_ready(self) -> None:
        """有 Self 但无 DNSName（MagicDNS 未启用）→ not ready。"""
        runner = FakeCommandRunner(
            {
                "status --json": CommandOutcome(
                    0, '{"Self": {"TailscaleIPs": ["100.1.1.1"]}}'
                )
            }
        )
        result = probe_tailscale_status(runner, binary=_FAKE_BINARY)
        assert result.state == TailscaleState.INSTALLED_NOT_READY
        assert "MagicDNS" in result.detail

    def test_probe_status_only_runs_readonly_command(self) -> None:
        """★ 红线（FR-D3）：probe 只跑只读 `status --json`，无 serve/up/sudo。"""
        runner = FakeCommandRunner(
            {"status --json": CommandOutcome(0, _READY_STATUS)}
        )
        probe_tailscale_status(runner, binary=_FAKE_BINARY)
        for command in runner.commands:
            argv = command[1:]  # 去 binary
            assert argv == ["status", "--json"]
            assert "sudo" not in command
            assert "serve" not in argv
            assert "up" not in argv


# ---------------------------------------------------------------------------
# FR-A3：serve 接管
# ---------------------------------------------------------------------------


class TestEnableServe:
    def test_enable_serve_success(self) -> None:
        runner = FakeCommandRunner(
            {"serve --bg --yes 8000": CommandOutcome(0, "")}
        )
        result = enable_tailscale_serve(
            8000, runner, binary=_FAKE_BINARY, dns_name="macmini.tail1234.ts.net"
        )
        assert result.ok is True
        assert result.published_url == "https://macmini.tail1234.ts.net/"
        # argv 精确匹配 openclaw 范式
        assert runner.commands[0][1:] == ["serve", "--bg", "--yes", "8000"]

    def test_enable_serve_https_required_hint(self) -> None:
        runner = FakeCommandRunner(
            {
                "serve --bg --yes 8000": CommandOutcome(
                    1, "", "HTTPS certificate is not enabled for this tailnet"
                )
            }
        )
        result = enable_tailscale_serve(8000, runner, binary=_FAKE_BINARY)
        assert result.ok is False
        assert result.error_code == "https_required"
        assert "admin" in result.hint.lower() or "https" in result.hint.lower()

    def test_enable_serve_permission_denied_no_sudo(self) -> None:
        """★ 红线：permission denied 时给手动命令，helper 自身不自动 sudo。"""
        runner = FakeCommandRunner(
            {
                "serve --bg --yes 8000": CommandOutcome(
                    1, "", "permission denied: must run as root"
                )
            }
        )
        result = enable_tailscale_serve(8000, runner, binary=_FAKE_BINARY)
        assert result.ok is False
        assert result.error_code == "permission_denied"
        # helper 只跑了一次（无自动 sudo 重试）
        assert len(runner.commands) == 1
        # helper 实际执行的 argv 里绝无 sudo token
        assert all("sudo" not in cmd for cmd in runner.commands)

    def test_enable_serve_generic_failure(self) -> None:
        runner = FakeCommandRunner(
            {"serve --bg --yes 8000": CommandOutcome(1, "", "some other error")}
        )
        result = enable_tailscale_serve(8000, runner, binary=_FAKE_BINARY)
        assert result.ok is False
        assert result.error_code == "serve_failed"

    def test_enable_serve_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "octoagent.provider.dx.tailscale_helper.find_tailscale_binary",
            lambda: None,
        )
        runner = FakeCommandRunner({})
        result = enable_tailscale_serve(8000, runner)
        assert result.ok is False
        assert result.error_code == "not_installed"
        assert runner.commands == []


# ---------------------------------------------------------------------------
# FR-A4：关闭 serve（scoped off / 全局 reset 回退）
# ---------------------------------------------------------------------------


class TestDisableServe:
    def test_disable_scoped_off_with_port(self) -> None:
        """Codex re-review P2：传 port → 只关本功能的 https/443 handler，
        不 `serve reset` 清整机他人配置。"""
        runner = FakeCommandRunner({"serve --https=443 off": CommandOutcome(0, "")})
        result = disable_tailscale_serve(runner, binary=_FAKE_BINARY, port=8000)
        assert result.ok is True
        assert runner.commands[0][1:] == ["serve", "--https=443", "off"]
        # 绝不出现全局 reset
        assert all("reset" not in cmd for cmd in runner.commands)

    def test_disable_global_reset_fallback_when_no_port(self) -> None:
        """不传 port → 回退全局 reset（调用方应尽量传 port）。"""
        runner = FakeCommandRunner({"serve reset": CommandOutcome(0, "")})
        result = disable_tailscale_serve(runner, binary=_FAKE_BINARY)
        assert result.ok is True
        assert runner.commands[0][1:] == ["serve", "reset"]

    def test_disable_permission_denied_no_sudo(self) -> None:
        runner = FakeCommandRunner(
            {"serve --https=443 off": CommandOutcome(1, "", "permission denied")}
        )
        result = disable_tailscale_serve(runner, binary=_FAKE_BINARY, port=8000)
        assert result.ok is False
        assert result.error_code == "permission_denied"
        assert all("sudo" not in cmd for cmd in runner.commands)


# ---------------------------------------------------------------------------
# FR-A5：优雅降级（Constitution #6）——所有函数不抛未捕获异常
# ---------------------------------------------------------------------------


class TestDegradeGracefully:
    def test_probe_degrades_on_runner_soft_failure(self) -> None:
        """runner 返回软化的非零（FileNotFound=127 等）不抛，归 not ready。"""
        runner = FakeCommandRunner(
            {"status --json": CommandOutcome(127, "", "tailscale: not found")}
        )
        result = probe_tailscale_status(runner, binary=_FAKE_BINARY)
        assert isinstance(result.state, TailscaleState)

    def test_all_entrypoints_return_typed_result_when_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "octoagent.provider.dx.tailscale_helper.find_tailscale_binary",
            lambda: None,
        )
        runner = FakeCommandRunner({})
        assert probe_tailscale_status(runner).state == TailscaleState.NOT_INSTALLED
        assert isinstance(enable_tailscale_serve(1, runner), TailscaleServeResult)
        assert isinstance(disable_tailscale_serve(runner), TailscaleServeResult)
