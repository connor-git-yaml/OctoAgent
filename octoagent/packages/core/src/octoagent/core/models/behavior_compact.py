"""F111 Behavior Compactor — 数据模型（Phase A 地基）。

行为文件 LLM 智能合并去冗余的候选状态载体。**不造 behavior 原语**（落盘/版本/
预算全复用 behavior_workspace 写核 + F107 版本历史）——只承载"提议 + 人审治理"
层的状态：

- ``BehaviorCompactCandidateStatus``：精简提议的人审生命周期状态机（C4 Two-Phase，
  五态模式仿 F127 ``ConsolidationCandidateStatus``）。
- ``BehaviorCompactCandidate``：一条整文件精简提议候选（落 ``behavior_compact_candidates``
  表）。与 F127 ``ConsolidationCandidate`` 的关键区别（spec §0.3 概念错配防治）：
  F127 合并单元是 SOR 记录（source_sor_ids/partition/proposal_id），F111 合并单元是
  **整个 behavior 文件文本**——字段是文件级（file_id/scope slugs/source_hash/
  compacted_content），故独立建模建表，复用的是**模式**不是表。

设计约束（继承宪法）：
- C2：每条提议 + 每次决策都有审计事件（``BEHAVIOR_COMPACT_*``）。
- C4：behavior 文件覆写绝不 agent 自主 commit，必须经候选 accept（唯一落盘入口）。
- PII/体积纪律：模型含 ``compacted_content`` 明文（候选表本就存待审内容供用户看 diff），
  但**事件 payload 不含原文**——事件用 candidate_id / hash / 计数引用。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class BehaviorCompactCandidateStatus(StrEnum):
    """行为文件精简提议候选状态机（C4 人审 Two-Phase，仿 F127 五态模式）。

    状态流转（atomic claim 防并发，条件 UPDATE + rowcount CAS）：

        PENDING ──(用户 accept, atomic claim 抢到)──▶ APPLYING ──(落盘+版本成功)──▶ APPLIED
           │                                             │
           │                                             ├──(落盘/版本自身异常回滚)──▶ PENDING
           │                                             └──(commit 前验证判定失败：源文件
           │                                                 hash 失配 / 禁区漏网 / H2 复验
           │                                                 不过)──▶ CONFLICT
           └──(用户 reject)──▶ REJECTED

    - PENDING：发现端产出待用户审。
    - APPLYING：用户已 accept，正在落盘（CAS 占用，防双 accept 双写）。
    - APPLIED：落盘 + F107 版本记录成功。终态。
    - REJECTED：用户拒绝，行为文件零触碰。终态。
    - CONFLICT：accept 时系统检测候选已失效——源文件在 pending 期间被编辑（sha256
      失配）/ file_id 不在 compact 白名单（漏网防御）/ H2 PROTECTED 复验不过。
      **不落盘**，终态（区别 REJECTED：系统检测非用户决策；区别回滚 PENDING：候选
      基于旧源文本，重审也不能安全覆写，终态引导用户重新触发 compact）。终态。
    """

    PENDING = "pending"
    APPLYING = "applying"
    APPLIED = "applied"
    REJECTED = "rejected"
    CONFLICT = "conflict"


#: 终态集合（不可再流转）。
BEHAVIOR_COMPACT_TERMINAL_STATUSES: frozenset[BehaviorCompactCandidateStatus] = frozenset(
    {
        BehaviorCompactCandidateStatus.APPLIED,
        BehaviorCompactCandidateStatus.REJECTED,
        BehaviorCompactCandidateStatus.CONFLICT,
    }
)


class BehaviorCompactCandidate(BaseModel):
    """一条行为文件整文件精简提议候选（落 ``behavior_compact_candidates`` 表）。

    由发现端（``BehaviorCompactDiscoveryService``）产出：LLM 读单文件全文识别语义
    重复/矛盾规则 → 输出精简后全文 → 确定性护栏（H1-H6）通过后写入本候选，等用户
    accept（唯一落盘入口）。

    新鲜度锚（spec §0.1.2 问题 3）：``source_hash`` = 提议时原文 sha256。accept 时
    重读盘对账——behavior 文件比 SOR 更易被用户并发编辑，pending 过夜期间源变更
    概率高，失配 → CONFLICT 终态。
    """

    candidate_id: str = Field(min_length=1, description="ULID，候选唯一标识")
    run_id: str = Field(min_length=1, description="所属 compact 运行 run_id（bcpt-*）")
    file_id: str = Field(min_length=1, description="behavior 文件短名，如 AGENTS.md")
    agent_slug: str = Field(
        default="main",
        description=(
            "AGENT scope 路径解析用 slug"
            "（v0.1 eligible 集不含 AGENT_PRIVATE 文件，保留供演化）"
        ),
    )
    project_slug: str = Field(
        default="default", description="PROJECT scope 路径解析用 slug"
    )
    source_hash: str = Field(
        min_length=1,
        description="提议时原文 sha256（新鲜度锚：accept 重读盘对账，失配→CONFLICT）",
    )
    compacted_content: str = Field(
        min_length=1, description="精简后完整全文（PROTECTED 区段已确定性插回）"
    )
    rationale: str = Field(default="", description="LLM 给出的合并理由（人审展示）")
    size_before: int = Field(ge=0, description="原文字符数")
    size_after: int = Field(ge=0, description="精简后字符数（H1 保证 < size_before）")
    content_hash: str = Field(
        default="", description="精简后全文 sha256（审计引用，事件 payload 用）"
    )
    status: BehaviorCompactCandidateStatus = Field(
        default=BehaviorCompactCandidateStatus.PENDING
    )
    created_at: datetime
    decided_at: datetime | None = Field(
        default=None, description="终态决策时间（accept/reject/conflict）"
    )


__all__ = [
    "BEHAVIOR_COMPACT_TERMINAL_STATUSES",
    "BehaviorCompactCandidate",
    "BehaviorCompactCandidateStatus",
]
