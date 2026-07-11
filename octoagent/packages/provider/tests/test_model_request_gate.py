"""F137 硬闸单测：model_request_gate 模块 + call()/embed() 植闸 + FallbackManager 守卫。

AC 绑定（spec §4）：
- AC-1：deny 下 ``ProviderClient.call()`` 抛 ``ModelRequestsNotAllowedError``，
  不返回 Echo、**不触发 auth resolve**（preemptive refresh 零网络副作用）。
- AC-2：allow（生产缺省）下普通异常仍走 Echo 降级，行为与 baseline 一致。
- AC-3：``FallbackManager.call_with_fallback`` 对本异常 re-raise 不 mask 成 Echo。
- FR-8d：deny 布线激活验证——pytest11 插件经 ``-p`` 显式加载生效（worktree 无
  metadata 注册也可验）；标准会话默认 deny（根 conftest / 插件二者之一生效即可）。
"""

from __future__ import annotations

from typing import Any

import pytest

from octoagent.provider import model_request_gate as mrg
from octoagent.provider.auth_resolver import ResolvedAuth
from octoagent.provider.exceptions import ProviderError
from octoagent.provider.fallback import FallbackManager
from octoagent.provider.model_request_gate import (
    ModelRequestsNotAllowedError,
    allow_model_requests,
    check_model_requests_allowed,
    model_requests_allowed,
    set_allow_model_requests,
)
from octoagent.provider.models import ModelCallResult
from octoagent.provider.provider_client import ProviderClient
from octoagent.provider.provider_runtime import ProviderRuntime
from octoagent.provider.transport import ProviderTransport

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _restore_gate_state():
    """每个测试后恢复 gate 全局状态（本模块大量翻转 deny/allow）。"""
    saved = model_requests_allowed()
    yield
    set_allow_model_requests(saved)


# ---------------------------------------------------------------------------
# gate 模块本体
# ---------------------------------------------------------------------------


