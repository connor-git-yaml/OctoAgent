"""Feature 054: 内建 Memory Engine bridge。"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import math
import os
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
from octoagent.memory import (
    DerivedMemoryQuery,
    FragmentRecord,
    MemoryAccessPolicy,
    MemoryBackendState,
    MemoryBackendStatus,
    MemoryDerivedProjection,
    MemoryEvidenceProjection,
    MemoryEvidenceQuery,
    MemoryIngestBatch,
    MemoryIngestResult,
    MemoryMaintenanceCommand,
    MemoryMaintenanceCommandKind,
    MemoryMaintenanceRun,
    MemorySearchHit,
    MemorySearchOptions,
    MemorySyncBatch,
    MemorySyncResult,
    SorRecord,
    SqliteMemoryBackend,
    SqliteMemoryStore,
    VaultRecord,
)

from .config_wizard import load_config

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+")
_EMBED_DIM = 128
_CANDIDATE_LIMIT = 240
_QWEN_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
_QWEN_LAYER_ID = "builtin-qwen3-embedding-0.6b"
_HASH_LAYER_ID = "builtin-hash-bilingual"
_LEXICAL_LAYER_ID = "sqlite-metadata-lexical"
_QWEN_QUERY_PREFIX = "Instruct: Retrieve semantically relevant bilingual memory passages.\nQuery: "
_QWEN_RETRY_BACKOFF_SECONDS = 60.0
_EMBEDDING_PROXY_TIMEOUT_SECONDS = 20.0


@dataclass(slots=True)
class _BuiltinEmbeddingRuntimeState:
    preferred_model_id: str = _QWEN_MODEL_ID
    preferred_layer: str = _QWEN_LAYER_ID
    active_layer: str = _HASH_LAYER_ID
    active_mode: str = "builtin-hash-embedding-fallback"
    status: str = "fallback"
    summary: str = "当前未检测到本地 Qwen embedding runtime，已回退到双语 hash embedding。"
    fallback_reason: str = "未安装 sentence-transformers 本地 embedding runtime。"
    attempted_load: bool = False
    model_loaded: bool = False
    encoder: Any | None = None
    cache: dict[str, list[float]] = field(default_factory=dict)
    warmup_task: asyncio.Task[None] | None = None
    last_failure_monotonic: float = 0.0
    retry_after_seconds: float = _QWEN_RETRY_BACKOFF_SECONDS

    @property
    def uses_qwen(self) -> bool:
        return self.model_loaded and self.encoder is not None


@dataclass(slots=True)
class _ResolvedEmbeddingTarget:
    requested_target: str
    effective_target: str
    layer_id: str
    mode: str
    uses_qwen: bool = False
    uses_proxy_alias: bool = False
    uses_lexical_only: bool = False
    proxy_alias: str = ""
    warning: str = ""


def _tokenize(value: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_PATTERN.finditer(value.lower()):
        token = match.group(0)
        if not token:
            continue
        tokens.append(token)
        if any("\u4e00" <= char <= "\u9fff" for char in token) and len(token) > 1:
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
    return tokens


def _stable_index(token: str, *, dim: int = _EMBED_DIM) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


def _hash_embed(text: str, *, dim: int = _EMBED_DIM) -> list[float]:
    vector = [0.0] * dim
    for token in _tokenize(text):
        vector[_stable_index(token, dim=dim)] += 1.0
    norm = math.sqrt(sum(item * item for item in vector))
    if norm == 0:
        return vector
    return [item / norm for item in vector]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    size = min(len(left), len(right))
    if size <= 0:
        return 0.0
    return sum(left[index] * right[index] for index in range(size))


def _module_exists(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


class BuiltinMemUBridge:
    """内建 Memory Engine。

    这条路径不依赖外部 MemU 进程，默认提供内建 Qwen / hash fallback recall。
    facts / Vault 的最终治理仍由 canonical store 承担。
    """

    def __init__(
        self,
        store: SqliteMemoryStore,
        *,
        project_binding: str,
        project_root: Path,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._store = store
        self._sqlite_backend = SqliteMemoryBackend(store)
        self._project_binding = project_binding
        self._project_root = project_root
        self._environ = environ or dict(os.environ)
        self._embedding_runtime = _BuiltinEmbeddingRuntimeState()
        self._proxy_embedding_cache: dict[tuple[str, str], list[float]] = {}
        self._schedule_initial_qwen_warmup()

    async def is_available(self) -> bool:
        return True

    async def get_status(self) -> MemoryBackendStatus:
        runtime_state = self._probe_embedding_runtime()
        try:
            base_status = await self._sqlite_backend.get_status()
        except Exception:
            base_status = MemoryBackendStatus(
                backend_id="sqlite-metadata",
                memory_engine_contract_version="1.0.0",
                state=MemoryBackendState.HEALTHY,
                active_backend="sqlite-metadata",
                message="SQLite metadata fallback backend",
            )
        return base_status.model_copy(
            update={
                "backend_id": "memu",
                "memory_engine_contract_version": "1.0.0",
                "state": MemoryBackendState.HEALTHY,
                "active_backend": "memu",
                "message": "当前使用内建 Memory Engine（默认 Qwen3-Embedding-0.6B，本机未就绪时回退 hash embedding）。",
                "project_binding": self._project_binding,
                "index_health": {
                    **base_status.index_health,
                    "mode": runtime_state.active_mode,
                    "projection_store": "canonical-live",
                    "embedding_layer": runtime_state.active_layer,
                    "preferred_embedding_model_id": runtime_state.preferred_model_id,
                    "preferred_embedding_layer": runtime_state.preferred_layer,
                    "embedding_runtime_status": runtime_state.status,
                    "embedding_runtime_summary": runtime_state.summary,
                    "fallback_reason": runtime_state.fallback_reason,
                },
            }
        )

    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
        search_options: MemorySearchOptions | None = None,
    ) -> list[MemorySearchHit]:
        normalized_query = (query or "").strip()
        expanded_queries = [
            item.strip()
            for item in ((search_options.expanded_queries if search_options is not None else []) or [])
            if item and item.strip()
        ]
        if normalized_query:
            expanded_queries = [normalized_query, *expanded_queries]
        expanded_queries = list(dict.fromkeys(expanded_queries))
        if not expanded_queries:
            return []
        requested_embedding_target = (
            (search_options.embedding_target if search_options is not None else "").strip()
            or "engine-default"
        )
        resolved_target = self._resolve_embedding_target(requested_embedding_target)

        policy = policy or MemoryAccessPolicy()
        candidate_limit = max(limit * 16, _CANDIDATE_LIMIT)
        sor_records = await self._store.search_sor(
            scope_id,
            query=None,
            include_history=policy.include_history,
            limit=candidate_limit,
        )
        fragment_records = await self._store.list_fragments(
            scope_id,
            query=None,
            limit=candidate_limit,
        )
        vault_records: list[VaultRecord] = []
        if policy.allow_vault:
            vault_records = await self._store.search_vault(scope_id, query=None, limit=candidate_limit)

        candidates: list[MemorySearchHit] = [
            *(self._sqlite_backend._to_sor_hit(item) for item in sor_records),
            *(self._sqlite_backend._to_fragment_hit(item) for item in fragment_records),
            *(self._sqlite_backend._to_vault_hit(item) for item in vault_records),
        ]
        if not candidates:
            return []

        scored: list[tuple[float, int, MemorySearchHit]] = []
        focus_terms = [
            item.strip().lower()
            for item in ((search_options.focus_terms if search_options is not None else []) or [])
            if item and item.strip()
        ]
        subject_hint = (
            (search_options.subject_hint if search_options is not None else "").strip().lower()
        )
        min_keyword_overlap = (
            search_options.min_keyword_overlap if search_options is not None else 1
        )
        query_embeddings = await self._embed_queries(
            expanded_queries,
            resolved_target=resolved_target,
        )
        candidate_texts = [self._candidate_text(hit) for hit in candidates]
        candidate_embeddings = await self._embed_candidate_batch(
            candidate_texts,
            resolved_target=resolved_target,
        )

        for ordinal, hit in enumerate(candidates):
            candidate_text = candidate_texts[ordinal]
            candidate_tokens = set(_tokenize(candidate_text))
            candidate_embedding = candidate_embeddings[ordinal] if candidate_embeddings else []
            focus_match_count = sum(1 for item in focus_terms if item in candidate_text)
            subject_match = bool(subject_hint and subject_hint in candidate_text)

            best_score = -1.0
            best_query = expanded_queries[0]
            best_overlap = 0
            for search_query in expanded_queries:
                query_tokens = set(_tokenize(search_query))
                overlap = len(query_tokens.intersection(candidate_tokens))
                if resolved_target.uses_lexical_only:
                    score = float(overlap)
                else:
                    score = _cosine(query_embeddings[search_query], candidate_embedding)
                    score += min(overlap * 0.08, 0.4)
                if normalized_query and normalized_query.lower() in candidate_text:
                    score += 0.12
                if focus_match_count:
                    score += min(focus_match_count * 0.05, 0.2)
                if subject_match:
                    score += 0.16
                if score > best_score:
                    best_score = score
                    best_query = search_query
                    best_overlap = overlap

            if (
                search_options is not None
                and search_options.post_filter_mode.value == "keyword_overlap"
                and best_overlap < min_keyword_overlap
                and not focus_match_count
                and not subject_match
            ):
                continue

            scored.append(
                (
                    best_score,
                    ordinal,
                    hit.model_copy(
                        update={
                            "metadata": {
                                **hit.metadata,
                                "search_query": best_query,
                                "embedding_target": requested_embedding_target,
                                "resolved_embedding_target": resolved_target.effective_target,
                                "projection_store": "canonical-live",
                                "builtin_embedding_layer": resolved_target.layer_id,
                                "builtin_embedding_status": resolved_target.mode,
                                "builtin_embedding_warning": resolved_target.warning,
                            }
                        }
                    ),
                )
            )

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored[:limit]]

    async def sync_batch(self, batch: MemorySyncBatch) -> MemorySyncResult:
        result = await self._sqlite_backend.sync_batch(batch)
        return result.model_copy(update={"backend_state": MemoryBackendState.HEALTHY})

    async def ingest_batch(self, batch: MemoryIngestBatch) -> MemoryIngestResult:
        result = await self._sqlite_backend.ingest_batch(batch)
        return result.model_copy(update={"backend_state": MemoryBackendState.HEALTHY})

    async def list_derivations(
        self,
        query: DerivedMemoryQuery,
    ) -> MemoryDerivedProjection:
        projection = await self._sqlite_backend.list_derivations(query)
        return projection.model_copy(
            update={
                "backend_used": "memu",
                "backend_state": MemoryBackendState.HEALTHY,
                "degraded_reason": "",
            }
        )

    async def resolve_evidence(
        self,
        query: MemoryEvidenceQuery,
    ) -> MemoryEvidenceProjection:
        return await self._sqlite_backend.resolve_evidence(query)

    async def run_maintenance(
        self,
        command: MemoryMaintenanceCommand,
    ) -> MemoryMaintenanceRun:
        run = await self._sqlite_backend.run_maintenance(command)
        metadata = {
            **run.metadata,
            "projection_store": "shared-retrieval-platform",
            "embedding_layer": self._probe_embedding_runtime().active_layer,
        }
        if command.kind is MemoryMaintenanceCommandKind.REINDEX:
            metadata["reindex_mode"] = self._probe_embedding_runtime().active_mode
            target_profile = str(command.metadata.get("target_profile", "") or "").strip()
            if target_profile == "engine-default":
                self._kickoff_qwen_warmup(force=True)
        return run.model_copy(
            update={
                "backend_used": "memu",
                "backend_state": MemoryBackendState.HEALTHY,
                "error_summary": "" if command.kind is MemoryMaintenanceCommandKind.REINDEX else run.error_summary,
                "metadata": metadata,
            }
        )

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        await self._sqlite_backend.sync_fragment(fragment)

    async def sync_sor(self, record: SorRecord) -> None:
        await self._sqlite_backend.sync_sor(record)

    async def sync_vault(self, record: VaultRecord) -> None:
        await self._sqlite_backend.sync_vault(record)

    def _schedule_initial_qwen_warmup(self) -> None:
        if not _module_exists("sentence_transformers"):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._kickoff_qwen_warmup()

    @staticmethod
    def _candidate_text(hit: MemorySearchHit) -> str:
        return " ".join(
            part
            for part in [
                hit.summary,
                hit.subject_key or "",
                str(hit.metadata.get("content_preview", "") or ""),
                str(hit.metadata.get("content", "") or ""),
            ]
            if part
        ).lower()

    def _probe_embedding_runtime(self) -> _BuiltinEmbeddingRuntimeState:
        runtime_state = self._embedding_runtime
        if runtime_state.warmup_task is not None and runtime_state.warmup_task.done():
            runtime_state.warmup_task = None
        if runtime_state.model_loaded:
            runtime_state.status = "ready"
            runtime_state.active_layer = _QWEN_LAYER_ID
            runtime_state.active_mode = "builtin-qwen3-embedding"
            runtime_state.summary = (
                "当前优先使用内建 Qwen3-Embedding-0.6B 做双语语义检索。"
            )
            runtime_state.fallback_reason = ""
            return runtime_state
        if runtime_state.warmup_task is not None:
            runtime_state.status = "warming"
            runtime_state.active_layer = _HASH_LAYER_ID
            runtime_state.active_mode = "builtin-hash-embedding-fallback"
            runtime_state.summary = (
                "当前正在后台预热 Qwen3-Embedding-0.6B；这段时间先继续使用双语 hash embedding。"
            )
            return runtime_state
        if _module_exists("sentence_transformers"):
            if (
                runtime_state.last_failure_monotonic
                and time.monotonic()
                < runtime_state.last_failure_monotonic + runtime_state.retry_after_seconds
            ):
                runtime_state.status = "fallback"
                runtime_state.active_layer = _HASH_LAYER_ID
                runtime_state.active_mode = "builtin-hash-embedding-fallback"
                runtime_state.summary = (
                    "Qwen3-Embedding-0.6B 上次预热失败，当前先继续使用双语 hash embedding；稍后会自动重试。"
                )
                if not runtime_state.fallback_reason:
                    runtime_state.fallback_reason = "Qwen embedding runtime 上次预热失败。"
                return runtime_state
            runtime_state.status = "available_on_demand"
            runtime_state.summary = (
                "当前已检测到本地 embedding runtime；系统会在后台预热 Qwen3-Embedding-0.6B。"
            )
            runtime_state.fallback_reason = ""
            return runtime_state
        runtime_state.status = "fallback"
        runtime_state.summary = (
            "当前未检测到本地 Qwen embedding runtime，语义检索会先回退到双语 hash embedding。"
        )
        runtime_state.fallback_reason = "未安装 sentence-transformers 本地 embedding runtime。"
        runtime_state.active_layer = _HASH_LAYER_ID
        runtime_state.active_mode = "builtin-hash-embedding-fallback"
        return runtime_state

    def _kickoff_qwen_warmup(self, *, force: bool = False) -> _BuiltinEmbeddingRuntimeState:
        runtime_state = self._probe_embedding_runtime()
        if runtime_state.model_loaded or runtime_state.warmup_task is not None:
            return runtime_state
        if runtime_state.status == "fallback":
            if not force:
                return runtime_state
            if not _module_exists("sentence_transformers"):
                return runtime_state
        if (
            not force
            and runtime_state.last_failure_monotonic
            and time.monotonic()
            < runtime_state.last_failure_monotonic + runtime_state.retry_after_seconds
        ):
            return runtime_state
        runtime_state.attempted_load = True
        runtime_state.warmup_task = asyncio.create_task(self._warmup_qwen_runtime())
        return self._probe_embedding_runtime()

    async def _warmup_qwen_runtime(self) -> None:
        runtime_state = self._embedding_runtime
        try:
            module = await asyncio.to_thread(self._load_sentence_transformers_module)
            encoder = await asyncio.to_thread(
                module.SentenceTransformer,
                _QWEN_MODEL_ID,
                trust_remote_code=True,
                device="cpu",
            )
        except Exception as exc:
            runtime_state.status = "fallback"
            runtime_state.active_layer = _HASH_LAYER_ID
            runtime_state.active_mode = "builtin-hash-embedding-fallback"
            runtime_state.summary = (
                "当前尝试加载 Qwen3-Embedding-0.6B 失败，已自动回退到双语 hash embedding。"
            )
            runtime_state.fallback_reason = str(exc)[:240]
            runtime_state.encoder = None
            runtime_state.model_loaded = False
            runtime_state.last_failure_monotonic = time.monotonic()
            runtime_state.warmup_task = None
            return
        runtime_state.encoder = encoder
        runtime_state.model_loaded = True
        runtime_state.status = "ready"
        runtime_state.active_layer = _QWEN_LAYER_ID
        runtime_state.active_mode = "builtin-qwen3-embedding"
        runtime_state.summary = "当前优先使用内建 Qwen3-Embedding-0.6B 做双语语义检索。"
        runtime_state.fallback_reason = ""
        runtime_state.last_failure_monotonic = 0.0
        runtime_state.warmup_task = None

    def _load_sentence_transformers_module(self) -> ModuleType:
        import sentence_transformers

        return sentence_transformers

    async def _embed_queries(
        self,
        queries: Sequence[str],
        *,
        resolved_target: _ResolvedEmbeddingTarget,
    ) -> dict[str, list[float]]:
        if resolved_target.uses_lexical_only:
            return {item: [] for item in queries}
        if resolved_target.uses_proxy_alias:
            vectors = await self._embed_texts_with_proxy_alias(
                list(queries),
                target_alias=resolved_target.proxy_alias,
                is_query=True,
            )
        elif resolved_target.uses_qwen:
            encoder = self._embedding_runtime.encoder
            assert encoder is not None
            prefixed_queries = [_QWEN_QUERY_PREFIX + item for item in queries]
            vectors = [
                [float(value) for value in vector]
                for vector in await asyncio.to_thread(
                    encoder.encode,
                    prefixed_queries,
                    normalize_embeddings=True,
                )
            ]
        else:
            vectors = [_hash_embed(item) for item in queries]
        return {
            query: [float(value) for value in vector]
            for query, vector in zip(queries, vectors, strict=False)
        }

    async def _embed_candidate_batch(
        self,
        candidate_texts: Sequence[str],
        *,
        resolved_target: _ResolvedEmbeddingTarget,
    ) -> list[list[float]]:
        if resolved_target.uses_lexical_only:
            return []
        if resolved_target.uses_proxy_alias:
            return await self._embed_texts_with_proxy_alias(
                list(candidate_texts),
                target_alias=resolved_target.proxy_alias,
                is_query=False,
            )
        if resolved_target.uses_qwen:
            runtime_state = self._embedding_runtime
            encoder = runtime_state.encoder
            assert encoder is not None
            pending_texts: list[str] = []
            pending_indexes: list[int] = []
            vectors: list[list[float] | None] = [None] * len(candidate_texts)
            for index, candidate_text in enumerate(candidate_texts):
                cache_key = hashlib.sha1(candidate_text.encode("utf-8")).hexdigest()
                cached = runtime_state.cache.get(cache_key)
                if cached is not None:
                    vectors[index] = cached
                    continue
                pending_indexes.append(index)
                pending_texts.append(candidate_text)
            if pending_texts:
                generated_vectors = [
                    [float(value) for value in vector]
                    for vector in await asyncio.to_thread(
                        encoder.encode,
                        pending_texts,
                        normalize_embeddings=True,
                    )
                ]
                for index, vector in zip(pending_indexes, generated_vectors, strict=False):
                    cache_key = hashlib.sha1(candidate_texts[index].encode("utf-8")).hexdigest()
                    runtime_state.cache[cache_key] = vector
                    vectors[index] = vector
            return [vector or _hash_embed(candidate_texts[index]) for index, vector in enumerate(vectors)]
        return [_hash_embed(item) for item in candidate_texts]

    def _resolve_embedding_target(self, requested_target: str) -> _ResolvedEmbeddingTarget:
        normalized = requested_target.strip() or "engine-default"
        if normalized == "sqlite-metadata":
            return _ResolvedEmbeddingTarget(
                requested_target=normalized,
                effective_target="sqlite-metadata",
                layer_id=_LEXICAL_LAYER_ID,
                mode="lexical-metadata",
                uses_lexical_only=True,
            )
        if normalized == "engine-default":
            runtime_state = self._probe_embedding_runtime()
            if runtime_state.model_loaded:
                return _ResolvedEmbeddingTarget(
                    requested_target=normalized,
                    effective_target="engine-default",
                    layer_id=_QWEN_LAYER_ID,
                    mode="builtin-qwen3-embedding",
                    uses_qwen=True,
                )
            self._kickoff_qwen_warmup()
            runtime_state = self._probe_embedding_runtime()
            return _ResolvedEmbeddingTarget(
                requested_target=normalized,
                effective_target="engine-default",
                layer_id=_HASH_LAYER_ID,
                mode=runtime_state.active_mode,
                warning=runtime_state.fallback_reason,
            )
        proxy_check = self._resolve_proxy_alias_target(normalized)
        if proxy_check is not None:
            return _ResolvedEmbeddingTarget(
                requested_target=normalized,
                effective_target=normalized,
                layer_id=f"proxy-alias:{normalized}",
                mode="proxy-alias-embedding",
                uses_proxy_alias=True,
                proxy_alias=normalized,
            )
        fallback = self._resolve_embedding_target("engine-default")
        fallback.requested_target = normalized
        fallback.warning = (
            f"当前无法启用 embedding alias {normalized}，已先回退到 {fallback.layer_id}。"
        )
        return fallback

    def _resolve_proxy_alias_target(self, target_alias: str) -> str | None:
        config = load_config(self._project_root)
        if config is None or config.runtime.llm_mode != "litellm":
            return None
        alias = config.model_aliases.get(target_alias)
        if alias is None:
            return None
        provider = config.get_provider(alias.provider)
        if provider is None or not provider.enabled:
            return None
        return target_alias

    async def _embed_texts_with_proxy_alias(
        self,
        texts: list[str],
        *,
        target_alias: str,
        is_query: bool,
    ) -> list[list[float]]:
        if not texts:
            return []
        config = load_config(self._project_root)
        if config is None or config.runtime.llm_mode != "litellm":
            return [_hash_embed(item) for item in texts]
        request_texts = (
            [_QWEN_QUERY_PREFIX + item for item in texts] if is_query else list(texts)
        )
        vectors: list[list[float] | None] = [None] * len(request_texts)
        uncached_texts: list[str] = []
        uncached_indexes: list[int] = []
        for index, item in enumerate(request_texts):
            cache_key = (target_alias, hashlib.sha1(item.encode("utf-8")).hexdigest())
            cached = self._proxy_embedding_cache.get(cache_key)
            if cached is not None:
                vectors[index] = cached
                continue
            uncached_indexes.append(index)
            uncached_texts.append(item)
        if uncached_texts:
            proxy_url = config.runtime.litellm_proxy_url.rstrip("/")
            proxy_key = self._environ.get(config.runtime.master_key_env, "").strip() or "no-key"
            headers = {
                "Authorization": f"Bearer {proxy_key}",
                "Content-Type": "application/json",
            }
            try:
                async with httpx.AsyncClient(timeout=_EMBEDDING_PROXY_TIMEOUT_SECONDS) as client:
                    response = await client.post(
                        f"{proxy_url}/v1/embeddings",
                        headers=headers,
                        json={
                            "model": target_alias,
                            "input": uncached_texts,
                            "encoding_format": "float",
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
            except Exception:
                return [_hash_embed(item) for item in texts]
            data = sorted(payload.get("data", []), key=lambda item: int(item.get("index", 0)))
            generated_vectors = [
                [float(value) for value in item.get("embedding", [])]
                for item in data
            ]
            for index, vector in zip(uncached_indexes, generated_vectors, strict=False):
                cache_key = (
                    target_alias,
                    hashlib.sha1(request_texts[index].encode("utf-8")).hexdigest(),
                )
                self._proxy_embedding_cache[cache_key] = vector
                vectors[index] = vector
        return [vector or _hash_embed(texts[index]) for index, vector in enumerate(vectors)]
