"""F138 Phase A：OctoHarness ``model_client`` + ``clock`` DI 注入点（AC-1 / AC-2）。

放 e2e_live 目录以获得 hermetic autouse fixture（env 重定向 + 单例 reset）——
全 11 段真 bootstrap 需要它们（同 ``test_hermetic_isolation.py`` 先例）。

case 列表：
- AC-1a  model_client=stub（默认 provider_direct 模式）→ SkillRunner 用 stub
- AC-1b  model_client=stub + OCTOAGENT_LLM_MODE=echo → SkillRunner 仍建、仍用 stub
         （override 与 llm_mode 解耦，spec §2.3 拍板③子决策）
- AC-2a  全 None + 非 echo → SkillRunner 存在且 model_client 是 ProviderModelClient
         （生产原路，None 行为等价）
- AC-2b  全 None + echo → SkillRunner 不建（baseline echo-skip 语义原样保留）
- AC-2c  clock=None → app.state.clock 是 _utc_now 默认（tz-aware UTC）；
         clock=注入 → app.state.clock 就是注入的 callable

全部 case 用**空 tmp CredentialStore**（load 返回空 store）——顺带在 bootstrap
层证明 override 模式不要求 provider 凭证（AC-8 的 DI 层前置）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_scripted, pytest.mark.e2e_live]


class _StubModelClient:
    """满足 StructuredModelClientProtocol 的最小 stub（Phase A 自足，不依赖
    octoagent.skills.testing——保证本文件在 pre-merge 窗口对 master src 也可收集）。"""

    async def generate(self, **_kwargs: Any) -> Any:  # pragma: no cover - DI 测试不驱动
        from octoagent.skills import SkillOutputEnvelope

        return SkillOutputEnvelope(content="stub", complete=True)


def _empty_credential_store(tmp_path: Path) -> Any:
    """空 tmp CredentialStore：路径不存在 → load() 返回空 store（无宿主 OAuth 依赖）。"""
    from octoagent.provider.auth.store import CredentialStore

    return CredentialStore(store_path=tmp_path / "creds" / "auth-profiles.json")


@pytest.fixture
async def harness_factory(tmp_path: Path):
    """构造 + bootstrap OctoHarness 的工厂；teardown 统一 shutdown。

    每次调用建独立 e2e root（防同测试多次 bootstrap 互踩 DB / behavior 目录）。
    """
    from fastapi import FastAPI

    from octoagent.gateway.harness.octo_harness import OctoHarness

    built: list[tuple[Any, Any]] = []
    counter = {"n": 0}

    async def _build(**harness_overrides: Any) -> dict[str, Any]:
        counter["n"] += 1
        e2e_root = tmp_path / f"octoagent_e2e_root_{counter['n']}"
        data_dir = e2e_root / "data"
        mcp_servers_dir = e2e_root / "mcp-servers"
        e2e_root.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        mcp_servers_dir.mkdir(parents=True, exist_ok=True)
        (e2e_root / "behavior" / "system").mkdir(parents=True, exist_ok=True)

        # 与 bootstrapped_harness fixture 同款：注入 local-instance 模板
        # （alias 配置等 bootstrap 前置内容）
        fixtures_root = (
            Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
        )
        if (fixtures_root / "octoagent.yaml.template").exists():
            from apps.gateway.tests.e2e_live.helpers.factories import (
                copy_local_instance_template,
            )

            copy_local_instance_template(fixtures_root, e2e_root)

        harness = OctoHarness(
            project_root=e2e_root,
            credential_store=_empty_credential_store(e2e_root),
            mcp_servers_dir=mcp_servers_dir,
            data_dir=data_dir,
            **harness_overrides,
        )
        app = FastAPI()
        await harness.bootstrap(app)
        harness.commit_to_app(app)
        built.append((harness, app))
        return {"harness": harness, "app": app, "project_root": e2e_root}

    yield _build

    for harness, app in built:
        await harness.shutdown(app)


# ---------------------------------------------------------------------------
# AC-1：model_client override 生效 + 与 llm_mode 解耦
# ---------------------------------------------------------------------------


async def test_model_client_override_wires_skill_runner(harness_factory) -> None:
    """AC-1a：model_client=stub 时 SkillRunner 的 model_client 就是 stub。"""
    stub = _StubModelClient()
    ctx = await harness_factory(model_client=stub)
    app = ctx["app"]

    skill_runner = app.state.llm_service._skill_runner
    assert skill_runner is not None, "AC-1a: override 模式必须构造 SkillRunner"
    assert skill_runner._model_client is stub, (
        "AC-1a: SkillRunner.model_client 应为注入的 stub，"
        f"实际: {type(skill_runner._model_client).__name__}"
    )


async def test_model_client_override_decoupled_from_echo_mode(
    harness_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1b：echo 模式下 override 仍无条件建 SkillRunner（不被 echo-skip 门挡）。"""
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    stub = _StubModelClient()
    ctx = await harness_factory(model_client=stub)
    app = ctx["app"]

    skill_runner = app.state.llm_service._skill_runner
    assert skill_runner is not None, (
        "AC-1b: echo 模式 + override 必须仍建 SkillRunner（与 llm_mode 解耦）"
    )
    assert skill_runner._model_client is stub


# ---------------------------------------------------------------------------
# AC-2：全 None 时行为与 baseline 等价（spec §3.2 None 等价语义）
# ---------------------------------------------------------------------------


async def test_none_override_keeps_provider_model_client_path(harness_factory) -> None:
    """AC-2a：override 全 None + 非 echo → SkillRunner 走 ProviderModelClient 原路。"""
    from octoagent.skills.provider_model_client import ProviderModelClient

    ctx = await harness_factory()  # model_client/clock 均缺省 None
    app = ctx["app"]

    skill_runner = app.state.llm_service._skill_runner
    assert skill_runner is not None, "AC-2a: 非 echo baseline 必须构造 SkillRunner"
    assert isinstance(skill_runner._model_client, ProviderModelClient), (
        "AC-2a: None 时 model_client 必须是生产 ProviderModelClient，"
        f"实际: {type(skill_runner._model_client).__name__}"
    )


async def test_none_override_preserves_echo_skip(
    harness_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-2b：override 全 None + echo → SkillRunner 跳过（baseline 语义原样）。"""
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    ctx = await harness_factory()
    app = ctx["app"]

    assert app.state.llm_service._skill_runner is None, (
        "AC-2b: echo + 全 None 必须保持 skill_runner_skipped baseline 语义"
    )


async def test_clock_seam_default_and_override(harness_factory) -> None:
    """AC-2c：clock=None → app.state.clock 是 _utc_now 默认；注入则原样透传。"""
    from datetime import UTC, datetime

    from octoagent.gateway.harness.octo_harness import _utc_now

    # 默认：clock=None
    ctx = await harness_factory()
    app = ctx["app"]
    assert app.state.clock is _utc_now, (
        "AC-2c: clock=None 时 app.state.clock 必须是模块默认 _utc_now"
    )
    now_val = app.state.clock()
    assert now_val.tzinfo is UTC, "AC-2c: 默认时钟必须 tz-aware UTC"

    # 注入：固定时钟
    frozen = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    ctx2 = await harness_factory(clock=lambda: frozen)
    app2 = ctx2["app"]
    assert app2.state.clock() == frozen, "AC-2c: 注入 clock 必须原样透传"
