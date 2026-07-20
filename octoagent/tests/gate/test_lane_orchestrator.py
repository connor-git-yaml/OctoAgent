"""F141 AC-3 + lane 组合：lane.py 编排器单测（fake runner 注入，零真子进程）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# lane 组合按模式过滤
# ---------------------------------------------------------------------------


class TestLanesForMode:
    def test_pr_lanes(self, lane_mod) -> None:
        ids = [lane.id for lane in lane_mod.lanes_for_mode("pr")]
        assert ids == [
            "quarantine-governance",
            "agent-config-sync",
            "frontend-complexity",
            "backend-smoke-scripted",
        ]

    def test_baseline_lanes(self, lane_mod) -> None:
        ids = [lane.id for lane in lane_mod.lanes_for_mode("baseline")]
        assert ids == [
            "quarantine-governance",
            "frontend-complexity",
            "backend-full",
            "frontend-vitest",
        ]

    def test_baseline_with_l1(self, lane_mod) -> None:
        ids = [lane.id for lane in lane_mod.lanes_for_mode("baseline", with_l1=True)]
        assert "l1-playwright" in ids

    def test_release_lanes_order(self, lane_mod) -> None:
        """release 含全部 live lane。"""
        ids = [lane.id for lane in lane_mod.lanes_for_mode("release")]
        assert ids == [
            "quarantine-governance",
            "attestation-signed",
            "frontend-complexity",
            "backend-deterministic",
            "frontend-vitest",
            "live-real-llm",
            "attest-service",
        ]

    def test_release_live_flags(self, lane_mod) -> None:
        lanes = {lane.id: lane for lane in lane_mod.lanes_for_mode("release")}
        assert lanes["live-real-llm"].live is True
        assert lanes["attest-service"].live is True
        assert lanes["backend-deterministic"].live is False

    def test_release_deterministic_excludes_real_llm_only(self, lane_mod) -> None:
        """D9：确定性 lane 用 not real_llm（而非 not e2e_full——那会误逐确定性 e2e_full）。"""
        lanes = {lane.id: lane for lane in lane_mod.lanes_for_mode("release")}
        assert lanes["backend-deterministic"].pytest_args[:2] == ["-m", "not real_llm"]
        assert lanes["live-real-llm"].pytest_args[:2] == ["-m", "real_llm"]

    def test_pr_pytest_expression_matches_hook(self, lane_mod) -> None:
        """pr lane 的 pytest 表达式与 pre-commit hook 一致（AC-6 抽样）。"""
        lanes = {lane.id: lane for lane in lane_mod.lanes_for_mode("pr")}
        assert lanes["backend-smoke-scripted"].pytest_args[:2] == [
            "-m", "e2e_smoke or e2e_scripted",
        ]
        hook_text = (Path(lane_mod.REPO_ROOT) / ".githooks" / "pre-commit").read_text(
            encoding="utf-8"
        )
        assert "e2e_smoke or e2e_scripted" in hook_text


# ---------------------------------------------------------------------------
# --skip 校验（AC-3③）
# ---------------------------------------------------------------------------


class TestSkipValidation:
    def test_release_rejects_skip_live(self, lane_mod) -> None:
        lanes = lane_mod.lanes_for_mode("release")
        err = lane_mod.validate_skip_args("release", ["live-real-llm"], lanes)
        assert err is not None and "release" in err

    def test_release_rejects_skip_attest_probe(self, lane_mod) -> None:
        lanes = lane_mod.lanes_for_mode("release")
        assert lane_mod.validate_skip_args("release", ["attest-service"], lanes)

    def test_release_rejects_skip_attestation_signed(self, lane_mod) -> None:
        lanes = lane_mod.lanes_for_mode("release")
        assert lane_mod.validate_skip_args("release", ["attestation-signed"], lanes)

    def test_release_allows_skip_nonlive(self, lane_mod) -> None:
        lanes = lane_mod.lanes_for_mode("release")
        assert lane_mod.validate_skip_args("release", ["frontend-vitest"], lanes) is None

    def test_unknown_lane_rejected(self, lane_mod) -> None:
        lanes = lane_mod.lanes_for_mode("pr")
        assert lane_mod.validate_skip_args("pr", ["nope"], lanes)

    def test_cli_exit_2_on_release_skip_live(self, lane_mod) -> None:
        rc = lane_mod.main(["release", "--skip", "live-real-llm"])
        assert rc == 2

    def test_dry_run_exit_3_not_zero(self, lane_mod, monkeypatch, tmp_path) -> None:
        """Codex final H2：--dry-run 有 planned lane → exit 3（彩排非通过）。

        pr 模式 dry-run：quarantine 治理真跑（PASS），其余 planned →
        必须非 0——任何把 lane.py 当 gate 的脚本只认 exit 0。
        HOME 重定向 tmp：write_report 落 ~/.octoagent/logs/lane/，不许碰宿主实例
        （hermetic 红线，octoagent/tests/AGENTS.md §6）。
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        rc = lane_mod.main(["pr", "--dry-run"])
        assert rc == 3
        reports = list((tmp_path / ".octoagent" / "logs" / "lane").glob("pr-*.json"))
        assert reports, "报告应落重定向后的 HOME"


