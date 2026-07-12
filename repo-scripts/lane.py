#!/usr/bin/env python3
"""F141 件1：三模式 lane 门禁编排器（pr / baseline / release）。

    uv run --project octoagent --no-sync python repo-scripts/lane.py <mode> [options]

模式语义（详表见 octoagent/tests/AGENTS.md §lane 模式表）：
- **pr**：commit 级快反馈（canonical 执行点是 pre-commit hook；本入口是同组检查的手动重放，
  不含 hook 的 staged-diff change-policy 路由——那只在 commit 语境有意义）。
- **baseline**：合 master 前本地全量（backend 全 testpaths 含 e2e_live + frontend
  complexity/vitest + L1 可选 ``--with-l1``）。
- **release**：真机部署前，**强制 live**（cc-haha enforceReleaseLiveLanes 范式）——
  live lane 整体被跳过即 FAIL；``SKIP_E2E`` 无效；``--skip`` 不得指向 live /
  attestation lanes；`octo attest` 探针按 F144 handoff 顺序 service → remote，
  **解析 --json 的 status 字段**（not_enabled 与 pass 同 exit 0，不可只看退出码）。

release live lane（live-real-llm）判定（spec D4v2）：
    exit 0 且 passed ≥ 1 且 unexpected_skip = 0
skip 三分类按 junit skip reason 文本匹配 ``ALLOWED_SKIP_PATTERNS``（fail-closed：
措辞漂移会把 skip 划成 unexpected → FAIL，宁可误伤不可放行）。

venv 漂移防护（spec D1v2 / Codex M2）：pytest 子进程显式 PYTHONPATH 锁**本 repo 树**
的 src 目录（PYTHONPATH 先于 site-packages——共享 venv editable 指向任何 worktree
都不影响被测代码来源）+ ``PYTHONNOUSERSITE=1``；stale venv 缺新依赖时 import error
快败，此时在主仓跑一次 ``uv sync`` 再来。

报告：stdout 摘要表 + JSON 全文落 ``~/.octoagent/logs/lane/<mode>-<ts>.json``
（attest JSON 原样归档——F144 保证 token 零泄漏）。

exit code：0 = 全部 lane 通过；1 = 至少 1 个 lane FAIL；2 = 参数错误。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OCTOAGENT_DIR = REPO_ROOT / "octoagent"
FRONTEND_DIR = OCTOAGENT_DIR / "frontend"
SCRIPTS_DIR = REPO_ROOT / "repo-scripts"

MODES = ("pr", "baseline", "release")

# release live lane 允许的 skip 分类（除此以外一切 skip = unexpected → FAIL）。
# 文本取自真实 skip reason（fail-closed：措辞漂移只会把 allowed 划成 unexpected）。
ALLOWED_SKIP_PATTERNS: dict[str, tuple[str, ...]] = {
    # 结构化 LLM 未命中（设计内变异性，e2e-testing.md GATE_P3_DEVIATION）
    "deviation_skip": (
        "GATE_P3_DEVIATION",
        "LLM 没触发",   # 域#12 IRREVERSIBLE 未触发（test_e2e_smoke_real_llm.py:486）
    ),
    # 人工闸 / 域#5 外部 MCP 环境族（文件 docstring 明示「e2e 环境通常 SKIP」）
    "manual_gate_skip": (
        "OCTOAGENT_E2E_PERPLEXITY_API_KEY",
        "manual gate",
        "域#5 SKIP",
    ),
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class LaneSpec:
    """一条 lane 的声明（cc-haha modes.ts LaneDefinition 精简版）。"""

    id: str
    title: str
    modes: frozenset[str]
    kind: str  # "script" | "command" | "pytest" | "pytest-live" | "attest"
    live: bool = False
    dry_runnable: bool = False  # --dry-run 下是否真执行（仅文件级校验类）
    command: list[str] = field(default_factory=list)  # script/command 用
    cwd: Path | None = None
    pytest_args: list[str] = field(default_factory=list)  # pytest* 用
    attest_probe: str = ""  # attest 用："service" | "remote"


@dataclass
class LaneResult:
    id: str
    title: str
    status: str  # "pass" | "fail" | "warn" | "skipped_explicit" | "planned"
    duration_s: float
    detail: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.status == "fail"


# ---------------------------------------------------------------------------
# lane 注册表（spec §3）
# ---------------------------------------------------------------------------


def lanes_for_mode(mode: str, *, with_l1: bool = False) -> list[LaneSpec]:
    """按模式过滤 lane 清单（顺序即执行顺序；attest 顺序 = F144 handoff §2）。"""
    py = sys.executable
    lanes = [
        LaneSpec(
            id="quarantine-governance",
            title="flaky quarantine 治理（过期即 FAIL）",
            modes=frozenset(MODES),
            kind="script",
            dry_runnable=True,
            command=[py, str(SCRIPTS_DIR / "check-quarantine.py"), "--enforce-review-date"],
        ),
        LaneSpec(
            id="attestation-signed",
            title="attestation 清单签署核对（release 必签项）",
            modes=frozenset({"release"}),
            kind="script",
            dry_runnable=True,
            command=[py, str(SCRIPTS_DIR / "check-attestation.py"), "--require-signed"],
        ),
        LaneSpec(
            id="agent-config-sync",
            title="agent-config 同步检查",
            modes=frozenset({"pr"}),
            kind="command",
            command=["bash", str(SCRIPTS_DIR / "sync-agent-config.sh"), "--check"],
        ),
        LaneSpec(
            id="frontend-complexity",
            title="前端复杂度护栏",
            modes=frozenset(MODES),
            kind="command",
            command=["node", str(SCRIPTS_DIR / "check-frontend-complexity.mjs")],
        ),
        LaneSpec(
            id="backend-smoke-scripted",
            title="backend e2e_smoke + e2e_scripted（pr 快反馈）",
            modes=frozenset({"pr"}),
            kind="pytest",
            pytest_args=["-m", "e2e_smoke or e2e_scripted", "-q", "--maxfail=1"],
        ),
        LaneSpec(
            id="backend-full",
            title="backend 全量（含 e2e_live；real_llm 凭证在场即真打）",
            modes=frozenset({"baseline"}),
            kind="pytest",
            pytest_args=["-q"],
        ),
        LaneSpec(
            id="backend-deterministic",
            title="backend 全量确定性（-m 'not real_llm'）",
            modes=frozenset({"release"}),
            kind="pytest",
            pytest_args=["-m", "not real_llm", "-q"],
        ),
        LaneSpec(
            id="frontend-vitest",
            title="前端 vitest",
            modes=frozenset({"baseline", "release"}),
            kind="command",
            command=["npx", "vitest", "run"],
            cwd=FRONTEND_DIR,
        ),
    ]
    if with_l1:
        lanes.append(
            LaneSpec(
                id="l1-playwright",
                title="L1 Playwright UI E2E（--with-l1 显式开启）",
                modes=frozenset({"baseline"}),
                kind="command",
                command=["npx", "playwright", "test"],
                cwd=FRONTEND_DIR,
            )
        )
    lanes += [
        LaneSpec(
            id="live-real-llm",
            title="真 LLM live e2e（-m real_llm；skip 即 FAIL）",
            modes=frozenset({"release"}),
            kind="pytest-live",
            live=True,
            pytest_args=["-m", "real_llm", "-q", "-r", "s"],
        ),
        # F144 handoff §2 顺序：service 先（闪断恢复后再跑 remote，避免撞闪断窗口）
        LaneSpec(
            id="attest-service",
            title="octo attest service（F129 崩溃自愈探针；秒级闪断）",
            modes=frozenset({"release"}),
            kind="attest",
            live=True,
            attest_probe="service",
        ),
        LaneSpec(
            id="attest-remote",
            title="octo attest remote（F130 远程链路探针）",
            modes=frozenset({"release"}),
            kind="attest",
            live=True,
            attest_probe="remote",
        ),
    ]
    return [lane for lane in lanes if mode in lane.modes]


# ---------------------------------------------------------------------------
# 纯判定函数（单测锚点）
# ---------------------------------------------------------------------------


def classify_skip_reason(reason: str) -> str:
    """skip reason → 三分类（deviation / manual_gate / unexpected）。"""
    for category, patterns in ALLOWED_SKIP_PATTERNS.items():
        if any(p in reason for p in patterns):
            return category
    return "unexpected_skip"


def parse_junit_counts(junit_path: Path) -> dict:
    """解析 junit xml → passed / failed / errors / skips 列表（含 reason）。"""
    tree = ET.parse(junit_path)
    root = tree.getroot()
    # pytest junit：root 可能是 <testsuites><testsuite> 或直接 <testsuite>
    suites = root.findall("testsuite") or [root]
    passed = 0
    failed = 0
    errors = 0
    skips: list[dict] = []
    for suite in suites:
        for case in suite.findall("testcase"):
            nodeid = f"{case.get('classname', '')}::{case.get('name', '')}"
            if case.find("failure") is not None:
                failed += 1
            elif case.find("error") is not None:
                errors += 1
            elif (skipped := case.find("skipped")) is not None:
                skips.append({
                    "nodeid": nodeid,
                    "reason": skipped.get("message") or (skipped.text or ""),
                })
            else:
                passed += 1
    return {"passed": passed, "failed": failed, "errors": errors, "skips": skips}


def evaluate_live_pytest(exit_code: int, counts: dict) -> tuple[bool, str, dict]:
    """live-real-llm lane 判定（spec D4v2）：exit 0 且 passed≥1 且 unexpected_skip=0。

    返回 (是否通过, 摘要文本, 分类明细)。
    """
    breakdown: dict[str, list[dict]] = {
        "deviation_skip": [], "manual_gate_skip": [], "unexpected_skip": [],
    }
    for skip in counts["skips"]:
        breakdown[classify_skip_reason(skip["reason"])].append(skip)

    summary = (
        f"passed={counts['passed']} failed={counts['failed']} errors={counts['errors']} "
        f"deviation_skip={len(breakdown['deviation_skip'])} "
        f"manual_gate_skip={len(breakdown['manual_gate_skip'])} "
        f"unexpected_skip={len(breakdown['unexpected_skip'])}"
    )
    if exit_code != 0:
        return False, f"pytest exit={exit_code}；{summary}", breakdown
    if counts["passed"] < 1:
        return False, f"live lane 零真跑（全 SKIP 假绿防护）；{summary}", breakdown
    if breakdown["unexpected_skip"]:
        detail = "; ".join(
            f"{s['nodeid']}: {s['reason'][:120]}" for s in breakdown["unexpected_skip"]
        )
        return (
            False,
            f"存在 unexpected_skip（凭证/quota/环境缺失即未验证）；{summary}；{detail}",
            breakdown,
        )
    return True, summary, breakdown


def evaluate_attest(
    probe: str, report: dict, *, allow_not_enabled: bool
) -> tuple[str, str]:
    """attest 探针判定（spec D2）。返回 (lane status, 摘要)。

    - status 字段必须解析（not_enabled 与 pass 同 exit 0，F144 handoff §1 坑）；
    - fail → FAIL（恒阻断，含 bearer+tailscale 断链）；
    - service not_enabled → FAIL（常驻服务是部署形态前提，无 flag）；
    - remote not_enabled → 默认 FAIL（防「忘了部署远程还以为验过」），
      显式 ``--allow-not-enabled`` → WARN 记录放行。
    """
    status = report.get("status")
    if status == "pass":
        return "pass", f"attest {probe} = pass"
    if status == "fail":
        return "fail", f"attest {probe} = fail（已启用但链路断，恒阻断）"
    if status == "not_enabled":
        if probe == "service":
            return "fail", (
                "attest service = not_enabled——部署机上服务未安装是 release 阻断"
                "（F129 常驻是部署形态前提）；先 `octo service install`"
            )
        if allow_not_enabled:
            return "warn", (
                "attest remote = not_enabled——已凭 --allow-not-enabled 显式确认"
                "（远程触达未部署，记录放行）"
            )
        return "fail", (
            "attest remote = not_enabled——默认阻断防「忘了部署远程还以为验过」；"
            "要么 `octo remote enable`，要么显式 `--allow-not-enabled` 确认"
        )
    return "fail", f"attest {probe} 输出无法识别的 status={status!r}"


def validate_skip_args(mode: str, skip_ids: list[str], lanes: list[LaneSpec]) -> str | None:
    """--skip 合法性（release 拒 live / attestation lanes）。返回错误信息或 None。"""
    lane_by_id = {lane.id: lane for lane in lanes}
    for skip_id in skip_ids:
        if skip_id not in lane_by_id:
            return f"--skip 指向未知 lane: {skip_id}（本模式可用: {sorted(lane_by_id)}）"
        lane = lane_by_id[skip_id]
        if mode == "release" and (lane.live or lane.id == "attestation-signed"):
            return (
                f"release 模式不允许 --skip {skip_id}（live / attestation lane 是 release "
                f"的存在意义；skip 即 FAIL 是设计而非缺陷）"
            )
    return None


def build_pytest_env(base_env: dict[str, str]) -> dict[str, str]:
    """PYTHONPATH 锁本 repo 树（spec D1v2）+ PYTHONNOUSERSITE。"""
    src_dirs = sorted(str(p) for p in OCTOAGENT_DIR.glob("packages/*/src"))
    src_dirs.append(str(OCTOAGENT_DIR / "apps" / "gateway" / "src"))
    env = {**base_env, "PYTHONNOUSERSITE": "1", "PYTHONPATH": os.pathsep.join(src_dirs)}
    return env


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------


def _default_runner(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None,
                    capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=capture, text=True,
    )


class LaneOrchestrator:
    """顺序执行 lane 并收集结果。``runner`` 可注入（单测用 fake）。"""

    def __init__(
        self,
        mode: str,
        *,
        allow_not_enabled: bool = False,
        skip_ids: list[str] | None = None,
        dry_run: bool = False,
        attest_max_age: int | None = None,
        runner=None,
    ) -> None:
        self.mode = mode
        self.allow_not_enabled = allow_not_enabled
        self.skip_ids = set(skip_ids or [])
        self.dry_run = dry_run
        self.attest_max_age = attest_max_age
        self.runner = runner or _default_runner

    # -- 单 lane 执行 --------------------------------------------------------

    def run_lane(self, lane: LaneSpec) -> LaneResult:
        start = time.monotonic()

        if lane.id in self.skip_ids:
            return LaneResult(lane.id, lane.title, "skipped_explicit",
                              0.0, "--skip 显式跳过（记录在案）")
        if self.dry_run and not lane.dry_runnable:
            return LaneResult(lane.id, lane.title, "planned", 0.0,
                              "--dry-run：仅列入计划未执行")

        try:
            if lane.kind == "script":
                return self._run_script(lane, start)
            if lane.kind == "command":
                return self._run_command(lane, start)
            if lane.kind == "pytest":
                return self._run_pytest(lane, start)
            if lane.kind == "pytest-live":
                return self._run_pytest_live(lane, start)
            if lane.kind == "attest":
                return self._run_attest(lane, start)
            raise ValueError(f"未知 lane kind: {lane.kind}")
        except FileNotFoundError as exc:
            return LaneResult(
                lane.id, lane.title, "fail", time.monotonic() - start,
                f"工具缺失: {exc}（安装后重跑，或非 live lane 可 --skip {lane.id} 显式记录）",
            )

    def _run_script(self, lane: LaneSpec, start: float) -> LaneResult:
        cmd = list(lane.command)
        if lane.id == "attestation-signed" and self.attest_max_age is not None:
            cmd += ["--attest-max-age", str(self.attest_max_age)]
        proc = self.runner(cmd, cwd=REPO_ROOT, capture=True)
        status = "pass" if proc.returncode == 0 else "fail"
        detail = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        if proc.returncode != 0:
            detail = ((proc.stderr or "").strip() or detail)[:2000]
        return LaneResult(lane.id, lane.title, status, time.monotonic() - start, detail)

    def _run_command(self, lane: LaneSpec, start: float) -> LaneResult:
        proc = self.runner(lane.command, cwd=lane.cwd or REPO_ROOT, capture=True)
        status = "pass" if proc.returncode == 0 else "fail"
        detail = "" if status == "pass" else \
            ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()[-2000:]
        return LaneResult(lane.id, lane.title, status, time.monotonic() - start, detail)

    def _pytest_cmd(self, extra_args: list[str]) -> list[str]:
        return ["uv", "run", "--no-sync", "python", "-m", "pytest", *extra_args]

    def _run_pytest(self, lane: LaneSpec, start: float) -> LaneResult:
        env = build_pytest_env(dict(os.environ))
        proc = self.runner(
            self._pytest_cmd(lane.pytest_args), cwd=OCTOAGENT_DIR, env=env, capture=True,
        )
        status = "pass" if proc.returncode == 0 else "fail"
        tail = "\n".join((proc.stdout or "").strip().splitlines()[-6:])
        return LaneResult(lane.id, lane.title, status, time.monotonic() - start, tail)

    def _run_pytest_live(self, lane: LaneSpec, start: float) -> LaneResult:
        env = build_pytest_env(dict(os.environ))
        with tempfile.TemporaryDirectory(prefix="f141-lane-") as tmp:
            junit_path = Path(tmp) / "junit-live.xml"
            args = [*lane.pytest_args, f"--junitxml={junit_path}"]
            proc = self.runner(
                self._pytest_cmd(args), cwd=OCTOAGENT_DIR, env=env, capture=True,
            )
            if not junit_path.is_file():
                return LaneResult(
                    lane.id, lane.title, "fail", time.monotonic() - start,
                    f"junit 产物缺失（pytest exit={proc.returncode}，collection 崩溃？）",
                )
            counts = parse_junit_counts(junit_path)
        ok, summary, breakdown = evaluate_live_pytest(proc.returncode, counts)
        return LaneResult(
            lane.id, lane.title, "pass" if ok else "fail",
            time.monotonic() - start, summary,
            extra={"skip_breakdown": {
                k: [s["nodeid"] for s in v] for k, v in breakdown.items()
            }},
        )

    def _run_attest(self, lane: LaneSpec, start: float) -> LaneResult:
        # 经模块入口调 octo（绕 console-script shebang 陷阱，同 hook 教训）；
        # PYTHONPATH 锁本树——探针代码与被 release 的代码同源。
        env = build_pytest_env(dict(os.environ))
        cmd = [
            "uv", "run", "--no-sync", "python", "-c",
            "from octoagent.provider.dx.cli import main; main()",
            "attest", lane.attest_probe, "--json",
        ]
        proc = self.runner(cmd, cwd=OCTOAGENT_DIR, env=env, capture=True)
        try:
            report = json.loads(proc.stdout or "")
        except json.JSONDecodeError:
            return LaneResult(
                lane.id, lane.title, "fail", time.monotonic() - start,
                f"attest --json stdout 不可解析（exit={proc.returncode}）: "
                f"{(proc.stdout or '')[:300]} / stderr: {(proc.stderr or '')[:300]}",
            )
        status, detail = evaluate_attest(
            lane.attest_probe, report, allow_not_enabled=self.allow_not_enabled,
        )
        next_steps = report.get("next_steps") or []
        if status != "pass" and next_steps:
            detail += " | next_steps: " + "; ".join(str(s) for s in next_steps[:3])
        return LaneResult(
            lane.id, lane.title, status, time.monotonic() - start, detail,
            extra={"attest_report": report},  # F144：token 零泄漏，可全文归档
        )

    # -- 编排 ---------------------------------------------------------------

    def run(self, lanes: list[LaneSpec]) -> list[LaneResult]:
        results = []
        for lane in lanes:
            print(f"[lane:{self.mode}] ▶ {lane.id} —— {lane.title}", flush=True)
            result = self.run_lane(lane)
            marker = {"pass": "✅", "fail": "❌", "warn": "⚠️",
                      "skipped_explicit": "⏭", "planned": "·"}[result.status]
            print(f"[lane:{self.mode}] {marker} {lane.id} ({result.duration_s:.1f}s) "
                  f"{result.detail.splitlines()[0] if result.detail else ''}", flush=True)
            results.append(result)
        return results


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def write_report(mode: str, args_ns: argparse.Namespace,
                 results: list[LaneResult], exit_code: int) -> Path | None:
    report = {
        "feature": "F141",
        "mode": mode,
        "started_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "args": {
            "dry_run": args_ns.dry_run,
            "skip": args_ns.skip,
            "allow_not_enabled": args_ns.allow_not_enabled,
            "with_l1": args_ns.with_l1,
        },
        "lanes": [
            {
                "id": r.id, "title": r.title, "status": r.status,
                "duration_s": round(r.duration_s, 2), "detail": r.detail,
                **({"extra": r.extra} if r.extra else {}),
            }
            for r in results
        ],
        "exit_code": exit_code,
    }
    try:
        out_dir = Path.home() / ".octoagent" / "logs" / "lane"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        out = out_dir / f"{mode}-{ts}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
    except OSError:
        return None


def print_summary(mode: str, results: list[LaneResult]) -> None:
    print()
    print(f"=== lane {mode} 结果矩阵 ===")
    width = max((len(r.id) for r in results), default=8)
    for r in results:
        print(f"  {r.id.ljust(width)}  {r.status.upper():18} {r.duration_s:7.1f}s")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="F141 三模式 lane 门禁编排（pr / baseline / release）",
    )
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--dry-run", action="store_true",
                        help="只跑文件级治理校验，其余 lane 列计划不执行（release 彩排）")
    parser.add_argument("--skip", action="append", default=[], metavar="LANE_ID",
                        help="显式跳过某 lane（记录在案；release 拒绝 live/attestation）")
    parser.add_argument("--allow-not-enabled", action="store_true",
                        help="release：attest remote = not_enabled 时降级 WARN 放行")
    parser.add_argument("--with-l1", action="store_true",
                        help="baseline：附加 L1 Playwright lane（需本地 playwright 浏览器）")
    parser.add_argument("--attest-max-age", type=int, default=None,
                        help="attestation 签署有效天数（默认走 check-attestation.py 的 90）")
    args = parser.parse_args(argv)

    lanes = lanes_for_mode(args.mode, with_l1=args.with_l1)

    err = validate_skip_args(args.mode, args.skip, lanes)
    if err:
        print(f"[lane] 参数错误: {err}", file=sys.stderr)
        return 2

    if os.environ.get("SKIP_E2E") == "1":
        if args.mode == "release":
            print("[lane] ⚠ SKIP_E2E=1 在 release 模式**无效**（skip 即 FAIL 是 release 的"
                  "存在意义）——live lane 照常执行", flush=True)
        else:
            print("[lane] ⚠ lane.py 不消费 SKIP_E2E（那是 pre-commit hook 专属逃生门）；"
                  "显式调 lane 即显式要跑门禁", flush=True)

    if shutil.which("uv") is None:
        print("[lane] uv 不可用——lane 依赖 uv 驱动 pytest/octo 子进程", file=sys.stderr)
        return 2

    orchestrator = LaneOrchestrator(
        args.mode,
        allow_not_enabled=args.allow_not_enabled,
        skip_ids=args.skip,
        dry_run=args.dry_run,
        attest_max_age=args.attest_max_age,
    )
    results = orchestrator.run(lanes)

    exit_code = 1 if any(r.blocking for r in results) else 0
    print_summary(args.mode, results)
    report_path = write_report(args.mode, args, results, exit_code)
    if report_path:
        print(f"[lane] 报告: {report_path}")
    if exit_code == 0:
        print(f"[lane] {args.mode} 全部通过")
    else:
        failed = [r.id for r in results if r.blocking]
        print(f"[lane] {args.mode} FAIL: {failed}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
