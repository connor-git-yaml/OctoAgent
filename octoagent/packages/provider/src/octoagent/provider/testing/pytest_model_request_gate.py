"""pytest11 entry-point 插件：测试会话默认 deny 真 LLM 网络调用（F137 硬闸）。

注册：``packages/provider/pyproject.toml`` ``[project.entry-points.pytest11]``
``octoagent_model_request_gate = "octoagent.provider.testing.pytest_model_request_gate"``
→ 安装本包的 venv 内**所有** pytest 会话构造性生效（含 per-package rootdir
直跑 / ``benchmarks/tests``），免 9 个 tests 目录 conftest 多点同步。

冗余布线：``octoagent/conftest.py`` ``pytest_configure`` 同置 deny（幂等）——
覆盖 worktree PYTHONPATH 锁模式（禁 uv sync）下 entry point 未注册进共享 venv
的窗口（memory ``project_worktree_venv_symlink``）。

opt-in 通道（deny 后如何合法打真 LLM / 直测 dispatch 机器）：
- e2e_full marker（``e2e_live/conftest.py`` autouse fixture 自动翻 allow）；
- ``with allow_model_requests():`` context / 同名 fixture（直测 dispatch 机器的单测）；
- 进程级 env ``OCTOAGENT_ALLOW_MODEL_REQUESTS=1``（如 OctoBench CLI——它本就
  不是 pytest 进程，构造性不受本插件影响）。

实现注意：
- 模块顶层零第三方 import（pytest 启动极早期加载；``import octoagent.provider.*``
  会执行包 ``__init__`` 拉起 gateway，实测冷启动 ~1.1s——放进 ``pytest_configure``
  一次性支付，不在 entry point 扫描期支付）。
- gate import **严格失败不吞 ImportError**（Codex 收窄评审 P2-2）：插件与 gate
  同包随包发布，插件可加载 ⟹ gate 必在；缺失=安装态损坏，静默 no-op 会让
  deny 保证悄然失效。旧 worktree（基于 pre-F137 master 的 PYTHONPATH 锁跑）
  插件模块本身 ImportError 属响亮失败，逃生门：``-p no:octoagent_model_request_gate``
  或 rebase master（research §I.6）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def pytest_configure(config: "pytest.Config") -> None:
    """会话起点置 gate=deny（幂等；与根 conftest 冗余布线同值）。"""
    del config
    from octoagent.provider.model_request_gate import set_allow_model_requests

    set_allow_model_requests(False)