class TestGateModule:
    def test_env_default_missing_is_allow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """缺省（生产形态）= allow。"""
        monkeypatch.delenv(mrg.ALLOW_MODEL_REQUESTS_ENV, raising=False)
        assert mrg._env_default() is True

    @pytest.mark.parametrize("raw", ["0", "false", "False", "NO", "off", " 0 "])
    def test_env_default_deny_values(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv(mrg.ALLOW_MODEL_REQUESTS_ENV, raw)
        assert mrg._env_default() is False

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "anything"])
    def test_env_default_allow_values(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv(mrg.ALLOW_MODEL_REQUESTS_ENV, raw)
        assert mrg._env_default() is True

    def test_deny_raises_with_opt_in_guidance(self) -> None:
        set_allow_model_requests(False)
        with pytest.raises(ModelRequestsNotAllowedError) as exc_info:
            check_model_requests_allowed()
        # message 必须含 opt-in 指引（FR-1）
        msg = str(exc_info.value)
        assert "e2e_full" in msg
        assert "allow_model_requests" in msg
        assert mrg.ALLOW_MODEL_REQUESTS_ENV in msg

    def test_allow_passes(self) -> None:
        set_allow_model_requests(True)
        check_model_requests_allowed()  # 不 raise

    def test_context_manager_restores_on_exit(self) -> None:
        set_allow_model_requests(False)
        with allow_model_requests():
            assert model_requests_allowed() is True
            check_model_requests_allowed()
        assert model_requests_allowed() is False

    def test_context_manager_restores_on_exception(self) -> None:
        set_allow_model_requests(False)
        with pytest.raises(ValueError), allow_model_requests():
            raise ValueError("boom")
        assert model_requests_allowed() is False

    def test_exception_is_runtime_error_not_provider_error(self) -> None:
        """FR-1 基类拍板：RuntimeError 子类，不入 ProviderError 链。"""
        assert issubclass(ModelRequestsNotAllowedError, RuntimeError)
        assert not issubclass(ModelRequestsNotAllowedError, ProviderError)

    def test_exception_penetrates_provider_error_handler(self) -> None:
        """``except ProviderError`` 不得捕获硬闸异常（防误吞，plan A.1）。"""
        with pytest.raises(ModelRequestsNotAllowedError):
            try:
                raise ModelRequestsNotAllowedError("leak")
            except ProviderError:  # pragma: no cover - 不应命中
                pytest.fail("ModelRequestsNotAllowedError 不应被 except ProviderError 捕获")


# ---------------------------------------------------------------------------
# ProviderClient.call() / embed() 植闸（AC-1）
# ---------------------------------------------------------------------------


class _CountingResolver:
    """记录 resolve 调用次数的 stub——断言 deny 下 auth resolve 零调用。"""

    def __init__(self) -> None:
        self.resolve_count = 0
        self.force_refresh_count = 0

    async def resolve(self) -> ResolvedAuth:
        self.resolve_count += 1
        return ResolvedAuth(bearer_token="tok-x")

    async def force_refresh(self) -> ResolvedAuth | None:
        self.force_refresh_count += 1
        return ResolvedAuth(bearer_token="tok-fresh")


class _ExplodingHttp:
    """任何请求都不该发生（deny 场景专用）。"""

    def stream(self, *args: Any, **kwargs: Any):  # pragma: no cover - 不应命中
        raise AssertionError("deny 下不得发起任何 HTTP 请求")

    async def post(self, *args: Any, **kwargs: Any):  # pragma: no cover - 不应命中
        raise AssertionError("deny 下不得发起任何 HTTP 请求")


def _runtime(resolver: _CountingResolver) -> ProviderRuntime:
    return ProviderRuntime(
        provider_id="siliconflow",
        transport=ProviderTransport.OPENAI_CHAT,
        api_base="https://api.siliconflow.cn",
        auth_resolver=resolver,
    )


class TestProviderClientGate:
    async def test_call_denied_raises_without_auth_resolve(self) -> None:
        """AC-1：deny 下 call() 炸且不触发 auth resolve（preemptive refresh 零副作用）。"""
        set_allow_model_requests(False)
        resolver = _CountingResolver()
        client = ProviderClient(_runtime(resolver), http_client=_ExplodingHttp())  # type: ignore[arg-type]
        with pytest.raises(ModelRequestsNotAllowedError):
            await client.call(
                instructions="x",
                history=[{"role": "user", "content": "hi"}],
                tools=[],
                model_name="m",
            )
        assert resolver.resolve_count == 0
        assert resolver.force_refresh_count == 0

    async def test_embed_denied_raises_without_auth_resolve(self) -> None:
        """AC-1（embed 面）：第二网络入口同样在 auth resolve 前炸。"""
        set_allow_model_requests(False)
        resolver = _CountingResolver()
        client = ProviderClient(_runtime(resolver), http_client=_ExplodingHttp())  # type: ignore[arg-type]
        with pytest.raises(ModelRequestsNotAllowedError):
            await client.embed(model_name="emb", texts=["a"])
        assert resolver.resolve_count == 0

    async def test_call_allowed_proceeds_to_transport(self) -> None:
        """allow 下 call() 照常走到 transport 层（用 fake http 断言到达）。"""
        set_allow_model_requests(True)

        class _FakeResponse:
            status_code = 200
            request = None

            async def aread(self) -> bytes:  # pragma: no cover
                return b""

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args: Any):
                return False

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
                yield "data: [DONE]"

        class _FakeHttp:
            def __init__(self) -> None:
                self.calls = 0

            def stream(self, *args: Any, **kwargs: Any) -> _FakeResponse:
                self.calls += 1
                return _FakeResponse()

        resolver = _CountingResolver()
        http = _FakeHttp()
        client = ProviderClient(_runtime(resolver), http_client=http)  # type: ignore[arg-type]
        content, tool_calls, _meta = await client.call(
            instructions="x",
            history=[{"role": "user", "content": "hi"}],
            tools=[],
            model_name="m",
        )
        assert content == "ok"
        assert http.calls == 1
        assert resolver.resolve_count == 1


# ---------------------------------------------------------------------------
# FallbackManager 守卫（AC-2 / AC-3）
# ---------------------------------------------------------------------------


class _StubAdapter:
    def __init__(self, *, error: Exception | None = None, content: str = "ok") -> None:
        self._error = error
        self._content = content
        self.calls = 0

    async def complete(self, **kwargs: Any) -> ModelCallResult:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return ModelCallResult(
            content=self._content,
            model_alias="main",
            model_name="m",
            provider="p",
            duration_ms=1,
        )


