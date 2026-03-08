"""Memory 领域枚举定义。"""

from enum import StrEnum


class MemoryLayer(StrEnum):
    """记忆层类型。"""

    FRAGMENT = "fragment"
    SOR = "sor"
    VAULT = "vault"


class MemoryPartition(StrEnum):
    """业务分区。"""

    CORE = "core"
    PROFILE = "profile"
    WORK = "work"
    HEALTH = "health"
    FINANCE = "finance"
    CHAT = "chat"


class SorStatus(StrEnum):
    """SoR 版本状态。"""

    CURRENT = "current"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class WriteAction(StrEnum):
    """记忆写入动作。"""

    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    NONE = "none"


class ProposalStatus(StrEnum):
    """WriteProposal 生命周期。"""

    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    COMMITTED = "committed"


class VaultAccessRequestStatus(StrEnum):
    """Vault 授权申请状态。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class VaultAccessGrantStatus(StrEnum):
    """Vault 授权 grant 状态。"""

    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class VaultAccessDecision(StrEnum):
    """Vault 授权审批决策。"""

    APPROVE = "approve"
    REJECT = "reject"


SENSITIVE_PARTITIONS: frozenset[MemoryPartition] = frozenset(
    {MemoryPartition.HEALTH, MemoryPartition.FINANCE}
)