# ---------------------------------------------------------------------------
# live lane 判定（AC-3①⑥）
# ---------------------------------------------------------------------------


def counts(passed=0, failed=0, errors=0, skips=()):
    return {"passed": passed, "failed": failed, "errors": errors, "skips": list(skips)}


class TestEvaluateLivePytest:
    def test_all_skip_fails(self, lane_mod) -> None:
        """全 SKIP（exit 0 passed=0）→ FAIL（假绿防护）。"""
        ok, detail, _ = lane_mod.evaluate_live_pytest(
            0, counts(skips=[{"nodeid": "t::a", "reason": "auth-profiles.json 不存在"}]),
        )
        assert ok is False and "零真跑" in detail

    def test_pass_with_deviation_skip_ok(self, lane_mod) -> None:
        ok, _, breakdown = lane_mod.evaluate_live_pytest(
            0,
            counts(passed=3, skips=[
                {"nodeid": "t::a", "reason": "域#7 层2 SKIP（GATE_P3_DEVIATION）: LLM 未选 graph"},
                {"nodeid": "t::b", "reason": "域#12 real LLM: LLM 没触发 IRREVERSIBLE 工具"},
            ]),
        )
        assert ok is True
        assert len(breakdown["deviation_skip"]) == 2

    def test_pass_with_manual_gate_skip_ok(self, lane_mod) -> None:
        ok, _, breakdown = lane_mod.evaluate_live_pytest(
            0,
            counts(passed=2, skips=[
                {"nodeid": "t::a",
                 "reason": "域#5 SKIP（manual gate）: 需设置 OCTOAGENT_E2E_PERPLEXITY_API_KEY"},
                {"nodeid": "t::b", "reason": "域#5 SKIP: npm 未安装，无法跑真 npm install。"},
            ]),
        )
        assert ok is True
        assert len(breakdown["manual_gate_skip"]) == 2

    def test_unexpected_skip_fails_even_with_pass(self, lane_mod) -> None:
        """1 pass + 凭证缺失 skip → FAIL（Codex H2：假诚实防护）。"""
        ok, detail, breakdown = lane_mod.evaluate_live_pytest(
            0,
            counts(passed=1, skips=[
                {"nodeid": "t::a", "reason": "auth-profiles.json 不存在（宿主 OAuth 未配置）"},
            ]),
        )
        assert ok is False
        assert "unexpected_skip" in detail
        assert len(breakdown["unexpected_skip"]) == 1

    def test_quota_skip_is_unexpected(self, lane_mod) -> None:
        """quota 耗尽 = 没验证成 → FAIL（不许「配额不足」冒充验过）。"""
        ok, _, breakdown = lane_mod.evaluate_live_pytest(
            0, counts(passed=2, skips=[{"nodeid": "t::a", "reason": "quota 耗尽 SKIP"}]),
        )
        assert ok is False
        assert len(breakdown["unexpected_skip"]) == 1

    def test_nonzero_exit_fails(self, lane_mod) -> None:
        ok, detail, _ = lane_mod.evaluate_live_pytest(1, counts(passed=5, failed=1))
        assert ok is False and "exit=1" in detail

    def test_classification_fail_closed(self, lane_mod) -> None:
        """未知措辞 → unexpected（fail-closed，措辞漂移宁可误伤不放行）。"""
        assert lane_mod.classify_skip_reason("随便什么新理由") == "unexpected_skip"


