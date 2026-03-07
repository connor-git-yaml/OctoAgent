"""A2A mapping helpers."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from octoagent.core.models import Artifact, ArtifactPart, PartType, TaskStatus

from .models import (
    A2AArtifact,
    A2ADataPart,
    A2AFilePart,
    A2ATaskState,
    A2ATextPart,
)


class A2AStateMapper:
    """Map OctoAgent internal task states to canonical A2A states."""

    _TO_A2A: dict[TaskStatus, str] = {
        TaskStatus.CREATED: A2ATaskState.SUBMITTED,
        TaskStatus.QUEUED: A2ATaskState.SUBMITTED,
        TaskStatus.RUNNING: A2ATaskState.WORKING,
        TaskStatus.WAITING_INPUT: A2ATaskState.INPUT_REQUIRED,
        TaskStatus.WAITING_APPROVAL: A2ATaskState.INPUT_REQUIRED,
        TaskStatus.PAUSED: A2ATaskState.WORKING,
        TaskStatus.SUCCEEDED: A2ATaskState.COMPLETED,
        TaskStatus.CANCELLED: A2ATaskState.CANCELED,
        TaskStatus.FAILED: A2ATaskState.FAILED,
        TaskStatus.REJECTED: A2ATaskState.REJECTED,
    }
    _FROM_A2A: dict[str, TaskStatus] = {
        A2ATaskState.SUBMITTED: TaskStatus.QUEUED,
        A2ATaskState.WORKING: TaskStatus.RUNNING,
        A2ATaskState.INPUT_REQUIRED: TaskStatus.WAITING_INPUT,
        A2ATaskState.COMPLETED: TaskStatus.SUCCEEDED,
        A2ATaskState.CANCELED: TaskStatus.CANCELLED,
        A2ATaskState.FAILED: TaskStatus.FAILED,
        A2ATaskState.REJECTED: TaskStatus.REJECTED,
        "auth-required": TaskStatus.WAITING_APPROVAL,
        "unknown": TaskStatus.FAILED,
    }

    @classmethod
    def to_a2a(cls, state: TaskStatus | str) -> str:
        internal = TaskStatus(state)
        return cls._TO_A2A[internal]

    @classmethod
    def from_a2a(cls, state: str) -> TaskStatus:
        try:
            return cls._FROM_A2A[state]
        except KeyError as exc:
            raise ValueError(f"unsupported A2A state: {state}") from exc


class A2AArtifactMapper:
    """Map OctoAgent Artifact models to A2A-compatible artifacts."""

    @staticmethod
    def to_a2a(artifact: Artifact) -> A2AArtifact:
        parts = [
            A2AArtifactMapper._to_a2a_part(part, artifact.storage_ref)
            for part in artifact.parts
        ]
        metadata = {
            "version": artifact.version,
            "hash": artifact.hash,
            "size": artifact.size,
        }
        return A2AArtifact(
            artifactId=artifact.artifact_id,
            name=artifact.name,
            description=artifact.description,
            parts=parts,
            append=False,
            lastChunk=False,
            metadata=metadata,
        )

    @staticmethod
    def from_a2a(
        artifact: A2AArtifact,
        *,
        task_id: str,
        ts: datetime | None = None,
    ) -> Artifact:
        metadata = artifact.metadata
        parts = [A2AArtifactMapper._from_a2a_part(part) for part in artifact.parts]
        storage_ref = next(
            (
                part.uri
                for part in artifact.parts
                if isinstance(part, (A2ATextPart, A2AFilePart)) and part.uri
            ),
            None,
        )
        return Artifact(
            artifact_id=artifact.artifact_id or "",
            task_id=task_id,
            ts=ts or datetime.now(UTC),
            name=artifact.name,
            description=artifact.description,
            parts=parts,
            storage_ref=storage_ref,
            size=int(metadata.get("size", 0) or 0),
            hash=str(metadata.get("hash", "")),
            version=int(metadata.get("version", 1) or 1),
        )

    @staticmethod
    def _to_a2a_part(
        part: ArtifactPart,
        storage_ref: str | None,
    ) -> A2ATextPart | A2AFilePart | A2ADataPart:
        uri = part.uri or storage_ref
        if part.type == PartType.TEXT:
            return A2ATextPart(text=part.content, uri=uri, mime=part.mime)
        if part.type == PartType.FILE:
            encoded = None
            if part.content is not None:
                encoded = base64.b64encode(part.content.encode("utf-8")).decode("ascii")
            return A2AFilePart(uri=uri, data=encoded, mime=part.mime)
        if part.type == PartType.JSON:
            data: Any = part.content
            if part.content is not None:
                try:
                    data = json.loads(part.content)
                except json.JSONDecodeError:
                    data = part.content
            return A2ADataPart(data=data, metadata={"mime": part.mime})
        encoded = None
        if part.content is not None:
            encoded = base64.b64encode(part.content.encode("utf-8")).decode("ascii")
        return A2AFilePart(uri=uri, data=encoded, mime=part.mime or "image/*")

    @staticmethod
    def _from_a2a_part(part: A2ATextPart | A2AFilePart | A2ADataPart) -> ArtifactPart:
        if isinstance(part, A2ATextPart):
            return ArtifactPart(
                type=PartType.TEXT,
                mime=part.mime,
                content=part.text,
                uri=part.uri,
            )
        if isinstance(part, A2ADataPart):
            return ArtifactPart(
                type=PartType.JSON,
                mime=str(part.metadata.get("mime", "application/json")),
                content=json.dumps(part.data, ensure_ascii=False),
            )
        content = None
        if part.data is not None:
            decoded = base64.b64decode(part.data.encode("ascii"))
            try:
                content = decoded.decode("utf-8")
            except UnicodeDecodeError:
                content = part.data
        inferred_type = PartType.IMAGE if part.mime.startswith("image/") else PartType.FILE
        return ArtifactPart(
            type=inferred_type,
            mime=part.mime,
            content=content,
            uri=part.uri,
        )
