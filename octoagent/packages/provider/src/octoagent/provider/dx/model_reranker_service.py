"""Feature 065 Phase 2: 本地 Cross-Encoder Reranker 服务 (US-6)。

封装 Qwen3-Reranker-0.6B（sentence-transformers CrossEncoder API），
在 MemoryService.recall_memory 的粗排结果上进行精排。

模型不可用时返回 degraded=True，调用方应降级到 HEURISTIC。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

_log = structlog.get_logger()


# ---------------------------------------------------------------------------
# 结果数据类
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RerankResult:
    """Rerank 结果。"""

    scores: list[float]  # 与 candidates 一一对应的相关性得分
    model_id: str = ""  # 使用的模型标识
    degraded: bool = False  # 是否降级
    degraded_reason: str = ""  # 降级原因


# ---------------------------------------------------------------------------
# ModelRerankerService
# ---------------------------------------------------------------------------


class ModelRerankerService:
    """基于 Qwen3-Reranker-0.6B 的本地 cross-encoder reranker。

    sentence-transformers 兼容，使用 CrossEncoder API。
    模型加载失败时降级到 heuristic rerank。
    """

    _RERANKER_MODEL_ID: str = "Qwen/Qwen3-Reranker-0.6B"
    _MIN_CANDIDATES_FOR_RERANK: int = 2
    _RERANK_INSTRUCTION: str = (
        "Given a query, retrieve relevant memory passages that contain "
        "the information needed to answer the query."
    )
    _WARMUP_BACKOFF_SECONDS: float = 60.0

    def __init__(self, *, auto_load: bool = True) -> None:
        self._model: Any | None = None
        self._model_loaded: bool = False
        self._load_attempted: bool = False
        self._load_error: str = ""
        self._warmup_task: asyncio.Task[None] | None = None
        if auto_load:
            self._schedule_warmup()

    @property
    def is_available(self) -> bool:
        """模型是否已加载且可用。"""
        return self._model_loaded and self._model is not None

    async def rerank(
        self,
        query: str,
        candidates: list[str],
    ) -> RerankResult:
        """对候选文本进行 cross-encoder 精排。

        如果模型不可用或 candidates < 2，返回 degraded=True。
        """
        # 候选不足 -> 跳过
        if len(candidates) < self._MIN_CANDIDATES_FOR_RERANK:
            return RerankResult(
                scores=[1.0] * len(candidates),
                degraded=True,
                degraded_reason=f"candidates < {self._MIN_CANDIDATES_FOR_RERANK}, skipped rerank",
            )

        # 模型不可用 -> 降级
        if not self.is_available:
            reason = self._load_error or "reranker model not loaded"
            return RerankResult(
                scores=[0.0] * len(candidates),
                degraded=True,
                degraded_reason=reason,
            )

        # 执行 cross-encoder 推理
        try:
            pairs = [
                {"query": query, "passage": candidate}
                for candidate in candidates
            ]
            scores = await asyncio.to_thread(
                self._model.predict,
                pairs,
            )
            return RerankResult(
                scores=[float(s) for s in scores],
                model_id=self._RERANKER_MODEL_ID,
            )
        except Exception as exc:
            _log.warning(
                "model_reranker_degraded",
                reason=f"rerank inference failed: {exc}",
            )
            return RerankResult(
                scores=[0.0] * len(candidates),
                degraded=True,
                degraded_reason=f"rerank inference failed: {exc}",
            )

    # ------------------------------------------------------------------
    # 模型加载
    # ------------------------------------------------------------------

    def _schedule_warmup(self) -> None:
        """安排后台异步模型加载。"""
        try:
            loop = asyncio.get_running_loop()
            self._warmup_task = loop.create_task(self._warmup_model())
        except RuntimeError:
            # 没有运行中的事件循环（例如在非 async 上下文中创建），跳过
            pass

    async def _warmup_model(self) -> None:
        """后台加载模型。"""
        if self._load_attempted:
            return

        self._load_attempted = True
        _log.info("model_reranker_warmup_started", model_id=self._RERANKER_MODEL_ID)

        try:
            def _load():
                from sentence_transformers import CrossEncoder
                return CrossEncoder(
                    self._RERANKER_MODEL_ID,
                    trust_remote_code=True,
                    device="cpu",
                )

            self._model = await asyncio.to_thread(_load)
            self._model_loaded = True
            _log.info(
                "model_reranker_ready",
                model_id=self._RERANKER_MODEL_ID,
            )
        except Exception as exc:
            self._load_error = str(exc)
            _log.warning(
                "model_reranker_warmup_failed",
                error=str(exc),
            )
            # 退避，避免频繁重试
            await asyncio.sleep(self._WARMUP_BACKOFF_SECONDS)
            self._load_attempted = False  # 允许下次重试
