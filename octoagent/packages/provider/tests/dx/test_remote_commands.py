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
    # 宽 COLUMNS：CliRunner 无 TTY 时 rich 默认 80 列会截断长中文提示行，
    # 设宽让 result.output 保留完整文本供断言（等价真实终端）。
    monkeypatch.setenv("COLUMNS", "200")
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
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        cfg = _FakeConfig(mode="loopback")
        saved: list = []
        _patch_config(monkeypatch, cfg, saved)
        # F134：token 未设时 enable 会真写实例 .env → root 须真实可写目录
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
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
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """已是 bearer → 不重复写 yaml（幂等），仍跑 serve。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(mode="bearer"), saved)
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
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

    def test_enable_generates_token_when_unset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """F134 AC-T1（原 test_enable_prompts_token_when_unset 语义演进）：
        token 未设 → 自动生成强随机值写入实例 .env（0600），stdout 零明文。"""
        _patch_env(monkeypatch)  # 不设 OCTOAGENT_FRONTDOOR_TOKEN
        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(
                ok=True, published_url="https://macmini.tail1234.ts.net/"
            ),
        )

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 0
        # .env 真被写入：变量名 + ≥32 bytes urlsafe（43 字符）值
        env_text = (tmp_path / ".env").read_text(encoding="utf-8")
        token_line = next(
            line
            for line in env_text.splitlines()
            if line.startswith("OCTOAGENT_FRONTDOOR_TOKEN=")
        )
        token_value = token_line.split("=", 1)[1]
        assert len(token_value) >= 43
        # 0600 权限（仅 owner 可读写）
        assert ((tmp_path / ".env").stat().st_mode & 0o777) == 0o600
        # ★ FR-2a 零明文：stdout 全文不含 token 值
        assert token_value not in result.output
        # 用户可发现路径：生成消息 + grep 查看指引
        assert "已生成" in result.output
        assert "OCTOAGENT_FRONTDOOR_TOKEN" in result.output
        assert "grep" in result.output

    def test_enable_serve_failure_reports_hint_and_no_yaml_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex re-review P2：serve 失败 → 报 hint + exit1 + **不写 yaml**
        （原子：serve 成功才持久化 bearer，避免 bearer-without-serve 状态）。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), saved)
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
        # ★ 原子：serve 失败绝不已把 yaml 切成 bearer
        assert saved == []
        assert "未改动" in result.output


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
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """实例 .env host=0.0.0.0 + loopback → status 标危险（裸奔）。

        Codex 第五轮 P2：host 是「实例权威」键——用实例 .env（服务真实生效值），
        shell-only 值不再被当服务值。"""
        _patch_env(monkeypatch)
        monkeypatch.delenv("OCTOAGENT_HOST", raising=False)
        _patch_probe(
            monkeypatch,
            TailscaleProbeResult(supported=True, state=TailscaleState.NOT_INSTALLED),
        )
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])
        (tmp_path / ".env").write_text("OCTOAGENT_HOST=0.0.0.0\n", encoding="utf-8")
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)

        result = CliRunner().invoke(remote_group, ["status"])
        assert result.exit_code == 0
        assert "裸奔" in result.output


class TestCodexReviewFixes:
    """Codex review P2/P3 闭环：读服务实际生效 env + 如实报告失败。"""

    def test_enable_reads_instance_env_port(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """P2：serve 端口读实例 .env（服务实际 source 的），非仅进程 env。"""
        _patch_env(monkeypatch)  # 进程 env 不设 OCTOAGENT_PORT
        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])
        # 实例根写 .env 设 9000
        (tmp_path / ".env").write_text("OCTOAGENT_PORT=9000\n", encoding="utf-8")
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
        serve_calls: list = []

        def _fake_serve(port: int, **kwargs: object) -> TailscaleServeResult:
            serve_calls.append(port)
            return TailscaleServeResult(ok=True, published_url="https://x.ts.net/")

        monkeypatch.setattr(remote_commands, "enable_tailscale_serve", _fake_serve)

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 0
        assert serve_calls == [9000]  # 用了实例 .env 的端口

    def test_enable_warns_when_env_shadows_yaml_mode(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """P2：实例 .env 的 OCTOAGENT_FRONTDOOR_MODE 会 shadow yaml → 显式警告。

        Codex 第五轮 P2：mode 是权威键，shadow 信号来自实例 .env（服务真实生效）
        而非 shell（服务不继承）。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])
        (tmp_path / ".env").write_text(
            "OCTOAGENT_FRONTDOOR_MODE=loopback\n", encoding="utf-8"
        )
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )
        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 0
        assert "OCTOAGENT_FRONTDOOR_MODE" in result.output
        assert "覆盖" in result.output

    def test_enable_token_hint_respects_custom_env_name(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """P2：token 生成/提示用运行时实际变量名（bearer_token_env），非硬编码。"""
        _patch_env(monkeypatch)

        class _CfgCustomToken(_FakeConfig):
            def __init__(self) -> None:
                super().__init__(mode="loopback")
                self.front_door.bearer_token_env = "MY_CUSTOM_TOKEN"

        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _CfgCustomToken(), [])
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )
        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 0
        assert "MY_CUSTOM_TOKEN" in result.output
        assert "OCTOAGENT_FRONTDOOR_TOKEN" not in result.output.replace(
            "MY_CUSTOM_TOKEN", ""
        )
        # F134：.env 里写的也是自定义变量名
        env_text = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "MY_CUSTOM_TOKEN=" in env_text

    def test_enable_token_hint_skipped_when_custom_token_in_instance_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """自定义 token env 已在**实例 .env** 设值 → 不提示（尊重用户配置）。

        Codex 第六轮 P2：token-set 判断只看实例 .env（服务真实 source），shell-only
        值不算（托管服务不继承）。"""
        _patch_env(monkeypatch)

        class _CfgCustomToken(_FakeConfig):
            def __init__(self) -> None:
                super().__init__(mode="loopback")
                self.front_door.bearer_token_env = "MY_CUSTOM_TOKEN"

        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _CfgCustomToken(), [])
        (tmp_path / ".env").write_text(
            "MY_CUSTOM_TOKEN=already-set-value\n", encoding="utf-8"
        )
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )
        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 0
        assert "MY_CUSTOM_TOKEN=" not in result.output  # 未提示追加
        # F134 AC-T2：已设不覆盖（幂等）——.env 内容逐字节不变
        assert (tmp_path / ".env").read_text(encoding="utf-8") == (
            "MY_CUSTOM_TOKEN=already-set-value\n"
        )

    def test_enable_token_hint_shown_when_custom_token_shell_only(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """★ Codex 第六轮 P2：自定义 token env 只在 shell 设（不在实例 .env）→
        shell-only 不算已设（托管服务不继承 shell，重启会 503）。
        F134 语义演进：从「仍提示」升级为「自动生成写入 .env」（治本）。"""
        _patch_env(monkeypatch, MY_CUSTOM_TOKEN="shell-only-value")

        class _CfgCustomToken(_FakeConfig):
            def __init__(self) -> None:
                super().__init__(mode="loopback")
                self.front_door.bearer_token_env = "MY_CUSTOM_TOKEN"

        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _CfgCustomToken(), [])
        # 实例 .env 无该 token（只 shell 有）
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )
        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 0
        assert "MY_CUSTOM_TOKEN" in result.output  # 生成消息含变量名
        # shell-only 值不算已设 → 实例 .env 被自动补上（值为新生成非 shell 值）
        env_text = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "MY_CUSTOM_TOKEN=" in env_text
        assert "shell-only-value" not in env_text

    # ------------------------------------------------------------------
    # F134 范围二新增格（AC-T2 dry-run / AC-T3 写失败即止 / AC-T4 追加保内容）
    # ------------------------------------------------------------------

    def test_enable_dry_run_previews_token_generation_without_writing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """AC-T2：dry-run 显示「将生成」但不落地 .env。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)

        result = CliRunner().invoke(remote_group, ["enable", "--dry-run"])
        assert result.exit_code == 0
        assert "将生成" in result.output
        assert not (tmp_path / ".env").exists()

    def test_enable_token_write_failure_gateway_down_rolls_back_serve(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """AC-T3：token 写入失败 + **gateway 未运行** → 红报 + exit 1 + 不切
        mode + 回滚本次 serve 映射（Codex 四轮收敛：服务死时残留映射会在端口
        易主时把任意本地进程暴露到 tailnet；此时无 outage 可言）。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), saved)
        # root 指向不存在的子路径 → .env 打开必 OSError（可移植的写失败注入）
        broken_root = tmp_path / "missing" / "instance"
        monkeypatch.setattr(
            remote_commands, "resolve_instance_root", lambda: broken_root
        )
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )
        monkeypatch.setattr(remote_commands, "_octo_gateway_on_port", lambda _port: False)
        rollback_calls: list = []

        def _fake_disable(**kwargs: object) -> TailscaleServeResult:
            rollback_calls.append(kwargs)
            return TailscaleServeResult(ok=True)

        monkeypatch.setattr(remote_commands, "disable_tailscale_serve", _fake_disable)

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 1
        assert saved == []  # ★ 不切 mode
        assert rollback_calls  # ★ 本次映射被回滚（防持久残留暴露面）
        assert "写入 .env 失败" in result.output
        assert "已回滚" in result.output
        assert "手动" in result.output

    def test_enable_token_write_failure_bearer_gateway_alive_keeps_serve(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """AC-T3c：token 写入失败 + **实例已按 bearer 生效且 Octo 在端口上**
        （repair 场景：token 仍在服务进程 env、远程真 working）→ 不回滚
        （Codex 第四轮 P1：回滚会立断）+ 警告重启后 bearer 缺凭证。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(mode="bearer"), saved)
        broken_root = tmp_path / "missing" / "instance"
        monkeypatch.setattr(
            remote_commands, "resolve_instance_root", lambda: broken_root
        )
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )
        monkeypatch.setattr(remote_commands, "_octo_gateway_on_port", lambda _port: True)
        rollback_calls: list = []
        monkeypatch.setattr(
            remote_commands,
            "disable_tailscale_serve",
            lambda **k: rollback_calls.append(k) or TailscaleServeResult(ok=True),
        )

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 1
        assert saved == []  # 不切 mode（yaml 已 bearer 本就不写；.env 缺 token）
        assert rollback_calls == []  # ★ bearer+Octo 活着不动 serve（保 working 远程）
        assert "保留 serve 映射" in result.output
        assert "重启后" in result.output  # 警告 stale in-process token 的时限

    def test_enable_token_write_failure_loopback_alive_still_rolls_back(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """AC-T3d（Codex 第六轮 P2 钉住）：loopback 实例首次 enable + 服务活着
        + token 写失败 → **仍回滚**——mode 未切时远程本就不可用（serve 转发带
        XFF 必 403），保留映射零收益纯留暴露隐患。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), saved)
        broken_root = tmp_path / "missing" / "instance"
        monkeypatch.setattr(
            remote_commands, "resolve_instance_root", lambda: broken_root
        )
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )
        monkeypatch.setattr(remote_commands, "_octo_gateway_on_port", lambda _port: True)
        rollback_calls: list = []
        monkeypatch.setattr(
            remote_commands,
            "disable_tailscale_serve",
            lambda **k: rollback_calls.append(k) or TailscaleServeResult(ok=True),
        )

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 1
        assert saved == []
        assert rollback_calls  # ★ mode 非 bearer ⇒ 服务活着也回滚
        assert "已回滚" in result.output

    def test_enable_token_write_failure_rollback_failure_gives_manual_hint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """AC-T3b：（gateway 未运行）回滚也失败 → 如实说明映射仍开 + 手动
        关闭指引（不假报）。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), saved)
        broken_root = tmp_path / "missing" / "instance"
        monkeypatch.setattr(
            remote_commands, "resolve_instance_root", lambda: broken_root
        )
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )
        monkeypatch.setattr(remote_commands, "_octo_gateway_on_port", lambda _port: False)
        monkeypatch.setattr(
            remote_commands,
            "disable_tailscale_serve",
            lambda **k: TailscaleServeResult(
                ok=False, error_code="permission_denied", hint="手动 sudo"
            ),
        )

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 1
        assert saved == []
        assert "回滚失败" in result.output
        assert "仍开着" in result.output
        assert "octo remote disable" in result.output

    def test_enable_token_append_preserves_existing_env_content(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """AC-T4：追加写保既有内容逐字节不动 + 尾部无换行先补行 + 收紧 0600。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])
        # 既有 .env 尾部**无换行**（用户手写常态）
        (tmp_path / ".env").write_text("OCTOAGENT_PORT=9000", encoding="utf-8")
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
        serve_ports: list = []

        def _fake_serve(port: int, **kwargs: object) -> TailscaleServeResult:
            serve_ports.append(port)
            return TailscaleServeResult(ok=True, published_url="https://x.ts.net/")

        monkeypatch.setattr(remote_commands, "enable_tailscale_serve", _fake_serve)

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 0
        assert serve_ports == [9000]  # 既有配置仍生效（读到端口）
        env_text = (tmp_path / ".env").read_text(encoding="utf-8")
        lines = env_text.splitlines()
        assert lines[0] == "OCTOAGENT_PORT=9000"  # 原行逐字节保留
        assert lines[1].startswith("OCTOAGENT_FRONTDOOR_TOKEN=")  # 换行后追加
        assert env_text.endswith("\n")
        # 既有文件也被收紧 0600（Codex 七轮 P2：写前收紧）
        assert ((tmp_path / ".env").stat().st_mode & 0o777) == 0o600

    def test_write_generated_token_chmod_failure_aborts_before_write(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Codex 七轮 P2 钉住（fail-closed）：既有 .env 收紧权限失败 → **不写
        token**（宁可失败也不让 secret 落入宽权限文件）。"""
        (tmp_path / ".env").write_text("EXISTING=1\n", encoding="utf-8")

        def _deny_chmod(path: object, mode: int) -> None:
            raise PermissionError("chmod denied")

        monkeypatch.setattr(remote_commands.os, "chmod", _deny_chmod)
        err = remote_commands._write_generated_token(
            tmp_path, "OCTOAGENT_FRONTDOOR_TOKEN"
        )
        assert err is not None
        # token 未落入未收紧的文件
        assert (tmp_path / ".env").read_text(encoding="utf-8") == "EXISTING=1\n"

    def test_enable_yaml_write_failure_settles_serve(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """AC-T5（Codex 七轮 P1）：mode 持久化（save_config）失败 → exit 1 +
        统一收尾（loopback 场景 → 回滚 serve），不再裸崩溃留悬挂映射。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
        import octoagent.gateway.services.config.config_wizard as cw

        def _fail_save(config: object, _root: object) -> None:
            raise OSError("read-only octoagent.yaml")

        monkeypatch.setattr(cw, "save_config", _fail_save)
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )
        monkeypatch.setattr(remote_commands, "_octo_gateway_on_port", lambda _port: False)
        rollback_calls: list = []
        monkeypatch.setattr(
            remote_commands,
            "disable_tailscale_serve",
            lambda **k: rollback_calls.append(k) or TailscaleServeResult(ok=True),
        )

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 1
        assert rollback_calls  # ★ yaml 失败同样收尾回滚（不留悬挂映射）
        assert "octoagent.yaml 失败" in result.output
        assert "已回滚" in result.output
        # token 已写成功（.env 有值）——重试幂等跳过生成
        assert (tmp_path / ".env").exists()

    def test_enable_yaml_write_failure_alive_gateway_still_rolls_back(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """AC-T5b（Codex 八轮 P2 钉住）：save_config 抛后 cfg 内存态已被 mutate
        为 bearer——若污染收尾判定，服务活着会被误判"bearer 生效"而保留悬挂
        映射。修复=先恢复内存态再判定 → loopback 实例即使服务活着也必回滚。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
        import octoagent.gateway.services.config.config_wizard as cw

        monkeypatch.setattr(
            cw,
            "save_config",
            lambda *_a: (_ for _ in ()).throw(OSError("read-only octoagent.yaml")),
        )
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )
        # ★ 服务活着——mutated cfg 若泄漏进判定会错误走"保留"分支
        monkeypatch.setattr(remote_commands, "_octo_gateway_on_port", lambda _port: True)
        rollback_calls: list = []
        monkeypatch.setattr(
            remote_commands,
            "disable_tailscale_serve",
            lambda **k: rollback_calls.append(k) or TailscaleServeResult(ok=True),
        )

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 1
        assert rollback_calls  # ★ 内存态已恢复 loopback ⇒ 回滚而非误保留
        assert "已回滚" in result.output

    def test_enable_blocked_by_litellm_blank_token_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """AC-T6（Codex 八轮 P2）：.env.litellm 的空赋值 `TOKEN=`（source 顺序
        靠后）会盖掉生成写入 .env 的值 → enable 生成前守卫报错 + 收尾回滚 +
        指引删行；.env 不被写入 token。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), saved)
        (tmp_path / ".env.litellm").write_text(
            "OCTOAGENT_FRONTDOOR_TOKEN=\n", encoding="utf-8"
        )
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )
        monkeypatch.setattr(remote_commands, "_octo_gateway_on_port", lambda _port: False)
        rollback_calls: list = []
        monkeypatch.setattr(
            remote_commands,
            "disable_tailscale_serve",
            lambda **k: rollback_calls.append(k) or TailscaleServeResult(ok=True),
        )

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 1
        assert saved == []  # 不切 mode
        assert rollback_calls  # 收尾回滚
        assert ".env.litellm" in result.output
        assert "删除" in result.output
        assert not (tmp_path / ".env").exists()  # 未做无效写入

    def test_enable_litellm_nonblank_token_counts_as_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """AC-T7：.env.litellm 里的**非空** token（source 后者生效）视作已设
        → 幂等不生成不覆盖。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])
        (tmp_path / ".env.litellm").write_text(
            "OCTOAGENT_FRONTDOOR_TOKEN=legacy-but-valid-token\n", encoding="utf-8"
        )
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )

        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 0
        assert not (tmp_path / ".env").exists()  # 已设（litellm）→ 不生成
        assert "已生成" not in result.output

    def test_disable_serve_reset_failure_exits_1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """P2：serve reset 失败 → 红色 + exit 1（不假报成功）。"""
        _patch_env(monkeypatch)
        _patch_config(monkeypatch, _FakeConfig(mode="bearer"), [])
        monkeypatch.setattr(
            remote_commands,
            "disable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(
                ok=False, error_code="permission_denied", hint="手动 sudo"
            ),
        )
        result = CliRunner().invoke(remote_group, ["disable"])
        assert result.exit_code == 1
        assert "失败" in result.output
        assert "仍开着" in result.output

    def test_enable_idempotent_when_yaml_bearer_but_env_loopback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """幂等比对持久化值：yaml=bearer 即不重写，即便实例 .env=loopback shadow。"""
        _patch_env(monkeypatch)
        _patch_probe(monkeypatch, _READY)
        saved: list = []
        _patch_config(monkeypatch, _FakeConfig(mode="bearer"), saved)
        (tmp_path / ".env").write_text(
            "OCTOAGENT_FRONTDOOR_MODE=loopback\n", encoding="utf-8"
        )
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)
        monkeypatch.setattr(
            remote_commands,
            "enable_tailscale_serve",
            lambda *a, **k: TailscaleServeResult(ok=True, published_url="https://x.ts.net/"),
        )
        result = CliRunner().invoke(remote_group, ["enable"])
        assert result.exit_code == 0
        assert saved == []  # yaml 已 bearer 不重写
        # 但仍警告 .env shadow
        assert "覆盖" in result.output

    def test_status_reads_instance_env_host_flags_naked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """P2：status 用实例 .env 的 OCTOAGENT_HOST 判暴露面（非仅进程 env）。

        实例 .env 设 0.0.0.0 + yaml loopback → 应标危险（裸奔），即使进程 env
        未 export OCTOAGENT_HOST。"""
        _patch_env(monkeypatch)  # 进程 env 无 OCTOAGENT_HOST
        monkeypatch.delenv("OCTOAGENT_HOST", raising=False)
        _patch_probe(
            monkeypatch,
            TailscaleProbeResult(supported=True, state=TailscaleState.NOT_INSTALLED),
        )
        _patch_config(monkeypatch, _FakeConfig(mode="loopback"), [])
        # 实例根 .env 设 0.0.0.0（服务实际会用）
        (tmp_path / ".env").write_text("OCTOAGENT_HOST=0.0.0.0\n", encoding="utf-8")
        monkeypatch.setattr(remote_commands, "resolve_instance_root", lambda: tmp_path)

        result = CliRunner().invoke(remote_group, ["status"])
        assert result.exit_code == 0
        assert "裸奔" in result.output  # 未被误报为安全
