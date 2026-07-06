"""F130：host↔mode 暴露判定纯函数矩阵测试（spec [@test] FR-C1 / AC-2 / AC-3）。

Hermetic：纯函数，零文件系统 / 零子进程。覆盖 spec §E 全组合 → verdict，
+ 校验只读（不改 env/config）。启动期 exit(78) 接入测试见 test_main.py。
"""

from __future__ import annotations

import pytest
from octoagent.gateway.services.frontdoor_exposure import (
    FrontDoorExposureVerdict,
    resolve_bind_host,
    validate_front_door_exposure,
)


class TestExposureMatrix:
    """spec §E 判定矩阵全组合。"""

    @pytest.mark.parametrize(
        ("host", "mode"),
        [
            ("127.0.0.1", "loopback"),  # 默认 baseline
            ("127.0.0.1", "bearer"),  # Tailscale serve 推荐组合
            ("127.0.0.1", "trusted_proxy"),
            ("localhost", "loopback"),
            ("::1", "bearer"),
            ("127.0.0.5", "loopback"),  # 127.0.0.0/8 全是 loopback
        ],
    )
    def test_loopback_host_always_safe(self, host: str, mode: str) -> None:
        verdict = validate_front_door_exposure(host, mode)
        assert verdict.verdict == "safe"

    def test_naked_exposure_rejected(self) -> None:
        """★ AC-3：0.0.0.0 + loopback = 确定裸奔 → reject。"""
        verdict = validate_front_door_exposure("0.0.0.0", "loopback")
        assert verdict.verdict == "reject"
        assert "裸奔" in verdict.reason
        assert verdict.fix_hint  # 有可操作修复建议

    def test_lan_ip_loopback_mode_rejected(self) -> None:
        """非 0.0.0.0 但仍是外部网卡（LAN IP）+ loopback mode 同样裸奔。"""
        verdict = validate_front_door_exposure("192.168.1.50", "loopback")
        assert verdict.verdict == "reject"

    def test_exposed_host_with_bearer_warns(self) -> None:
        """0.0.0.0 + bearer：有 token 但暴露面大 → warn（不 reject）。"""
        verdict = validate_front_door_exposure("0.0.0.0", "bearer")
        assert verdict.verdict == "warn"
        assert verdict.fix_hint

    def test_tailnet_ip_bearer_warns(self) -> None:
        """绑 tailnet IP（bind-tailnet 备选）+ bearer → warn（可用但非最小）。"""
        verdict = validate_front_door_exposure("100.101.102.103", "bearer")
        assert verdict.verdict == "warn"

    def test_exposed_host_trusted_proxy_warns(self) -> None:
        verdict = validate_front_door_exposure("0.0.0.0", "trusted_proxy")
        assert verdict.verdict == "warn"

    def test_verdict_carries_host_and_mode(self) -> None:
        verdict = validate_front_door_exposure("0.0.0.0", "loopback")
        assert verdict.host == "0.0.0.0"
        assert verdict.mode == "loopback"
        assert isinstance(verdict, FrontDoorExposureVerdict)

    def test_unparseable_host_treated_as_exposed(self) -> None:
        """无法解析的 host 保守当非 loopback（不给虚假安全感）。"""
        verdict = validate_front_door_exposure("garbage-host", "loopback")
        assert verdict.verdict == "reject"


class TestResolveBindHost:
    def test_default_is_loopback(self) -> None:
        assert resolve_bind_host(env={}) == "127.0.0.1"

    def test_env_override(self) -> None:
        assert resolve_bind_host(env={"OCTOAGENT_HOST": "0.0.0.0"}) == "0.0.0.0"

    def test_blank_env_falls_back_to_default(self) -> None:
        assert resolve_bind_host(env={"OCTOAGENT_HOST": "   "}) == "127.0.0.1"


class TestValidationIsReadOnly:
    def test_validate_does_not_mutate_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR-C4：校验只读，不改 env/系统。"""
        import os

        before = dict(os.environ)
        validate_front_door_exposure("0.0.0.0", "loopback")
        resolve_bind_host()
        assert dict(os.environ) == before


class TestReadInstanceEffectiveEnv:
    """Codex 第四/五轮 P2：托管服务诊断——权威键只来自实例 .env（不继承 shell）。"""

    def test_non_authoritative_key_falls_back_to_shell(
        self, tmp_path, monkeypatch
    ) -> None:
        """非权威键（如 PATH）.env 未设时回退进程 env。"""
        from octoagent.gateway.services.frontdoor_exposure import (
            read_instance_effective_env,
        )

        monkeypatch.setenv("SOME_NON_OCTO_KEY", "shellval")
        (tmp_path / ".env").write_text("OCTOAGENT_PORT=8000\n", encoding="utf-8")
        env = read_instance_effective_env(tmp_path)
        assert env["SOME_NON_OCTO_KEY"] == "shellval"  # 非权威键回退 shell

    def test_authoritative_key_shell_only_is_dropped(
        self, tmp_path, monkeypatch
    ) -> None:
        """★ Codex 第五轮 P2：权威键（host/port/mode/token）shell-only 不生效
        （托管服务不继承 CLI export）——.env 无此键则视为未设，不回退 shell。"""
        from octoagent.gateway.services.frontdoor_exposure import (
            read_instance_effective_env,
        )

        monkeypatch.setenv("OCTOAGENT_PORT", "9001")  # 仅 shell，无 .env
        env = read_instance_effective_env(tmp_path)  # 无 .env 文件
        assert "OCTOAGENT_PORT" not in env  # shell-only 权威键被丢弃

    def test_authoritative_key_from_instance_env_wins(
        self, tmp_path, monkeypatch
    ) -> None:
        """权威键：shell=9001 + 实例 .env=8000 → 取 .env 的 8000。"""
        from octoagent.gateway.services.frontdoor_exposure import (
            read_instance_effective_env,
        )

        monkeypatch.setenv("OCTOAGENT_PORT", "9001")
        (tmp_path / ".env").write_text("OCTOAGENT_PORT=8000\n", encoding="utf-8")
        env = read_instance_effective_env(tmp_path)
        assert env["OCTOAGENT_PORT"] == "8000"

    def test_does_not_mutate_os_environ(self, tmp_path, monkeypatch) -> None:
        import os

        from octoagent.gateway.services.frontdoor_exposure import (
            read_instance_effective_env,
        )

        (tmp_path / ".env").write_text("OCTOAGENT_PORT=8000\n", encoding="utf-8")
        before = dict(os.environ)
        read_instance_effective_env(tmp_path)
        assert dict(os.environ) == before
