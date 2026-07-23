"""F129 service_manager 单元测试。

hermetic 红线（plan §硬约束 3）：**绝不真装**到用户 ~/Library/LaunchAgents /
systemd——所有用例：
- 服务目录 = tmp（``service_dir`` 注入）
- launchctl/systemctl = FakeCommandRunner（subprocess 注入，零真实子进程）
- descriptor 存储 = tmp（``UpdateStatusStore(data_dir=...)`` 显式注入）
- /ready 探测 = fake prober（零真实 HTTP）

覆盖 spec [@test] 绑定：FR-A（backend 探测 / AC-2 稳定路径 / KeepAlive+StartLimit）、
FR-B（三态幂等 / 归一化剔 PATH / uninstall 残留清单 / dry-run 不落地）、
FR-C1（status 三态 + 超时软化）、FR-H（keep-awake）。
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest
from octoagent.core.models import (
    ManagedRuntimeDescriptor,
    RestartStrategy,
    utc_now,
)
from octoagent.gateway.services.operations.service_manager import (
    CONFIG_ERROR_EXIT_CODE,
    LAUNCHD_LABEL,
    SYSTEMD_UNIT_NAME,
    CommandOutcome,
    LaunchdBackend,
    ServiceManager,
    ServiceManagerError,
    SystemdUserBackend,
    build_backend,
    build_service_path_value,
    detect_init_system,
    start_command_stability_warnings,
    validate_stable_paths,
    validate_start_command,
)
from octoagent.gateway.services.operations.update_status_store import UpdateStatusStore

# ---------------------------------------------------------------------------
# 测试基建
# ---------------------------------------------------------------------------


class FakeCommandRunner:
    """记录调用并按规则返回结果的 launchctl/systemctl stub。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        #: (谓词子串 tuple) -> CommandOutcome；未命中默认 ok
        self.rules: list[tuple[tuple[str, ...], CommandOutcome]] = []
        self.default = CommandOutcome(0, "", "")

    def __call__(self, command: list[str], timeout_s: float) -> CommandOutcome:
        self.calls.append(list(command))
        joined = " ".join(command)
        for needles, outcome in self.rules:
            if all(needle in joined for needle in needles):
                return outcome
        return self.default

    def commands_containing(self, *needles: str) -> list[list[str]]:
        return [
            command
            for command in self.calls
            if all(needle in " ".join(command) for needle in needles)
        ]


@pytest.fixture()
def instance_root(tmp_path: Path) -> Path:
    root = tmp_path / "octo-home"
    root.mkdir()
    return root


@pytest.fixture()
def stable_script(tmp_path: Path) -> Path:
    """模拟稳定安装位的 run-octo-home.sh（真实存在，路径不含 worktree 标记）。"""
    script = tmp_path / "octo-home" / "app" / "octoagent" / "scripts" / "run-octo-home.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    return script


def _write_descriptor(
    instance_root: Path,
    *,
    start_command: list[str],
    restart_strategy: RestartStrategy = RestartStrategy.COMMAND,
) -> UpdateStatusStore:
    store = UpdateStatusStore(instance_root, data_dir=instance_root / "data")
    now = utc_now()
    store.save_runtime_descriptor(
        ManagedRuntimeDescriptor(
            project_root=str(instance_root / "app" / "octoagent"),
            restart_strategy=restart_strategy,
            start_command=start_command,
            verify_url="http://127.0.0.1:8000/ready?profile=core",
            environment_overrides={
                "OCTOAGENT_INSTANCE_ROOT": str(instance_root),
                "OCTOAGENT_PROJECT_ROOT": str(instance_root),
                "OCTOAGENT_DATA_DIR": str(instance_root / "data"),
                "OCTOAGENT_PORT": "8000",
            },
            created_at=now,
            updated_at=now,
        )
    )
    return store


def _build_manager(
    instance_root: Path,
    stable_script: Path,
    tmp_path: Path,
    *,
    backend_kind: str = "launchd",
    runner: FakeCommandRunner | None = None,
    ready: bool = True,
    running_pid: int | None = 4242,
) -> tuple[ServiceManager, FakeCommandRunner, UpdateStatusStore]:
    store = _write_descriptor(
        instance_root,
        start_command=["/bin/bash", str(stable_script)],
    )
    runner = runner or FakeCommandRunner()
    if backend_kind == "launchd":
        if running_pid is not None:
            runner.rules.append(
                (
                    ("launchctl print",),
                    CommandOutcome(0, f"state = running\n\tpid = {running_pid}\n", ""),
                )
            )
        backend: LaunchdBackend | SystemdUserBackend = LaunchdBackend(
            service_dir=tmp_path / "LaunchAgents",
            command_runner=runner,
            uid=501,
        )
    else:
        if running_pid is not None:
            runner.rules.append(
                (
                    ("systemctl", "show"),
                    CommandOutcome(0, f"ActiveState=active\nMainPID={running_pid}\n", ""),
                )
            )
        backend = SystemdUserBackend(
            service_dir=tmp_path / "systemd-user",
            command_runner=runner,
        )
    manager = ServiceManager(
        instance_root,
        backend=backend,
        status_store=store,
        ready_prober=lambda url, timeout: ready,
        start_gate_timeout_s=1.0,
        sleeper=lambda seconds: None,
    )
    return manager, runner, store


# ---------------------------------------------------------------------------
# detect_init_system / build_backend（FR-A1）
# ---------------------------------------------------------------------------


class TestDetectInitSystem:
    def test_darwin_maps_to_launchd(self) -> None:
        assert detect_init_system("darwin") == "launchd"

    def test_linux_with_systemctl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "octoagent.gateway.services.operations.service_manager.shutil.which",
            lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
        )
        assert detect_init_system("linux") == "systemd"

    def test_linux_without_systemctl_degrades_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "octoagent.gateway.services.operations.service_manager.shutil.which", lambda name: None
        )
        assert detect_init_system("linux") == "none"

    def test_windows_maps_to_none(self) -> None:
        assert detect_init_system("win32") == "none"

    def test_build_backend_none_raises_friendly_error(self) -> None:
        with pytest.raises(ServiceManagerError, match="不支持"):
            build_backend("none")


# ---------------------------------------------------------------------------
# 稳定路径校验（spec §0.4 / AC-2 红线）
# ---------------------------------------------------------------------------


