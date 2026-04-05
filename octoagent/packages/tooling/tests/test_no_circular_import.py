"""验证 tooling ↔ policy 循环导入已消除。

使用 subprocess 获得干净的解释器状态。
"""

import subprocess
import sys


def test_import_tooling_then_policy():
    """先导入 tooling 再导入 policy，不报 ImportError。"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import octoagent.tooling; import octoagent.policy",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"import failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_import_policy_then_tooling():
    """先导入 policy 再导入 tooling，不报 ImportError。"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import octoagent.policy; import octoagent.tooling",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"import failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_no_tooling_to_policy_import():
    """tooling 包源码中不存在指向 policy 的导入。"""
    import importlib
    import inspect
    import os

    tooling_pkg = importlib.import_module("octoagent.tooling")
    tooling_root = os.path.dirname(inspect.getfile(tooling_pkg))

    for dirpath, _dirs, files in os.walk(tooling_root):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            with open(fpath) as f:
                content = f.read()
            assert "from octoagent.policy" not in content, (
                f"{fpath} still imports from octoagent.policy"
            )
            assert "import octoagent.policy" not in content, (
                f"{fpath} still imports octoagent.policy"
            )
