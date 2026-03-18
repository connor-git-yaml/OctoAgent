"""Feature 030: ToolIndex 与动态工具选择。"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass

from octoagent.core.models import DynamicToolSelection, ToolIndexHit, ToolIndexQuery
from ulid import ULID

from .models import ToolMeta, ToolProfile, ToolSearchHit, ToolSearchResult, ToolTier, profile_allows

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+")
_EMBED_DIM = 96


def _tokenize(value: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_PATTERN.finditer(value.lower()):
        token = match.group(0)
        if not token:
            continue
        tokens.append(token)
        if any("\u4e00" <= char <= "\u9fff" for char in token):
            if len(token) == 1:
                continue
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
    return tokens


def _hash_embed(text: str, *, dim: int = _EMBED_DIM) -> list[float]:
    vector = [0.0] * dim
    for token in _tokenize(text):
        index = hash(token) % dim
        vector[index] += 1.0
    norm = math.sqrt(sum(item * item for item in vector))
    if norm == 0:
        return vector
    return [item / norm for item in vector]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    size = min(len(left), len(right))
    if size == 0:
        return 0.0
    return sum(left[i] * right[i] for i in range(size))


@dataclass(slots=True)
class ToolIndexRecord:
    """ToolIndex 内部记录。"""

    meta: ToolMeta
    search_text: str
    embedding: list[float]


class ToolIndexBackend:
    """ToolIndex backend 抽象。"""

    backend_name = "unknown"

    async def rebuild(self, records: list[ToolIndexRecord]) -> None:
        raise NotImplementedError

    async def query(self, request: ToolIndexQuery) -> list[ToolIndexHit]:
        raise NotImplementedError


class InMemoryToolIndexBackend(ToolIndexBackend):
    """默认本地向量检索 backend。"""

    backend_name = "in_memory"

    def __init__(self) -> None:
        self._records: list[ToolIndexRecord] = []

    async def rebuild(self, records: list[ToolIndexRecord]) -> None:
        self._records = list(records)

    async def query(self, request: ToolIndexQuery) -> list[ToolIndexHit]:
        query_embedding = _hash_embed(request.query)
        hits: list[ToolIndexHit] = []
        for record in self._records:
            matched_filters = _matched_filters(record.meta, request)
            if matched_filters is None:
                continue
            score = _score_record(record, query_embedding, request, matched_filters)
            hits.append(
                ToolIndexHit(
                    tool_name=record.meta.name,
                    score=round(score, 5),
                    match_reason=_match_reason(record.meta, matched_filters),
                    matched_filters=matched_filters,
                    tool_group=record.meta.tool_group,
                    tool_profile=record.meta.tool_profile,
                    metadata={
                        "tags": list(record.meta.tags),
                        "worker_types": list(record.meta.worker_types),
                        "manifest_ref": record.meta.manifest_ref,
                        **record.meta.metadata,
                    },
                )
            )
        hits.sort(key=lambda item: (-item.score, item.tool_name))
        return hits[: request.limit]


class LanceDBToolIndexBackend(InMemoryToolIndexBackend):
    """可选 LanceDB backend。

    当前环境缺少 lancedb 依赖时显式回退，不影响主链。
    """

    backend_name = "lancedb"

    def __init__(self) -> None:
        try:
            import lancedb  # noqa: F401
        except Exception as exc:  # pragma: no cover - 依赖可选
            raise RuntimeError("lancedb backend unavailable") from exc
        super().__init__()


def _matched_filters(meta: ToolMeta, request: ToolIndexQuery) -> list[str] | None:
    matched: list[str] = []
    if request.tool_groups and meta.tool_group not in request.tool_groups:
        return None
    if request.tool_groups:
        matched.append("tool_group")
    if request.worker_type is not None:
        if meta.worker_types and request.worker_type.value not in meta.worker_types:
            return None
        matched.append("worker_type")
    if request.tool_profile:
        requested_profile = ToolProfile(str(request.tool_profile))
        if not profile_allows(meta.tool_profile, requested_profile):
            return None
        matched.append("tool_profile")
    if request.tags:
        if not set(request.tags).intersection(meta.tags):
            return None
        matched.append("tags")
    return matched


def _score_record(
    record: ToolIndexRecord,
    query_embedding: Sequence[float],
    request: ToolIndexQuery,
    matched_filters: list[str],
) -> float:
    score = _cosine(record.embedding, query_embedding)
    query_tokens = set(_tokenize(request.query))
    meta_tokens = set(_tokenize(record.search_text))
    overlap = len(query_tokens.intersection(meta_tokens))
    score += min(overlap * 0.08, 0.4)
    score += len(matched_filters) * 0.02
    if record.meta.name in query_tokens:
        score += 0.2
    return max(score, 0.0)


def _match_reason(meta: ToolMeta, matched_filters: list[str]) -> str:
    parts = [meta.description.strip() or meta.name]
    if matched_filters:
        parts.append(f"filters={','.join(matched_filters)}")
    if meta.tags:
        parts.append(f"tags={','.join(meta.tags[:3])}")
    return " | ".join(parts)


class ToolIndex:
    """ToolIndex facade。"""

    def __init__(self, *, preferred_backend: str = "auto") -> None:
        self._preferred_backend = preferred_backend
        self._degraded_reason = ""
        self._backend = self._build_backend(preferred_backend)
        self._records: list[ToolIndexRecord] = []
        if self._backend.backend_name == "in_memory" and not self._degraded_reason:
            self._degraded_reason = "static_index"

    @property
    def backend_name(self) -> str:
        return self._backend.backend_name

    @property
    def degraded_reason(self) -> str:
        return self._degraded_reason

    async def rebuild(self, tools: Sequence[ToolMeta]) -> None:
        self._records = [
            ToolIndexRecord(
                meta=tool,
                search_text=" ".join(
                    [
                        tool.name,
                        tool.description,
                        tool.tool_group,
                        " ".join(tool.tags),
                        " ".join(tool.worker_types),
                        tool.manifest_ref,
                    ]
                ),
                embedding=_hash_embed(
                    " ".join(
                        [
                            tool.name,
                            tool.description,
                            tool.tool_group,
                            " ".join(tool.tags),
                            " ".join(tool.worker_types),
                            tool.manifest_ref,
                        ]
                    )
                ),
            )
            for tool in tools
        ]
        await self._backend.rebuild(self._records)

    async def select_tools(
        self,
        request: ToolIndexQuery,
        *,
        static_fallback: Sequence[str] | None = None,
    ) -> DynamicToolSelection:
        hits = await self._backend.query(request)
        selected_tools = [item.tool_name for item in hits]
        warnings: list[str] = []
        is_fallback = False
        if not selected_tools and static_fallback:
            selected_tools = list(static_fallback)[: request.limit]
            warnings.append("tool_index_empty_fallback_to_static_toolset")
            is_fallback = True

        return DynamicToolSelection(
            selection_id=str(ULID()),
            query=request,
            selected_tools=selected_tools,
            recommended_tools=list(selected_tools),
            hits=hits,
            backend=self._backend.backend_name,
            is_fallback=is_fallback,
            warnings=warnings,
        )

    async def search_for_deferred(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> ToolSearchResult:
        """Feature 061 T-019: 搜索 Deferred 工具并返回完整 ToolMeta（含 schema）

        复用现有 cosine + BM25 混合打分。
        降级逻辑：ToolIndex 不可用或零命中时回退全量 Deferred 名称列表。

        Args:
            query: 自然语言查询
            limit: 最大返回数量

        Returns:
            ToolSearchResult 含匹配结果、降级标志、后端信息、延迟
        """
        start_ns = time.monotonic_ns()

        if not query or not query.strip():
            elapsed_ms = (time.monotonic_ns() - start_ns) // 1_000_000
            return ToolSearchResult(
                query=query or "",
                results=[],
                total_deferred=self._count_deferred(),
                is_fallback=False,
                backend=self._backend.backend_name,
                latency_ms=elapsed_ms,
            )

        # 构建 ToolIndexQuery 并检索
        index_query = ToolIndexQuery(query=query.strip(), limit=limit)

        try:
            hits = await self._backend.query(index_query)
        except Exception:
            # ToolIndex 不可用 → 降级返回全量 Deferred 名称列表
            elapsed_ms = (time.monotonic_ns() - start_ns) // 1_000_000
            return self._fallback_deferred_list(query, limit, elapsed_ms)

        # 仅保留 Deferred 层级的工具（CORE 工具已在 context 中）
        deferred_hits = [
            h for h in hits
            if self._find_record(h.tool_name) is not None
            and self._find_record(h.tool_name).meta.tier == ToolTier.DEFERRED  # type: ignore[union-attr]
        ]

        if not deferred_hits:
            # 零命中 → 降级返回全量 Deferred 名称列表
            elapsed_ms = (time.monotonic_ns() - start_ns) // 1_000_000
            return self._fallback_deferred_list(query, limit, elapsed_ms)

        # 转换为 ToolSearchHit（含完整 schema）
        results: list[ToolSearchHit] = []
        for hit in deferred_hits[:limit]:
            record = self._find_record(hit.tool_name)
            if record is None:
                continue
            meta = record.meta
            results.append(
                ToolSearchHit(
                    tool_name=meta.name,
                    description=meta.description,
                    parameters_schema=dict(meta.parameters_json_schema),
                    score=hit.score,
                    side_effect_level=meta.side_effect_level.value,
                    tool_group=meta.tool_group,
                    tags=list(meta.tags),
                )
            )

        elapsed_ms = (time.monotonic_ns() - start_ns) // 1_000_000

        return ToolSearchResult(
            query=query,
            results=results,
            total_deferred=self._count_deferred(),
            is_fallback=False,
            backend=self._backend.backend_name,
            latency_ms=elapsed_ms,
        )

    def _find_record(self, tool_name: str) -> ToolIndexRecord | None:
        """通过工具名查找内部记录"""
        for record in self._records:
            if record.meta.name == tool_name:
                return record
        return None

    def _count_deferred(self) -> int:
        """计算 Deferred 工具总数"""
        return sum(1 for r in self._records if r.meta.tier == ToolTier.DEFERRED)

    def _fallback_deferred_list(
        self,
        query: str,
        limit: int,
        elapsed_ms: int,
    ) -> ToolSearchResult:
        """降级: 返回全量 Deferred 工具名称列表"""
        deferred_records = [
            r for r in self._records if r.meta.tier == ToolTier.DEFERRED
        ]
        results = [
            ToolSearchHit(
                tool_name=r.meta.name,
                description=r.meta.description,
                parameters_schema=dict(r.meta.parameters_json_schema),
                score=0.0,
                side_effect_level=r.meta.side_effect_level.value,
                tool_group=r.meta.tool_group,
                tags=list(r.meta.tags),
            )
            for r in deferred_records[:limit]
        ]
        return ToolSearchResult(
            query=query,
            results=results,
            total_deferred=len(deferred_records),
            is_fallback=True,
            backend=self._backend.backend_name,
            latency_ms=elapsed_ms,
        )

    def _build_backend(self, preferred_backend: str) -> ToolIndexBackend:
        choice = preferred_backend.strip().lower()
        if choice in {"", "auto", "in_memory"}:
            if choice == "auto":
                try:
                    return LanceDBToolIndexBackend()
                except RuntimeError:
                    self._degraded_reason = "lancedb_unavailable"
            return InMemoryToolIndexBackend()
        if choice == "lancedb":
            try:
                return LanceDBToolIndexBackend()
            except RuntimeError:
                self._degraded_reason = "lancedb_unavailable"
                return InMemoryToolIndexBackend()
        self._degraded_reason = "unknown_backend_fallback"
        return InMemoryToolIndexBackend()
