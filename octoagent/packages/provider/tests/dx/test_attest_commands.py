"""F144 交付②：`octo attest` 探针 hermetic 单测（spec AC-B / AC-C）。

Hermetic 红线：tailscale 探测 / HTTP / kill / sleep 全经 DI 注入 fake——
**零真实 tailscale、零真实网络、零真实 kill、零真实 sleep**（虚拟时钟）。
真机执行留给用户/主 session（探针本体不进 CI，本文件测的是探针逻辑本身）。

机械断言三红线：
- FR-B3 token 零泄漏：高熵 sentinel token 跑全链，扫描 report JSON 全文无命中；
- FR-B5 只读：remote 探针 HTTP 全 GET、探针前后实例配置文件字节不变；
- FR-C2 dry-run 零 kill：kill_fn 调用记录为空。
"""

from __future__ import annotations

import json
import signal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

# pre-merge 窗口防御（F138 spec §3.5 同款）：pre-commit hook 可能以非本 worktree
# 的 src 收集本文件，彼时 attest_commands 不存在 → 优雅 SKIP；合入后恒可 import。
pytest.importorskip("octoagent.provider.dx.attest_commands")

from octoagent.provider.dx.attest_commands import (
    AttestReport,
    run_remote_probe,
    run_service_probe,
)
from octoagent.provider.dx.service_manager import ServiceStatus
from octoagent.provider.dx.tailscale_helper import TailscaleProbeResult, TailscaleState

# 高熵 sentinel：若泄漏，任何输出面一 grep 即中（FR-B3）。
_SENTINEL_TOKEN = "attest-SENTINEL-9f2c7d1e8b4a6053-TOKEN"
_DNS = "macmini.tail1234.ts.net"


def _ready_probe() -> TailscaleProbeResult:
    return TailscaleProbeResult(
        supported=True,
        state=TailscaleState.READY,
        dns_name=_DNS,
        ipv4="100.101.102.103",
        detail=f"tailnet 就绪：{_DNS}",
    )


def _not_ready_probe() -> TailscaleProbeResult:
    return TailscaleProbeResult(
        supported=True,
        state=TailscaleState.INSTALLED_NOT_READY,
        detail="daemon 未运行",
    )


def _bearer_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        front_door=SimpleNamespace(
            mode="bearer", bearer_token_env="OCTOAGENT_FRONTDOOR_TOKEN"
        )
    )


def _loopback_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        front_door=SimpleNamespace(
            mode="loopback", bearer_token_env="OCTOAGENT_FRONTDOOR_TOKEN"
        )
    )


class _RecordingTransportWrapper:
    """记录全部请求（method+path）的 httpx handler 包装——只读红线机械断言用。"""

    def __init__(self, handler):
        self._handler = handler
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        return self._handler(request)


def _happy_remote_handler(request: httpx.Request) -> httpx.Response:
    """全链健康的假远端（serve 反代后的 gateway 形态）。"""
    path = request.url.path
    if path == "/ready":
        return httpx.Response(200, json={"status": "ready"})
    if path == "/":
        return httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text="<html/>"
        )
    if path == "/api/control/snapshot":
        if request.headers.get("authorization") == f"Bearer {_SENTINEL_TOKEN}":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(
            401, json={"detail": {"code": "FRONT_DOOR_TOKEN_REQUIRED"}}
        )
    if path == "/api/tasks":
        if request.headers.get("authorization") != f"Bearer {_SENTINEL_TOKEN}":
            return httpx.Response(401)
        return httpx.Response(
            200,
            json={
                "tasks": [
                    {
                        "task_id": "task-recent-1",
                        "created_at": "2026-07-12T00:00:00Z",
                        "updated_at": "2026-07-12T00:00:00Z",
                        "status": "SUCCEEDED",
                        "title": "近期任务",
                        "thread_id": "th-1",
                    }
                ]
            },
        )
    if path.startswith("/api/stream/task/"):
        if request.url.params.get("access_token") != _SENTINEL_TOKEN:
            return httpx.Response(
                401, json={"detail": {"code": "FRONT_DOOR_TOKEN_INVALID"}}
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            # 迭代器 content = 真流式响应（bytes 会被 MockTransport 预载，
            # 消费端 iter_raw 抛 StreamConsumed——与真 SSE 语义不符）
            content=iter([b'data: {"type": "history"}\n\n']),
        )
    return httpx.Response(404, json={"detail": "not found"})


