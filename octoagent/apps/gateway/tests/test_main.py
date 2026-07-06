"""gateway.main 辅助函数测试。"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from octoagent.core.models import ManagedRuntimeDescriptor, RuntimeManagementMode, utc_now
from octoagent.gateway.services.config.config_schema import (
    ChannelsConfig,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    TelegramChannelConfig,
)
from octoagent.gateway.services.config.config_wizard import save_config


def _write_litellm_config(tmp_path: Path, content: str) -> None:
    (tmp_path / "litellm-config.yaml").write_text(content, encoding="utf-8")


def test_resolve_telegram_polling_timeout_from_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-07",
            channels=ChannelsConfig(
                telegram=TelegramChannelConfig(
                    enabled=True,
                    mode="polling",
                    polling_timeout_seconds=42,
                )
            ),
        ),
        tmp_path,
    )
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")

    assert gateway_main._resolve_telegram_polling_timeout(tmp_path) == 42


def test_resolve_telegram_polling_timeout_falls_back_on_invalid_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "octoagent.yaml").write_text(
        "\n".join(
            [
                "config_version: 1",
                "updated_at: '2026-03-07'",
                "channels:",
                "  telegram:",
                "    enabled: true",
                "    mode: webhook",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")

    assert gateway_main._resolve_telegram_polling_timeout(tmp_path) == 15


def test_create_app_loads_dotenv_from_resolved_project_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    gateway_main = importlib.import_module("octoagent.gateway.main")
    calls: list[tuple[Path | None, bool]] = []

    def fake_load_project_dotenv(
        project_root: Path | None = None,
        override: bool = False,
    ) -> bool:
        calls.append((project_root, override))
        return True

    monkeypatch.setattr(gateway_main, "load_project_dotenv", fake_load_project_dotenv)

    gateway_main.create_app()

    assert calls == [(tmp_path, False)]


def test_spa_static_files_fallback_to_index_for_frontend_routes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>chat app</body></html>", encoding="utf-8")
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "app.js").write_text("console.log('ok');\n", encoding="utf-8")

    app = FastAPI()
    app.mount("/", gateway_main.SpaStaticFiles(directory=str(dist_dir), html=True), name="frontend")
    client = TestClient(app)

    html_response = client.get("/chat")
    assert html_response.status_code == 200
    assert "chat app" in html_response.text

    asset_response = client.get("/assets/app.js")
    assert asset_response.status_code == 200
    assert "console.log('ok');" in asset_response.text

    missing_asset = client.get("/assets/missing.js")
    assert missing_asset.status_code == 404


# Feature 081 P1：_resolve_stream_model_aliases / _resolve_responses_reasoning_aliases
# 已随 LiteLLM Proxy 退役从 main.py 删除——transport 现在由 ProviderEntry.transport 直接表达，
# 不再需要从 alias 名推断。原对应单测（test_resolve_stream_model_aliases_*）随之删除。


def test_build_runtime_alias_registry_uses_configured_aliases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-07",
            providers=[
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                    enabled=True,
                ),
            ],
            model_aliases={
                "main": ModelAlias(provider="openrouter", model="openrouter/auto"),
                "cheap": ModelAlias(provider="openrouter", model="openrouter/auto"),
                "reasoning": ModelAlias(provider="openrouter", model="openrouter/auto"),
                "summarizer": ModelAlias(provider="openrouter", model="openrouter/auto"),
            },
        ),
        tmp_path,
    )
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")

    registry = gateway_main._build_runtime_alias_registry(tmp_path)

    assert registry.resolve("reasoning") == "reasoning"
    assert registry.resolve("summarizer") == "summarizer"
    assert registry.resolve("planner") == "main"
    assert registry.resolve("router") == "cheap"


def test_build_runtime_alias_registry_falls_back_when_config_invalid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "octoagent.yaml").write_text("not: [valid\n", encoding="utf-8")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")

    registry = gateway_main._build_runtime_alias_registry(tmp_path)

    assert registry.resolve("planner") == "main"
    assert registry.resolve("unknown-alias") == "main"


def test_resolve_verify_url_from_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("OCTOAGENT_VERIFY_URL", "http://127.0.0.1:9000/ready?profile=core")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")

    assert gateway_main._resolve_verify_url() == "http://127.0.0.1:9000/ready?profile=core"


def test_resolve_verify_url_from_host_and_port_env(monkeypatch) -> None:
    monkeypatch.delenv("OCTOAGENT_VERIFY_URL", raising=False)
    monkeypatch.setenv("OCTOAGENT_VERIFY_HOST", "localhost")
    monkeypatch.setenv("OCTOAGENT_GATEWAY_PORT", "8123")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")

    assert gateway_main._resolve_verify_url() == "http://localhost:8123/ready?profile=core"


def test_persist_runtime_state_uses_store_and_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")
    captured = []

    class FakeStore:
        def save_runtime_state(self, snapshot) -> None:
            captured.append(snapshot)

    sentinel = {"pid": 1234}
    monkeypatch.setattr(
        gateway_main,
        "_create_runtime_state_snapshot",
        lambda project_root, active_attempt_id=None, management_mode=None: sentinel,
    )

    assert gateway_main._persist_runtime_state(tmp_path, store=FakeStore()) is True
    assert captured == [sentinel]


def test_persist_runtime_state_returns_false_without_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")
    monkeypatch.setattr(
        gateway_main,
        "_create_runtime_state_snapshot",
        lambda project_root, active_attempt_id=None, management_mode=None: None,
    )

    assert gateway_main._persist_runtime_state(tmp_path) is False


def test_persist_runtime_state_marks_managed_when_descriptor_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")
    captured: list[object] = []

    class FakeStore:
        def load_runtime_descriptor(self):
            return ManagedRuntimeDescriptor(
                project_root=str(tmp_path),
                runtime_mode=RuntimeManagementMode.MANAGED,
                start_command=["uv", "run", "uvicorn", "octoagent.gateway.main:app"],
                verify_url="http://127.0.0.1:8000/ready?profile=core",
                created_at=utc_now(),
                updated_at=utc_now(),
            )

        def save_runtime_state(self, snapshot) -> None:
            captured.append(snapshot)

    assert gateway_main._persist_runtime_state(tmp_path, store=FakeStore()) is True
    assert captured
    assert captured[0].management_mode == RuntimeManagementMode.MANAGED


def test_spa_static_files_falls_back_to_index_for_nested_frontend_route(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")
    dist = tmp_path / "frontend-dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>chat shell</body></html>", encoding="utf-8")

    app = FastAPI()
    app.mount("/", gateway_main.SpaStaticFiles(directory=str(dist), html=True), name="frontend")
    client = TestClient(app)

    response = client.get("/chat")

    assert response.status_code == 200
    assert "chat shell" in response.text


def test_spa_static_files_keeps_missing_asset_as_404(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    gateway_main = importlib.import_module("octoagent.gateway.main")
    dist = tmp_path / "frontend-dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>chat shell</body></html>", encoding="utf-8")

    app = FastAPI()
    app.mount("/", gateway_main.SpaStaticFiles(directory=str(dist), html=True), name="frontend")
    client = TestClient(app)

    response = client.get("/assets/missing.js")

    assert response.status_code == 404


async def test_lifespan_ensures_default_project_migration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    gateway_main = importlib.import_module("octoagent.gateway.main")

    app = FastAPI()
    async with gateway_main.lifespan(app):
        run = app.state.project_migration_run
        default_project = await app.state.store_group.project_store.get_default_project()
        assert run.validation.ok is True
        assert default_project is not None


async def test_lifespan_echo_mode_uses_pure_echo_no_skill_runner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Feature 081 P4 修复（Codex F2）：echo mode 必须保持纯 echo 行为。

    回归断言：
    - FallbackManager.primary 是 EchoMessageAdapter（不是 ProviderRouterMessageAdapter）
    - LLMService 没有 SkillRunner（避免 ProviderModelClient 绕过 fallback 直连 provider）
    """
    from octoagent.provider.echo_adapter import EchoMessageAdapter

    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    gateway_main = importlib.import_module("octoagent.gateway.main")

    app = FastAPI()
    async with gateway_main.lifespan(app):
        llm_service = app.state.llm_service
        # 内部 _fallback_manager.primary 应该是 EchoMessageAdapter
        primary = llm_service._fallback_manager._primary
        assert isinstance(primary, EchoMessageAdapter), (
            f"echo mode 期望 primary=EchoMessageAdapter，实际 {type(primary).__name__}"
        )
        # echo mode 不创建 SkillRunner（避免 ProviderModelClient 绕过 fallback）
        assert llm_service._skill_runner is None


