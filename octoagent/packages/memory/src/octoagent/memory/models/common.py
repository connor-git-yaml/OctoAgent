"""Memory 公共模型。"""

from datetime import datetime

from pydantic import BaseModel, Field

from ..enums import MemoryLayer, MemoryPartition


class EvidenceRef(BaseModel):
    """证据引用。"""

    ref_id: str = Field(description="引用 ID")
    ref_type: str = Field(description="引用类型")
    snippet: str | None = Field(default=None, description="可选摘要")


class ProposalValidation(BaseModel):
    """提案验证结果。"""

    proposal_id: str
    accepted: bool
    errors: list[str] = Field(default_factory=list)
    persist_vault: bool = Field(default=False, description="是否同步生成 Vault skeleton")
    current_version: int | None = Field(default=None)


class MemorySearchHit(BaseModel):
    """检索摘要结果。"""

    record_id: str
    layer: MemoryLayer
    scope_id: str
    partition: MemoryPartition
    summary: str
    subject_key: str | None = None
    version: int | None = None
    status: str | None = None
    created_at: datetime
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class MemoryAccessPolicy(BaseModel):
    """读取策略。"""

    allow_vault: bool = Field(default=False)
    include_history: bool = Field(default=False)
    actor_id: str = Field(
        default="",
        description="请求方 actor（worker_id / session_id）。Vault 授权校验与审计均依赖此字段。",
    )
    actor_label: str = Field(default="")
    project_id: str = Field(default="")


class CompactionFlushResult(BaseModel):
    """Compaction 前 flush 草案结果。"""

    fragment: "FragmentRecord"
    proposal: "WriteProposal | None" = None


class CommitResult(BaseModel):
    """记忆提交结果。"""

    proposal_id: str
    fragment_id: str | None = None
    sor_id: str | None = None
    vault_id: str | None = None
    committed: bool = True


class MemoryAccessDeniedError(PermissionError):
    """Vault 未授权访问。"""


class ProposalNotValidatedError(RuntimeError):
    """提案未经验证即提交。"""


from .fragment import FragmentRecord  # noqa: E402
from .proposal import WriteProposal  # noqa: E402

CompactionFlushResult.model_rebuild()
