"""F111 Phase E — `octo behavior compact` CLI 测试（AC-14）。

薄 HTTP 壳：测试 monkeypatch ``_compact_request``（模块级测试缝）验证参数拼装 +
输出渲染 + 错误引导；--list-size 本地路径用真 tmp 项目根。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner
from octoagent.provider.dx import behavior_commands as bc


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class _FakeRequest:
    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, path: str, payload: dict | None = None):
        self.calls.append((method, path, payload))
        return self._responses.pop(0)


class TestTrigger:
    def test_trigger_default_set_prints_diff_and_hint(self, runner, monkeypatch):
        fake = _FakeRequest(
            [
                (
                    200,
                    {
                        "run_id": "bcpt-1",
                        "proposals_made": 1,
                        "outcomes": [
                            {
                                "file_id": "AGENTS.md",
                                "status": "proposed",
                                "candidate_id": "cand-1",
                                "size_before": 3200,
                                "size_after": 2100,
                                "diff": (
                                    "--- AGENTS.md（当前）\n"
                                    "+++ AGENTS.md（精简提议）\n-旧\n+新\n"
                                ),
                                "reason": "",
                            },
                            {
                                "file_id": "TOOLS.md",
                                "status": "skipped",
                                "reason": "too_small",
                                "candidate_id": "",
                                "size_before": 0,
                                "size_after": 0,
                                "diff": "",
                            },
                        ],
                    },
                )
            ]
        )
        monkeypatch.setattr(bc, "_compact_request", fake)
        result = runner.invoke(bc.behavior_group, ["compact"])
        assert result.exit_code == 0, result.output
        method, path, payload = fake.calls[0]
        assert (method, path) == ("POST", "/api/behavior/compact/trigger")
        assert payload == {"file_id": "", "project_slug": "default"}
        assert "3200 → 2100" in result.output
        assert "cand-1" in result.output
        assert "--apply" in result.output
        assert "too_small" in result.output

    def test_trigger_specific_file_with_project(self, runner, monkeypatch):
        fake = _FakeRequest(
            [(200, {"run_id": "r", "proposals_made": 0, "outcomes": []})]
        )
        monkeypatch.setattr(bc, "_compact_request", fake)
        monkeypatch.setattr(
            bc, "_compact_resolve_project_slug", lambda ref: ref or "selected-proj"
        )
        result = runner.invoke(
            bc.behavior_group, ["compact", "PROJECT.md", "--project", "myproj"]
        )
        assert result.exit_code == 0, result.output
        assert fake.calls[0][2] == {"file_id": "PROJECT.md", "project_slug": "myproj"}
        assert "未产生精简提议" in result.output

    def test_trigger_project_file_resolves_selected_project(self, runner, monkeypatch):
        """Codex round4 P2：PROJECT scope 文件缺省 --project 时走选中 project
        解析（不硬编码 default）。"""
        fake = _FakeRequest(
            [(200, {"run_id": "r", "proposals_made": 0, "outcomes": []})]
        )
        monkeypatch.setattr(bc, "_compact_request", fake)
        monkeypatch.setattr(
            bc, "_compact_resolve_project_slug", lambda ref: "selected-proj"
        )
        result = runner.invoke(bc.behavior_group, ["compact", "KNOWLEDGE.md"])
        assert result.exit_code == 0, result.output
        assert fake.calls[0][2] == {
            "file_id": "KNOWLEDGE.md",
            "project_slug": "selected-proj",
        }

    def test_trigger_file_id_normalized(self, runner, monkeypatch):
        """Codex round4 P3：agents / project.md 等拼法与 behavior show/edit 同款
        归一化，不被白名单误判 not_eligible。"""
        fake = _FakeRequest(
            [(200, {"run_id": "r", "proposals_made": 0, "outcomes": []})]
        )
        monkeypatch.setattr(bc, "_compact_request", fake)
        result = runner.invoke(bc.behavior_group, ["compact", "agents"])
        assert result.exit_code == 0, result.output
        assert fake.calls[0][2] == {"file_id": "AGENTS.md", "project_slug": "default"}

    def test_trigger_409_single_flight(self, runner, monkeypatch):
        fake = _FakeRequest([(409, {"detail": "busy"})])
        monkeypatch.setattr(bc, "_compact_request", fake)
        result = runner.invoke(bc.behavior_group, ["compact"])
        assert result.exit_code != 0
        assert "正在运行中" in result.output


class TestDecide:
    def test_apply_200(self, runner, monkeypatch):
        fake = _FakeRequest(
            [(200, {"ok": True, "status": "applied", "file_id": "AGENTS.md"})]
        )
        monkeypatch.setattr(bc, "_compact_request", fake)
        result = runner.invoke(bc.behavior_group, ["compact", "--apply", "cand-1"])
        assert result.exit_code == 0, result.output
        assert fake.calls[0][1] == "/api/behavior/compact/candidates/cand-1/accept"
        assert "已落盘" in result.output
        assert "F107" in result.output  # 版本兜底提示

    def test_apply_409_conflict_guides_retrigger(self, runner, monkeypatch):
        fake = _FakeRequest(
            [(409, {"ok": False, "status": "conflict", "detail": "源文件已变更"})]
        )
        monkeypatch.setattr(bc, "_compact_request", fake)
        result = runner.invoke(bc.behavior_group, ["compact", "--apply", "cand-1"])
        assert result.exit_code != 0
        assert "重新 octo behavior compact" in result.output

    def test_apply_409_pending_guides_retry(self, runner, monkeypatch):
        """Codex round7 P3：临时故障回滚（status=pending）引导重试同一命令，
        不与候选失效（conflict）混为一谈送错恢复路径。"""
        fake = _FakeRequest(
            [
                (
                    409,
                    {
                        "ok": False,
                        "status": "pending",
                        "detail": "落盘失败已回滚：OSError",
                    },
                )
            ]
        )
        monkeypatch.setattr(bc, "_compact_request", fake)
        result = runner.invoke(bc.behavior_group, ["compact", "--apply", "cand-1"])
        assert result.exit_code != 0
        assert "重试同一命令" in result.output
        assert "重新 octo behavior compact" not in result.output

    def test_apply_404(self, runner, monkeypatch):
        fake = _FakeRequest([(404, {"detail": "不存在"})])
        monkeypatch.setattr(bc, "_compact_request", fake)
        result = runner.invoke(bc.behavior_group, ["compact", "--apply", "ghost"])
        assert result.exit_code != 0
        assert "不存在" in result.output

    def test_reject_200(self, runner, monkeypatch):
        fake = _FakeRequest([(200, {"ok": True, "status": "rejected"})])
        monkeypatch.setattr(bc, "_compact_request", fake)
        result = runner.invoke(bc.behavior_group, ["compact", "--reject", "cand-1"])
        assert result.exit_code == 0
        assert fake.calls[0][1] == "/api/behavior/compact/candidates/cand-1/reject"
        assert "零触碰" in result.output


class TestList:
    def test_list_pending(self, runner, monkeypatch):
        fake = _FakeRequest(
            [
                (
                    200,
                    {
                        "pending_count": 2,
                        "candidates": [
                            {
                                "candidate_id": "cand-1",
                                "file_id": "AGENTS.md",
                                "project_slug": "default",
                                "size_before": 3000,
                                "size_after": 2000,
                                "created_at": "2026-07-15T03:30:00+00:00",
                                "rationale": "合并了 3 组重复规则",
                                "diff": "-旧规则\n+新规则\n",
                            },
                            {
                                "candidate_id": "cand-2",
                                "file_id": "PROJECT.md",
                                "project_slug": "myproj",
                                "size_before": 900,
                                "size_after": 700,
                                "created_at": "2026-07-15T03:31:00+00:00",
                                "rationale": "",
                                "diff": "",
                            },
                        ],
                    },
                )
            ]
        )
        monkeypatch.setattr(bc, "_compact_request", fake)
        result = runner.invoke(bc.behavior_group, ["compact", "--list"])
        assert result.exit_code == 0, result.output
        assert "cand-1" in result.output
        assert "合并了 3 组重复规则" in result.output
        # Codex round4 P2：PROJECT scope 候选显示归属 project
        assert "(project=myproj)" in result.output

    def test_list_empty(self, runner, monkeypatch):
        fake = _FakeRequest([(200, {"pending_count": 0, "candidates": []})])
        monkeypatch.setattr(bc, "_compact_request", fake)
        result = runner.invoke(bc.behavior_group, ["compact", "--list"])
        assert result.exit_code == 0
        assert "没有待审精简候选" in result.output


class TestGuards:
    def test_modes_mutually_exclusive(self, runner):
        result = runner.invoke(
            bc.behavior_group, ["compact", "--list", "--apply", "cand-1"]
        )
        assert result.exit_code != 0
        assert "互斥" in result.output

    def test_trusted_proxy_mode_fails_fast(self, runner, monkeypatch, tmp_path):
        """Codex round6 P2 闭环：trusted_proxy 模式显式 fail-fast（优于每个
        子命令神秘 403）。"""
        import octoagent.gateway.services.config.config_wizard as cw
        import octoagent.gateway.services.frontdoor_exposure as fde
        import octoagent.provider.dx.service_manager as sm

        monkeypatch.setattr(sm, "resolve_instance_root", lambda: tmp_path)
        monkeypatch.setattr(cw, "load_config", lambda root: None)
        monkeypatch.setattr(
            fde,
            "read_instance_effective_env",
            lambda root: {"OCTOAGENT_FRONTDOOR_MODE": "trusted_proxy"},
        )

        def _passthrough(method: str, path: str, payload: dict | None = None):
            # 真走 _compact_gateway_settings（不打真 HTTP——settings 阶段就该炸）
            bc._compact_gateway_settings()
            raise AssertionError("trusted_proxy 应在 settings 阶段 fail-fast")

        monkeypatch.setattr(bc, "_compact_request", _passthrough)
        result = runner.invoke(bc.behavior_group, ["compact", "--list"])
        assert result.exit_code != 0
        assert "trusted_proxy" in result.output

    def test_gateway_down_guides_service(self, runner, monkeypatch):
        def _boom(method: str, path: str, payload: dict | None = None):
            raise click.ClickException(
                "无法连接 gateway（ConnectError）。compact 需要 gateway 运行："
                "请先 `octo service status` / `octo restart`"
            )

        monkeypatch.setattr(bc, "_compact_request", _boom)
        result = runner.invoke(bc.behavior_group, ["compact"])
        assert result.exit_code != 0
        assert "octo service" in result.output


class TestListSize:
    def test_list_size_local_measurement(self, runner, monkeypatch, tmp_path: Path):
        """--list-size 纯本地只读（不走 HTTP），按选中 project 测（round13 P2）。"""
        from octoagent.core.behavior_workspace import resolve_write_path_by_file_id

        root = tmp_path / "instance"
        agents = resolve_write_path_by_file_id(root, "AGENTS.md")
        agents.parent.mkdir(parents=True, exist_ok=True)
        agents.write_text("# AGENTS\n- 规则\n", encoding="utf-8")
        # 非 default project 的 PROJECT.md（度量必须命中它）
        proj_md = root / "projects" / "myproj" / "behavior" / "PROJECT.md"
        proj_md.parent.mkdir(parents=True, exist_ok=True)
        proj_md.write_text("# PROJECT myproj 专属内容\n", encoding="utf-8")
        monkeypatch.setattr(bc, "_resolve_project_root", lambda: str(root))
        monkeypatch.setattr(
            bc, "_compact_resolve_project_slug", lambda ref: ref or "myproj"
        )

        def _no_http(*args: Any, **kwargs: Any):
            raise AssertionError("--list-size 不得发起 HTTP")

        monkeypatch.setattr(bc, "_compact_request", _no_http)
        result = runner.invoke(bc.behavior_group, ["compact", "--list-size"])
        assert result.exit_code == 0, result.output
        assert "AGENTS.md" in result.output
        assert "project=myproj" in result.output
        assert "总计" in result.output

    def test_measure_primitive_project_slug(self, tmp_path: Path):
        """原语 project_slug 参数：非 default project 的 PROJECT.md 被正确度量。"""
        from octoagent.core.behavior_workspace import measure_behavior_total_size

        root = tmp_path / "instance"
        proj_md = root / "projects" / "myproj" / "behavior" / "PROJECT.md"
        proj_md.parent.mkdir(parents=True, exist_ok=True)
        proj_md.write_text("x" * 42, encoding="utf-8")

        default_sizes = measure_behavior_total_size(root)  # 既有行为零破坏
        assert default_sizes["PROJECT.md"] == 0
        myproj_sizes = measure_behavior_total_size(root, project_slug="myproj")
        assert myproj_sizes["PROJECT.md"] == 42