async def test_lifespan_default_mode_uses_provider_router_with_skill_runner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Feature 081 P4：默认（非 echo）模式应该用 ProviderRouterMessageAdapter +
    SkillRunner 走 Provider 直连。
    """
    from octoagent.provider.router_message_adapter import ProviderRouterMessageAdapter

    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "test.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    # 不设置 OCTOAGENT_LLM_MODE → 默认 "litellm"（Provider 直连语义）
    monkeypatch.delenv("OCTOAGENT_LLM_MODE", raising=False)
    gateway_main = importlib.import_module("octoagent.gateway.main")

    app = FastAPI()
    async with gateway_main.lifespan(app):
        llm_service = app.state.llm_service
        primary = llm_service._fallback_manager._primary
        assert isinstance(primary, ProviderRouterMessageAdapter), (
            f"default mode 期望 primary=ProviderRouterMessageAdapter，实际 {type(primary).__name__}"
        )
        assert llm_service._skill_runner is not None


# ---------------------------------------------------------------------------
# F130 Phase D：host↔mode 启动期防裸奔 fail-fast（spec [@test] FR-C2/C3/AC-3）
# ---------------------------------------------------------------------------


def _import_gateway_main_safely(monkeypatch):
    """先在**安全 env**下 import gateway.main（触发模块底部 app=create_app()），
    再返回模块——之后调用方才设危险 env。

    Codex re-review P2：若在危险 env 下首次 import，模块底部 create_app() 会在
    pytest.raises 之外直接 sys.exit(78) 让单测失败（依赖别的用例先缓存模块）。
    此处先清 env + import 消除该顺序依赖。
    """
    monkeypatch.delenv("OCTOAGENT_HOST", raising=False)
    monkeypatch.delenv("OCTOAGENT_FRONTDOOR_MODE", raising=False)
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    return importlib.import_module("octoagent.gateway.main")


def test_create_app_default_host_mode_is_safe_no_exit(
    tmp_path: Path, monkeypatch
) -> None:
    """★ 最高危护栏：默认 127.0.0.1 + loopback = safe，create_app 正常返回
    （不误 exit——否则 gateway 起不来连本机都用不了，plan §2 Phase D）。"""
    gateway_main = _import_gateway_main_safely(monkeypatch)
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    app = gateway_main.create_app()  # 不抛 SystemExit
    assert isinstance(app, FastAPI)


def test_enforce_exposure_naked_combo_exits_78(
    tmp_path: Path, monkeypatch
) -> None:
    """★ AC-3：host=0.0.0.0 + mode=loopback → 启动期 sys.exit(78)。"""
    gateway_main = _import_gateway_main_safely(monkeypatch)  # 安全 env 下先 import
    monkeypatch.setenv("OCTOAGENT_HOST", "0.0.0.0")
    monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "loopback")
    with pytest.raises(SystemExit) as exc_info:
        gateway_main._enforce_front_door_exposure(tmp_path)
    assert exc_info.value.code == 78


def test_enforce_exposure_exit_message_mentions_exposure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """裸奔拒绝的 stderr 信息含关键词（供 err.log / octo logs 诊断）。"""
    gateway_main = _import_gateway_main_safely(monkeypatch)
    monkeypatch.setenv("OCTOAGENT_HOST", "0.0.0.0")
    monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "loopback")
    with pytest.raises(SystemExit):
        gateway_main._enforce_front_door_exposure(tmp_path)
    captured = capsys.readouterr()
    assert "裸奔" in captured.err
    assert "0.0.0.0" in captured.err
    assert "octo remote enable" in captured.err


def test_enforce_exposure_warn_combo_does_not_exit(
    tmp_path: Path, monkeypatch
) -> None:
    """FR-C3：host=0.0.0.0 + mode=bearer → 强警告放行，不 exit。"""
    gateway_main = _import_gateway_main_safely(monkeypatch)
    monkeypatch.setenv("OCTOAGENT_HOST", "0.0.0.0")
    monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
    # 不抛 SystemExit
    gateway_main._enforce_front_door_exposure(tmp_path)


def test_enforce_exposure_serve_recommended_combo_safe(
    tmp_path: Path, monkeypatch
) -> None:
    """Tailscale serve 推荐组合 127.0.0.1 + bearer → safe，不 exit。"""
    gateway_main = _import_gateway_main_safely(monkeypatch)
    monkeypatch.setenv("OCTOAGENT_HOST", "127.0.0.1")
    monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
    gateway_main._enforce_front_door_exposure(tmp_path)


def test_enforce_exposure_uses_config_error_exit_code_constant(
    tmp_path: Path, monkeypatch
) -> None:
    """exit code 复用 service_manager.CONFIG_ERROR_EXIT_CODE 单一事实源
    （对齐 systemd RestartPreventExitStatus）。"""
    from octoagent.provider.dx.service_manager import CONFIG_ERROR_EXIT_CODE

    gateway_main = _import_gateway_main_safely(monkeypatch)
    monkeypatch.setenv("OCTOAGENT_HOST", "0.0.0.0")
    monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "loopback")
    with pytest.raises(SystemExit) as exc_info:
        gateway_main._enforce_front_door_exposure(tmp_path)
    assert exc_info.value.code == CONFIG_ERROR_EXIT_CODE


def test_resolve_front_door_mode_env_overrides_config(
    tmp_path: Path, monkeypatch
) -> None:
    gateway_main = _import_gateway_main_safely(monkeypatch)
    monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "bearer")
    assert gateway_main._resolve_front_door_mode(tmp_path) == "bearer"


def test_resolve_front_door_mode_defaults_loopback_when_no_config(
    tmp_path: Path, monkeypatch
) -> None:
    gateway_main = _import_gateway_main_safely(monkeypatch)
    # 空 tmp_path 无 octoagent.yaml → 默认 loopback
    assert gateway_main._resolve_front_door_mode(tmp_path) == "loopback"


def test_resolve_startup_host_argv_overrides_env(monkeypatch) -> None:
    """★ Codex 第六轮 P1：argv --host 覆盖 env（uvicorn CLI 参数是真实绑定）。

    env=127.0.0.1 但 uvicorn --host 0.0.0.0 → 真实绑定 0.0.0.0，取 argv。"""
    gateway_main = _import_gateway_main_safely(monkeypatch)
    monkeypatch.setenv("OCTOAGENT_HOST", "127.0.0.1")
    monkeypatch.setattr(gateway_main.sys, "argv", ["uvicorn", "--host", "0.0.0.0"])
    assert gateway_main._resolve_startup_host() == "0.0.0.0"


def test_resolve_startup_host_env_used_when_no_argv_host(monkeypatch) -> None:
    """无 argv --host 时用 env。"""
    gateway_main = _import_gateway_main_safely(monkeypatch)
    monkeypatch.setenv("OCTOAGENT_HOST", "0.0.0.0")
    monkeypatch.setattr(gateway_main.sys, "argv", ["uvicorn", "app"])
    assert gateway_main._resolve_startup_host() == "0.0.0.0"


def test_resolve_startup_host_falls_back_to_argv(monkeypatch) -> None:
    """Codex re-review P2：无 env 时扫 argv --host（兜住 uvicorn --host 0.0.0.0）。"""
    gateway_main = _import_gateway_main_safely(monkeypatch)  # 清 OCTOAGENT_HOST
    monkeypatch.setattr(
        gateway_main.sys, "argv", ["uvicorn", "app", "--host", "0.0.0.0", "--port", "8000"]
    )
    assert gateway_main._resolve_startup_host() == "0.0.0.0"


def test_resolve_startup_host_argv_equals_form(monkeypatch) -> None:
    gateway_main = _import_gateway_main_safely(monkeypatch)
    monkeypatch.setattr(gateway_main.sys, "argv", ["uvicorn", "--host=0.0.0.0"])
    assert gateway_main._resolve_startup_host() == "0.0.0.0"


def test_resolve_startup_host_defaults_loopback(monkeypatch) -> None:
    gateway_main = _import_gateway_main_safely(monkeypatch)
    monkeypatch.setattr(gateway_main.sys, "argv", ["uvicorn", "app"])
    assert gateway_main._resolve_startup_host() == "127.0.0.1"


def test_enforce_exposure_argv_host_triggers_fail_fast(tmp_path, monkeypatch) -> None:
    """★ 端到端：无 env 但 uvicorn --host 0.0.0.0 + loopback mode → exit(78)。"""
    gateway_main = _import_gateway_main_safely(monkeypatch)
    monkeypatch.delenv("OCTOAGENT_HOST", raising=False)
    monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "loopback")
    monkeypatch.setattr(gateway_main.sys, "argv", ["uvicorn", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit) as exc_info:
        gateway_main._enforce_front_door_exposure(tmp_path)
    assert exc_info.value.code == 78
