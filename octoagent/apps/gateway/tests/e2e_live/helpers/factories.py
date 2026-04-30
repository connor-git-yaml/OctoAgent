"""F087 e2e_live helpers/factories.py（T-P2-8 主 fixture + T-P2-12 helper 复制）。

提供：

1. ``octo_harness_e2e`` fixture：注入 4 DI 钩子（``credential_store`` /
   ``llm_adapter`` / ``mcp_servers_dir`` / ``data_dir``）+ ProviderRouter
   timeout 120s + max_steps=10。
2. ``_build_real_user_profile_handler`` / ``_ensure_audit_task`` /
   ``_insert_turn_events`` 复制版本（T-P2-12 双源共存——P5 删除旧位置）。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fixture：octo_harness_e2e（T-P2-8）
# ---------------------------------------------------------------------------


@pytest.fixture
async def octo_harness_e2e(
    tmp_path: Path,
    real_codex_credential_store: Any,  # 由 fixtures_real_credentials 提供
) -> AsyncIterator[Any]:
    """OctoHarness e2e fixture：注入 4 DI 钩子 + ProviderRouter 120s timeout。

    布置：
      - ``data_dir`` = ``tmp_path / "data"``（隔离宿主 SQLite / artifacts）
      - ``mcp_servers_dir`` = ``tmp_path / "mcp-servers"``（隔离 ~/.octoagent/mcp-servers）
      - ``credential_store`` = e2e tmp 副本（隔离宿主 auth-profiles.json）
      - ``llm_adapter`` = None → 走默认 ProviderRouterMessageAdapter
      - ``ProviderRouter(timeout_s=120.0)`` 由 OctoHarness 内部已构造，timeout
        由 alias_registry / provider config 控制；e2e 不强行覆盖 router timeout
        （改在 alias config 注入），保留代码路径自然。
      - ``max_steps=10`` 由 task_runner 默认值控制；e2e 不需要单独 override。

    F087 P2 阶段不实际触发 ``OctoHarness.bootstrap()``——
    P3 起每个 case 自行决定 bootstrap 时机；本 fixture 仅提供构造好的 harness 实例。
    """
    from fastapi import FastAPI

    from octoagent.gateway.harness.octo_harness import OctoHarness

    # e2e 实例 root 在 tmp 下，绝不动宿主
    e2e_root = tmp_path / "octoagent_e2e_root"
    data_dir = e2e_root / "data"
    mcp_servers_dir = e2e_root / "mcp-servers"
    project_root = e2e_root  # OctoHarness 内 project_root 就是 instance root

    # 必要骨架目录（OctoHarness bootstrap 内部还会按需 mkdir，这里仅保证 root 存在）
    project_root.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    mcp_servers_dir.mkdir(parents=True, exist_ok=True)
    (project_root / "behavior" / "system").mkdir(parents=True, exist_ok=True)

    harness = OctoHarness(
        project_root=project_root,
        credential_store=real_codex_credential_store,
        llm_adapter=None,  # 走默认 ProviderRouterMessageAdapter
        mcp_servers_dir=mcp_servers_dir,
        data_dir=data_dir,
    )

    app = FastAPI()
    yield {"harness": harness, "app": app, "project_root": project_root}

    # teardown：调 shutdown 释放资源（仅当 bootstrap 实际跑过）
    try:
        await harness.shutdown(app)
    except Exception:
        # bootstrap 未跑或部分失败时 shutdown 可能 raise，e2e fixture 兜底
        pass


# ---------------------------------------------------------------------------
# T-P2-12: 旧 helper 复制版本（双源共存到 P5 T-P5-1 删除旧位置）
# ---------------------------------------------------------------------------
# 旧位置：apps/gateway/tests/e2e/test_acceptance_scenarios.py
#
# 复制原因：避免 T-P3-6（旧 acceptance 仍跑）+ T-P5-1（删旧文件）时序冲突。
# 复制后 P3/P4 新 case 用本文件版本；旧 acceptance 仍用旧位置；P5 一并清理。


def _build_real_user_profile_handler(*args: Any, **kwargs: Any) -> Any:
    """复制 placeholder——实际 body 在 T-P3 完成时按需从旧 acceptance 拷贝。

    P2 阶段先建立函数符号，避免 helpers 包 import 时缺失。F087 P3 case 实际
    需要时按 spec 附录 A.2 真正实现。"""
    raise NotImplementedError(
        "F087 P2: _build_real_user_profile_handler placeholder. "
        "Implementation copied from old acceptance_scenarios in P3 task."
    )


async def _ensure_audit_task(*args: Any, **kwargs: Any) -> Any:
    """同上，P3 实现时按需复制。"""
    raise NotImplementedError(
        "F087 P2: _ensure_audit_task placeholder; copy from old location in P3."
    )


async def _insert_turn_events(*args: Any, **kwargs: Any) -> Any:
    """同上，P3 实现时按需复制。"""
    raise NotImplementedError(
        "F087 P2: _insert_turn_events placeholder; copy from old location in P3."
    )


def copy_local_instance_template(template_root: Path, dst_root: Path) -> None:
    """把 ``tests/fixtures/local-instance/`` 模板复制到 e2e tmp dst_root。

    供 e2e fixture 在 bootstrap 前调用，给 dst_root 注入 USER.md / MEMORY.md /
    octoagent.yaml 初始内容。
    """
    dst_behavior = dst_root / "behavior" / "system"
    dst_behavior.mkdir(parents=True, exist_ok=True)

    src_behavior = template_root / "behavior" / "system"
    if (src_behavior / "USER.md.template").exists():
        shutil.copy(src_behavior / "USER.md.template", dst_behavior / "USER.md")
    if (src_behavior / "MEMORY.md.template").exists():
        shutil.copy(src_behavior / "MEMORY.md.template", dst_behavior / "MEMORY.md")

    if (template_root / "octoagent.yaml.template").exists():
        shutil.copy(template_root / "octoagent.yaml.template", dst_root / "octoagent.yaml")


__all__ = [
    "octo_harness_e2e",
    "_build_real_user_profile_handler",
    "_ensure_audit_task",
    "_insert_turn_events",
    "copy_local_instance_template",
]
