"""安装验证脚本

Task ID   : T-0-1
关联 FR   : FR-G02, IA-3（W6 安装前提）
PoC 假设  : IA-3（tau-bench pip 可安装）
实测维度  : 各依赖包 import 成功 / 版本输出
期望输出  : JSON 报告到 stdout，每项含 status + version

运行方式  ::

    cd <project_root>
    python .specify/features/103d-octobench/poc/install_check.py

注意：此脚本只做 try-import 检测，不执行任何 pip install。
安装请用::

    uv add tau-bench datasets

"""
from __future__ import annotations

import json
import sys


def check_import(module: str, attr: str | None = None) -> dict:
    """尝试 import 指定模块，返回 status + version 字典。"""
    try:
        import importlib

        mod = importlib.import_module(module)
        version = getattr(mod, "__version__", None)
        if version is None and attr:
            version = getattr(mod, attr, None)
        return {"status": "ok", "version": str(version) if version else "unknown"}
    except ImportError as e:
        return {"status": "MISSING", "error": str(e)}
    except Exception as e:  # 导入时意外错误，不吞掉
        return {"status": "ERROR", "error": str(e)}


def main() -> None:
    report: dict[str, object] = {
        "script": "install_check.py",
        "task": "T-0-1",
        "packages": {},
    }

    checks = [
        # (label, module_path, version_attr)
        ("tau_bench",         "tau_bench",                None),
        ("tau_bench.envs.airline", "tau_bench.envs.airline",  None),
        ("datasets",          "datasets",                 "__version__"),
        ("aiosqlite",         "aiosqlite",                "__version__"),
        ("pydantic",          "pydantic",                 "VERSION"),
        ("fastapi",           "fastapi",                  "__version__"),
        ("anthropic",         "anthropic",                "__version__"),
    ]

    all_ok = True
    for label, module, attr in checks:
        result = check_import(module, attr)
        report["packages"][label] = result
        if result["status"] != "ok":
            all_ok = False

    report["overall"] = "PASS" if all_ok else "FAIL_PARTIAL"
    report["advice"] = (
        "所有依赖已就绪，可继续运行 PoC 脚本。"
        if all_ok
        else (
            "部分依赖缺失。建议：\n"
            "  uv add datasets   # 安装 HuggingFace datasets\n"
            "  uv add git+https://github.com/sierra-research/tau-bench.git  # 安装 tau-bench\n"
            "安装完成后重新运行此脚本验证。"
        )
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    # 按整体结果决定退出码（非零不代表脚本崩溃，只是提示用户还有缺失包）
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
