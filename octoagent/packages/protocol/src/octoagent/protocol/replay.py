"""Replay protection helpers for A2A-Lite messages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .models import A2AMessage


class A2AReplayVerdict(str):
    """Replay inspection verdict."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REPLAYED = "replayed"


@dataclass(frozen=True)
class A2AReplayDecision:
    """Replay inspection result."""

    verdict: str
    fingerprint: str
    previous_message_id: str | None = None

    @property
    def accepted(self) -> bool:
        return self.verdict == A2AReplayVerdict.ACCEPTED


@dataclass(frozen=True)
class _ReplayRecord:
    message_id: str
    fingerprint: str


class A2AReplayProtector:
    """In-memory replay protection keyed by task + idempotency key."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], _ReplayRecord] = {}

    def inspect(self, message: A2AMessage) -> A2AReplayDecision:
        fingerprint = self._fingerprint(message)
        key = (message.task_id, message.idempotency_key)
        existing = self._records.get(key)
        if existing is None:
            self._records[key] = _ReplayRecord(
                message_id=message.message_id,
                fingerprint=fingerprint,
            )
            return A2AReplayDecision(A2AReplayVerdict.ACCEPTED, fingerprint)

        if existing.fingerprint == fingerprint:
            return A2AReplayDecision(
                A2AReplayVerdict.DUPLICATE,
                fingerprint,
                previous_message_id=existing.message_id,
            )

        return A2AReplayDecision(
            A2AReplayVerdict.REPLAYED,
            fingerprint,
            previous_message_id=existing.message_id,
        )

    @staticmethod
    def _fingerprint(message: A2AMessage) -> str:
        canonical = message.model_dump(
            mode="json",
            by_alias=True,
            exclude={"message_id", "timestamp_ms"},
        )
        payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
