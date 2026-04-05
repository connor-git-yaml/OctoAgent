"""Memory 召回（recall）子服务。"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from .backends import MemoryBackend, SqliteMemoryBackend
from .enums import MemoryLayer, MemoryPartition
from .models import (
    EvidenceRef,
    FragmentRecord,
    MemoryAccessDeniedError,
    MemoryAccessPolicy,
    MemoryBackendState,
    MemoryBackendStatus,
    MemoryEvidenceQuery,
    MemoryRecallHit,
    MemoryRecallHookOptions,
    MemoryRecallHookTrace,
    MemoryRecallPostFilterMode,
    MemoryRecallRerankMode,
    MemoryRecallResult,
    MemorySearchHit,
    MemorySearchOptions,
    SorRecord,
    VaultRecord,
)
from .store.memory_store import SqliteMemoryStore

log = structlog.get_logger(__name__)


# 类型别名——候选四元组 (scope_index, query_index, ordinal, hit)
_Candidate = tuple[int, int, int, MemorySearchHit]


class MemoryRecallService:
    """负责 recall_memory / search_memory 以及 Phase 3 hooks 的子服务。

    本类不持有 ``conn``——所有 DB 写入通过 backend / store 完成；
    若需要 ``get_memory`` / ``resolve_memory_evidence`` 等读路径，
    Facade 在构造时注入回调。
    """

    def __init__(
        self,
        *,
        store: SqliteMemoryStore,
        backend: MemoryBackend,
        fallback_backend: SqliteMemoryBackend,
        reranker_service: Any | None = None,
        # 由 Facade 注入的宿主引用，避免 sub-service 反向 import MemoryService
        # 使用 Any 类型以避免循环导入
        facade: Any = None,
    ) -> None:
        self._store = store
        self._backend = backend
        self._fallback_backend = fallback_backend
        self._reranker_service = reranker_service
        # 宿主 Facade 引用——recall 所需的跨子服务方法通过 facade 晚绑定调用
        self._facade = facade

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    async def search_memory(
        self,
        *,
        scope_id: str,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
        search_options: MemorySearchOptions | None = None,
        # 由 Facade 传入的 backend 状态辅助
        backend_degraded: bool = False,
        should_force_fallback_fn: Any = None,
        mark_backend_healthy_fn: Any = None,
        mark_backend_degraded_fn: Any = None,
    ) -> list[MemorySearchHit]:
        """基础检索接口。"""

        policy = policy or MemoryAccessPolicy()
        if self._backend.backend_id == self._fallback_backend.backend_id:
            hits = await self._fallback_backend.search(
                scope_id,
                query=query,
                policy=policy,
                limit=limit,
                search_options=search_options,
            )
            if mark_backend_healthy_fn:
                mark_backend_healthy_fn()
            return hits

        if should_force_fallback_fn and await should_force_fallback_fn():
            return await self._search_via_store(
                scope_id,
                query=query,
                policy=policy,
                limit=limit,
                search_options=search_options,
            )

        try:
            if not await self._backend.is_available():
                if mark_backend_degraded_fn:
                    mark_backend_degraded_fn(
                        "BACKEND_UNAVAILABLE",
                        "高级 memory backend 当前不可用，已切换到 SQLite fallback。",
                    )
                return await self._search_via_store(
                    scope_id,
                    query=query,
                    policy=policy,
                    limit=limit,
                )
            hits = await self._backend.search(
                scope_id,
                query=query,
                policy=policy,
                limit=limit,
                search_options=search_options,
            )
            if mark_backend_healthy_fn:
                mark_backend_healthy_fn()
            return hits
        except Exception as exc:
            if mark_backend_degraded_fn:
                mark_backend_degraded_fn(
                    "BACKEND_SEARCH_FAILED",
                    str(exc) or "高级 memory backend search 失败，已切换到 SQLite fallback。",
                )
            log.warning(
                "memory_backend_search_degraded",
                backend=self._backend.backend_id,
                scope_id=scope_id,
                error=str(exc),
            )
            return await self._search_via_store(
                scope_id,
                query=query,
                policy=policy,
                limit=limit,
            )

    async def recall_memory(
        self,
        *,
        scope_ids: list[str],
        query: str,
        policy: MemoryAccessPolicy | None = None,
        per_scope_limit: int = 3,
        max_hits: int = 4,
        hook_options: MemoryRecallHookOptions | None = None,
    ) -> MemoryRecallResult:
        """构建 recall pack，供 Agent/runtime 复用。"""

        _recall_start = time.monotonic()

        normalized_query = query.strip()
        selected_scope_ids = [item.strip() for item in scope_ids if item and item.strip()]
        normalized_hook_options = hook_options or MemoryRecallHookOptions()
        hook_trace = self._initialize_recall_hook_trace(
            query=normalized_query,
            hook_options=normalized_hook_options,
        )
        backend_status = await self._facade.get_backend_status()
        if not normalized_query or not selected_scope_ids:
            return MemoryRecallResult(
                query=normalized_query,
                expanded_queries=[],
                scope_ids=selected_scope_ids,
                hits=[],
                backend_status=backend_status,
                hook_trace=hook_trace,
            )

        policy = policy or MemoryAccessPolicy()
        expanded_queries = self._expand_recall_queries(normalized_query)
        degraded_reasons = self._recall_degraded_reasons(backend_status)
        collected: list[_Candidate] = []
        seen: set[str] = set()
        scope_hit_distribution: dict[str, int] = {}

        if self._should_use_backend_recall_contract(backend_status):
            search_options = self._build_backend_recall_search_options(
                expanded_queries=expanded_queries,
                hook_options=normalized_hook_options,
                hook_trace=hook_trace,
            )
            scope_results = await self._parallel_search_scopes(
                scope_ids=selected_scope_ids,
                query=normalized_query,
                policy=policy,
                limit=max(1, max(max_hits, per_scope_limit)),
                search_options=search_options,
                use_expanded=False,
            )
            collected, seen, scope_hit_distribution = self._collect_and_dedup(
                scope_ids=selected_scope_ids,
                scope_results=scope_results,
            )
            hook_trace.candidate_count = len(collected)
            hook_trace.delivered_count = min(len(collected), max(1, max_hits))
            selected_candidates = collected[: max(1, max_hits)]
        else:
            scope_results = await self._parallel_search_scopes(
                scope_ids=selected_scope_ids,
                query=normalized_query,
                policy=policy,
                limit=max(1, per_scope_limit),
                expanded_queries=expanded_queries,
                use_expanded=True,
            )
            collected, seen, scope_hit_distribution = self._collect_and_dedup(
                scope_ids=selected_scope_ids,
                scope_results=scope_results,
            )
            collected.sort(key=self._recall_sort_key)
            selected_candidates, hook_trace = await self._apply_recall_hooks(
                collected=collected,
                query=normalized_query,
                max_hits=max_hits,
                hook_options=normalized_hook_options,
                degraded_reasons=degraded_reasons,
            )

        selected = [item[-1] for item in selected_candidates]
        recall_hits: list[MemoryRecallHit] = []
        for hit in selected:
            recall_hits.append(await self._build_recall_hit(hit=hit, policy=policy))

        result = MemoryRecallResult(
            query=normalized_query,
            expanded_queries=expanded_queries,
            scope_ids=selected_scope_ids,
            hits=recall_hits,
            backend_status=backend_status,
            degraded_reasons=degraded_reasons,
            hook_trace=hook_trace,
        )

        _recall_latency_ms = int((time.monotonic() - _recall_start) * 1000)
        log.info(
            "memory_recall_completed",
            query=query[:100] if query else "",
            scope_count=len(selected_scope_ids),
            candidate_count=len(collected),
            delivered_count=len(result.hits),
            empty=len(result.hits) == 0,
            latency_ms=_recall_latency_ms,
            backend_used=self._backend.backend_id if self._backend else "sqlite_only",
            scope_hit_distribution=scope_hit_distribution,
        )

        return result

    # ------------------------------------------------------------------
    # 并行搜索 + 去重
    # ------------------------------------------------------------------

    async def _parallel_search_scopes(
        self,
        *,
        scope_ids: list[str],
        query: str,
        policy: MemoryAccessPolicy,
        limit: int,
        search_options: MemorySearchOptions | None = None,
        expanded_queries: list[str] | None = None,
        use_expanded: bool = False,
    ) -> list[list[_Candidate] | BaseException]:
        """对多个 scope 并行执行搜索，返回每个 scope 的结果列表。"""

        async def _search_one_scope(scope_index: int, scope_id: str) -> list[_Candidate]:
            results: list[_Candidate] = []
            if use_expanded and expanded_queries:
                for query_index, search_query in enumerate(expanded_queries):
                    hits = await self._facade.search_memory(
                        scope_id=scope_id,
                        query=search_query,
                        policy=policy,
                        limit=limit,
                    )
                    for ordinal, hit in enumerate(hits):
                        results.append((
                            scope_index,
                            query_index,
                            ordinal,
                            hit.model_copy(
                                update={
                                    "metadata": {
                                        **hit.metadata,
                                        "search_query": search_query,
                                    }
                                }
                            ),
                        ))
            else:
                hits = await self._facade.search_memory(
                    scope_id=scope_id,
                    query=query,
                    policy=policy,
                    limit=limit,
                    search_options=search_options,
                )
                for ordinal, hit in enumerate(hits):
                    resolved_search_query = str(hit.metadata.get("search_query", "")).strip()
                    results.append((
                        scope_index,
                        0,
                        ordinal,
                        hit.model_copy(
                            update={
                                "metadata": {
                                    **hit.metadata,
                                    "search_query": resolved_search_query or query,
                                    **({"backend_recall_contract": "memu_search_options_v1"} if not use_expanded else {}),
                                }
                            }
                        ),
                    ))
            return results

        tasks = [_search_one_scope(i, sid) for i, sid in enumerate(scope_ids)]
        return await asyncio.gather(*tasks, return_exceptions=True)  # type: ignore[return-value]

    @staticmethod
    def _collect_and_dedup(
        *,
        scope_ids: list[str],
        scope_results: list[list[_Candidate] | BaseException],
    ) -> tuple[list[_Candidate], set[str], dict[str, int]]:
        """将并行搜索结果合并去重，返回 (collected, seen, scope_hit_distribution)。"""

        collected: list[_Candidate] = []
        seen: set[str] = set()
        scope_hit_distribution: dict[str, int] = {}

        for i, result in enumerate(scope_results):
            if isinstance(result, BaseException):
                log.warning("recall_scope_failed", scope_id=scope_ids[i], error=str(result))
                scope_hit_distribution[scope_ids[i]] = 0
                continue
            hit_count = 0
            for item in result:
                _, _, _, hit = item
                if hit.record_id in seen:
                    continue
                seen.add(hit.record_id)
                collected.append(item)
                hit_count += 1
            scope_hit_distribution[scope_ids[i]] = hit_count

        return collected, seen, scope_hit_distribution

    # ------------------------------------------------------------------
    # Recall hooks
    # ------------------------------------------------------------------

    async def _apply_recall_hooks(
        self,
        *,
        collected: list[_Candidate],
        query: str,
        max_hits: int,
        hook_options: MemoryRecallHookOptions,
        degraded_reasons: list[str],
    ) -> tuple[list[_Candidate], MemoryRecallHookTrace]:
        candidates = list(collected)
        trace = self._initialize_recall_hook_trace(query=query, hook_options=hook_options)
        trace.candidate_count = len(candidates)
        if not candidates:
            return [], trace

        annotated_candidates: list[_Candidate] = []
        for candidate in candidates:
            overlap = self._recall_keyword_overlap(candidate[-1], trace.focus_terms)
            subject_score = self._recall_subject_match_score(candidate[-1], trace.subject_hint)
            annotated_candidates.append(
                self._annotate_recall_candidate(
                    candidate,
                    recall_keyword_overlap=overlap,
                    recall_subject_match=subject_score,
                )
            )
        candidates = annotated_candidates

        if (
            hook_options.post_filter_mode is MemoryRecallPostFilterMode.KEYWORD_OVERLAP
            and trace.focus_terms
        ):
            filtered = [
                candidate
                for candidate in candidates
                if int(candidate[-1].metadata.get("recall_keyword_overlap", 0) or 0)
                >= hook_options.min_keyword_overlap
            ]
            trace.filtered_count = len(candidates) - len(filtered)
            if filtered:
                candidates = filtered
            else:
                trace.fallback_applied = True
                if "recall_post_filter_fallback" not in degraded_reasons:
                    degraded_reasons.append("recall_post_filter_fallback")

        if hook_options.rerank_mode is MemoryRecallRerankMode.HEURISTIC and candidates:
            candidates = self._rerank_recall_candidates(candidates)

        elif hook_options.rerank_mode is MemoryRecallRerankMode.MODEL and candidates:
            if len(candidates) >= 2 and self._reranker_service is not None:
                candidate_texts = [
                    c[-1].summary or c[-1].subject_key or ""
                    for c in candidates
                ]
                rerank_result = await self._reranker_service.rerank(
                    query=query,
                    candidates=candidate_texts,
                )
                if rerank_result.degraded:
                    candidates = self._rerank_recall_candidates(candidates)
                    degraded_reasons.append(
                        f"reranker_degraded:{rerank_result.degraded_reason}"
                    )
                else:
                    scored = list(zip(rerank_result.scores, candidates))
                    scored.sort(key=lambda x: -x[0])
                    candidates = [
                        self._annotate_recall_candidate(
                            c,
                            recall_rerank_score=round(s, 4),
                            recall_rerank_mode="model",
                            recall_rerank_model=rerank_result.model_id,
                        )
                        for s, c in scored
                    ]
            else:
                if candidates:
                    candidates = self._rerank_recall_candidates(candidates)

        # --- Phase 3: Temporal Decay (US-8, FR-020) ---
        if hook_options.temporal_decay_enabled and candidates:
            candidates = self._apply_temporal_decay(
                candidates,
                half_life_days=hook_options.temporal_decay_half_life_days,
            )
            trace.temporal_decay_applied = True
            trace.temporal_decay_half_life_days = hook_options.temporal_decay_half_life_days

        # --- Phase 3: MMR 去重 (US-8, FR-021) ---
        if hook_options.mmr_enabled and len(candidates) > 1:
            before_count = len(candidates)
            candidates = self._apply_mmr_dedup(
                candidates,
                max_hits=max_hits,
                mmr_lambda=hook_options.mmr_lambda,
            )
            trace.mmr_applied = True
            trace.mmr_lambda = hook_options.mmr_lambda
            trace.mmr_removed_count = before_count - len(candidates)

        bounded_max_hits = max(1, max_hits)
        trace.delivered_count = min(len(candidates), bounded_max_hits)
        return candidates[:bounded_max_hits], trace

    # ------------------------------------------------------------------
    # Recall hit 构建
    # ------------------------------------------------------------------

    async def _build_recall_hit(
        self,
        *,
        hit: MemorySearchHit,
        policy: MemoryAccessPolicy,
    ) -> MemoryRecallHit:
        preview = ""
        evidence_refs: list[EvidenceRef] = []
        derived_refs: list[str] = []
        metadata = dict(hit.metadata)
        try:
            record = await self._facade.get_memory(
                hit.record_id,
                layer=hit.layer,
                policy=policy,
            )
            if record is not None:
                if hasattr(record, "content"):
                    preview = self._truncate_preview(str(getattr(record, "content", "")))
                elif hasattr(record, "summary"):
                    preview = self._truncate_preview(str(getattr(record, "summary", "")))
        except MemoryAccessDeniedError:
            preview = ""

        try:
            evidence = await self._facade.resolve_memory_evidence(
                MemoryEvidenceQuery(
                    record_id=hit.record_id,
                    layer=hit.layer,
                    scope_id=hit.scope_id,
                )
            )
            evidence_refs = [
                EvidenceRef(ref_id=ref_id, ref_type="artifact")
                for ref_id in evidence.artifact_refs[:6]
            ]
            evidence_refs.extend(
                EvidenceRef(ref_id=ref_id, ref_type="fragment")
                for ref_id in evidence.fragment_refs[:6]
            )
            derived_refs = evidence.derived_refs[:6]
            metadata.setdefault("proposal_refs", evidence.proposal_refs[:6])
            metadata.setdefault("maintenance_run_refs", evidence.maintenance_run_refs[:6])
        except Exception as exc:
            metadata.setdefault("evidence_error", str(exc))

        citation = self._build_recall_citation(hit)
        return MemoryRecallHit(
            record_id=hit.record_id,
            layer=hit.layer,
            scope_id=hit.scope_id,
            partition=hit.partition,
            summary=hit.summary,
            subject_key=hit.subject_key or "",
            search_query=str(hit.metadata.get("search_query", "")),
            citation=citation,
            content_preview=preview,
            evidence_refs=evidence_refs,
            derived_refs=derived_refs,
            created_at=hit.created_at,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Query 展开与关键词
    # ------------------------------------------------------------------

    @classmethod
    def _expand_recall_queries(cls, query: str) -> list[str]:
        normalized = " ".join(query.split()).strip()
        if not normalized:
            return []
        keywords = cls._extract_recall_keywords(normalized)
        candidates = [normalized]
        if keywords:
            candidates.append(" ".join(keywords[:3]))
            candidates.extend(keywords[:3])
        return list(dict.fromkeys(item for item in candidates if item))[:4]

    @staticmethod
    def _extract_recall_keywords(query: str) -> list[str]:
        import re

        candidates = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,}|[\u4e00-\u9fff]{2,12}", query)
        stopwords = {
            "请", "继续", "推进", "一下", "这个", "那个", "现在",
            "当前", "需要", "帮我", "我们", "你们", "问题",
        }
        cleaned: list[str] = []
        for item in candidates:
            token = re.sub(
                r"^(?:请|继续|推进|一下|把|将|帮我|再|当前|这个|那个|关于|并且|需要|想要)+",
                "",
                item,
            )
            token = re.sub(
                r"(?:请|继续|推进|一下|把|将|帮我|再|当前|这个|那个|关于|并且|需要|想要)+$",
                "",
                token,
            )
            token = token.strip()
            if not token or token in stopwords or len(token) < 2:
                continue
            cleaned.append(token)
        return list(dict.fromkeys(cleaned))

    # ------------------------------------------------------------------
    # Backend contract 判断与 search options 构建
    # ------------------------------------------------------------------

    def _should_use_backend_recall_contract(
        self,
        backend_status: MemoryBackendStatus,
    ) -> bool:
        return self._backend.backend_id == "memu" and backend_status.active_backend == "memu"

    @staticmethod
    def _build_backend_recall_search_options(
        *,
        expanded_queries: list[str],
        hook_options: MemoryRecallHookOptions,
        hook_trace: MemoryRecallHookTrace,
    ) -> MemorySearchOptions:
        return MemorySearchOptions(
            expanded_queries=list(expanded_queries),
            reasoning_target=hook_options.reasoning_target,
            expand_target=hook_options.expand_target,
            embedding_target=hook_options.embedding_target,
            rerank_target=hook_options.rerank_target,
            focus_terms=list(hook_trace.focus_terms),
            subject_hint=hook_trace.subject_hint,
            post_filter_mode=hook_options.post_filter_mode,
            rerank_mode=hook_options.rerank_mode,
            min_keyword_overlap=hook_options.min_keyword_overlap,
        )

    # ------------------------------------------------------------------
    # Hook trace 初始化 + focus terms 解析
    # ------------------------------------------------------------------

    @classmethod
    def _initialize_recall_hook_trace(
        cls,
        *,
        query: str,
        hook_options: MemoryRecallHookOptions,
    ) -> MemoryRecallHookTrace:
        focus_terms, subject_hint = cls._resolve_recall_focus_terms(
            query=query,
            hook_options=hook_options,
        )
        return MemoryRecallHookTrace(
            post_filter_mode=hook_options.post_filter_mode,
            rerank_mode=hook_options.rerank_mode,
            focus_terms=focus_terms,
            subject_hint=subject_hint,
        )

    @classmethod
    def _resolve_recall_focus_terms(
        cls,
        *,
        query: str,
        hook_options: MemoryRecallHookOptions,
    ) -> tuple[list[str], str]:
        subject_hint = " ".join(hook_options.subject_hint.split()).strip()
        focus_terms: list[str] = []
        for value in [*hook_options.focus_terms, subject_hint, query]:
            normalized = " ".join(str(value).split()).strip()
            if not normalized:
                continue
            if normalized not in focus_terms:
                focus_terms.append(normalized)
            for keyword in cls._extract_recall_keywords(normalized):
                if keyword not in focus_terms:
                    focus_terms.append(keyword)
        return focus_terms[:8], subject_hint

    # ------------------------------------------------------------------
    # Annotation / sort / rerank (heuristic)
    # ------------------------------------------------------------------

    @staticmethod
    def _annotate_recall_candidate(
        candidate: _Candidate,
        **metadata_updates: Any,
    ) -> _Candidate:
        scope_index, query_index, ordinal, hit = candidate
        metadata = dict(hit.metadata)
        metadata.update(metadata_updates)
        return (
            scope_index,
            query_index,
            ordinal,
            hit.model_copy(update={"metadata": metadata}),
        )

    @staticmethod
    def _recall_sort_key(item: _Candidate) -> tuple[int, int, int, int]:
        scope_index, query_index, ordinal, hit = item
        layer_priority = {
            MemoryLayer.SOR: 0,
            MemoryLayer.FRAGMENT: 1,
            MemoryLayer.VAULT: 2,
        }.get(hit.layer, 9)
        return scope_index, layer_priority, query_index, ordinal

    def _recall_rerank_score(
        self,
        candidate: _Candidate,
    ) -> float:
        scope_index, query_index, ordinal, hit = candidate
        overlap = int(hit.metadata.get("recall_keyword_overlap", 0) or 0)
        subject_match = int(hit.metadata.get("recall_subject_match", 0) or 0)
        layer_bonus = {
            MemoryLayer.SOR: 24,
            MemoryLayer.FRAGMENT: 16,
            MemoryLayer.VAULT: 8,
        }.get(hit.layer, 0)
        scope_bonus = max(0, 8 - scope_index * 2)
        query_bonus = max(0, 6 - query_index * 2)
        ordinal_bonus = max(0, 4 - ordinal)
        recency_bonus = max(
            0.0,
            min(
                4.0,
                (datetime.now(UTC) - hit.created_at).total_seconds() / -86400.0 + 4.0,
            ),
        )
        return (
            overlap * 100.0
            + subject_match * 20.0
            + layer_bonus
            + scope_bonus
            + query_bonus
            + ordinal_bonus
            + recency_bonus
        )

    def _rerank_recall_candidates(
        self,
        candidates: list[_Candidate],
    ) -> list[_Candidate]:
        scored: list[tuple[float, _Candidate]] = []
        for candidate in candidates:
            score = self._recall_rerank_score(candidate)
            scored.append(
                (
                    score,
                    self._annotate_recall_candidate(
                        candidate,
                        recall_rerank_score=round(score, 4),
                        recall_rerank_mode=MemoryRecallRerankMode.HEURISTIC.value,
                    ),
                )
            )
        scored.sort(
            key=lambda item: (
                -item[0],
                *self._recall_sort_key(item[1]),
            )
        )
        return [item[1] for item in scored]

    # ------------------------------------------------------------------
    # Keyword overlap / subject match
    # ------------------------------------------------------------------

    @classmethod
    def _recall_keyword_overlap(cls, hit: MemorySearchHit, focus_terms: list[str]) -> int:
        if not focus_terms:
            return 0
        parts = [
            hit.summary,
            hit.subject_key or "",
        ]
        normalized_text = " ".join(part.strip().lower() for part in parts if part.strip())
        tokens = {
            token.lower()
            for token in cls._extract_recall_keywords(" ".join(parts))
            if token.strip()
        }
        overlap = 0
        for term in focus_terms:
            normalized_term = term.strip().lower()
            if not normalized_term:
                continue
            if normalized_term in tokens or normalized_term in normalized_text:
                overlap += 1
        return overlap

    @classmethod
    def _recall_subject_match_score(cls, hit: MemorySearchHit, subject_hint: str) -> int:
        normalized_hint = subject_hint.strip().lower()
        if not normalized_hint:
            return 0
        haystacks = [
            (hit.subject_key or "").lower(),
            hit.summary.lower(),
        ]
        if any(normalized_hint in value for value in haystacks if value):
            return 2
        hint_keywords = {
            keyword.lower()
            for keyword in cls._extract_recall_keywords(subject_hint)
            if keyword.strip()
        }
        overlap = 0
        for keyword in hint_keywords:
            if any(keyword in value for value in haystacks if value):
                overlap += 1
        return 1 if overlap > 0 else 0

    # ------------------------------------------------------------------
    # Phase 3: Temporal Decay + MMR 去重 (US-8)
    # ------------------------------------------------------------------

    def _apply_temporal_decay(
        self,
        candidates: list[_Candidate],
        *,
        half_life_days: float = 30.0,
    ) -> list[_Candidate]:
        """对候选结果应用指数时间衰减。"""
        if not candidates:
            return []

        decay_constant = math.log(2) / max(half_life_days, 1.0)
        now = datetime.now(UTC)

        scored: list[tuple[float, _Candidate]] = []
        for candidate in candidates:
            hit = candidate[-1]
            age_seconds = (now - hit.created_at).total_seconds()
            age_days = max(0.0, age_seconds / 86400.0)

            decay_factor = math.exp(-decay_constant * age_days)
            existing_score = float(hit.metadata.get("recall_rerank_score", 1.0) or 1.0)
            adjusted_score = existing_score * decay_factor

            scored.append((
                adjusted_score,
                self._annotate_recall_candidate(
                    candidate,
                    recall_temporal_decay_factor=round(decay_factor, 4),
                    recall_decay_adjusted_score=round(adjusted_score, 4),
                ),
            ))

        scored.sort(key=lambda x: -x[0])
        return [item[1] for item in scored]

    def _apply_mmr_dedup(
        self,
        candidates: list[_Candidate],
        *,
        max_hits: int,
        mmr_lambda: float = 0.7,
    ) -> list[_Candidate]:
        """Maximal Marginal Relevance 去重。"""
        if len(candidates) <= 1:
            return candidates

        n = min(max_hits, len(candidates))

        texts = [
            (c[-1].summary or c[-1].subject_key or "").lower()
            for c in candidates
        ]
        token_sets = [set(text.split()) for text in texts]

        relevance_scores: list[float] = []
        for c in candidates:
            score = float(
                c[-1].metadata.get("recall_decay_adjusted_score")
                or c[-1].metadata.get("recall_rerank_score")
                or 1.0
            )
            relevance_scores.append(score)

        max_rel = max(relevance_scores) if relevance_scores else 1.0
        if max_rel > 0:
            norm_relevance = [s / max_rel for s in relevance_scores]
        else:
            norm_relevance = [1.0] * len(relevance_scores)

        selected_indices: list[int] = []
        remaining = set(range(len(candidates)))

        for _ in range(n):
            if not remaining:
                break

            best_idx = -1
            best_mmr = float("-inf")

            for idx in remaining:
                rel = norm_relevance[idx]
                max_sim = 0.0
                for sel_idx in selected_indices:
                    sim = self._jaccard_similarity(token_sets[idx], token_sets[sel_idx])
                    if sim > max_sim:
                        max_sim = sim

                mmr_score = mmr_lambda * rel - (1 - mmr_lambda) * max_sim
                if mmr_score > best_mmr:
                    best_mmr = mmr_score
                    best_idx = idx

            if best_idx >= 0:
                selected_indices.append(best_idx)
                remaining.discard(best_idx)

        return [
            self._annotate_recall_candidate(
                candidates[idx],
                recall_mmr_rank=rank,
            )
            for rank, idx in enumerate(selected_indices)
        ]

    @staticmethod
    def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
        if not set_a and not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    # ------------------------------------------------------------------
    # 辅助 / degraded reasons
    # ------------------------------------------------------------------

    @staticmethod
    def _recall_degraded_reasons(status: MemoryBackendStatus) -> list[str]:
        reasons: list[str] = []
        if status.state is not MemoryBackendState.HEALTHY:
            reasons.append(f"memory_backend_{status.state.value}")
        if status.pending_replay_count > 0:
            reasons.append("memory_sync_backlog")
        if status.failure_code:
            reasons.append(status.failure_code.lower())
        return reasons

    @staticmethod
    def _truncate_preview(text: str, limit: int = 240) -> str:
        value = " ".join(text.split())
        if len(value) <= limit:
            return value
        return value[:limit].rstrip() + "..."

    @staticmethod
    def _build_recall_citation(hit: MemorySearchHit) -> str:
        subject = hit.subject_key or hit.record_id
        return f"memory://{hit.scope_id}/{hit.layer.value}/{subject}"

    async def _search_via_store(
        self,
        scope_id: str,
        *,
        query: str | None,
        policy: MemoryAccessPolicy,
        limit: int,
        search_options: MemorySearchOptions | None = None,
    ) -> list[MemorySearchHit]:
        return await self._fallback_backend.search(
            scope_id,
            query=query,
            policy=policy,
            limit=limit,
            search_options=search_options,
        )
