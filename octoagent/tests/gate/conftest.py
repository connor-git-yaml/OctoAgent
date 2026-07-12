"""F141 gate 脚本测试共享 fixture——按路径加载 repo-scripts 模块。

repo-scripts/ 不在 python 包结构里（薄脚本，stdlib/venv 直跑），测试经
importlib 按 ``__file__`` 相对路径加载——门禁脚本自身必须被测试
（cc-haha 教训：门禁脚本有单测且在 CI 第一步跑）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "repo-scripts"


def load_script(name: str):
    """按文件名加载 repo-scripts 下的脚本为模块（如 ``check-quarantine.py``）。"""
    path = REPO_SCRIPTS_DIR / name
    module_name = "f141_" + name.replace("-", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, f"无法加载 {path}"
    module = importlib.util.module_from_spec(spec)
    # 注册进 sys.modules：dataclass/typing 反射需要
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def quarantine_mod():
    return load_script("check-quarantine.py")


@pytest.fixture(scope="session")
def attestation_mod():
    return load_script("check-attestation.py")


@pytest.fixture(scope="session")
def coverage_mod():
    return load_script("check-changed-lines-coverage.py")


@pytest.fixture(scope="session")
def lane_mod():
    return load_script("lane.py")