def _remote_kwargs(handler, *, cfg=None, probe=None, token=_SENTINEL_TOKEN, root=None):
    """run_remote_probe 全 DI 便捷组装。"""
    recorder = _RecordingTransportWrapper(handler)

    def factory():
        return httpx.Client(
            transport=httpx.MockTransport(recorder), timeout=5.0
        )

    return recorder, {
        "tailscale_probe": probe or _ready_probe,
        "env_reader": lambda _root: {},
        "config_loader": lambda _root: cfg or _bearer_cfg(),
        "token_reader": lambda _root, _name: token,
        "http_client_factory": factory,
        "root": root or Path("/nonexistent-attest-root"),
    }


def _report_text(report: AttestReport) -> str:
    return json.dumps(report.to_json_dict(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# remote 探针（AC-B）
# ---------------------------------------------------------------------------


class TestAttestRemoteStates:
    def test_not_enabled_when_mode_not_bearer(self) -> None:
        """FR-B1：mode≠bearer → not_enabled（exit 0）+ 指引，零 HTTP 请求。"""
        recorder, kwargs = _remote_kwargs(_happy_remote_handler, cfg=_loopback_cfg())
        report = run_remote_probe(**kwargs)

        assert report.status == "not_enabled"
        assert report.exit_code == 0
        assert any("octo remote enable" in step for step in report.next_steps)
        assert recorder.requests == [], "not_enabled 分支不得发任何 HTTP 请求"

    def test_fail_when_bearer_but_tailscale_not_ready(self) -> None:
        """FR-B1（Codex spec 评审 P2-1 闭环）：已启用（bearer）+ tailscale 断链
        = fail（exit 1），绝不能归 not_enabled 被 release lane 忽略。"""
        recorder, kwargs = _remote_kwargs(_happy_remote_handler, probe=_not_ready_probe)
        report = run_remote_probe(**kwargs)

        assert report.status == "fail"
        assert report.exit_code == 1
        failed = [c for c in report.checks if c.ok is False]
        assert failed and failed[0].name == "tailscale_ready"
        assert recorder.requests == []

    def test_fail_when_token_missing(self) -> None:
        recorder, kwargs = _remote_kwargs(_happy_remote_handler, token=None)
        report = run_remote_probe(**kwargs)

        assert report.status == "fail"
        failed = [c for c in report.checks if c.ok is False]
        assert failed and failed[0].name == "bearer_token_configured"
        assert recorder.requests == []

    def test_config_load_failure_is_fail(self) -> None:
        """配置读取失败 → fail（探针无法判定即不给绿，Constitution #6 软化不抛）。"""
        _, kwargs = _remote_kwargs(_happy_remote_handler)
        kwargs["config_loader"] = lambda _root: (_ for _ in ()).throw(
            ValueError("yaml broken")
        )
        report = run_remote_probe(**kwargs)

        assert report.status == "fail"
        assert report.checks[0].name == "config_readable"


class TestAttestRemoteHttpChain:
    def test_pass_full_chain(self) -> None:
        """FR-B2：五项检查全绿 → pass + published URL；全程只读（GET-only）。"""
        recorder, kwargs = _remote_kwargs(_happy_remote_handler)
        report = run_remote_probe(**kwargs)

        assert report.status == "pass"
        assert report.exit_code == 0
        by_name = {c.name: c for c in report.checks}
        for name in (
            "front_door_mode",
            "tailscale_ready",
            "bearer_token_configured",
            "ready_endpoint",
            "spa_index",
            "bearer_enforced",
            "bearer_token_valid",
            "sse_channel",
        ):
            assert by_name[name].ok is True, f"{name} 应为 True：{by_name[name].detail}"
        assert "真握手" in by_name["sse_channel"].detail
        assert any(f"https://{_DNS}/" in step for step in report.next_steps)
        # FR-B5 只读红线：全部请求都是 GET
        assert recorder.requests, "应发出 HTTP 检查请求"
        assert {method for method, _ in recorder.requests} == {"GET"}

    def test_fail_when_ready_endpoint_down(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/ready":
                return httpx.Response(502)
            return _happy_remote_handler(request)

        recorder, kwargs = _remote_kwargs(handler)
        report = run_remote_probe(**kwargs)

        assert report.status == "fail"
        by_name = {c.name: c for c in report.checks}
        assert by_name["ready_endpoint"].ok is False
        # 链路基座不通 → 后续检查短路（不产生 spa/bearer 检查项）
        assert "spa_index" not in by_name

    def test_fail_when_connection_error(self) -> None:
        """FR-B4：httpx 异常软化为 check fail + hint，不抛未捕获异常。"""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(
                f"connect failed to {request.url}"  # 真实异常会回显 URL
            )

        _, kwargs = _remote_kwargs(handler)
        report = run_remote_probe(**kwargs)

        assert report.status == "fail"
        by_name = {c.name: c for c in report.checks}
        assert by_name["ready_endpoint"].ok is False
        assert "ConnectError" in by_name["ready_endpoint"].detail

    def test_fail_when_auth_not_enforced(self) -> None:
        """无 token 也 200 = 认证未生效（暴露面问题）→ fail + 显著提示。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/control/snapshot":
                return httpx.Response(200, json={"ok": True})  # 不看 token 一律放行
            return _happy_remote_handler(request)

        _, kwargs = _remote_kwargs(handler)
        report = run_remote_probe(**kwargs)

        assert report.status == "fail"
        by_name = {c.name: c for c in report.checks}
        assert by_name["bearer_enforced"].ok is False
        assert "认证未生效" in by_name["bearer_enforced"].detail

    def test_fail_when_token_rejected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if (
                request.url.path == "/api/control/snapshot"
                and request.headers.get("authorization")
            ):
                return httpx.Response(
                    401, json={"detail": {"code": "FRONT_DOOR_TOKEN_INVALID"}}
                )
            return _happy_remote_handler(request)

        _, kwargs = _remote_kwargs(handler)
        report = run_remote_probe(**kwargs)

        assert report.status == "fail"
        by_name = {c.name: c for c in report.checks}
        assert by_name["bearer_token_valid"].ok is False
        # token 无效 → SSE 检查短路
        assert "sse_channel" not in by_name

    def test_sse_fallback_404_when_no_tasks(self) -> None:
        """无历史任务 → 退化 404 判别（认证语义仍验，streaming 未实测注明）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tasks":
                return httpx.Response(200, json={"tasks": []})
            if request.url.path.startswith("/api/stream/task/"):
                if request.url.params.get("access_token") == _SENTINEL_TOKEN:
                    return httpx.Response(404, json={"error": "TASK_NOT_FOUND"})
                return httpx.Response(401)
            return _happy_remote_handler(request)

        _, kwargs = _remote_kwargs(handler)
        report = run_remote_probe(**kwargs)

        assert report.status == "pass"
        by_name = {c.name: c for c in report.checks}
        assert by_name["sse_channel"].ok is True
        assert "streaming 未实测" in by_name["sse_channel"].detail

    def test_sse_auth_failure_is_fail(self) -> None:
        """SSE query-token 被拒（401）→ fail（serve 场景 SSE 半边真验）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/api/stream/task/"):
                return httpx.Response(
                    401, json={"detail": {"code": "FRONT_DOOR_TOKEN_INVALID"}}
                )
            return _happy_remote_handler(request)

        _, kwargs = _remote_kwargs(handler)
        report = run_remote_probe(**kwargs)

        assert report.status == "fail"
        by_name = {c.name: c for c in report.checks}
        assert by_name["sse_channel"].ok is False

    def test_sse_guard_missing_detected_by_negative_probe(self) -> None:
        """Codex re-review P2 回归钉住：stream 路由 guard 丢失（任意 token 都
        404）时，正向 404-判别会被骗过——负向「错 token 必须 401」专抓这种回归。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/api/stream/task/"):
                # 模拟 guard 丢失：不看 token，直接走 task 查询 → 404
                return httpx.Response(404, json={"error": "TASK_NOT_FOUND"})
            if request.url.path == "/api/tasks":
                return httpx.Response(200, json={"tasks": []})
            return _happy_remote_handler(request)

        _, kwargs = _remote_kwargs(handler)
        report = run_remote_probe(**kwargs)

        assert report.status == "fail"
        by_name = {c.name: c for c in report.checks}
        assert by_name["sse_channel"].ok is False
        assert "未被拒" in by_name["sse_channel"].detail

    def test_sse_negative_probe_accepts_rate_limited_429(self) -> None:
        """F134 AC-A1：限流生效期错 token 得 429（FRONT_DOOR_RATE_LIMITED）——
        同样证明 guard 在挡，负向探针视为通过（只认 401 会假阴性 fail）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/api/stream/task/"):
                if request.url.params.get("access_token") == _SENTINEL_TOKEN:
                    return httpx.Response(404, json={"error": "TASK_NOT_FOUND"})
                return httpx.Response(
                    429,
                    json={"detail": {"code": "FRONT_DOOR_RATE_LIMITED"}},
                    headers={"Retry-After": "300"},
                )
            if request.url.path == "/api/tasks":
                return httpx.Response(200, json={"tasks": []})
            return _happy_remote_handler(request)

        _, kwargs = _remote_kwargs(handler)
        report = run_remote_probe(**kwargs)

        assert report.status == "pass"
        by_name = {c.name: c for c in report.checks}
        assert by_name["sse_channel"].ok is True

    def test_sse_negative_probe_rejects_generic_429_without_code(self) -> None:
        """Codex final P2：通用节流 429（无 FRONT_DOOR_RATE_LIMITED code）不能
        证明 query-token 校验在挡——负向探针必须 fail（保住"专抓 guard 丢失"）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/api/stream/task/"):
                if request.url.params.get("access_token") == _SENTINEL_TOKEN:
                    return httpx.Response(404, json={"error": "TASK_NOT_FOUND"})
                # 中间件式通用节流：429 但无 front_door code
                return httpx.Response(429, text="Too Many Requests")
            if request.url.path == "/api/tasks":
                return httpx.Response(200, json={"tasks": []})
            return _happy_remote_handler(request)

        _, kwargs = _remote_kwargs(handler)
        report = run_remote_probe(**kwargs)

        assert report.status == "fail"
        by_name = {c.name: c for c in report.checks}
        assert by_name["sse_channel"].ok is False
        assert "通用节流" in by_name["sse_channel"].detail

    def test_sse_zero_chunk_stream_is_fail(self) -> None:
        """Codex re-review P2 回归钉住：200 + event-stream 但零字节即断流
        （代理不支持流式/立即关闭）不得报 pass。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/api/stream/task/"):
                if request.url.params.get("access_token") != _SENTINEL_TOKEN:
                    return httpx.Response(401)
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=iter([]),  # 零 chunk
                )
            return _happy_remote_handler(request)

        _, kwargs = _remote_kwargs(handler)
        report = run_remote_probe(**kwargs)

        assert report.status == "fail"
        by_name = {c.name: c for c in report.checks}
        assert by_name["sse_channel"].ok is False
        assert "零字节" in by_name["sse_channel"].detail

    def test_sse_token_with_url_special_chars_survives(self) -> None:
        """Codex final P2 回归钉住：token 含 ``+``/``&``/``#``/``=`` 等 URL 特殊
        字符时，SSE query 必须 percent-encoding——服务端解码后与原值逐字相等
        （裸拼 query 会把 ``+`` 解码成空格 / ``&``、``#`` 截断参数 → 有效配置
        误报 SSE fail）。"""
        special_token = "attest+special&chars#2026/=="

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/api/control/snapshot":
                if (
                    request.headers.get("authorization")
                    == f"Bearer {special_token}"
                ):
                    return httpx.Response(200, json={"ok": True})
                return httpx.Response(401)
            if path == "/api/tasks":
                return httpx.Response(200, json={"tasks": []})
            if path.startswith("/api/stream/task/"):
                # httpx 会解码 query——服务端视角必须拿到逐字原值
                if request.url.params.get("access_token") == special_token:
                    return httpx.Response(404, json={"error": "TASK_NOT_FOUND"})
                return httpx.Response(401)
            return _happy_remote_handler(request)

        _, kwargs = _remote_kwargs(handler, token=special_token)
        report = run_remote_probe(**kwargs)

        assert report.status == "pass", _report_text(report)
        by_name = {c.name: c for c in report.checks}
        assert by_name["sse_channel"].ok is True


class TestAttestRemoteRedlines:
    @pytest.mark.parametrize(
        "scenario",
        ["happy", "sse_401", "connect_error", "sse_raises"],
        ids=["pass 全链", "SSE 拒绝", "连接异常", "SSE 流中异常"],
    )
    def test_token_never_leaks_into_report(self, scenario: str) -> None:
        """FR-B3 机械断言：sentinel token 在 report JSON 全文零命中——
        含 SSE 检查（URL 带 query token 的唯一位置）与异常回显路径。

        ``sse_raises`` 是泄漏最高危路径（Opus 自审补格）：异常发生在**带
        access_token query 的 SSE 请求上**，真实 httpx 异常 str 会回显完整
        URL——connect_error 场景在 /ready 就短路，压不到这条。"""

        def sse_401_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/api/stream/task/"):
                return httpx.Response(401)
            return _happy_remote_handler(request)

        def connect_error_handler(request: httpx.Request) -> httpx.Response:
            # 真实 httpx 异常文本会含完整 URL（可能带 access_token query）
            raise httpx.ConnectTimeout(f"timeout for {request.url}")

        def sse_raises_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith("/api/stream/task/"):
                # 模拟真实 httpx 异常回显：message 里带上含 token 的完整 URL
                raise httpx.ReadTimeout(f"read timeout for {request.url}")
            return _happy_remote_handler(request)

        handler = {
            "happy": _happy_remote_handler,
            "sse_401": sse_401_handler,
            "connect_error": connect_error_handler,
            "sse_raises": sse_raises_handler,
        }[scenario]
        _, kwargs = _remote_kwargs(handler)
        report = run_remote_probe(**kwargs)

        assert _SENTINEL_TOKEN not in _report_text(report), (
            f"token 泄漏进 report（scenario={scenario}）"
        )
        if scenario == "sse_raises":
            by_name = {c.name: c for c in report.checks}
            assert by_name["sse_channel"].ok is False, "流中异常应判 SSE fail"

    def test_probe_does_not_touch_instance_files(self, tmp_path: Path) -> None:
        """FR-B5：探针只读——实例根 octoagent.yaml / .env 前后字节不变。"""
        yaml_path = tmp_path / "octoagent.yaml"
        env_path = tmp_path / ".env"
        yaml_path.write_text("front_door:\n  mode: bearer\n", encoding="utf-8")
        env_path.write_text(
            f"OCTOAGENT_FRONTDOOR_TOKEN={_SENTINEL_TOKEN}\n", encoding="utf-8"
        )
        before = (yaml_path.read_bytes(), env_path.read_bytes())

        _, kwargs = _remote_kwargs(_happy_remote_handler, root=tmp_path)
        report = run_remote_probe(**kwargs)

        assert report.status == "pass"
        assert (yaml_path.read_bytes(), env_path.read_bytes()) == before


class TestDefaultTokenReader:
    """FR-B（Codex spec 评审 P2-2 闭环）：token 值只从实例 .env 读。"""

    def test_reads_value_from_instance_env_file(self, tmp_path: Path) -> None:
        from octoagent.provider.dx.attest_commands import _default_token_reader

        (tmp_path / ".env").write_text(
            f"OCTOAGENT_FRONTDOOR_TOKEN={_SENTINEL_TOKEN}\n", encoding="utf-8"
        )
        assert (
            _default_token_reader(tmp_path, "OCTOAGENT_FRONTDOOR_TOKEN")
            == _SENTINEL_TOKEN
        )

    def test_shell_only_value_not_trusted(self, tmp_path: Path, monkeypatch) -> None:
        """自定义变量只在 shell 存在（.env 没有）→ 返回 None（托管服务不继承
        shell export，采信会假通过）。"""
        from octoagent.provider.dx.attest_commands import _default_token_reader

        monkeypatch.setenv("MY_CUSTOM_FRONT_TOKEN", _SENTINEL_TOKEN)
        (tmp_path / ".env").write_text("OTHER=1\n", encoding="utf-8")
        assert _default_token_reader(tmp_path, "MY_CUSTOM_FRONT_TOKEN") is None

    def test_env_litellm_overrides_env_like_source_order(self, tmp_path: Path) -> None:
        """Codex re-review P2 回归钉住：run-octo-home.sh 先 source .env 再
        source .env.litellm（后者覆盖）——两文件同时定义时必须取 .env.litellm
        的值（遇 .env 即 return 会拿旧 token 误报 fail）。"""
        from octoagent.provider.dx.attest_commands import _default_token_reader

        (tmp_path / ".env").write_text(
            "OCTOAGENT_FRONTDOOR_TOKEN=stale-old-token\n", encoding="utf-8"
        )
        (tmp_path / ".env.litellm").write_text(
            f"OCTOAGENT_FRONTDOOR_TOKEN={_SENTINEL_TOKEN}\n", encoding="utf-8"
        )
        assert (
            _default_token_reader(tmp_path, "OCTOAGENT_FRONTDOOR_TOKEN")
            == _SENTINEL_TOKEN
        )


# ---------------------------------------------------------------------------
# service 探针（AC-C）
# ---------------------------------------------------------------------------


def _status(
    *,
    installed: bool = True,
    running: bool = True,
    pid: int | None = 100,
    ready: bool | None = True,
) -> ServiceStatus:
    return ServiceStatus(
        backend="launchd",
        installed=installed,
        loaded=installed,
        running=running,
        pid=pid,
        ready=ready,
    )


class FakeServiceManager:
    """status() 按序列返回（末项重复）——编排 kill 前后的 pid 演化。"""

    def __init__(self, statuses: list[ServiceStatus]) -> None:
        self._statuses = list(statuses)
        self.status_calls = 0

    def status(self) -> ServiceStatus:
        self.status_calls += 1
        if len(self._statuses) > 1:
            return self._statuses.pop(0)
        return self._statuses[0]


class KillRecorder:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[int, int]] = []
        self._error = error

    def __call__(self, pid: int, sig: int) -> None:
        self.calls.append((pid, sig))
        if self._error is not None:
            raise self._error


class VirtualClock:
    """虚拟时钟：sleep 累加虚拟秒，monotonic 读虚拟秒——测试零真实等待。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleep_calls = 0

    def sleep(self, seconds: float) -> None:
        self.sleep_calls += 1
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


def _service_kwargs(manager: FakeServiceManager, kill: KillRecorder, clock: VirtualClock):
    return {
        "manager_factory": lambda _root: manager,
        "kill_fn": kill,
        "sleep_fn": clock.sleep,
        "monotonic_fn": clock.monotonic,
        "root": Path("/nonexistent-attest-root"),
    }


class TestAttestService:
    def test_not_enabled_when_not_installed(self) -> None:
        manager = FakeServiceManager([_status(installed=False, running=False, pid=None)])
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "not_enabled"
        assert report.exit_code == 0
        assert any("octo service install" in step for step in report.next_steps)
        assert kill.calls == [], "未安装分支不得 kill"

    def test_fail_when_unhealthy(self) -> None:
        manager = FakeServiceManager([_status(running=False, pid=None)])
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "fail"
        assert kill.calls == [], "不健康分支不得 kill（先修复再验自愈）"

    def test_dry_run_checks_but_never_kills(self) -> None:
        """FR-C2 机械断言：dry-run 零 kill、零等待。"""
        manager = FakeServiceManager([_status()])
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(dry_run=True, **_service_kwargs(manager, kill, clock))

        assert report.status == "pass"
        assert kill.calls == []
        assert clock.sleep_calls == 0
        dry = [c for c in report.checks if c.name == "crash_recovery"][0]
        assert dry.ok is None and "[dry-run]" in dry.detail

    def test_recovery_with_new_pid_passes(self) -> None:
        """FR-C3 主路径：SIGKILL → 轮询拿到新 pid + ready → pass。"""
        manager = FakeServiceManager(
            [
                _status(pid=100),  # 初始健康
                _status(running=False, pid=None, ready=None),  # 崩溃窗口
                _status(pid=100, ready=False),  # 拉起中旧观测（pid 未更替，跳过）
                _status(pid=200, ready=False),  # 新 pid 但未就绪（继续等）
                _status(pid=200, ready=True),  # 自愈完成
            ]
        )
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "pass"
        assert kill.calls == [(100, signal.SIGKILL)], "必须 SIGKILL 旧 pid（spec §D-3）"
        recovery = [c for c in report.checks if c.name == "crash_recovery"][0]
        assert recovery.ok is True
        assert "100 → 200" in recovery.detail

    def test_ready_unknown_degrades_to_pid_change(self) -> None:
        """descriptor 无 verify_url（ready 恒 None）→ 以 pid 更替 + running 判自愈。"""
        manager = FakeServiceManager(
            [
                _status(pid=100, ready=None),
                _status(pid=200, ready=None),
            ]
        )
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "pass"
        recovery = [c for c in report.checks if c.name == "crash_recovery"][0]
        assert "ready 未知" in recovery.detail

    def test_fail_when_pid_never_changes(self) -> None:
        """kill 后 pid 一直是旧值（launchd 未拉起新进程）→ 预算耗尽 fail。"""
        manager = FakeServiceManager([_status(pid=100)])
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "fail"
        recovery = [c for c in report.checks if c.name == "crash_recovery"][0]
        assert recovery.ok is False
        assert clock.now >= 90.0, "应耗尽恢复预算后才判失败"

    def test_fail_when_never_recovers(self) -> None:
        manager = FakeServiceManager(
            [_status(pid=100), _status(running=False, pid=None, ready=None)]
        )
        kill, clock = KillRecorder(), VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "fail"
        assert any("未恢复" in c.detail for c in report.checks if c.ok is False)

    def test_kill_failure_is_fail(self) -> None:
        manager = FakeServiceManager([_status(pid=100)])
        kill = KillRecorder(error=ProcessLookupError("no such process"))
        clock = VirtualClock()
        report = run_service_probe(**_service_kwargs(manager, kill, clock))

        assert report.status == "fail"
        injected = [c for c in report.checks if c.name == "crash_injected"][0]
        assert injected.ok is False

    def test_status_exception_softened_to_fail(self) -> None:
        """Constitution #6：manager 异常软化为 fail，不抛未捕获异常。"""

        def broken_factory(_root: Path):
            raise RuntimeError("launchctl unavailable")

        report = run_service_probe(
            manager_factory=broken_factory,
            kill_fn=KillRecorder(),
            sleep_fn=VirtualClock().sleep,
            monotonic_fn=VirtualClock().monotonic,
            root=Path("/nonexistent-attest-root"),
        )
        assert report.status == "fail"


# ---------------------------------------------------------------------------
# CLI 层（渲染 / exit code / JSON 契约）
# ---------------------------------------------------------------------------


class TestAttestCli:
    def test_attest_group_mounted_on_main(self) -> None:
        from octoagent.provider.dx.cli import main as octo_main

        assert "attest" in octo_main.commands
        assert set(octo_main.commands["attest"].commands) == {"remote", "service"}

    def test_remote_json_output_and_exit_code(self, monkeypatch) -> None:
        from click.testing import CliRunner
        from octoagent.provider.dx import attest_commands

        canned = AttestReport(probe="remote", status="fail")
        monkeypatch.setattr(attest_commands, "run_remote_probe", lambda: canned)
        runner = CliRunner()
        result = runner.invoke(
            attest_commands.attest_group, ["remote", "--json"]
        )

        assert result.exit_code == 1, "fail 必须 exit 1（F141 lane 阻断信号）"
        payload = json.loads(result.stdout)
        assert payload["probe"] == "remote"
        assert payload["status"] == "fail"
        assert payload["exit_code"] == 1

    def test_remote_not_enabled_exits_zero(self, monkeypatch) -> None:
        from click.testing import CliRunner
        from octoagent.provider.dx import attest_commands

        canned = AttestReport(
            probe="remote", status="not_enabled", next_steps=["octo remote enable"]
        )
        monkeypatch.setattr(attest_commands, "run_remote_probe", lambda: canned)
        runner = CliRunner()
        result = runner.invoke(attest_commands.attest_group, ["remote"])

        assert result.exit_code == 0, "not_enabled 不是失败（三态协议）"

    def test_service_json_declaration_goes_to_stderr(self, monkeypatch) -> None:
        """--json 下闪断声明走 stderr（先于 kill），stdout 保持纯 JSON 可解析。"""
        from click.testing import CliRunner
        from octoagent.provider.dx import attest_commands

        canned = AttestReport(probe="service", status="pass")
        monkeypatch.setattr(
            attest_commands, "run_service_probe", lambda dry_run: canned
        )
        runner = CliRunner()
        result = runner.invoke(
            attest_commands.attest_group, ["service", "--json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.stdout)  # stdout 必须纯 JSON
        assert payload["probe"] == "service"
        assert "秒级闪断" in result.stderr

    def test_service_dry_run_skips_declaration(self, monkeypatch) -> None:
        from click.testing import CliRunner
        from octoagent.provider.dx import attest_commands

        canned = AttestReport(probe="service", status="pass")
        monkeypatch.setattr(
            attest_commands, "run_service_probe", lambda dry_run: canned
        )
        runner = CliRunner()
        result = runner.invoke(
            attest_commands.attest_group, ["service", "--dry-run", "--json"]
        )

        assert result.exit_code == 0
        assert "秒级闪断" not in result.stderr