class TestParseJunit:
    def test_parse_counts_and_skip_reasons(self, lane_mod, tmp_path: Path) -> None:
        junit = tmp_path / "junit.xml"
        junit.write_text(
            """<?xml version="1.0"?>
<testsuites><testsuite name="pytest" tests="4">
  <testcase classname="t" name="a"/>
  <testcase classname="t" name="b"><failure message="boom"/></testcase>
  <testcase classname="t" name="c"><skipped message="域#5 SKIP（manual gate）: key"/></testcase>
  <testcase classname="t" name="d"><skipped message="GATE_P3_DEVIATION: LLM 未选"/></testcase>
</testsuite></testsuites>""",
            encoding="utf-8",
        )
        c = lane_mod.parse_junit_counts(junit)
        assert c["passed"] == 1 and c["failed"] == 1
        assert {s["nodeid"] for s in c["skips"]} == {"t::c", "t::d"}


# ---------------------------------------------------------------------------
# attest 判定（AC-3④⑤）
# ---------------------------------------------------------------------------


class TestEvaluateAttest:
    def test_pass(self, lane_mod) -> None:
        status, _ = lane_mod.evaluate_attest("service", {"status": "pass"})
        assert status == "pass"

    def test_fail_always_blocks(self, lane_mod) -> None:
        status, _ = lane_mod.evaluate_attest("service", {"status": "fail"})
        assert status == "fail"

    def test_service_not_enabled_fails_no_flag(self, lane_mod) -> None:
        status, detail = lane_mod.evaluate_attest(
            "service", {"status": "not_enabled"},
        )
        assert status == "fail" and "service install" in detail

    def test_unknown_status_fails(self, lane_mod) -> None:
        status, _ = lane_mod.evaluate_attest("service", {"status": "weird"})
        assert status == "fail"

    def test_warn_is_not_blocking(self, lane_mod) -> None:
        r = lane_mod.LaneResult("x", "x", "warn", 0.1)
        assert r.blocking is False


# ---------------------------------------------------------------------------
# 编排（fake runner，零真子进程）
# ---------------------------------------------------------------------------


class FakeRunner:
    """按命令特征返回预置 CompletedProcess 的 fake。"""

    def __init__(self, attest_reports: dict[str, dict] | None = None,
                 default_rc: int = 0) -> None:
        self.attest_reports = attest_reports or {}
        self.default_rc = default_rc
        self.calls: list[list[str]] = []

    def __call__(self, cmd, *, cwd, env=None, capture=False):
        self.calls.append(list(cmd))
        stdout = "ok"
        if "attest" in cmd:
            probe = cmd[cmd.index("attest") + 1]
            stdout = json.dumps(self.attest_reports.get(probe, {"status": "pass"}))
        return subprocess.CompletedProcess(cmd, self.default_rc, stdout=stdout, stderr="")


