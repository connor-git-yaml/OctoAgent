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

from pathlib import Path

import pytest
from octoagent.core.models import (
    ManagedRuntimeDescriptor,
    RestartStrategy,
    utc_now,
)
from octoagent.provider.dx.service_manager import (
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
    validate_start_command,
)
from octoagent.provider.dx.update_status_store import UpdateStatusStore

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
            "octoagent.provider.dx.service_manager.shutil.which",
            lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
        )
        assert detect_init_system("linux") == "systemd"

    def test_linux_without_systemctl_degrades_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "octoagent.provider.dx.service_manager.shutil.which", lambda name: None
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
        script = (
            tmp_path / ".claude" / "worktrees" / "F129" / "scripts" / "run-octo-home.sh"
        )
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
        problems = validate_start_command(
            ["uv", "run", "uvicorn", "octoagent.gateway.main:app"]
        )
        assert any("run-octo-home.sh" in problem for problem in problems)

    def test_empty_command_rejected(self) -> None:
        assert validate_start_command([])

    def test_stable_script_passes(self, stable_script: Path) -> None:
        assert validate_start_command(["/bin/bash", str(stable_script)]) == []

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
        store = _write_descriptor(
            instance_root, start_command=["/bin/bash", str(stable_script)]
        )
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
        assert any("跳过非 OCTOAGENT_*" in message for message in messages)

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
            "octoagent.provider.dx.service_manager.shutil.which",
            lambda name: "/fake/uv-bin/uv" if name == "uv" else None,
        )
        value = build_service_path_value()
        assert value.startswith("/fake/uv-bin")
        assert "/usr/bin" in value


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
            "octoagent.provider.dx.service_manager.Path.exists",
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
        assert (
            len(runner.commands_containing("launchctl", "bootstrap")) > bootstrap_count_before
        )

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
            "octoagent.provider.dx.service_manager.shutil.which",
            lambda name: "/entirely/new/place/uv" if name == "uv" else None,
        )
        result = manager.install()
        assert result.action == "skipped"

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

        result = manager.uninstall()
        assert result.action == "uninstalled"
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
        manager.uninstall()
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
        manager, _, _ = _build_manager(
            instance_root, stable_script, tmp_path, running_pid=8888
        )
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
