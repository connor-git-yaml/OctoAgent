"""WriteProposal 模型。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ..enums import MemoryPartition, ProposalStatus, WriteAction
from .common import EvidenceRef


class WriteProposal(BaseModel):
    """长期记忆写入提案。"""

    schema_version: int = Field(default=1)
    proposal_id: str = Field(description="ULID")
    scope_id: str
    partition: MemoryPartition
    action: WriteAction
    subject_key: str | None = None
    content: str | None = None
    rationale: str = Field(default="")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    expected_version: int | None = Field(default=None, ge=1)
    is_sensitive: bool = Field(default=False)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: ProposalStatus = Field(default=ProposalStatus.PENDING)
    validation_errors: list[str] = Field(default_factory=list)
    created_at: datetime
    validated_at: datetime | None = None
    committed_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_action_requirements(self) -> "WriteProposal":
        if self.action is WriteAction.NONE:
            return self

        if not self.subject_key:
            raise ValueError("非 NONE proposal 必须提供 subject_key")
        if not self.evidence_refs:
            raise ValueError("非 NONE proposal 必须提供 evidence_refs")
        if self.action in {WriteAction.ADD, WriteAction.UPDATE} and not self.content:
            raise ValueError("ADD/UPDATE proposal 必须提供 content")
        return self
