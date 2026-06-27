"""F127 Sleep-Time Memory Consolidation — 数据模型（Phase A 地基）。

本模块定义后台记忆巩固编排所需的持久化模型，**不造 memory 原语**（合并/软删/写管道
全复用现有 write_service）——只承载"编排 + 审批治理"层的状态：

- ``ConsolidationCandidateStatus``：合并提议的人审生命周期状态机（C4 Two-Phase）。
- ``ConsolidationCandidate``：一条 MERGE 提议候选（落 ``consolidation_candidates`` 表）。
  与 ``observation_candidates`` 的关键区别（OQ-1 实测）：observation 候选 promote 走
  USER.md（``snapshot_store.append_entry``，不调 write_service）；本候选 approve 走
  **SOR 层 ``write_service`` MERGE commit**（源标 SUPERSEDED）。数据流不同故独立建表。
- ``MemoryConsolidationRun``：一次 cron 巩固运行的审计记录（参考 ``MemoryMaintenanceRun``）。

设计约束（继承项目宪法）：
- C2 Everything-is-an-Event：每条提议 + 每次决策都有审计事件（``MEMORY_CONSOLIDATION_*``）。
- C4 Side-effect Two-Phase：合并/删既有事实绝不 agent 自主 commit，必须经候选审批。
- PII 防护：模型可含 ``merged_content`` 明文（候选表本就存待审内容供用户看），但**事件 payload
  不含原文**——事件用 ``candidate_id`` / ``content_hash`` 引用（见 payload schema）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# SENSITIVE_PARTITIONS 复用 enums.py 既有定义（{HEALTH, FINANCE}），不重复造（避免双定义偏移）。
from ..enums import SENSITIVE_PARTITIONS, MemoryPartition


class ConsolidationCandidateStatus(StrEnum):
    """巩固合并提议候选状态机（C4 人审 Two-Phase）。

    状态流转（atomic claim 防并发，复用 memory_candidates.py:304 条件 UPDATE 范式）：

        PENDING ──(用户 accept, atomic claim 抢到)──▶ APPLYING ──(MERGE commit 成功)──▶ APPLIED
           │                                              │
           │                                              └──(commit 失败回滚)──▶ PENDING
           └──(用户 reject)──▶ REJECTED

    - PENDING：LLM 产出待用户审。
    - APPLYING：用户已 accept，正在调 write_service MERGE commit（CAS 占用，防双 accept）。
    - APPLIED：MERGE commit 成功（源已标 SUPERSEDED）。终态。
    - REJECTED：用户拒绝，不碰 SOR。终态。
    """

    PENDING = "pending"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"


#: 终态集合（不可再流转）。
CONSOLIDATION_TERMINAL_STATUSES: frozenset[ConsolidationCandidateStatus] = frozenset(
    {
        ConsolidationCandidateStatus.APPLIED,
        ConsolidationCandidateStatus.REJECTED,
    }
)

#: 敏感分区护栏（NFR-3）：这些分区的事实合并**强制人审**，绝不进任何自动路径。
#: 直接复用 ``enums.SENSITIVE_PARTITIONS``（{HEALTH, FINANCE}）——v0.1 全部提议都人审，
#: 此集合是 v0.2 引入自动模式时的白名单护栏地基（自动模式必须排除它们）。
#: 在本模块 re-export 供 consolidation 服务/store 就近引用，定义仍单一事实源于 enums.py。


class ConsolidationCandidate(BaseModel):
    """一条记忆巩固 MERGE 提议候选（落 ``consolidation_candidates`` 表）。

    由巩固 subagent 发现端（Phase C）产出：LLM 识别若干同主题冗余 CURRENT 事实 →
    提议合并成一条权威事实。**validate 通过但不 commit**，写入本候选等用户 accept（Phase D）。

    关联键不压扁（WriteResult 契约精神）：``source_sor_ids`` 保留待合并源 SOR ids，
    ``proposal_id`` 关联 write_service 已 validate 的 WriteProposal。
    """

    candidate_id: str = Field(min_length=1, description="ULID，候选唯一标识")
    run_id: str = Field(min_length=1, description="所属巩固运行 run_id（关联 MemoryConsolidationRun）")
    scope_id: str = Field(min_length=1, description="记忆 scope（AGENT_PRIVATE 等）")
    partition: MemoryPartition = Field(description="提议合并目标事实所属分区")
    subject_key: str = Field(default="", description="合并后权威事实的 subject_key")
    source_sor_ids: list[str] = Field(
        default_factory=list,
        description="待合并的源 SOR memory_id 列表（accept 后这些标 SUPERSEDED）",
    )
    merged_content: str = Field(
        default="",
        description="LLM 合成的权威事实内容（候选表存明文供用户审；事件 payload 不含此字段）",
    )
    rationale: str = Field(default="", description="LLM 给的合并理由（供用户判断）")
    proposal_id: str = Field(
        default="",
        description="关联 write_service 已 validate 的 WriteProposal id（accept 时 commit 它）",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="LLM 置信度")
    is_sensitive: bool = Field(
        default=False,
        description="是否敏感分区（HEALTH/FINANCE）——强制人审，禁自动路径（NFR-3）",
    )
    status: ConsolidationCandidateStatus = Field(
        default=ConsolidationCandidateStatus.PENDING,
        description="人审生命周期状态",
    )
    content_hash: str = Field(
        default="",
        description="merged_content 的 sha256（幂等账本 + 事件引用用，NFR-4）",
    )
    created_at: datetime = Field(description="候选产出时间 UTC")
    decided_at: datetime | None = Field(
        default=None, description="用户 accept/reject 时间；None 表示待审"
    )


class MemoryConsolidationRun(BaseModel):
    """一次 cron 巩固运行的审计记录（FR-D3，参考 ``MemoryMaintenanceRun``）。

    一次运行 = 一次 cron 触发 → spawn subagent → 回顾窗口 → 产 N 条提议。覆盖运行级
    可观测（NFR-5：trigger→propose→approve/reject→complete 全链路可经 event_store + 本表查）。
    """

    run_id: str = Field(min_length=1, description="ULID，巩固运行唯一标识")
    scope_id: str = Field(default="", description="巩固的记忆 scope")
    status: ConsolidationCandidateStatus | Literal["running", "completed", "failed", "skipped"] = (
        Field(
            default="running",
            description="运行状态（running/completed/failed/skipped）",
        )
    )
    trigger_ts: datetime = Field(description="cron 触发时刻 UTC")
    window_days: int = Field(default=7, ge=1, description="回顾窗口天数")
    max_facts: int = Field(default=50, ge=1, description="窗口内最多纳入事实数")
    facts_reviewed: int = Field(default=0, ge=0, description="实际回顾的事实数")
    proposals_made: int = Field(default=0, ge=0, description="产出的 MERGE 提议数")
    proposals_approved: int = Field(default=0, ge=0, description="用户接受的提议数")
    proposals_rejected: int = Field(default=0, ge=0, description="用户拒绝的提议数")
    elapsed_ms: int = Field(default=0, ge=0, description="运行总耗时（毫秒）")
    fallback: bool = Field(
        default=False, description="是否走 deterministic fallback（LLM 不可用，C6）"
    )
    error_summary: str = Field(default="", description="失败时精简错误（不含 traceback 原文）")
    child_task_id: str = Field(default="", description="派出的巩固 subagent task_id（H2 对等审计）")
    started_at: datetime = Field(description="运行开始 UTC")
    finished_at: datetime | None = Field(default=None, description="运行结束 UTC；None 表示进行中")


# ============================================================
# 事件 Payload Schemas（FR-D1）——PII 防护：不含 merged_content 原文，用 id/hash 引用
# ============================================================


class ConsolidationTriggeredPayload(BaseModel):
    """MEMORY_CONSOLIDATION_TRIGGERED 事件 payload（cron 到点 + spawn 成功）。"""

    run_id: str = Field(description="巩固运行 run_id")
    trigger_ts: str = Field(description="触发时间戳 ISO 8601 UTC")
    child_task_id: str = Field(default="", description="派出的 subagent task_id")
    window_days: int = Field(default=7, ge=1)
    max_facts: int = Field(default=50, ge=1)


class ConsolidationCompletedPayload(BaseModel):
    """MEMORY_CONSOLIDATION_COMPLETED 事件 payload（巩固运行结束）。"""

    run_id: str = Field(description="巩固运行 run_id")
    facts_reviewed: int = Field(default=0, ge=0)
    proposals_made: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)
    fallback: bool = Field(default=False, description="是否走 deterministic fallback")


class ConsolidationFailedPayload(BaseModel):
    """MEMORY_CONSOLIDATION_FAILED 事件 payload（不可恢复异常）。

    error_type/error_msg 不含 traceback 原始文本，避免 PII 泄露（沿用 F102 范式）。
    """

    run_id: str = Field(default="", description="巩固运行 run_id（若已分配）")
    error_type: str = Field(description="异常类名短字符串（如 'spawn_failed' / 'TimeoutError'）")
    error_msg: str = Field(description="精简错误说明（不含 traceback 原文）")


class ConsolidationSkippedPayload(BaseModel):
    """MEMORY_CONSOLIDATION_SKIPPED 事件 payload（跳过本次触发）。"""

    reason: str = Field(
        description="跳过原因（'disabled' / 'capacity' / 'already_running'）"
    )
    run_id: str = Field(default="", description="巩固运行 run_id（若已分配）")


class ConsolidationProposedPayload(BaseModel):
    """MEMORY_CONSOLIDATION_PROPOSED 事件 payload（一条 MERGE 提议产出）。

    **PII 防护**：不含 merged_content / rationale 原文——用 candidate_id + content_hash
    引用，源用 source_sor_ids（id 非内容）。审计可追溯但不在事件里泄漏记忆原文。
    """

    run_id: str = Field(description="巩固运行 run_id")
    candidate_id: str = Field(description="候选 id")
    partition: str = Field(description="目标分区")
    source_count: int = Field(default=0, ge=0, description="待合并源事实数")
    content_hash: str = Field(default="", description="merged_content 的 sha256（非原文）")
    is_sensitive: bool = Field(default=False, description="是否敏感分区")


class ConsolidationApprovedPayload(BaseModel):
    """MEMORY_CONSOLIDATION_APPROVED 事件 payload（用户接受 → MERGE commit）。"""

    run_id: str = Field(default="", description="巩固运行 run_id")
    candidate_id: str = Field(description="候选 id")
    new_sor_id: str = Field(default="", description="合并产生的新权威 SOR memory_id")
    superseded_count: int = Field(default=0, ge=0, description="标 SUPERSEDED 的源事实数")


class ConsolidationRejectedPayload(BaseModel):
    """MEMORY_CONSOLIDATION_REJECTED 事件 payload（用户拒绝 → 不碰 SOR）。"""

    run_id: str = Field(default="", description="巩固运行 run_id")
    candidate_id: str = Field(description="候选 id")


__all__ = [
    "CONSOLIDATION_TERMINAL_STATUSES",
    "SENSITIVE_PARTITIONS",
    "ConsolidationApprovedPayload",
    "ConsolidationCandidate",
    "ConsolidationCandidateStatus",
    "ConsolidationCompletedPayload",
    "ConsolidationFailedPayload",
    "ConsolidationProposedPayload",
    "ConsolidationRejectedPayload",
    "ConsolidationSkippedPayload",
    "ConsolidationTriggeredPayload",
    "MemoryConsolidationRun",
]
