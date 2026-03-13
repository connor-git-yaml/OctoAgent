"""Feature 049: Butler persona / clarification behavior 领域模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


class BehaviorLayerKind(StrEnum):
    ROLE = "role"
    COMMUNICATION = "communication"
    SOLVING = "solving"
    TOOL_BOUNDARY = "tool_boundary"
    MEMORY_POLICY = "memory_policy"
    BOOTSTRAP = "bootstrap"


class BehaviorVisibility(StrEnum):
    SHARED = "shared"
    PRIVATE = "private"


class ClarificationAction(StrEnum):
    DIRECT = "direct"
    CLARIFY = "clarify"
    BEST_EFFORT_FALLBACK = "best_effort_fallback"
    DELEGATE_AFTER_CLARIFICATION = "delegate_after_clarification"


class BehaviorPackFile(BaseModel):
    file_id: str = Field(min_length=1)
    title: str = Field(default="")
    path_hint: str = Field(default="")
    layer: BehaviorLayerKind = BehaviorLayerKind.ROLE
    content: str = Field(default="")
    visibility: BehaviorVisibility = BehaviorVisibility.PRIVATE
    share_with_workers: bool = False
    source_kind: str = Field(default="default_template")
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorLayer(BaseModel):
    layer: BehaviorLayerKind
    content: str = Field(default="")
    source_file_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorPack(BaseModel):
    pack_id: str = Field(default="")
    profile_id: str = Field(default="")
    scope: str = Field(default="system")
    source_chain: list[str] = Field(default_factory=list)
    files: list[BehaviorPackFile] = Field(default_factory=list)
    layers: list[BehaviorLayer] = Field(default_factory=list)
    clarification_policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClarificationDecision(BaseModel):
    action: ClarificationAction = ClarificationAction.DIRECT
    category: str = Field(default="")
    rationale: str = Field(default="")
    missing_inputs: list[str] = Field(default_factory=list)
    followup_prompt: str = Field(default="")
    fallback_hint: str = Field(default="")
    delegate_after_clarification: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorSliceEnvelope(BaseModel):
    summary: str = Field(default="")
    shared_file_ids: list[str] = Field(default_factory=list)
    layers: list[BehaviorLayer] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorFileChange(BaseModel):
    file_id: str = Field(min_length=1)
    summary: str = Field(default="")
    before_content: str = Field(default="")
    proposed_content: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehaviorPatchProposal(BaseModel):
    proposal_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    title: str = Field(default="")
    rationale: str = Field(default="")
    review_required: bool = True
    target_file_ids: list[str] = Field(default_factory=list)
    file_changes: list[BehaviorFileChange] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
