"""F137 硬闸：memory bridge embedding 检索路径对 ModelRequestsNotAllowedError re-raise。

Codex re-review P2-1：``_fetch_embeddings`` 的守卫 re-raise 若被上层
``_try_embed_query`` 的 broad catch 吞成 ``None``，会退化 FTS-only 假绿——
漏网 embedding 调用信号被掩埋。本文件锁定该链路两级守卫的贯通。
"""

from __future__ import annotations

import pytest
from octoagent.gateway.services.memory.builtin_memu_bridge import (
    BuiltinMemUBridge,
    _ResolvedEmbeddingTarget,
)
from octoagent.provider import ModelRequestsNotAllowedError


def _proxy_target() -> _ResolvedEmbeddingTarget:
    return _ResolvedEmbeddingTarget(
        requested_target="proxy",
        effective_target="proxy",
        layer_id="layer-proxy",
        mode="proxy_alias",
        uses_proxy_alias=True,
        proxy_alias="emb",
    )


async def test_try_embed_query_reraises_gate_error() -> None:
    """gate 异常从 embed 链穿透 _try_embed_query（不吞成 None → FTS-only 假绿）。"""
    bridge = object.__new__(BuiltinMemUBridge)  # 不走重型 __init__，只测该方法

    async def _boom(*args: object, **kwargs: object) -> list[list[float]]:
        raise ModelRequestsNotAllowedError("leak via embedding")

    bridge._embed_texts_with_proxy_alias = _boom  # type: ignore[method-assign]
    with pytest.raises(ModelRequestsNotAllowedError):
        await bridge._try_embed_query("query", _proxy_target())


async def test_try_embed_query_ordinary_error_still_degrades() -> None:
    """对照组：普通异常保持 baseline 降级语义（返回 None → 纯 BM25）。"""
    bridge = object.__new__(BuiltinMemUBridge)

    async def _fail(*args: object, **kwargs: object) -> list[list[float]]:
        raise RuntimeError("provider transient failure")

    bridge._embed_texts_with_proxy_alias = _fail  # type: ignore[method-assign]
    assert await bridge._try_embed_query("query", _proxy_target()) is None