class TestStablePathValidation:
    def test_worktree_marker_rejected(self, tmp_path: Path) -> None:
        script = tmp_path / ".worktrees" / "F129" / "scripts" / "run-octo-home.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/bash\n", encoding="utf-8")
        problems = validate_start_command(["/bin/bash", str(script)])
        assert problems, "worktree 路径必须被拒绝"
        assert any(".worktrees" in problem for problem in problems)

    def test_claude_worktrees_segment_rejected(self, tmp_path: Path) -> None:
        """Codex review P1：`.claude/worktrees/...`（无 `.worktrees` 子串的
        真实 worktree 布局——本 feature 自己的 worktree 就是这形态）必须被拒。"""
        script = tmp_path / ".claude" / "worktrees" / "F129" / "scripts" / "run-octo-home.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/bash\n", encoding="utf-8")
        problems = validate_start_command(["/bin/bash", str(script)])
        assert problems, ".claude/worktrees 路径必须被拒绝"
        assert any("worktree" in problem for problem in problems)

    def test_bare_worktrees_segment_rejected(self, tmp_path: Path) -> None:
        problems = validate_start_command(
            ["/bin/bash", str(tmp_path / "worktrees" / "x" / "run-octo-home.sh")]
        )
        assert any("worktree" in problem for problem in problems)

    def test_missing_script_rejected(self, tmp_path: Path) -> None:
        problems = validate_start_command(
            ["/bin/bash", str(tmp_path / "nowhere" / "run-octo-home.sh")]
        )
        assert any("不存在" in problem for problem in problems)

    def test_dev_mode_uvicorn_command_rejected(self) -> None:
        problems = validate_start_command(["uv", "run", "uvicorn", "octoagent.gateway.main:app"])
        assert any("run-octo-home.sh" in problem for problem in problems)

    def test_empty_command_rejected(self) -> None:
        assert validate_start_command([])

    def test_stable_script_passes(self, stable_script: Path) -> None:
        assert validate_start_command(["/bin/bash", str(stable_script)]) == []

    def test_script_inside_instance_root_no_warning(
        self, instance_root: Path, stable_script: Path
    ) -> None:
        assert (
            start_command_stability_warnings(["/bin/bash", str(stable_script)], instance_root) == []
        )

    def test_script_outside_instance_root_warns_but_not_blocked(
        self, instance_root: Path, tmp_path: Path
    ) -> None:
        """Codex review 三轮 P2 曾硬拒实例根外脚本 → 四轮抓出与现有
        bootstrap 流程不兼容（install-octo-home.sh 的 descriptor 指向源码
        checkout，可在任意位置）。裁决分级：稳定 clone **警告放行**、
        worktree 标记仍硬拒。"""
        outside = tmp_path / "some-clone" / "octoagent" / "scripts" / "run-octo-home.sh"
        outside.parent.mkdir(parents=True)
        outside.write_text("#!/bin/bash\n", encoding="utf-8")
        # 不阻断（validate_start_command 无实例根 problem）
        assert validate_start_command(["/bin/bash", str(outside)]) == []
        # 但给出知情警告
        warnings = start_command_stability_warnings(["/bin/bash", str(outside)], instance_root)
        assert any("实例根之外" in warning for warning in warnings)

    def test_worktree_instance_root_blocks_install(
        self, tmp_path: Path, stable_script: Path
    ) -> None:
        """instance_root 本身指向 worktree → install blocked（repair_required）。"""
        bad_root = tmp_path / ".worktrees" / "octo-home"
        bad_root.mkdir(parents=True)
        store = _write_descriptor(bad_root, start_command=["/bin/bash", str(stable_script)])
        runner = FakeCommandRunner()
        manager = ServiceManager(
            bad_root,
            backend=LaunchdBackend(
                service_dir=tmp_path / "LaunchAgents", command_runner=runner, uid=501
            ),
            status_store=store,
            ready_prober=lambda url, timeout: True,
            start_gate_timeout_s=0.1,
            sleeper=lambda seconds: None,
        )
        result = manager.install()
        assert result.action == "blocked"
        assert result.repair_required is True


# ---------------------------------------------------------------------------
# 服务定义内容（FR-A2 / FR-A3 / AC-2）
# ---------------------------------------------------------------------------


