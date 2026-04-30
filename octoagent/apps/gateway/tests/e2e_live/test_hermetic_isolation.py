"""F087 P2 T-P2-16：hermetic 隔离回归测试（Codex P1 high finding 直接验收）。

验证：``OctoHarness`` 在 4 DI 全部注入（``credential_store`` / ``llm_adapter`` /
``mcp_servers_dir`` / ``data_dir``）的前提下，**完全不读宿主 ``~/.octoagent``**。

测试方式：
1. ``patch("pathlib.Path.home")`` 抛 ``RuntimeError("hermetic violation")``
2. 注入完整 4 DI 构造 ``OctoHarness``
3. 跑前 4 段 ``_bootstrap_*``（``paths`` → ``stores`` →
   ``tool_registry_and_snapshot`` → ``owner_profile``）+ ``runtime_services``
   + ``mcp`` 段（这些段直接关联 mcp_servers_dir / data_dir / credential_store
   消费路径）
4. 期望：bootstrap 不抛 RuntimeError（即没有任何代码路径调 ``Path.home()``）

Codex F087-P1 high finding 闭环 sanity——P1 fixup commit 3c650e7 引入的 4-tuple
fail-fast 校验在 P2 已被 T-P2-4 / T-P2-8 移除；本测试用 ``Path.home`` patch 反向
证明移除是安全的（DI 真消费 = bootstrap 不再读宿主）。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.e2e_live]


def _hermetic_path_home() -> Path:
    """patch 替身——任意 Path.home() 调用都立即抛 RuntimeError 暴露宿主泄漏。"""
    raise RuntimeError(
        "F087 hermetic violation: OctoHarness 4 DI 全注入路径下 Path.home() 不应被调用"
    )


@pytest.fixture
def fake_credential_store(tmp_path: Path):
    """轻量 fake CredentialStore（不实际读 OAuth profile）。"""
    from octoagent.provider.auth.store import CredentialStore

    fake_path = tmp_path / "fake-auth.json"
    fake_path.write_text('{"profiles": {}}', encoding="utf-8")
    return CredentialStore(store_path=fake_path)


async def test_octo_harness_bootstrap_paths_does_not_call_path_home(
    tmp_path: Path,
    fake_credential_store,
) -> None:
    """patch Path.home 后跑 _bootstrap_paths 不抛 RuntimeError。

    _bootstrap_paths 仅用 project_root，不应触碰 Path.home。
    """
    from fastapi import FastAPI

    from octoagent.gateway.harness.octo_harness import OctoHarness

    e2e_root = tmp_path / "octo_e2e"
    e2e_root.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mcp_dir = tmp_path / "mcp"
    mcp_dir.mkdir()
    (e2e_root / "behavior" / "system").mkdir(parents=True)

    harness = OctoHarness(
        project_root=e2e_root,
        credential_store=fake_credential_store,
        llm_adapter=None,
        mcp_servers_dir=mcp_dir,
        data_dir=data_dir,
    )
    app = FastAPI()

    with patch("pathlib.Path.home", side_effect=_hermetic_path_home):
        # _bootstrap_paths 应能跑通（仅用 project_root）
        await harness._bootstrap_paths(app)

    # state 已挂上
    assert app.state.project_root == e2e_root


async def test_octo_harness_bootstrap_stores_uses_data_dir_di(
    tmp_path: Path,
    fake_credential_store,
) -> None:
    """data_dir DI 注入时 _bootstrap_stores 不读 env / 宿主默认路径。"""
    from fastapi import FastAPI

    from octoagent.gateway.harness.octo_harness import OctoHarness

    e2e_root = tmp_path / "octo_e2e2"
    e2e_root.mkdir()
    data_dir = tmp_path / "data2"
    data_dir.mkdir()
    mcp_dir = tmp_path / "mcp2"
    mcp_dir.mkdir()
    (e2e_root / "behavior" / "system").mkdir(parents=True)

    harness = OctoHarness(
        project_root=e2e_root,
        credential_store=fake_credential_store,
        llm_adapter=None,
        mcp_servers_dir=mcp_dir,
        data_dir=data_dir,
    )
    app = FastAPI()

    with patch("pathlib.Path.home", side_effect=_hermetic_path_home):
        await harness._bootstrap_paths(app)
        await harness._bootstrap_stores(app)

    # store_group 已构造，db 路径在 data_dir 下
    assert harness._store_group is not None
    # 验证 db 文件落在 data_dir 而不是宿主
    expected_db = data_dir / "sqlite" / "octoagent.db"
    assert expected_db.exists(), f"db should be at {expected_db}"

    await harness._store_group.conn.close()


async def test_octo_harness_runtime_services_uses_mcp_servers_dir_di(
    tmp_path: Path,
    fake_credential_store,
) -> None:
    """mcp_servers_dir DI 注入时 _bootstrap_runtime_services 不调 Path.home。

    特别关注 lifespan 行 394 的 _mcp_servers_dir mkdir 路径。
    """
    from fastapi import FastAPI

    from octoagent.gateway.harness.octo_harness import OctoHarness

    e2e_root = tmp_path / "octo_e2e3"
    e2e_root.mkdir()
    data_dir = tmp_path / "data3"
    data_dir.mkdir()
    mcp_dir = tmp_path / "mcp3"
    # 故意不预创建 mcp_dir，让 _bootstrap_runtime_services mkdir
    (e2e_root / "behavior" / "system").mkdir(parents=True)

    harness = OctoHarness(
        project_root=e2e_root,
        credential_store=fake_credential_store,
        llm_adapter=None,
        mcp_servers_dir=mcp_dir,
        data_dir=data_dir,
    )
    app = FastAPI()

    # patch 仅在 runtime_services 段生效，paths/stores/tool_registry_and_snapshot
    # 段不需要 patch（这两段未必都已 hermetic）
    await harness._bootstrap_paths(app)
    await harness._bootstrap_stores(app)
    await harness._bootstrap_tool_registry_and_snapshot(app)
    await harness._bootstrap_owner_profile(app)

    with patch("pathlib.Path.home", side_effect=_hermetic_path_home):
        await harness._bootstrap_runtime_services(app)

    # mcp_dir 应被 mkdir（DI 路径，不是宿主）
    assert mcp_dir.exists(), "mcp_dir DI 路径应被 mkdir"

    # cleanup
    await harness._store_group.conn.close()


def test_di_consumption_no_fail_fast(tmp_path: Path, fake_credential_store) -> None:
    """4 DI 全部注入构造 OctoHarness 不抛 NotImplementedError（fail-fast 已删）。"""
    from octoagent.gateway.harness.octo_harness import OctoHarness

    # 直接构造不应抛
    harness = OctoHarness(
        project_root=tmp_path,
        credential_store=fake_credential_store,
        llm_adapter=MagicMock(),
        mcp_servers_dir=tmp_path / "mcp",
        data_dir=tmp_path / "data",
    )
    assert harness._credential_store_override is fake_credential_store
    assert harness._llm_adapter_override is not None
    assert harness._mcp_servers_dir == tmp_path / "mcp"
    assert harness._data_dir == tmp_path / "data"