class TestFallbackManagerGuard:
    async def test_gate_error_propagates_not_echo(self) -> None:
        """AC-3：漏网真调用（gate 异常）必须 propagate，不得降级 Echo 假成功。"""
        primary = _StubAdapter(error=ModelRequestsNotAllowedError("leak"))
        fallback = _StubAdapter(content="echo")
        fm = FallbackManager(primary=primary, fallback=fallback)
        with pytest.raises(ModelRequestsNotAllowedError):
            await fm.call_with_fallback([{"role": "user", "content": "x"}])
        assert fallback.calls == 0  # Echo 从未被调用

    async def test_ordinary_exception_still_falls_back(self) -> None:
        """AC-2：合法降级零改动——普通异常仍走 Echo（is_fallback=True）。"""
        primary = _StubAdapter(error=RuntimeError("transport blew up"))
        fallback = _StubAdapter(content="echo")
        fm = FallbackManager(primary=primary, fallback=fallback)
        result = await fm.call_with_fallback([{"role": "user", "content": "x"}])
        assert result.is_fallback is True
        assert result.content == "echo"
        assert "transport blew up" in result.fallback_reason


# ---------------------------------------------------------------------------
# deny 布线（FR-8）
# ---------------------------------------------------------------------------


class TestDenyWiring:
    def test_session_default_is_deny(self) -> None:
        """FR-8d：标准测试会话下 gate 默认 deny——无论由根 conftest（worktree
        PYTHONPATH 锁模式）还是 pytest11 插件（安装态 venv）布线，效果必须成立。

        注意：`_restore_gate_state` fixture 保存的正是会话默认值；本测试不翻转
        gate，直接断言进入时的会话态。
        """
        assert model_requests_allowed() is False

    def test_plugin_configure_sets_deny(self) -> None:
        """插件 pytest_configure 直调翻 deny（无需 metadata 注册即可验证插件逻辑）。"""
        from octoagent.provider.testing import pytest_model_request_gate as plugin

        set_allow_model_requests(True)
        plugin.pytest_configure(config=None)  # type: ignore[arg-type]
        assert model_requests_allowed() is False

    def test_plugin_explicit_load_denies(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR-8d 激活断言：`-p` 显式加载插件 → 子进程会话 deny。

        worktree PYTHONPATH 锁模式下 entry point 未注册进共享 venv，
        `-p 模块路径` 是等价的构造性激活通道（子进程继承 PYTHONPATH）。
        """
        monkeypatch.delenv(mrg.ALLOW_MODEL_REQUESTS_ENV, raising=False)
        pytester.makepyfile(
            """
            import pytest
            from octoagent.provider.model_request_gate import (
                ModelRequestsNotAllowedError,
                check_model_requests_allowed,
            )

            def test_gate_denied():
                with pytest.raises(ModelRequestsNotAllowedError):
                    check_model_requests_allowed()
            """
        )
        result = pytester.runpytest_subprocess(
            "-p",
            "octoagent.provider.testing.pytest_model_request_gate",
            "-p",
            "no:cacheprovider",
        )
        result.assert_outcomes(passed=1)

    def test_plugin_disabled_defaults_to_env_allow(
        self, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """对照组 + 逃生门：`-p no:octoagent_model_request_gate` 显式禁用插件后，
        隔离 rootdir（无根 conftest）会话回落 env 缺省 allow——证明 deny 确实
        来自布线而非其它侧信道；同时验证旧 worktree 逃生门语义。
        """
        monkeypatch.delenv(mrg.ALLOW_MODEL_REQUESTS_ENV, raising=False)
        pytester.makepyfile(
            """
            from octoagent.provider.model_request_gate import (
                check_model_requests_allowed,
                model_requests_allowed,
            )

            def test_gate_allows():
                assert model_requests_allowed() is True
                check_model_requests_allowed()
            """
        )
        result = pytester.runpytest_subprocess(
            "-p",
            "no:octoagent_model_request_gate",
            "-p",
            "no:cacheprovider",
        )
        result.assert_outcomes(passed=1)