class TestRenderedDefinitions:
    def test_launchd_plist_pins_stable_paths(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        spec, _ = manager.build_spec()
        assert spec is not None
        content = manager.backend.render(spec)
        # AC-2 硬验收：不含任何 worktree 标记
        assert ".worktrees" not in content
        assert f"<string>{instance_root}</string>" in content  # WorkingDirectory
        assert str(stable_script) in content  # ExecStart 稳定脚本
        assert "run-octo-home.sh" in content

    def test_launchd_plist_restart_policy_fields(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        spec, _ = manager.build_spec()
        content = manager.backend.render(spec)
        assert "<key>RunAtLoad</key>" in content
        assert "<key>KeepAlive</key>" in content
        assert "<key>SuccessfulExit</key>" in content  # 只异常退出重启
        assert "<key>ThrottleInterval</key>" in content  # 崩溃退避（GATE-6）
        assert "<key>ExitTimeOut</key>" in content
        assert "octoagent.out.log" in content  # DP-6 层 2 stdout 落盘
        assert "octoagent.err.log" in content
        assert "OCTOAGENT_SUPERVISED" in content

    def test_launchd_plist_excludes_non_octoagent_env(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """secret 类 env 绝不进服务定义（Constitution #5）。"""
        store = _write_descriptor(instance_root, start_command=["/bin/bash", str(stable_script)])
        descriptor = store.load_runtime_descriptor()
        assert descriptor is not None
        descriptor.environment_overrides["SILICONFLOW_API_KEY"] = "sk-super-secret"
        store.save_runtime_descriptor(descriptor)
        runner = FakeCommandRunner()
        manager = ServiceManager(
            instance_root,
            backend=LaunchdBackend(
                service_dir=tmp_path / "LaunchAgents", command_runner=runner, uid=501
            ),
            status_store=store,
            ready_prober=lambda url, timeout: True,
            start_gate_timeout_s=0.1,
            sleeper=lambda seconds: None,
        )
        spec, messages = manager.build_spec()
        assert spec is not None
        content = manager.backend.render(spec)
        assert "sk-super-secret" not in content
        assert "SILICONFLOW_API_KEY" not in content
        assert any("跳过" in message and "OCTOAGENT_*" in message for message in messages)

    def test_systemd_unit_pins_stable_paths_and_backoff(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, _, _ = _build_manager(
            instance_root, stable_script, tmp_path, backend_kind="systemd"
        )
        spec, _ = manager.build_spec()
        content = manager.backend.render(spec)
        assert ".worktrees" not in content  # AC-2
        assert f"WorkingDirectory={instance_root}" in content
        assert str(stable_script) in content
        assert "Restart=on-failure" in content
        assert "StartLimitBurst=5" in content  # 崩溃风暴熔断（GATE-6）
        assert "StartLimitIntervalSec=60" in content
        assert f"RestartPreventExitStatus={CONFIG_ERROR_EXIT_CODE}" in content
        assert "TimeoutStopSec=" in content
        assert "KillMode=control-group" in content
        assert "WantedBy=default.target" in content
        assert "StandardOutput=append:" in content
        assert "StandardError=append:" in content

    def test_path_value_contains_uv_dir_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "octoagent.gateway.services.operations.service_manager.shutil.which",
            lambda name: "/fake/uv-bin/uv" if name == "uv" else None,
        )
        value = build_service_path_value()
        assert value.startswith("/fake/uv-bin")
        assert "/usr/bin" in value

    @pytest.mark.parametrize(
        "unstable_uv",
        [
            "/Users/x/repo/.claude/worktrees/F129/octoagent/.venv/bin/uv",
            "/Users/x/repo/.worktrees/F129/.venv/bin/uv",
            "/Users/x/repo/octoagent/.venv/bin/uv",
        ],
    )
    def test_path_value_rejects_unstable_uv_dir(
        self, monkeypatch: pytest.MonkeyPatch, unstable_uv: str
    ) -> None:
        """Codex review P1（二轮）：worktree/.venv 里的 uv 目录绝不进服务
        PATH（PATH 被幂等比对剔除，写错永不自愈）——弃用 + ~/.local/bin 兜底。"""
        monkeypatch.setattr(
            "octoagent.gateway.services.operations.service_manager.shutil.which",
            lambda name: unstable_uv if name == "uv" else None,
        )
        value = build_service_path_value()
        assert ".venv" not in value
        assert "worktrees" not in value
        assert str(Path.home() / ".local" / "bin") in value

    # -- F135 gap-2：launchd 服务 PATH 注入 node/npx 稳定位置 --

    def test_path_value_includes_node_locations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-2.1：build_service_path_value 含 node/npx 稳定位置（~/.volta/bin 等）。

        launchd 干净环境无 node → npx 型 MCP 启动失败（F135 gap-2）。
        """
        monkeypatch.setattr(
            "octoagent.gateway.services.operations.service_manager.shutil.which",
            lambda name: None,  # 无 uv 也不影响 node 注入
        )
        value = build_service_path_value()
        assert str(Path.home() / ".volta" / "bin") in value, (
            "服务 PATH 必须含 ~/.volta/bin（用户 node/npx 实际位置），否则 npx 型 MCP 在"
            "常驻服务下 [Errno 2] No such file 启动失败"
        )
        # homebrew node 常见位置（Apple Silicon / Intel）也在列
        assert "/opt/homebrew/bin" in value
        assert "/usr/local/bin" in value

    def test_path_value_node_locations_are_stable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-2.2：注入的 node 位置均过 validate_stable_paths（stable-working-dir 红线）。

        node 路径写错进 PATH（如 worktree/.venv）= 服务永久崩溃循环，比装不上严重。
        """
        monkeypatch.setattr(
            "octoagent.gateway.services.operations.service_manager.shutil.which",
            lambda name: None,
        )
        value = build_service_path_value()
        for segment in value.split(":"):
            assert not validate_stable_paths([segment]), (
                f"服务 PATH 段含 worktree 标记（stable-working-dir 违规）: {segment}"
            )
            assert ".venv" not in Path(segment).parts

    def test_launchd_plist_path_contains_node(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """AC-2.3（hermetic，不真装 launchd）：
        渲染的 plist EnvironmentVariables.PATH 含 node 位置。
        """
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        spec, _ = manager.build_spec()
        assert spec is not None
        content = manager.backend.render(spec)
        payload = plistlib.loads(content.encode("utf-8"))
        env = payload.get("EnvironmentVariables", {})
        path_value = env.get("PATH", "")
        assert str(Path.home() / ".volta" / "bin") in path_value, (
            "launchd plist 的 PATH 必须含 node/npx 稳定位置（F135 gap-2 治本）"
        )
        # 仍不含 worktree 标记（AC-2 不回归）
        assert ".worktrees" not in path_value


# ---------------------------------------------------------------------------
# keep-awake（FR-H1/H2，GATE-2 选项 C opt-in）
# ---------------------------------------------------------------------------


class TestKeepAwake:
    def test_keep_awake_wraps_with_caffeinate(
        self,
        instance_root: Path,
        stable_script: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        spec, _ = manager.build_spec(keep_awake=True)
        content = manager.backend.render(spec)
        assert "/usr/bin/caffeinate" in content
        # caffeinate 必须在启动命令首位（伴随整个 gateway 生命周期）
        assert content.index("caffeinate") < content.index("run-octo-home.sh")

    def test_default_install_has_no_caffeinate(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """FR-H2：不加 --keep-awake 时零副作用。"""
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        spec, _ = manager.build_spec()
        content = manager.backend.render(spec)
        assert "caffeinate" not in content

    def test_keep_awake_on_systemd_skips_with_notice(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, _, _ = _build_manager(
            instance_root, stable_script, tmp_path, backend_kind="systemd"
        )
        result = manager.install(keep_awake=True)
        assert result.action == "installed"
        assert any("keep-awake 仅支持 macOS" in message for message in result.messages)
        content = manager.backend.service_file_path().read_text(encoding="utf-8")
        assert "caffeinate" not in content

    def test_uninstall_removes_caffeinate_definition(
        self,
        instance_root: Path,
        stable_script: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """uninstall 后 caffeinate 不残留（服务定义删除即止）。"""
        monkeypatch.setattr(
            "octoagent.gateway.services.operations.service_manager.Path.exists",
            Path.exists,  # 保持真实语义；caffeinate 检查用真实 /usr/bin/caffeinate
        )
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        install_result = manager.install(keep_awake=True)
        assert install_result.action == "installed"
        uninstall_result = manager.uninstall()
        assert uninstall_result.action == "uninstalled"
        assert not manager.backend.service_file_path().exists()


# ---------------------------------------------------------------------------
# install 三态幂等（FR-B1/B2/B4，GATE-3）
# ---------------------------------------------------------------------------


class TestInstallIdempotency:
    def test_missing_installs_and_activates(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, runner, store = _build_manager(instance_root, stable_script, tmp_path)
        result = manager.install()
        assert result.action == "installed"
        assert result.repair_required is False
        service_path = manager.backend.service_file_path()
        assert service_path.exists()
        assert runner.commands_containing("launchctl", "bootstrap")
        assert runner.commands_containing("launchctl", "kickstart")
        # FR-A4：策略切 OS_SERVICE
        descriptor = store.load_runtime_descriptor()
        assert descriptor is not None
        assert descriptor.restart_strategy == RestartStrategy.OS_SERVICE

    def test_sensitive_octoagent_env_keys_never_enter_service_definition(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2（四轮）：OCTOAGENT_ 前缀不是安全边界——
        OCTOAGENT_API_KEY 这类键名照样是 secret，不得进持久化 plist/unit。"""
        store = _write_descriptor(instance_root, start_command=["/bin/bash", str(stable_script)])
        descriptor = store.load_runtime_descriptor()
        assert descriptor is not None
        descriptor.environment_overrides["OCTOAGENT_API_KEY"] = "sk-super-secret-value"
        descriptor.environment_overrides["OCTOAGENT_BOT_TOKEN"] = "tg-secret"
        store.save_runtime_descriptor(descriptor)
        manager = ServiceManager(
            instance_root,
            backend=LaunchdBackend(
                service_dir=tmp_path / "LaunchAgents",
                command_runner=FakeCommandRunner(),
                uid=501,
            ),
            status_store=store,
            ready_prober=lambda url, timeout: True,
            start_gate_timeout_s=0.1,
            sleeper=lambda seconds: None,
        )
        spec, messages = manager.build_spec()
        assert spec is not None
        assert "OCTOAGENT_API_KEY" not in spec.environment
        assert "OCTOAGENT_BOT_TOKEN" not in spec.environment
        assert "OCTOAGENT_PORT" in spec.environment  # 正常键不受影响
        rendered = manager.backend.render(spec)
        assert "sk-super-secret-value" not in rendered
        assert any("敏感" in message for message in messages)

    def test_install_precreates_service_logs_with_tight_permissions(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """G-1（Opus 评审）：service 层 out/err 抓的是未脱敏裸 stdout——
        若交给 launchd/systemd 首次创建会跟随 umask（0644）。install 必须
        预创建 0600 + 目录 0700（两个 init 系统对已存在文件 append 保留权限）。"""
        manager, _runner, _ = _build_manager(instance_root, stable_script, tmp_path)
        result = manager.install()
        assert result.action == "installed"
        log_dir = instance_root / "logs"
        assert (log_dir.stat().st_mode & 0o777) == 0o700
        for name in ("octoagent.out.log", "octoagent.err.log"):
            target = log_dir / name
            assert target.exists()
            assert (target.stat().st_mode & 0o777) == 0o600

    def test_second_install_skips_when_content_identical(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, runner, _ = _build_manager(instance_root, stable_script, tmp_path)
        first = manager.install()
        assert first.action == "installed"
        second = manager.install()
        assert second.action == "skipped"
        assert any("--force" in message for message in second.messages)

    def test_skip_still_activates_when_service_not_running(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """skip ≠ 放任服务死着：未 loaded/running 时补 activate（幂等 install 保证态）。"""
        manager, runner, _ = _build_manager(
            instance_root, stable_script, tmp_path, running_pid=None
        )
        # 第一次装（未 running 的 fake：print 默认 ok 无 pid → running False）
        runner.rules.append((("launchctl", "print"), CommandOutcome(3, "", "not found")))
        manager.install()
        bootstrap_count_before = len(runner.commands_containing("launchctl", "bootstrap"))
        second = manager.install()
        assert second.action == "skipped"
        assert len(runner.commands_containing("launchctl", "bootstrap")) > bootstrap_count_before

    def test_activation_failure_with_stale_running_process_is_repair(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2（五轮）：enable 失败但旧进程在跑 + ready 通过时，
        gate 会放行——新定义可能没注册到 OS（开机自启失效）。gate 后补验
        loaded，未注册即 repair-required 且不切 OS_SERVICE。"""
        runner = FakeCommandRunner()
        # systemd：is-enabled 恒失败（注册失败）；show 显示旧进程 active
        runner.rules.append((("is-enabled",), CommandOutcome(1, "", "disabled")))
        manager, _, store = _build_manager(
            instance_root,
            stable_script,
            tmp_path,
            backend_kind="systemd",
            runner=runner,
            running_pid=4242,
        )
        result = manager.install()
        assert result.repair_required is True
        # 六轮改法：activate 硬失败直接置 repair（文案"激活步骤存在失败"）
        assert any(
            "激活步骤存在失败" in message or "未注册到 OS" in message for message in result.messages
        )
        descriptor = store.load_runtime_descriptor()
        assert descriptor is not None
        assert descriptor.restart_strategy == RestartStrategy.COMMAND  # 未切换

    def test_install_stops_legacy_command_process_before_activation(
        self,
        instance_root: Path,
        stable_script: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Codex review P1（十一轮）：迁移场景——旧自管 COMMAND 进程占端口
        时新服务起不来但旧进程 /ready 骗过 gate。install 前须优雅停旧进程。"""
        from octoagent.core.models import RuntimeStateSnapshot

        manager, _, store = _build_manager(instance_root, stable_script, tmp_path)
        now = utc_now()
        store.save_runtime_state(
            RuntimeStateSnapshot(
                pid=54321,
                project_root=str(instance_root),
                started_at=now,
                heartbeat_at=now,
                verify_url="http://127.0.0.1:8000/ready?profile=core",
            )
        )
        sent_signals: list[tuple[int, int]] = []
        alive = {"value": True}

        def fake_kill(pid: int, sig: int) -> None:
            if sig == 0:
                if not alive["value"]:
                    raise ProcessLookupError(pid)
                return
            sent_signals.append((pid, sig))
            alive["value"] = False  # SIGTERM 后旧进程退出

        monkeypatch.setattr(
            "octoagent.gateway.services.operations.service_manager.os.kill", fake_kill
        )
        result = manager.install()
        assert result.repair_required is False
        import signal as signal_module

        assert (54321, signal_module.SIGTERM) in sent_signals
        assert any("交由 OS 服务接管" in message for message in result.messages)
        assert store.load_runtime_state() is None  # 干净交接

    def test_install_does_not_touch_supervisor_managed_pid(
        self,
        instance_root: Path,
        stable_script: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """策略已 OS_SERVICE 时 pid 属 supervisor 管理，绝不能杀。"""
        from octoagent.core.models import RuntimeStateSnapshot

        manager, _, store = _build_manager(instance_root, stable_script, tmp_path)
        descriptor = store.load_runtime_descriptor()
        assert descriptor is not None
        descriptor.restart_strategy = RestartStrategy.OS_SERVICE
        store.save_runtime_descriptor(descriptor)
        now = utc_now()
        store.save_runtime_state(
            RuntimeStateSnapshot(
                pid=54321,
                project_root=str(instance_root),
                started_at=now,
                heartbeat_at=now,
                verify_url="http://127.0.0.1:8000/ready?profile=core",
            )
        )
        killed: list[tuple[int, int]] = []
        monkeypatch.setattr(
            "octoagent.gateway.services.operations.service_manager.os.kill",
            lambda pid, sig: killed.append((pid, sig)),
        )
        manager.install()
        assert killed == []  # 零信号

    def test_lifecycle_commands_use_drain_timeout(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2（十一轮）：stop/restart/bootout 须用 >= drain 窗口
        的超时（systemctl restart 合法等 90s，5s probe 超时会误判 124）。"""
        from octoagent.gateway.services.operations.service_manager import (
            LIFECYCLE_TIMEOUT_SECONDS,
            STOP_TIMEOUT_SECONDS,
        )

        class TimeoutRecorder(FakeCommandRunner):
            def __init__(self) -> None:
                super().__init__()
                self.timeouts: dict[str, float] = {}

            def __call__(self, command: list[str], timeout_s: float) -> CommandOutcome:
                self.timeouts[" ".join(command)] = timeout_s
                return super().__call__(command, timeout_s)

        assert LIFECYCLE_TIMEOUT_SECONDS >= STOP_TIMEOUT_SECONDS
        recorder = TimeoutRecorder()
        manager, _, _ = _build_manager(
            instance_root,
            stable_script,
            tmp_path,
            backend_kind="systemd",
            runner=recorder,
        )
        manager.backend.restart_service()
        restart_key = next(key for key in recorder.timeouts if "restart" in key)
        assert recorder.timeouts[restart_key] >= STOP_TIMEOUT_SECONDS
        manager.backend.deactivate()
        stop_key = next(key for key in recorder.timeouts if " stop " in f" {key} ")
        assert recorder.timeouts[stop_key] >= STOP_TIMEOUT_SECONDS
        # 只读探测保持短超时（防 wedged 挂死 status）
        manager.backend.probe_loaded()
        enabled_key = next(key for key in recorder.timeouts if "is-enabled" in key)
        assert recorder.timeouts[enabled_key] <= 10

    def test_systemd_install_warns_when_linger_disabled(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2（六轮）：无 linger 时登出即停——install 必须知情
        提示（只检测不自动 enable-linger）。"""
        runner = FakeCommandRunner()
        runner.rules.append((("loginctl", "show-user"), CommandOutcome(0, "Linger=no\n", "")))
        manager, runner, _ = _build_manager(
            instance_root,
            stable_script,
            tmp_path,
            backend_kind="systemd",
            runner=runner,
        )
        result = manager.install()
        assert any("linger" in message for message in result.messages)
        assert any("enable-linger" in message for message in result.messages)
        # 只读探测：loginctl 只有 show-user 形态
        for command in runner.commands_containing("loginctl"):
            assert "show-user" in command
            assert "enable-linger" not in " ".join(command)

    def test_systemd_install_no_linger_note_when_enabled(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        runner = FakeCommandRunner()
        runner.rules.append((("loginctl", "show-user"), CommandOutcome(0, "Linger=yes\n", "")))
        manager, _, _ = _build_manager(
            instance_root,
            stable_script,
            tmp_path,
            backend_kind="systemd",
            runner=runner,
        )
        result = manager.install()
        assert not any("enable-linger" in message for message in result.messages)

    def test_skip_gate_failure_reports_repair_required(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2：skipped 路径 gate 失败必须透传 repair_required
        （否则 CLI exit 0 假成功——用户 install 修复坏服务时被骗）。"""
        manager, runner, _ = _build_manager(
            instance_root, stable_script, tmp_path, running_pid=None
        )
        # print 恒失败 → loaded False / running False → activate + gate 超时
        runner.rules.append((("launchctl", "print"), CommandOutcome(3, "", "not found")))
        first = manager.install()
        assert first.repair_required is True
        second = manager.install()
        assert second.action == "skipped"
        assert second.repair_required is True, "skipped 路径不得吞掉 gate 失败"

    def test_skip_running_but_not_ready_reruns_gate_to_repair(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2（二轮）：skipped + 进程在跑但 /ready 明确 False →
        必须重走 start gate（超时转 repair-required），不得 exit 0 假成功。"""
        manager, _runner, store = _build_manager(instance_root, stable_script, tmp_path)
        first = manager.install()
        assert first.repair_required is False
        # 复用同一 service_dir/store 重建 manager，唯一差异：ready 恒 False
        manager_bad_ready, _, _ = _build_manager(
            instance_root, stable_script, tmp_path, ready=False
        )
        second = manager_bad_ready.install()
        assert second.action == "skipped"
        assert second.repair_required is True, "ready=False 不得绕过 FR-A5 gate"

    def test_skip_gate_pass_resyncs_restart_strategy(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """skipped 且健康时策略位补切 OS_SERVICE（descriptor 漂移自愈）。"""
        manager, _runner, store = _build_manager(instance_root, stable_script, tmp_path)
        manager.install()
        # 模拟 descriptor 漂移（手工/旧版本把策略改回 COMMAND）
        descriptor = store.load_runtime_descriptor()
        assert descriptor is not None
        descriptor.restart_strategy = RestartStrategy.COMMAND
        store.save_runtime_descriptor(descriptor)
        second = manager.install()
        assert second.action == "skipped"
        assert second.repair_required is False
        refreshed = store.load_runtime_descriptor()
        assert refreshed is not None
        assert refreshed.restart_strategy == RestartStrategy.OS_SERVICE

    def test_stale_definition_auto_refreshes(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """手动改坏服务文件 → install 自愈重写（无需 --force）。"""
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        manager.install()
        service_path = manager.backend.service_file_path()
        service_path.write_text("<!-- corrupted -->", encoding="utf-8")
        result = manager.install()
        assert result.action == "refreshed"
        assert "run-octo-home.sh" in service_path.read_text(encoding="utf-8")

    def test_path_only_difference_does_not_trigger_refresh(
        self,
        instance_root: Path,
        stable_script: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """归一化比对剔 PATH：uv 安装位变化不应误判过时反复重装（FR-B1）。"""
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        manager.install()
        # 模拟 uv 挪了位置 → PATH 值变化
        monkeypatch.setattr(
            "octoagent.gateway.services.operations.service_manager.shutil.which",
            lambda name: "/entirely/new/place/uv" if name == "uv" else None,
        )
        result = manager.install()
        assert result.action == "skipped"

    def test_path_schema_bump_triggers_refresh_on_installed_service(
        self,
        instance_root: Path,
        stable_script: Path,
        tmp_path: Path,
    ) -> None:
        """F135 gap-2 / Codex P2：PATH 生成逻辑变更（schema bump）必须让**已装**服务自愈重写。

        PATH 值本身被 definitions_equivalent 剔除，所以单纯改 PATH 内容不触发重装（上一 test）。
        用非 PATH 的 OCTOAGENT_PATH_SCHEMA marker 承载版本——模拟旧版本安装（schema=1）落盘后
        升级到当前版本（schema=2），install 必须走 refreshed 分支重写 plist（否则已部署用户
        保留旧 PATH，npx 型 MCP 继续失败）。
        """
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        manager.install()
        service_path = manager.backend.service_file_path()
        # 模拟"旧版本"已装服务：把落盘定义里的 schema 版本降级为 1（PATH 无 node 段的旧世界）。
        old_content = service_path.read_text(encoding="utf-8")
        downgraded = old_content.replace(
            "<string>2</string>",
            "<string>1</string>",  # launchd plist
        ).replace(
            "OCTOAGENT_PATH_SCHEMA=2",
            "OCTOAGENT_PATH_SCHEMA=1",  # systemd unit（若适用）
        )
        assert downgraded != old_content, "测试前置：应能把 schema marker 降级"
        service_path.write_text(downgraded, encoding="utf-8")
        # 升级到当前版本（schema=2）重跑 install → 必须重写（非 skipped）。
        result = manager.install()
        assert result.action == "refreshed", (
            "PATH schema bump 必须让已装服务自愈重写；否则已部署用户保留旧 PATH（无 node）"
        )
        assert "<string>2</string>" in service_path.read_text(encoding="utf-8") or (
            "OCTOAGENT_PATH_SCHEMA=2" in service_path.read_text(encoding="utf-8")
        )

    def test_force_reinstalls_even_when_identical(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        manager.install()
        result = manager.install(force=True)
        assert result.action == "refreshed"

    def test_dry_run_writes_nothing_and_runs_no_commands(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """FR-B2：dry-run 打印计划，不落地、不跑 launchctl。"""
        manager, runner, store = _build_manager(instance_root, stable_script, tmp_path)
        result = manager.install(dry_run=True)
        assert result.action == "installed"
        assert result.dry_run is True
        assert not manager.backend.service_file_path().exists()
        assert runner.calls == []
        descriptor = store.load_runtime_descriptor()
        assert descriptor is not None
        assert descriptor.restart_strategy == RestartStrategy.COMMAND  # 未切换
        assert any("[dry-run]" in message for message in result.messages)

    def test_dry_run_shows_diff_for_stale_definition(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        manager.install()
        service_path = manager.backend.service_file_path()
        service_path.write_text("<!-- old content -->", encoding="utf-8")
        result = manager.install(dry_run=True)
        assert result.action == "refreshed"
        assert any("diff" in message for message in result.messages)
        assert service_path.read_text(encoding="utf-8") == "<!-- old content -->"

    def test_missing_descriptor_blocks_with_guidance(
        self, instance_root: Path, tmp_path: Path
    ) -> None:
        store = UpdateStatusStore(instance_root, data_dir=instance_root / "data")
        runner = FakeCommandRunner()
        manager = ServiceManager(
            instance_root,
            backend=LaunchdBackend(
                service_dir=tmp_path / "LaunchAgents", command_runner=runner, uid=501
            ),
            status_store=store,
            ready_prober=lambda url, timeout: True,
            start_gate_timeout_s=0.1,
            sleeper=lambda seconds: None,
        )
        result = manager.install()
        assert result.action == "blocked"
        assert result.repair_required is True
        assert any("install-octo-home.sh" in message for message in result.messages)
        assert runner.calls == []

    def test_worktree_descriptor_blocked_even_with_force(
        self, instance_root: Path, tmp_path: Path
    ) -> None:
        """§0.4 红线不可被 --force 绕过。"""
        bad_script = tmp_path / ".worktrees" / "x" / "scripts" / "run-octo-home.sh"
        bad_script.parent.mkdir(parents=True)
        bad_script.write_text("#!/bin/bash\n", encoding="utf-8")
        store = _write_descriptor(instance_root, start_command=["/bin/bash", str(bad_script)])
        runner = FakeCommandRunner()
        manager = ServiceManager(
            instance_root,
            backend=LaunchdBackend(
                service_dir=tmp_path / "LaunchAgents", command_runner=runner, uid=501
            ),
            status_store=store,
            ready_prober=lambda url, timeout: True,
            start_gate_timeout_s=0.1,
            sleeper=lambda seconds: None,
        )
        result = manager.install(force=True)
        assert result.action == "blocked"
        assert result.repair_required is True
        assert not manager.backend.service_file_path().exists()


# ---------------------------------------------------------------------------
# start gate / repair-required（FR-A5）
# ---------------------------------------------------------------------------


class TestStartGate:
    def test_install_reports_repair_required_when_service_never_starts(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        runner = FakeCommandRunner()
        runner.rules.append((("launchctl", "print"), CommandOutcome(3, "", "not running")))
        manager, _, store = _build_manager(
            instance_root,
            stable_script,
            tmp_path,
            runner=runner,
            ready=False,
            running_pid=None,
        )
        result = manager.install()
        assert result.action == "installed"
        assert result.repair_required is True
        assert any("repair-required" in message for message in result.messages)
        # 起不来 → 不切策略（restart 委托无意义）
        descriptor = store.load_runtime_descriptor()
        assert descriptor is not None
        assert descriptor.restart_strategy == RestartStrategy.COMMAND

    def test_install_passes_gate_via_ready_probe(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, _, _ = _build_manager(
            instance_root, stable_script, tmp_path, ready=True, running_pid=77
        )
        result = manager.install()
        assert result.repair_required is False
        assert any("/ready" in message for message in result.messages)


# ---------------------------------------------------------------------------
# uninstall（FR-B3：尽力清理 + 残留清单）
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_uninstall_removes_file_and_resets_strategy(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, runner, store = _build_manager(instance_root, stable_script, tmp_path)
        manager.install()
        descriptor = store.load_runtime_descriptor()
        assert descriptor is not None
        assert descriptor.restart_strategy == RestartStrategy.OS_SERVICE

        # 模拟 bootout 生效后的真实语义：launchctl print 不再命中（复查干净）
        runner.rules.insert(0, (("launchctl print",), CommandOutcome(3, "", "not found")))
        result = manager.uninstall()
        assert result.action == "uninstalled"
        assert result.repair_required is False
        assert not manager.backend.service_file_path().exists()
        assert runner.commands_containing("launchctl", "bootout")
        descriptor = store.load_runtime_descriptor()
        assert descriptor is not None
        assert descriptor.restart_strategy == RestartStrategy.COMMAND
        assert any("残留清单为空" in message for message in result.messages)

    def test_uninstall_missing_file_is_idempotent_success(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        result = manager.uninstall()
        assert result.action == "absent"

    def test_uninstall_clears_runtime_state_both_branches(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2（三轮）：uninstall 必须清 runtime-state——旧 pid
        残留会被 COMMAND 模式 stop/restart 误用（PID 复用误杀风险）。
        uninstalled 与 absent 两分支都要清。"""
        from octoagent.core.models import RuntimeStateSnapshot

        manager, runner_ref, store = _build_manager(instance_root, stable_script, tmp_path)
        manager.install()
        # 复查干净语义：bootout 生效后 print 不再命中
        runner_ref.rules.insert(0, (("launchctl print",), CommandOutcome(3, "", "not found")))
        now = utc_now()
        store.save_runtime_state(
            RuntimeStateSnapshot(
                pid=99999,
                project_root=str(instance_root),
                started_at=now,
                heartbeat_at=now,
                verify_url="http://127.0.0.1:8000/ready?profile=core",
            )
        )
        result = manager.uninstall()
        assert result.action == "uninstalled"
        assert store.load_runtime_state() is None

        # absent 分支同样清理
        store.save_runtime_state(
            RuntimeStateSnapshot(
                pid=88888,
                project_root=str(instance_root),
                started_at=now,
                heartbeat_at=now,
                verify_url="http://127.0.0.1:8000/ready?profile=core",
            )
        )
        second = manager.uninstall()
        assert second.action == "absent"
        assert store.load_runtime_state() is None

    def test_uninstall_absent_branch_reports_residue_when_still_loaded(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2（九轮）：文件缺失但 supervisor 仍 loaded/running
        （手工删文件/上次 unload 失败）→ absent 分支对称复查残留。"""
        manager, _runner, _ = _build_manager(instance_root, stable_script, tmp_path)
        # 不 install（文件缺失）；print 默认 ok 带 pid → loaded/running 仍在
        result = manager.uninstall()
        assert result.action == "absent"
        assert result.repair_required is True
        assert any("仍在运行" in message or "unload 失败" in message for message in result.messages)

    def test_dry_run_diff_is_redacted(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2（九轮）：现有服务文件含 secret 时 dry-run diff
        删除行不得原样打出。"""
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        manager.install()
        service_path = manager.backend.service_file_path()
        secret = "sk-abcdef1234567890abcdefXYZ"
        service_path.write_text(
            service_path.read_text(encoding="utf-8").replace(
                "</dict>", f"<key>LEAK</key><string>{secret}</string></dict>"
            ),
            encoding="utf-8",
        )
        result = manager.install(dry_run=True)
        joined = "\n".join(result.messages)
        assert secret not in joined

    def test_uninstall_reports_residue_when_service_still_running(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2（八轮）：bootout/stop 真实失败（权限/卡死）时服务
        仍 loaded/running——不得报"残留清单为空"假成功，须 repair_required。"""
        manager, runner, _ = _build_manager(instance_root, stable_script, tmp_path)
        manager.install()
        # print 恒 ok 带 pid（默认规则保持）→ 复查发现仍 loaded/running
        result = manager.uninstall()
        assert result.action == "uninstalled"
        assert result.repair_required is True
        assert any("仍在运行" in message or "unload 失败" in message for message in result.messages)
        assert not any("残留清单为空" in message for message in result.messages)

    def test_uninstall_dry_run_removes_nothing(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, runner, store = _build_manager(instance_root, stable_script, tmp_path)
        manager.install()
        runner.calls.clear()
        result = manager.uninstall(dry_run=True)
        assert result.dry_run is True
        assert manager.backend.service_file_path().exists()
        assert runner.calls == []
        descriptor = store.load_runtime_descriptor()
        assert descriptor is not None
        assert descriptor.restart_strategy == RestartStrategy.OS_SERVICE

    def test_systemd_uninstall_stops_disables_and_reloads(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, runner, _ = _build_manager(
            instance_root, stable_script, tmp_path, backend_kind="systemd"
        )
        manager.install()
        # 模拟 stop/disable 生效后的真实语义：is-enabled/show 不再命中
        runner.rules.insert(0, (("is-enabled",), CommandOutcome(1, "", "disabled")))
        runner.rules.insert(
            0, (("systemctl", "show"), CommandOutcome(0, "ActiveState=inactive\nMainPID=0\n", ""))
        )
        result = manager.uninstall()
        assert result.repair_required is False
        assert runner.commands_containing("systemctl", "stop")
        assert runner.commands_containing("systemctl", "disable")
        assert runner.commands_containing("systemctl", "daemon-reload")


# ---------------------------------------------------------------------------
# status 三态（FR-C1，DP-5）
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_all_true_when_installed_loaded_running(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path, running_pid=8888)
        manager.install()
        status = manager.status()
        assert status.installed is True
        assert status.loaded is True
        assert status.running is True
        assert status.pid == 8888
        assert status.ready is True
        assert status.backend == "launchd"

    def test_status_not_installed(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        runner = FakeCommandRunner()
        runner.rules.append((("launchctl", "print"), CommandOutcome(3, "", "not found")))
        manager, _, _ = _build_manager(
            instance_root,
            stable_script,
            tmp_path,
            runner=runner,
            ready=False,
            running_pid=None,
        )
        status = manager.status()
        assert status.installed is False
        assert status.loaded is False
        assert status.running is False
        assert status.pid is None

    def test_status_last_error_line_is_redacted(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2（三轮）：err.log 是 service 层未脱敏原始输出，
        last_error_line 展示前必须脱敏。"""
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        log_dir = instance_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        secret = "sk-abcdef1234567890abcdefXYZ"
        (log_dir / "octoagent.err.log").write_text(
            f"boot ok\nERROR: provider init failed key={secret}\n",
            encoding="utf-8",
        )
        status = manager.status()
        assert secret not in status.last_error_line
        assert "ERROR" in status.last_error_line

    def test_status_last_error_redacts_before_truncation(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2（七轮）：先截断会破坏跨 300 边界的 token 形状
        导致漏脱敏——必须先对完整行脱敏再截断。"""
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        log_dir = instance_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        secret = "sk-abcdef1234567890abcdefXYZ"
        padding = "x" * 290  # secret 跨越 300 截断边界
        (log_dir / "octoagent.err.log").write_text(
            f"ERROR: {padding} key={secret}\n", encoding="utf-8"
        )
        status = manager.status()
        assert "sk-abcdef" not in status.last_error_line
        assert len(status.last_error_line) <= 300

    def test_status_returns_even_when_probe_thread_hangs(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """Codex review P2：probe 线程真卡死时 status 必须仍能返回
        （shutdown(wait=False)，不 join 卡死线程）。"""
        import threading
        import time as time_module

        release = threading.Event()

        class HangingRunner(FakeCommandRunner):
            def __call__(self, command: list[str], timeout_s: float) -> CommandOutcome:
                if "print" in " ".join(command):
                    release.wait(timeout=30.0)  # 模拟 wedged launchctl
                return super().__call__(command, timeout_s)

        try:
            runner = HangingRunner()
            store = _write_descriptor(
                instance_root, start_command=["/bin/bash", str(stable_script)]
            )
            manager = ServiceManager(
                instance_root,
                backend=LaunchdBackend(
                    service_dir=tmp_path / "LaunchAgents",
                    command_runner=runner,
                    uid=501,
                ),
                status_store=store,
                ready_prober=lambda url, timeout: True,
                start_gate_timeout_s=0.1,
                sleeper=lambda seconds: None,
                probe_future_timeout_s=0.3,
            )
            started = time_module.monotonic()
            status = manager.status()
            elapsed = time_module.monotonic() - started
            assert elapsed < 5.0, "status 不得等待卡死线程"
            assert status.loaded is False  # 软化默认值
            assert any("探测失败" in message for message in status.messages)
        finally:
            release.set()  # 释放挂起线程，防测试进程退出被 join 卡住

    def test_status_probe_exception_softens_to_default(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        """探测抛异常 → 软化为默认值 + message，不 crash（防 wedged systemctl）。"""

        class ExplodingRunner(FakeCommandRunner):
            def __call__(self, command: list[str], timeout_s: float) -> CommandOutcome:
                raise RuntimeError("systemctl wedged")

        manager, _, _ = _build_manager(
            instance_root,
            stable_script,
            tmp_path,
            runner=ExplodingRunner(),
            ready=False,
            running_pid=None,
        )
        status = manager.status()
        assert status.loaded is False
        assert status.running is False
        assert any("探测失败" in message for message in status.messages)

    def test_status_surfaces_last_error_line(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, _, _ = _build_manager(instance_root, stable_script, tmp_path)
        log_dir = instance_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "octoagent.err.log").write_text(
            "boot ok\nTraceback (most recent call last):\nValueError: bad config\n",
            encoding="utf-8",
        )
        status = manager.status()
        assert "ValueError: bad config" in status.last_error_line

    def test_systemd_status_parses_mainpid(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, _, _ = _build_manager(
            instance_root, stable_script, tmp_path, backend_kind="systemd", running_pid=555
        )
        manager.install()
        status = manager.status()
        assert status.running is True
        assert status.pid == 555


# ---------------------------------------------------------------------------
# restart 委托原语（Phase C `octo restart` 用）
# ---------------------------------------------------------------------------


class TestRestartDelegation:
    def test_launchd_restart_uses_kickstart_k(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, runner, _ = _build_manager(instance_root, stable_script, tmp_path)
        outcome = manager.restart_service()
        assert outcome.ok
        kickstart_calls = runner.commands_containing("launchctl", "kickstart", "-k")
        assert kickstart_calls
        assert f"gui/501/{LAUNCHD_LABEL}" in " ".join(kickstart_calls[0])

    def test_systemd_restart_uses_systemctl_user_restart(
        self, instance_root: Path, stable_script: Path, tmp_path: Path
    ) -> None:
        manager, runner, _ = _build_manager(
            instance_root, stable_script, tmp_path, backend_kind="systemd"
        )
        manager.restart_service()
        calls = runner.commands_containing("systemctl", "--user", "restart")
        assert calls
        assert SYSTEMD_UNIT_NAME in calls[0]


def test_plain_start_and_restart_reject_legacy_or_invalid_descriptor_without_write_or_serve(
    tmp_path: Path,
    stable_script: Path,
) -> None:
    oracle = "F151_RUNTIME_DESCRIPTOR_READ_HAS_HIDDEN_WRITE"
    issues: list[str] = []
    for label in ("legacy", "invalid"):
        root = tmp_path / label
        root.mkdir()
        store = UpdateStatusStore(root, data_dir=root / "data")
        if label == "legacy":
            legacy = root / "app/octoagent/data/ops/managed-runtime.json"
            legacy.parent.mkdir(parents=True)
            descriptor = ManagedRuntimeDescriptor(
                project_root=str(root / "app/octoagent"),
                start_command=["/bin/bash", str(stable_script)],
                verify_url="http://127.0.0.1:8000/ready?profile=core",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            legacy.write_text(descriptor.model_dump_json(indent=2), encoding="utf-8")
        else:
            store.descriptor_path.parent.mkdir(parents=True)
            store.descriptor_path.write_text("{broken\n", encoding="utf-8")
        before = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        runner = FakeCommandRunner()
        manager = ServiceManager(
            root,
            backend=LaunchdBackend(
                service_dir=root / "LaunchAgents",
                command_runner=runner,
                uid=501,
            ),
            status_store=store,
            ready_prober=lambda _url, _timeout: True,
            start_gate_timeout_s=0,
            sleeper=lambda _seconds: None,
        )
        installed = manager.install()
        restarted = manager.restart_service()
        after = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        if installed.action != "blocked":
            issues.append(f"{label} install action={installed.action}")
        if restarted.ok:
            issues.append(f"{label} restart was accepted")
        if runner.calls:
            issues.append(f"{label} reached supervisor {len(runner.calls)} times")
        if before != after:
            issues.append(f"{label} start/restart changed bytes")
    if issues:
        pytest.fail(f"{oracle}: {'; '.join(issues)}", pytrace=False)
