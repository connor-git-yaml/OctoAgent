"""F087 P2 T-P2-11：state_diff sha256 工具集（SC-7 支撑）。

3 个函数：

- ``sha256_file(path)`` — 单文件 sha256 hex
- ``sha256_dir(path)`` — 目录递归 sha256 hex（按 sorted 文件路径，深度优先；
  内容 + 相对路径都纳入 hash 防止文件 rename 漏报）
- ``module_singletons_snapshot()`` — 当前 5 个 stateful 单例的快照
  （供 e2e 跑前后比对，验证 _reset_module_state 真生效）
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path | str) -> str:
    """单文件 sha256 hex。文件不存在 → 返回 ``""``（调用方按需判别）。"""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ""
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_dir(path: Path | str) -> str:
    """目录递归 sha256 hex。

    算法：
    1. 找到所有 regular file（含子目录），按相对路径 sorted
    2. 每个文件 hash 内容 + 相对路径串入主 hash
    3. 不存在的目录返回 ``""``

    rename / move / 新增 / 删除任一都会改变最终 hex。
    """
    root = Path(path)
    if not root.exists() or not root.is_dir():
        return ""

    h = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file())
    for f in files:
        rel = f.relative_to(root)
        h.update(str(rel).encode("utf-8"))
        h.update(b"\x00")
        with f.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        h.update(b"\x01")  # 文件 boundary
    return h.hexdigest()


def module_singletons_snapshot() -> dict[str, Any]:
    """快照 5 个 stateful 单例的当前值，供 e2e fixture 跑前后比对。

    返回 dict 内容：
      - ``tool_registry_count`` — _REGISTRY._entries 长度
      - ``agent_context_llm_set`` — bool（是否非 None）
      - ``agent_context_router_set`` — bool（是否非 None）
      - ``execution_context_var`` — None / 'set'

    （``_tiktoken_encoder`` 是 lazy import 一次性 init，**不**纳入 reset 快照——
    详见 MODULE_SINGLETONS.md 与 conftest.py:_reset_module_state docstring。）

    json-serializable，可直接 dump 进失败信息。
    """
    snapshot: dict[str, Any] = {}

    try:
        from octoagent.gateway.harness import tool_registry as _tr

        snapshot["tool_registry_count"] = len(_tr._REGISTRY._entries)  # type: ignore[attr-defined]
    except Exception:
        snapshot["tool_registry_count"] = "unavailable"

    try:
        from octoagent.gateway.services.agent_context import AgentContextService

        snapshot["agent_context_llm_set"] = AgentContextService._shared_llm_service is not None
        snapshot["agent_context_router_set"] = (
            AgentContextService._shared_provider_router is not None
        )
    except Exception:
        snapshot["agent_context_llm_set"] = "unavailable"
        snapshot["agent_context_router_set"] = "unavailable"

    try:
        from octoagent.gateway.services import execution_context as _ec

        snapshot["execution_context_var"] = (
            "set" if _ec._CURRENT_EXECUTION_CONTEXT.get() is not None else "None"  # type: ignore[attr-defined]
        )
    except Exception:
        snapshot["execution_context_var"] = "unavailable"

    return snapshot


def snapshot_to_json(snapshot: dict[str, Any]) -> str:
    """diff 失败时方便 dump 进 AssertionError。"""
    return json.dumps(snapshot, sort_keys=True, ensure_ascii=False)


__all__ = [
    "sha256_file",
    "sha256_dir",
    "module_singletons_snapshot",
    "snapshot_to_json",
]
