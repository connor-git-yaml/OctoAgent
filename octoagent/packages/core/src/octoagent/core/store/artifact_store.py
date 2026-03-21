"""ArtifactStore SQLite + 文件系统实现 -- 对齐 data-model.md §3

T044/T045/T046: 完整实现将在 Phase 6 完成。
此处提供骨架以确保 store 包可导入。
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import aiosqlite

from ..config import ARTIFACT_INLINE_THRESHOLD
from ..models.artifact import Artifact, ArtifactPart


def compute_hash_and_size(content: bytes) -> tuple[str, int]:
    """计算 SHA-256 hash 和内容大小

    Args:
        content: 原始内容字节

    Returns:
        (sha256_hex, size_bytes) 元组
    """
    return hashlib.sha256(content).hexdigest(), len(content)


def is_utf8_inline_safe(content: bytes) -> bool:
    """判断内容是否可无损以内联 UTF-8 形式存储。"""

    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return decoded.encode("utf-8") == content


class SqliteArtifactStore:
    """ArtifactStore 的 SQLite + 文件系统实现"""

    def __init__(self, conn: aiosqlite.Connection, artifacts_dir: Path) -> None:
        self._conn = conn
        self._artifacts_dir = artifacts_dir

    async def put_artifact(
        self,
        artifact: Artifact,
        content: bytes | None = None,
    ) -> None:
        """存储 Artifact（元数据写 SQLite + 大文件写文件系统）

        如果 content 不为 None 且大小 >= ARTIFACT_INLINE_THRESHOLD，
        或者内容不是可无损 round-trip 的 UTF-8，则写入文件系统并设置 storage_ref。
        其余小文本 inline 存储在 parts.content 中。
        """
        if content is not None:
            hash_hex, size = compute_hash_and_size(content)
            artifact.hash = hash_hex
            artifact.size = size

            if size >= ARTIFACT_INLINE_THRESHOLD or not is_utf8_inline_safe(content):
                # 大文件：写入文件系统
                file_path = self._get_artifact_path(artifact.task_id, artifact.artifact_id)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(content)
                artifact.storage_ref = str(file_path)
                # 更新 parts 中的 uri
                if artifact.parts:
                    artifact.parts[0].uri = str(file_path)
                    artifact.parts[0].content = None
            else:
                # 小文件：inline 存储在 parts.content
                if artifact.parts:
                    artifact.parts[0].content = content.decode("utf-8")
                    artifact.parts[0].uri = None

        # 写入 SQLite 元数据
        parts_json = json.dumps(
            [p.model_dump() for p in artifact.parts],
            ensure_ascii=False,
        )
        await self._conn.execute(
            """
            INSERT INTO artifacts (artifact_id, task_id, ts, name, description,
                                   parts, storage_ref, size, hash, version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                artifact.task_id,
                artifact.ts.isoformat(),
                artifact.name,
                artifact.description,
                parts_json,
                artifact.storage_ref,
                artifact.size,
                artifact.hash,
                artifact.version,
            ),
        )

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        """根据 artifact_id 查询 Artifact 元数据"""
        cursor = await self._conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_artifact(row)

    async def list_artifacts_for_task(self, task_id: str) -> list[Artifact]:
        """查询指定任务的所有 Artifact"""
        cursor = await self._conn.execute(
            "SELECT * FROM artifacts WHERE task_id = ? ORDER BY ts ASC",
            (task_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_artifact(row) for row in rows]

    async def get_artifact_content(self, artifact_id: str) -> bytes | None:
        """获取 Artifact 内容

        - inline 内容：从 parts.content 返回
        - 文件内容：从 storage_ref 路径读取
        """
        artifact = await self.get_artifact(artifact_id)
        if artifact is None:
            return None

        # 优先从文件系统读取
        if artifact.storage_ref:
            file_path = self._resolve_storage_ref(artifact.storage_ref)
            if file_path is not None and file_path.exists() and file_path.is_file():
                return file_path.read_bytes()

        # 从 inline content 返回
        for part in artifact.parts:
            if part.content is not None:
                return part.content.encode("utf-8")

        return None

    def _get_artifact_path(self, task_id: str, artifact_id: str) -> Path:
        """获取 Artifact 文件存储路径"""
        return self._artifacts_dir / task_id / artifact_id

    def _resolve_storage_ref(self, storage_ref: str) -> Path | None:
        """解析并校验 storage_ref，拒绝 artifacts_dir 之外的路径。"""
        base_dir = self._artifacts_dir.resolve()
        candidate = Path(storage_ref)
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve()
        else:
            candidate = candidate.resolve()

        try:
            candidate.relative_to(base_dir)
        except ValueError:
            return None

        return candidate

    @staticmethod
    def _row_to_artifact(row: aiosqlite.Row) -> Artifact:
        """将数据库行转换为 Artifact 模型"""
        parts_data = json.loads(row[5]) if row[5] else []
        parts = [ArtifactPart(**p) for p in parts_data]
        return Artifact(
            artifact_id=row[0],
            task_id=row[1],
            ts=datetime.fromisoformat(row[2]),
            name=row[3],
            description=row[4],
            parts=parts,
            storage_ref=row[6],
            size=row[7],
            hash=row[8],
            version=row[9],
        )

    async def collect_storage_refs_for_tasks(self, task_ids: list[str]) -> list[str]:
        """收集指定 tasks 的 artifact storage_ref（事务后文件清理用）。"""
        if not task_ids:
            return []
        placeholders = ",".join("?" * len(task_ids))
        cursor = await self._conn.execute(
            f"SELECT storage_ref FROM artifacts WHERE task_id IN ({placeholders}) AND storage_ref IS NOT NULL AND storage_ref != ''",
            tuple(task_ids),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows if row[0]]

    async def delete_artifacts_by_task_ids(self, task_ids: list[str]) -> int:
        """按 task_id 批量删除 artifact 元数据（不自动提交）。"""
        if not task_ids:
            return 0
        placeholders = ",".join("?" * len(task_ids))
        await self._conn.execute(
            f"DELETE FROM artifacts WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        )
        cursor = await self._conn.execute("SELECT changes()")
        row = await cursor.fetchone()
        return int(row[0]) if row else 0