class TestOrchestration:
    def test_dry_run_only_runs_governance(self, lane_mod) -> None:
        """--dry-run：治理校验真跑，pytest/attest 只列计划（探针有真副作用）。"""
        fake = FakeRunner()
        orch = lane_mod.LaneOrchestrator("release", dry_run=True, runner=fake)
        results = orch.run(lane_mod.lanes_for_mode("release"))
        by_id = {r.id: r for r in results}
        assert by_id["quarantine-governance"].status == "pass"
        assert by_id["attestation-signed"].status in ("pass", "fail")
        for lane_id in ("backend-deterministic", "live-real-llm",
                        "attest-service"):
            assert by_id[lane_id].status == "planned"
        # dry-run 不得触碰 attest 探针 / pytest 子进程（探针有真副作用：service 闪断）
        joined = [" ".join(c) for c in fake.calls]
        assert not any("octoagent.provider.dx.cli" in c for c in joined)
        assert not any("pytest" in c for c in joined)
        # 治理校验脚本允许真跑（文件级只读）
        assert any("check-quarantine.py" in c for c in joined)

    def test_explicit_skip_recorded(self, lane_mod) -> None:
        fake = FakeRunner()
        orch = lane_mod.LaneOrchestrator("baseline", skip_ids=["frontend-vitest"], runner=fake)
        results = orch.run(lane_mod.lanes_for_mode("baseline"))
        by_id = {r.id: r for r in results}
        assert by_id["frontend-vitest"].status == "skipped_explicit"

    def test_attest_lane_parses_json_and_archives(self, lane_mod) -> None:
        fake = FakeRunner(attest_reports={
            "service": {"status": "pass", "checks": []},
        })
        orch = lane_mod.LaneOrchestrator("release", runner=fake)
        lanes = [lane for lane in lane_mod.lanes_for_mode("release") if lane.kind == "attest"]
        results = orch.run(lanes)
        by_id = {r.id: r for r in results}
        assert by_id["attest-service"].status == "pass"
        assert by_id["attest-service"].extra["attest_report"]["status"] == "pass"

    def test_attest_garbage_stdout_fails(self, lane_mod) -> None:
        class GarbageRunner(FakeRunner):
            def __call__(self, cmd, *, cwd, env=None, capture=False):
                return subprocess.CompletedProcess(cmd, 0, stdout="not-json", stderr="")

        orch = lane_mod.LaneOrchestrator("release", runner=GarbageRunner())
        lanes = [lane for lane in lane_mod.lanes_for_mode("release") if lane.kind == "attest"]
        results = orch.run(lanes[:1])
        assert results[0].status == "fail"
        assert "不可解析" in results[0].detail

    def test_missing_tool_fails_with_hint(self, lane_mod) -> None:
        class MissingToolRunner(FakeRunner):
            def __call__(self, cmd, *, cwd, env=None, capture=False):
                raise FileNotFoundError("node")

        orch = lane_mod.LaneOrchestrator("baseline", runner=MissingToolRunner())
        lanes = [lane for lane in lane_mod.lanes_for_mode("baseline")
                 if lane.id == "frontend-complexity"]
        results = orch.run(lanes)
        assert results[0].status == "fail" and "工具缺失" in results[0].detail


class TestLiveLaneCollectScope:
    def test_live_lane_collect_path_pinned_to_e2e_live(self, lane_mod) -> None:
        """live-real-llm 收集路径必须限定 e2e_live/——防 rootdir 收集触发无关 module
        的 collection-skip（如 lib_semantics piper importorskip 缺可选依赖时 module
        级 skip）先于 -m 过滤进 junit 被误判 unexpected_skip → 假 FAIL（release 首跑
        run 29220837662 实证）。所有 real_llm 用例都在 e2e_live 域，此限定不漏真跑。
        """
        lanes = {lane.id: lane for lane in lane_mod.lanes_for_mode("release")}
        args = lanes["live-real-llm"].pytest_args
        assert "apps/gateway/tests/e2e_live" in args, (
            f"live lane 收集范围须限定 e2e_live，实际 pytest_args={args}"
        )


class TestPytestEnvPin:
    def test_pythonpath_locks_repo_tree(self, lane_mod) -> None:
        """D1v2：PYTHONPATH 锁本 repo 树 9 个 src 目录 + PYTHONNOUSERSITE。"""
        env = lane_mod.build_pytest_env({"PATH": "/usr/bin"})
        assert env["PYTHONNOUSERSITE"] == "1"
        parts = env["PYTHONPATH"].split(":")
        assert str(lane_mod.OCTOAGENT_DIR / "apps" / "gateway" / "src") in parts
        pkg_srcs = [p for p in parts if "/packages/" in p and p.endswith("/src")]
        assert len(pkg_srcs) == 8, f"应锁 8 个包 src，实际 {pkg_srcs}"
