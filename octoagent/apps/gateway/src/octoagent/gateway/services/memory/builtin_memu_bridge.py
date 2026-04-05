"""Feature 054/063: 内建 Memory Engine bridge + LanceDB 语义检索。

写入路径:  SQLite (canonical) + LanceDB (向量 + FTS 索引)
检索路径:  Qwen3 可用时 → LanceDB hybrid (0.7 vector + 0.3 BM25)
           Qwen3 不可用时 → LanceDB FTS-only (纯 BM25)
降级链:    Qwen3+BM25 > 纯 BM25 > 报错（无 SQLite LIKE fallback）
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import jieba
import pyarrow as pa
import structlog
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

_log = structlog.get_logger()

# ── Embedding 常量 ──────────────────────────────────────────────
_QWEN_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
_QWEN_LAYER_ID = "builtin-qwen3-embedding-0.6b"
_BM25_LAYER_ID = "lancedb-fts-bm25"
_LEXICAL_LAYER_ID = "sqlite-metadata-lexical"
_QWEN_QUERY_PREFIX = "Instruct: Retrieve semantically relevant bilingual memory passages.\nQuery: "
_QWEN_RETRY_BACKOFF_SECONDS = 60.0
_EMBEDDING_PROXY_TIMEOUT_SECONDS = 20.0

# ── LanceDB 常量 ───────────────────────────────────────────────
_REINDEX_BATCH_SIZE = 64
_LANCEDB_DEFAULT_DIM = 1024  # Qwen3-Embedding-0.6B 输出维度


def _tokenize_for_fts(text: str) -> str:
    """用 jieba 搜索模式分词，返回空格分隔的 token 序列（供 LanceDB FTS 索引）。"""
    return " ".join(jieba.cut_for_search(text))


def _module_exists(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _lancedb_table_name(dim: int) -> str:
    return f"memory_vectors_{dim}"


def _build_table_schema(dim: int) -> pa.Schema:
    """构建 LanceDB 表 schema。"""
    return pa.schema([
        pa.field("record_id", pa.string()),
        pa.field("layer", pa.string()),
        pa.field("scope_id", pa.string()),
        pa.field("partition", pa.string()),
        pa.field("subject_key", pa.string()),
        pa.field("content_text", pa.string()),
        pa.field("text_tokens", pa.string()),
        pa.field("summary", pa.string()),
        pa.field("status", pa.string()),
        pa.field("version", pa.int32()),
        pa.field("created_at", pa.string()),
        pa.field("embed_model", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), dim)),
    ])


# ── Embedding 运行时状态 ────────────────────────────────────────

@dataclass(slots=True)
class _BuiltinEmbeddingRuntimeState:
    preferred_model_id: str = _QWEN_MODEL_ID
    preferred_layer: str = _QWEN_LAYER_ID
    active_layer: str = _BM25_LAYER_ID
    active_mode: str = "lancedb-fts-bm25-fallback"
    status: str = "fallback"
    summary: str = "当前未检测到本地 Qwen embedding runtime，已回退到 BM25 全文检索。"
    fallback_reason: str = "未安装 sentence-transformers 本地 embedding runtime。"
    attempted_load: bool = False
    model_loaded: bool = False
    encoder: Any | None = None
    embed_dim: int = _LANCEDB_DEFAULT_DIM
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


# ── BuiltinMemUBridge ──────────────────────────────────────────

class BuiltinMemUBridge:
    """内建 Memory Engine + LanceDB 语义检索。

    - 写入：SQLite（canonical）+ LanceDB（向量 + FTS 索引）
    - 检索：Qwen3 可用时 hybrid (0.7 vector + 0.3 BM25)，否则纯 BM25
    - 不使用 hash embedding fallback（纯 BM25 效果更优）
    """

    def __init__(
        self,
        store: SqliteMemoryStore,
        *,
        project_binding: str,
        project_root: Path,
        lancedb_dir: Path,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._store = store
        self._sqlite_backend = SqliteMemoryBackend(store)
        self._project_binding = project_binding
        self._project_root = project_root
        self._lancedb_dir = lancedb_dir
        self._environ = environ or dict(os.environ)
        self._embedding_runtime = _BuiltinEmbeddingRuntimeState()
        self._proxy_embedding_cache: dict[tuple[str, str], list[float]] = {}

        # LanceDB 异步连接（延迟初始化）
        self._lancedb_conn: Any | None = None
        self._lancedb_table: Any | None = None
        self._fts_index_created: bool = False
        self._reindex_in_progress: bool = False

        self._schedule_initial_qwen_warmup()

    # ── 公共接口 ───────────────────────────────────────────────

    @property
    def backend_id(self) -> str:
        return "memu"

    @property
    def memory_engine_contract_version(self) -> str:
        return "1.0.0"

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
                "message": "当前使用内建 Memory Engine（LanceDB 混合检索 + Qwen3-Embedding-0.6B）。",
                "project_binding": self._project_binding,
                "index_health": {
                    **base_status.index_health,
                    "mode": runtime_state.active_mode,
                    "projection_store": "lancedb-hybrid",
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
        """LanceDB 混合检索：Qwen3 可用时 hybrid，否则纯 BM25。"""
        normalized_query = (query or "").strip()
        if not normalized_query:
            # 无查询时返回最近记录（从 SQLite 读取）
            return await self._sqlite_backend.search(
                scope_id, query=None, policy=policy, limit=limit,
                search_options=search_options,
            )

        table = await self._ensure_table()
        if table is None:
            # 表不存在（首次启动，尚未 sync 数据）→ 触发后台 reindex + 返回空
            self._trigger_background_reindex(scope_id)
            return []

        policy = policy or MemoryAccessPolicy()

        # 构建 metadata filter
        where_parts = [f"scope_id = '{_escape_sql(scope_id)}'"]
        if not policy.include_history:
            where_parts.append("(layer != 'sor' OR status = 'current')")
        where_clause = " AND ".join(where_parts)

        # 构建 FTS 查询（含 focus_terms / subject_hint 扩展）
        fts_parts = [normalized_query]
        if search_options and search_options.focus_terms:
            fts_parts.extend(
                t.strip() for t in search_options.focus_terms if t and t.strip()
            )
        if search_options and search_options.subject_hint:
            hint = search_options.subject_hint.strip()
            if hint:
                fts_parts.append(hint)
        fts_query_text = _tokenize_for_fts(" ".join(fts_parts))

        # 尝试计算 query embedding
        requested_target = (
            (search_options.embedding_target if search_options is not None else "").strip()
            or "engine-default"
        )
        resolved_target = self._resolve_embedding_target(requested_target)
        query_vec = await self._try_embed_query(normalized_query, resolved_target)

        try:
            if query_vec is not None:
                # Tier 1: Hybrid search (Qwen3 + BM25)
                import lancedb.rerankers
                reranker = lancedb.rerankers.LinearCombinationReranker(weight=0.7)
                vq = await table.search(query_vec)
                hybrid = vq.nearest_to_text(fts_query_text, columns="text_tokens")
                results = await (
                    hybrid.rerank(reranker=reranker)
                    .where(where_clause)
                    .limit(limit)
                    .to_list()
                )
            else:
                # Tier 2: FTS-only (纯 BM25)
                fq = await table.search(
                    fts_query_text,
                    query_type="fts",
                    fts_columns=["text_tokens"],
                )
                results = await fq.where(where_clause).limit(limit).to_list()
        except Exception as exc:
            _log.warning("lancedb_search_error", error=str(exc)[:200])
            return []

        return [
            self._row_to_search_hit(row, resolved_target, requested_target)
            for row in results
        ]

    async def sync_batch(self, batch: MemorySyncBatch) -> MemorySyncResult:
        """SQLite 同步 + LanceDB upsert（向量 + FTS）。"""
        result = await self._sqlite_backend.sync_batch(batch)

        # 收集需要写入 LanceDB 的记录
        records = []
        for frag in (batch.fragments or []):
            content = frag.content or ""
            records.append({
                "record_id": frag.fragment_id,
                "layer": "fragment",
                "scope_id": batch.scope_id,
                "partition": frag.partition or "",
                "subject_key": "",
                "content_text": content,
                "text_tokens": _tokenize_for_fts(content),
                "summary": content[:200],
                "status": "",
                "version": 0,
                "created_at": frag.created_at.isoformat() if frag.created_at else "",
                "embed_model": "",
            })
        for sor in (batch.sor_records or []):
            content = f"{sor.subject_key or ''}: {sor.content or ''}"
            records.append({
                "record_id": sor.memory_id,
                "layer": "sor",
                "scope_id": batch.scope_id,
                "partition": sor.partition or "",
                "subject_key": sor.subject_key or "",
                "content_text": content,
                "text_tokens": _tokenize_for_fts(content),
                "summary": (sor.summary or sor.content or "")[:200],
                "status": sor.status or "current",
                "version": sor.version if hasattr(sor, "version") else 0,
                "created_at": sor.updated_at.isoformat() if hasattr(sor, "updated_at") and sor.updated_at else "",
                "embed_model": "",
            })
        for vault in (batch.vault_records or []):
            content = f"{vault.subject_key or ''}: {vault.summary or ''}"
            records.append({
                "record_id": vault.vault_id,
                "layer": "vault",
                "scope_id": batch.scope_id,
                "partition": "",
                "subject_key": vault.subject_key or "",
                "content_text": content,
                "text_tokens": _tokenize_for_fts(content),
                "summary": (vault.summary or "")[:200],
                "status": "",
                "version": 0,
                "created_at": vault.created_at.isoformat() if hasattr(vault, "created_at") and vault.created_at else "",
                "embed_model": "",
            })

        if records:
            await self._upsert_to_lancedb(records)

        # 处理 tombstones
        if batch.tombstones:
            await self._delete_from_lancedb([t.record_id for t in batch.tombstones])

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
            "projection_store": "lancedb-hybrid",
            "embedding_layer": self._probe_embedding_runtime().active_layer,
        }

        if command.kind is MemoryMaintenanceCommandKind.REINDEX:
            scope_id = str(command.metadata.get("scope_id", "") or "").strip()
            if scope_id:
                await self._reindex_from_sqlite(scope_id)
                metadata["reindex_mode"] = "lancedb-full-reindex"
                metadata["reindex_scope"] = scope_id
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
        content = fragment.content or ""
        await self._upsert_to_lancedb([{
            "record_id": fragment.fragment_id,
            "layer": "fragment",
            "scope_id": fragment.scope_id,
            "partition": fragment.partition or "",
            "subject_key": "",
            "content_text": content,
            "text_tokens": _tokenize_for_fts(content),
            "summary": content[:200],
            "status": "",
            "version": 0,
            "created_at": fragment.created_at.isoformat() if fragment.created_at else "",
            "embed_model": "",
        }])

    async def sync_sor(self, record: SorRecord) -> None:
        await self._sqlite_backend.sync_sor(record)
        content = f"{record.subject_key or ''}: {record.content or ''}"
        await self._upsert_to_lancedb([{
            "record_id": record.memory_id,
            "layer": "sor",
            "scope_id": record.scope_id,
            "partition": record.partition or "",
            "subject_key": record.subject_key or "",
            "content_text": content,
            "text_tokens": _tokenize_for_fts(content),
            "summary": (record.summary or record.content or "")[:200],
            "status": record.status or "current",
            "version": record.version if hasattr(record, "version") else 0,
            "created_at": record.updated_at.isoformat() if hasattr(record, "updated_at") and record.updated_at else "",
            "embed_model": "",
        }])

    async def sync_vault(self, record: VaultRecord) -> None:
        await self._sqlite_backend.sync_vault(record)
        content = f"{record.subject_key or ''}: {record.summary or ''}"
        await self._upsert_to_lancedb([{
            "record_id": record.vault_id,
            "layer": "vault",
            "scope_id": record.scope_id,
            "partition": "",
            "subject_key": record.subject_key or "",
            "content_text": content,
            "text_tokens": _tokenize_for_fts(content),
            "summary": (record.summary or "")[:200],
            "status": "",
            "version": 0,
            "created_at": record.created_at.isoformat() if hasattr(record, "created_at") and record.created_at else "",
            "embed_model": "",
        }])

    # ── LanceDB 内部方法 ──────────────────────────────────────

    async def _get_lancedb_conn(self) -> Any:
        """获取或创建 LanceDB 异步连接。"""
        if self._lancedb_conn is None:
            import lancedb as _lancedb
            self._lancedb_dir.mkdir(parents=True, exist_ok=True)
            self._lancedb_conn = await _lancedb.connect_async(str(self._lancedb_dir))
        return self._lancedb_conn

    async def _ensure_table(self) -> Any | None:
        """确保 LanceDB 表存在，返回 AsyncTable 或 None（无表且无数据）。"""
        if self._lancedb_table is not None:
            return self._lancedb_table

        conn = await self._get_lancedb_conn()
        runtime = self._embedding_runtime
        dim = runtime.embed_dim if runtime.uses_qwen else _LANCEDB_DEFAULT_DIM
        table_name = _lancedb_table_name(dim)

        try:
            table_names = await conn.list_tables()
            if table_name in table_names:
                self._lancedb_table = await conn.open_table(table_name)
                await self._ensure_fts_index()
                return self._lancedb_table
        except Exception as exc:
            _log.warning("lancedb_open_table_error", error=str(exc)[:200])

        # 表不存在 → 如果有数据则创建，否则返回 None
        return None

    async def _ensure_or_create_table(self, dim: int) -> Any:
        """确保表存在，不存在则创建。处理并发创建的竞态条件。"""
        if self._lancedb_table is not None:
            return self._lancedb_table

        conn = await self._get_lancedb_conn()
        table_name = _lancedb_table_name(dim)

        try:
            table_names = await conn.list_tables()
            if table_name in table_names:
                self._lancedb_table = await conn.open_table(table_name)
            else:
                schema = _build_table_schema(dim)
                try:
                    self._lancedb_table = await conn.create_table(table_name, schema=schema)
                    _log.info("lancedb_table_created", table=table_name, dim=dim)
                except Exception:
                    # 竞态条件：另一个实例已先创建了表，直接打开
                    self._lancedb_table = await conn.open_table(table_name)
        except Exception as exc:
            _log.error("lancedb_table_error", error=str(exc)[:200])
            raise

        await self._ensure_fts_index()
        return self._lancedb_table

    async def _ensure_fts_index(self) -> None:
        """确保 FTS 索引存在。"""
        if self._fts_index_created or self._lancedb_table is None:
            return
        try:
            import lancedb.index as _idx
            fts_config = _idx.FTS(
                stem=False,
                remove_stop_words=False,
                ascii_folding=False,
            )
            await self._lancedb_table.create_index(
                "text_tokens", config=fts_config, replace=True,
            )
            self._fts_index_created = True
            _log.info("lancedb_fts_index_created")
        except Exception as exc:
            _log.warning("lancedb_fts_index_error", error=str(exc)[:200])

    async def _upsert_to_lancedb(self, records: list[dict[str, Any]]) -> None:
        """将记录 embed 后 upsert 到 LanceDB。"""
        if not records:
            return
        try:
            # 计算 embedding
            texts = [r["content_text"] for r in records]
            embeddings = await self._try_embed_batch(texts)
            dim = len(embeddings[0]) if embeddings and embeddings[0] else _LANCEDB_DEFAULT_DIM

            table = await self._ensure_or_create_table(dim)
            embed_model = self._current_embed_model_label()

            for rec, vec in zip(records, embeddings):
                rec["vector"] = vec
                rec["embed_model"] = embed_model

            await table.merge_insert("record_id").when_matched_update_all().when_not_matched_insert_all().execute(records)
        except Exception as exc:
            _log.warning("lancedb_upsert_error", error=str(exc)[:200], count=len(records))

    async def _delete_from_lancedb(self, record_ids: list[str]) -> None:
        """从 LanceDB 删除记录。"""
        if not record_ids:
            return
        table = await self._ensure_table()
        if table is None:
            return
        try:
            for rid in record_ids:
                await table.delete(f"record_id = '{_escape_sql(rid)}'")
        except Exception as exc:
            _log.warning("lancedb_delete_error", error=str(exc)[:200])

    async def _reindex_from_sqlite(self, scope_id: str) -> None:
        """从 SQLite 全量读取 → embed → 写入 LanceDB + 重建 FTS 索引。"""
        if self._reindex_in_progress:
            _log.info("reindex_already_in_progress")
            return
        self._reindex_in_progress = True
        try:
            _log.info("reindex_started", scope_id=scope_id)

            all_sor = await self._store.search_sor(
                scope_id, query=None, include_history=True, limit=999999,
            )
            all_frag = await self._store.list_fragments(
                scope_id, query=None, limit=999999,
            )
            all_vault = await self._store.search_vault(
                scope_id, query=None, limit=999999,
            )

            records: list[dict[str, Any]] = []
            for sor in all_sor:
                content = f"{sor.subject_key or ''}: {sor.content or ''}"
                records.append({
                    "record_id": sor.memory_id,
                    "layer": "sor",
                    "scope_id": scope_id,
                    "partition": sor.partition or "",
                    "subject_key": sor.subject_key or "",
                    "content_text": content,
                    "text_tokens": _tokenize_for_fts(content),
                    "summary": (sor.summary or sor.content or "")[:200],
                    "status": sor.status or "current",
                    "version": sor.version if hasattr(sor, "version") else 0,
                    "created_at": sor.updated_at.isoformat() if hasattr(sor, "updated_at") and sor.updated_at else "",
                    "embed_model": "",
                })
            for frag in all_frag:
                content = frag.content or ""
                records.append({
                    "record_id": frag.fragment_id,
                    "layer": "fragment",
                    "scope_id": scope_id,
                    "partition": frag.partition or "",
                    "subject_key": "",
                    "content_text": content,
                    "text_tokens": _tokenize_for_fts(content),
                    "summary": content[:200],
                    "status": "",
                    "version": 0,
                    "created_at": frag.created_at.isoformat() if frag.created_at else "",
                    "embed_model": "",
                })
            for vault in all_vault:
                content = f"{vault.subject_key or ''}: {vault.summary or ''}"
                records.append({
                    "record_id": vault.vault_id,
                    "layer": "vault",
                    "scope_id": scope_id,
                    "partition": "",
                    "subject_key": vault.subject_key or "",
                    "content_text": content,
                    "text_tokens": _tokenize_for_fts(content),
                    "summary": (vault.summary or "")[:200],
                    "status": "",
                    "version": 0,
                    "created_at": vault.created_at.isoformat() if hasattr(vault, "created_at") and vault.created_at else "",
                    "embed_model": "",
                })

            if not records:
                _log.info("reindex_no_records", scope_id=scope_id)
                return

            # 分批 embed + upsert
            for i in range(0, len(records), _REINDEX_BATCH_SIZE):
                batch = records[i : i + _REINDEX_BATCH_SIZE]
                await self._upsert_to_lancedb(batch)

            # 重建 FTS 索引
            self._fts_index_created = False
            await self._ensure_fts_index()

            _log.info("reindex_completed", scope_id=scope_id, total=len(records))
        except Exception as exc:
            _log.error("reindex_error", error=str(exc)[:200])
        finally:
            self._reindex_in_progress = False

    def _trigger_background_reindex(self, scope_id: str) -> None:
        """在后台触发 reindex（不阻塞当前请求）。"""
        if self._reindex_in_progress:
            return
        try:
            asyncio.get_running_loop()
            asyncio.create_task(self._reindex_from_sqlite(scope_id))
        except RuntimeError:
            pass

    # ── Embedding 方法 ─────────────────────────────────────────

    async def _try_embed_query(
        self,
        query: str,
        resolved_target: _ResolvedEmbeddingTarget,
    ) -> list[float] | None:
        """尝试计算 query embedding，失败时返回 None（降级到纯 BM25）。"""
        if resolved_target.uses_lexical_only:
            return None
        if resolved_target.uses_proxy_alias:
            try:
                vecs = await self._embed_texts_with_proxy_alias(
                    [query], target_alias=resolved_target.proxy_alias, is_query=True,
                )
                return vecs[0] if vecs else None
            except Exception:
                return None
        if resolved_target.uses_qwen:
            encoder = self._embedding_runtime.encoder
            if encoder is None:
                return None
            try:
                prefixed = _QWEN_QUERY_PREFIX + query
                vectors = await asyncio.to_thread(
                    encoder.encode, [prefixed], normalize_embeddings=True,
                )
                return [float(v) for v in vectors[0]]
            except Exception as exc:
                _log.warning("qwen_embed_query_error", error=str(exc)[:200])
                return None
        # engine-default 但 Qwen 不可用 → 无 embedding，降级到 BM25
        return None

    async def _try_embed_batch(
        self,
        texts: Sequence[str],
    ) -> list[list[float]]:
        """批量 embed。Qwen3 可用时返回真向量，否则返回零向量（FTS 仍可用）。"""
        if not texts:
            return []

        runtime = self._embedding_runtime
        dim = runtime.embed_dim

        if runtime.uses_qwen:
            encoder = runtime.encoder
            assert encoder is not None
            try:
                vectors = await asyncio.to_thread(
                    encoder.encode, list(texts), normalize_embeddings=True,
                )
                result = [[float(v) for v in vec] for vec in vectors]
                if result:
                    dim = len(result[0])
                    runtime.embed_dim = dim
                return result
            except Exception as exc:
                _log.warning("qwen_embed_batch_error", error=str(exc)[:200])

        # Qwen3 不可用 → 零向量（LanceDB 需要向量列，FTS 不依赖向量）
        return [[0.0] * dim for _ in texts]

    def _current_embed_model_label(self) -> str:
        runtime = self._embedding_runtime
        if runtime.uses_qwen:
            return "qwen3-0.6b"
        return "none"

    # ── 搜索结果转换 ──────────────────────────────────────────

    def _row_to_search_hit(
        self,
        row: dict[str, Any],
        resolved_target: _ResolvedEmbeddingTarget,
        requested_target: str,
    ) -> MemorySearchHit:
        """将 LanceDB 行转换为 MemorySearchHit。"""
        layer = row.get("layer", "")
        record_id = row.get("record_id", "")
        score = row.get("_relevance_score") or row.get("_score") or 0.0

        return MemorySearchHit(
            memory_id=record_id if layer == "sor" else "",
            fragment_id=record_id if layer == "fragment" else "",
            vault_id=record_id if layer == "vault" else "",
            layer=layer or "sor",
            subject_key=row.get("subject_key", ""),
            summary=row.get("summary", ""),
            scope_id=row.get("scope_id", ""),
            partition=row.get("partition", ""),
            relevance_score=float(score),
            metadata={
                "search_mode": "hybrid" if resolved_target.uses_qwen or resolved_target.uses_proxy_alias else "fts-bm25",
                "embedding_target": requested_target,
                "resolved_embedding_target": resolved_target.effective_target,
                "projection_store": "lancedb-hybrid",
                "builtin_embedding_layer": resolved_target.layer_id,
                "builtin_embedding_status": resolved_target.mode,
                "builtin_embedding_warning": resolved_target.warning,
                "embed_model": row.get("embed_model", ""),
                "status": row.get("status", ""),
            },
        )

    # ── Embedding 运行时管理（复用原有逻辑）────────────────────

    def _schedule_initial_qwen_warmup(self) -> None:
        if not _module_exists("sentence_transformers"):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._kickoff_qwen_warmup()

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
            runtime_state.active_layer = _BM25_LAYER_ID
            runtime_state.active_mode = "lancedb-fts-bm25-fallback"
            runtime_state.summary = (
                "当前正在后台预热 Qwen3-Embedding-0.6B；这段时间先使用 BM25 全文检索。"
            )
            return runtime_state
        if _module_exists("sentence_transformers"):
            if (
                runtime_state.last_failure_monotonic
                and time.monotonic()
                < runtime_state.last_failure_monotonic + runtime_state.retry_after_seconds
            ):
                runtime_state.status = "fallback"
                runtime_state.active_layer = _BM25_LAYER_ID
                runtime_state.active_mode = "lancedb-fts-bm25-fallback"
                runtime_state.summary = (
                    "Qwen3-Embedding-0.6B 上次预热失败，当前使用 BM25 全文检索；稍后会自动重试。"
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
            "当前未检测到本地 Qwen embedding runtime，使用 BM25 全文检索。"
        )
        runtime_state.fallback_reason = "未安装 sentence-transformers 本地 embedding runtime。"
        runtime_state.active_layer = _BM25_LAYER_ID
        runtime_state.active_mode = "lancedb-fts-bm25-fallback"
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
            runtime_state.active_layer = _BM25_LAYER_ID
            runtime_state.active_mode = "lancedb-fts-bm25-fallback"
            runtime_state.summary = (
                "当前尝试加载 Qwen3-Embedding-0.6B 失败，已自动回退到 BM25 全文检索。"
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

        # 探测实际维度
        try:
            test_vec = await asyncio.to_thread(
                encoder.encode, ["test"], normalize_embeddings=True,
            )
            runtime_state.embed_dim = len(test_vec[0])
        except Exception:
            pass

    def _load_sentence_transformers_module(self) -> ModuleType:
        import sentence_transformers
        return sentence_transformers

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
                layer_id=_BM25_LAYER_ID,
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
            return [[0.0] * _LANCEDB_DEFAULT_DIM for _ in texts]
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
                return [[0.0] * _LANCEDB_DEFAULT_DIM for _ in texts]
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
        zero = [0.0] * _LANCEDB_DEFAULT_DIM
        return [v or zero for v in vectors]


# ── 辅助函数 ────────────────────────────────────────────────────

def _escape_sql(value: str) -> str:
    """防止 SQL 注入的简单转义（LanceDB where 子句用）。"""
    return value.replace("'", "''")
