"""F127 Sleep-Time Memory Consolidation — 发现端（Phase C）。

巩固"发现端"=回顾近期 AGENT_PRIVATE 事实 → LLM 识别冗余/可合并组 → 产
``WriteProposal[MERGE]``（**validate 通过但不 commit**）→ 写 ``consolidation_candidates``
候选表（status=PENDING，等用户 Phase D 审批）→ emit ``MEMORY_CONSOLIDATION_PROPOSED``。

★ 架构定位（spec §0.1.4 实施期偏离归档，见 §"为什么发现端是确定性组件而非 subagent free-loop"）：
本服务是**确定性编排组件**，``llm_client`` 注入（测试可 stub，production 注 provider adapter）。
Phase B 派出的后台 subagent 仍是 H2 对等的 SUBAGENT_INTERNAL 执行容器（spawn-and-die +
cleanup hook + 并发隔离），但"拉窗口→LLM 识别→propose→写候选"的发现逻辑放在本确定性组件——
理由：①FR-B 的 ``[@test]`` 绑定要求确定性单测（窗口拉取 / 提议→MERGE / validate-no-commit /
fallback），LLM free-loop 不可确定性测；②plan §Phase C 明确"确定性层（validate/写候选）可单测，
LLM 层留强 model 验证"；③``tool_profile="minimal"`` 当前挂不到任何工具（无 builtin 工具标
minimal），free-loop 路径需先打通 per-tool profile override 链路（spec 第一决策点未拍板）。
**不造 memory 原语**——propose/validate/commit 全复用 ``MemoryService`` 写管道；窗口读复用
``search_sor``。

继承宪法：
- C2 Everything-is-an-Event：每条提议 emit ``MEMORY_CONSOLIDATION_PROPOSED``（PII 防护：
  candidate_id + content_hash 引用，不含 merged_content 原文）。
- C4 Side-effect Two-Phase：发现端**只提议不 commit**——既有事实的 MERGE 落到 Phase D 人审。
- C6 Degrade Gracefully：LLM 不可用/空响应/解析失败 → deterministic fallback（0 提议，
  ``fallback=True``），不崩。
- C9 Agent Autonomy：是否冗余/可合并**由 LLM 决策**——本服务不写任何关键词/相似度规则判重，
  只提供候选窗口 + 写管道。
- NFR-3 C5（codex P1-1/P1-2 修复后语义）：**v0.1 巩固只处理非敏感事实**——窗口直接排除
  敏感分区（``SENSITIVE_PARTITIONS``，与 write_service ``_safe_sor_content`` 同一判定源）。
  根因：①敏感性若按推断目标 partition 算，LLM 混组（敏感+普通源）会把敏感内容降级到
  非敏感路径（P1-1）；②敏感 MERGE 提议 commit 时 ``_safe_sor_content`` 会把 SOR content
  替换成 rationale 且 MERGE 不建 vault——accept 后源 SUPERSEDED、新事实只剩合并理由，
  **毁掉敏感记忆**（P1-2）。敏感事实合并推 v0.2（vault-aware MERGE，spec §2.2 deferred）。
  纵深防御：``_propose_group`` 对"任一源或目标 partition 敏感"的组拒绝产候选；
  审批端 accept 前再验一道（三层防御，防 LLM/数据侧漏网）。
- NFR-4 幂等：``content_hash`` 账本——同 scope 已有 pending/applied 同内容候选则跳过（防 crash
  重放产重复候选）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.memory.enums import SENSITIVE_PARTITIONS, MemoryPartition, WriteAction
from octoagent.memory.models import (
    ConsolidationCandidate,
    ConsolidationCandidateStatus,
    ConsolidationProposedPayload,
    EvidenceRef,
)
from ulid import ULID

# 复用 F065 inference 的 LLM JSON 解析（code fence 剥离 + 正则兜底 + dict 包裹数组提取）。
# 单一事实源不重复造——但 F127 在其上加专属组校验（id 白名单 + 最小源数）。
from .inference.llm_common import parse_llm_json_array

if TYPE_CHECKING:
    from octoagent.core.store.event_store import SqliteEventStore
    from octoagent.memory.models import SorRecord
    from octoagent.memory.service import MemoryService
    from octoagent.memory.store.consolidation_store import ConsolidationStore
    from octoagent.memory.store.memory_store import SqliteMemoryStore


logger = structlog.get_logger(__name__)


# ============================================================
# 常量
# ============================================================

#: LLM 输入字符预算（沿用 F102 LLM_INPUT_CHAR_BUDGET 范式，FR-B5）。
#: ~2000 中文字符 ≈ 3000 token；超限优先纳入同 subject_key 聚集事实（提高合并命中）。
LLM_INPUT_CHAR_BUDGET: int = 2000

#: LLM 输出 token 预算（结构化 JSON 提议，比 F102 自然语言摘要稍宽）。
LLM_OUTPUT_TOKEN_BUDGET: int = 1024

#: 巩固发现端用的 model alias（与 production main/cheap 解耦，可独立配置）。
#: v0.1 用 "cheap"（沿用 F102 daily_routine 范式——发现冗余是相对轻量的判断任务）。
CONSOLIDATION_MODEL_ALIAS: str = "cheap"

#: 单次运行最多产出的提议数（防 LLM 失控产海量候选淹没用户审批）。
MAX_PROPOSALS_PER_RUN: int = 20

#: 一个合并组至少需要的源事实数（少于 2 条无所谓"合并"）。
MIN_GROUP_SOURCE_COUNT: int = 2


class ConsolidationLLMClient(Protocol):
    """巩固发现端 LLM 客户端最小契约（注入式，测试可 stub）。

    匹配 ``ProviderRouterMessageAdapter.complete`` 签名——production 注入它，
    单测注入返回固定 ``.content`` 的 fake。**绝不**在本服务内构造 provider，保持
    可测 + 解耦（与 spec NFR：发现端确定性可单测一致）。
    """

    async def complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        **kwargs: Any,
    ) -> Any:  # 返回对象需有 .content: str（或可被 _extract_llm_text 容错取出）
        ...


@dataclass(slots=True)
class _MergeGroup:
    """LLM 识别出的一个可合并组（解析中间态）。"""

    source_sor_ids: list[str]
    merged_content: str
    subject_key: str
    rationale: str
    confidence: float


@dataclass(slots=True)
class DiscoveryOutcome:
    """发现端一次运行的产出（供 service 写 run 审计 + 通知判断）。

    Attributes:
        facts_reviewed: 实际回顾的 CURRENT 事实数。
        proposals_made: 实际写入的候选数（已 validate 通过 + 去重后）。
        fallback: 是否走 deterministic fallback（LLM 不可用/空/解析失败）。
        candidate_ids: 写入的候选 id 列表（供通知引用）。
    """

    facts_reviewed: int = 0
    proposals_made: int = 0
    fallback: bool = False
    candidate_ids: list[str] = field(default_factory=list)


class ConsolidationDiscoveryService:
    """巩固发现端（确定性编排：拉窗口 → LLM 识别冗余 → propose/validate → 写候选）。

    依赖注入（全部测试可 stub）：
    - ``memory_service``：propose_write / validate_proposal（**不 commit**，写管道复用）。
    - ``memory_store``：search_sor（拉 AGENT_PRIVATE CURRENT 事实窗口）。
    - ``consolidation_store``：insert_candidate / list_candidates（候选持久化 + 去重账本）。
    - ``event_store``：emit MEMORY_CONSOLIDATION_PROPOSED。
    - ``llm_client``：识别冗余（C9 LLM 决策）；None → 直接 fallback（无 LLM 不崩）。
    """

    def __init__(
        self,
        *,
        memory_service: MemoryService,
        memory_store: SqliteMemoryStore,
        consolidation_store: ConsolidationStore,
        event_store: SqliteEventStore,
        llm_client: ConsolidationLLMClient | None = None,
    ) -> None:
        self._memory_service = memory_service
        self._memory_store = memory_store
        self._consolidation_store = consolidation_store
        self._event_store = event_store
        self._llm_client = llm_client

    # ============================================================
    # 主入口
    # ============================================================

    async def discover_and_propose(
        self,
        *,
        run_id: str,
        scope_id: str,
        root_task_id: str,
        window_days: int = 7,
        max_facts: int = 50,
    ) -> DiscoveryOutcome:
        """回顾窗口 → LLM 识别冗余 → propose/validate → 写候选 → emit PROPOSED。

        **C4**：本方法只产 PENDING 候选，**绝不 commit**——既有事实的 MERGE 在 Phase D 人审后
        才走 ``write_service.commit_memory``。

        Args:
            run_id: 所属巩固运行 run_id。
            scope_id: 巩固的记忆 scope（主 Agent AGENT_PRIVATE）。
            root_task_id: 事件 FK 占位（consolidation root task）。
            window_days: 回顾窗口天数（FR-B1）。
            max_facts: 窗口内最多纳入事实数（FR-B1 + token budget）。

        Returns:
            DiscoveryOutcome：facts_reviewed / proposals_made / fallback / candidate_ids。
        """
        # FR-B1：拉近期窗口 AGENT_PRIVATE CURRENT 事实
        facts = await self._pull_window(scope_id, window_days, max_facts)
        outcome = DiscoveryOutcome(facts_reviewed=len(facts))

        if len(facts) < MIN_GROUP_SOURCE_COUNT:
            # 事实太少无合并空间——直接 0 提议返回（非 fallback，是正常空运行）
            logger.info(
                "consolidation_discovery_too_few_facts",
                run_id=run_id,
                facts=len(facts),
            )
            return outcome

        # FR-B2 / C9：LLM 识别冗余/可合并组（系统不写规则判重）
        groups, used_fallback = await self._identify_merge_groups(facts)
        outcome.fallback = used_fallback

        if not groups:
            logger.info(
                "consolidation_discovery_no_groups",
                run_id=run_id,
                fallback=used_fallback,
            )
            return outcome

        # FR-B3/B4：每组 → propose MERGE（不 commit）→ validate → 写候选
        facts_by_id = {f.memory_id: f for f in facts}
        for group in groups:
            if outcome.proposals_made >= MAX_PROPOSALS_PER_RUN:
                logger.warning(
                    "consolidation_discovery_proposal_cap_reached",
                    run_id=run_id,
                    cap=MAX_PROPOSALS_PER_RUN,
                )
                break
            candidate_id = await self._propose_group(
                run_id=run_id,
                scope_id=scope_id,
                root_task_id=root_task_id,
                group=group,
                facts_by_id=facts_by_id,
            )
            if candidate_id:
                outcome.proposals_made += 1
                outcome.candidate_ids.append(candidate_id)

        return outcome

    # ============================================================
    # Step 1：拉窗口（FR-B1）
    # ============================================================

    async def _pull_window(
        self, scope_id: str, window_days: int, max_facts: int
    ) -> list[SorRecord]:
        """拉近期窗口 AGENT_PRIVATE CURRENT 事实（updated_after 截断 + max_facts 限量）。

        复用 ``search_sor(include_history=False)``——默认只看 status='current'，按 updated_at
        DESC 排序。window_days → updated_after cutoff；max_facts → limit。窗口超限时
        search_sor 自身按 updated_at DESC 取最近的（FR-B5 优先近期）。

        **敏感分区排除（codex P1-1/P1-2 根治，NFR-3 v0.1 收窄）**：拉取后过滤掉
        ``SENSITIVE_PARTITIONS``（HEALTH/FINANCE，单一事实源 ``octoagent.memory.enums``，
        与 write_service ``_safe_sor_content``/``persist_vault`` 同一判定源）的事实——
        v0.1 巩固只处理非敏感事实；LLM 看不到敏感事实 → ``valid_ids`` 白名单不含敏感 id
        → 不可能产出含敏感源的合并组。敏感事实合并推 v0.2 vault-aware MERGE。

        Known limitation（v0.1 接受）：过滤在 SQL limit 之后——敏感事实会占 max_facts
        名额（如 50 条里 10 条敏感则本次只回顾 40 条非敏感）。窗口本就是 best-effort
        截断，下次运行会补上；不为此把排除下沉 store 层（避免动共享 search_sor 面）。
        """
        cutoff = (datetime.now(UTC) - timedelta(days=window_days)).isoformat()
        try:
            facts = await self._memory_store.search_sor(
                scope_id,
                include_history=False,
                limit=max_facts,
                updated_after=cutoff,
            )
        except Exception:
            logger.exception("consolidation_pull_window_failed", scope_id=scope_id)
            return []
        non_sensitive = [f for f in facts if f.partition not in SENSITIVE_PARTITIONS]
        excluded = len(facts) - len(non_sensitive)
        if excluded:
            logger.info(
                "consolidation_window_sensitive_excluded",
                scope_id=scope_id,
                excluded=excluded,
            )
        return non_sensitive

    # ============================================================
    # Step 2：LLM 识别冗余（FR-B2 / C9 / FR-B6 fallback）
    # ============================================================

    async def _identify_merge_groups(
        self, facts: list[SorRecord]
    ) -> tuple[list[_MergeGroup], bool]:
        """LLM 识别可合并组。返回 (groups, used_fallback)。

        **C9**：是否冗余/可合并完全由 LLM 判断，本方法不写任何相似度/关键词规则。
        **FR-B6**：LLM 为 None / 调用异常 / 空响应 / JSON 解析失败 → fallback（空 groups +
        used_fallback=True），不崩。
        """
        if self._llm_client is None:
            logger.info("consolidation_llm_unavailable_fallback")
            return [], True

        prompt = self._build_discovery_prompt(facts)
        valid_ids = {f.memory_id for f in facts}
        try:
            result = await self._llm_client.complete(
                messages=[{"role": "user", "content": prompt}],
                model_alias=CONSOLIDATION_MODEL_ALIAS,
                max_tokens=LLM_OUTPUT_TOKEN_BUDGET,
            )
        except Exception:
            logger.warning("consolidation_llm_call_failed_fallback", exc_info=True)
            return [], True

        text = self._extract_llm_text(result)
        if not text.strip():
            logger.warning("consolidation_llm_empty_response_fallback")
            return [], True

        groups = self._parse_groups(text, valid_ids)
        # 解析出 0 组不算 fallback——可能 LLM 真判定无可合并（正常空运行）；但若解析阶段
        # 整体失败（_parse_groups 返回 None 语义用空列表 + 标记），本实现里 _parse_groups
        # 对 JSON 解析失败返回空列表，此处无法区分"真无组"vs"解析失败"。为不误判 fallback，
        # 只在 LLM 不可用/异常/空响应时标 fallback；解析失败按"无组"对待（保守不产候选）。
        return groups, False

    def _build_discovery_prompt(self, facts: list[SorRecord]) -> str:
        """构造发现端 prompt（token budget 截断，FR-B5 同 subject_key 聚集优先）。

        C9：prompt 让 LLM 自主判断哪些事实冗余/可合并，**不**给相似度阈值/关键词规则。
        输出要求结构化 JSON（便于确定性解析转 WriteProposal[MERGE]）。
        """
        # FR-B5：同 subject_key 聚集排序（提高合并命中），再按 updated_at 已 DESC
        ordered = sorted(facts, key=lambda f: (f.subject_key, f.partition.value))

        lines: list[str] = []
        used_chars = 0
        for fact in ordered:
            # 单条事实摘要：id + partition + subject_key + content（截断长 content）
            content_preview = fact.content.strip().replace("\n", " ")
            if len(content_preview) > 200:
                content_preview = content_preview[:200] + "…"
            entry = (
                f"- id={fact.memory_id} | partition={fact.partition.value} | "
                f"subject={fact.subject_key} | content={content_preview}"
            )
            if used_chars + len(entry) > LLM_INPUT_CHAR_BUDGET:
                # 已纳入条数 = len(lines)（lines 只累加成功纳入的 entry）
                lines.append(
                    f"...（还有 {len(ordered) - len(lines)} 条事实未列出，本次不纳入）"
                )
                break
            lines.append(entry)
            used_chars += len(entry)

        facts_block = "\n".join(lines)
        return (
            "你是一个记忆整理助手。下面是用户记忆库里近期的若干事实，每条带一个唯一 id。\n"
            "请识别其中**语义重复或可以合并成一条更权威事实**的事实组"
            "（例如三次分别记录的同一时区/口味/称呼偏好）。\n\n"
            "判断标准完全由你决定，不要机械按字面相似度——要理解语义是否指向同一件事。\n"
            "只有当一组事实确实指向同一主题、合并不会丢失关键差异时才提议合并；"
            "拿不准就不要合并（宁缺毋滥，合并是破坏性操作要谨慎）。\n\n"
            f"事实列表：\n{facts_block}\n\n"
            "请用 JSON 输出，格式严格如下（不要任何额外解释文字，只输出 JSON）：\n"
            "{\n"
            '  "groups": [\n'
            "    {\n"
            '      "source_ids": ["<待合并事实的 id>", "..."],\n'
            '      "merged_content": "<合并后的一条权威事实内容>",\n'
            '      "subject_key": "<合并后事实的简短主题键，如 timezone>",\n'
            '      "rationale": "<为什么这几条该合并>",\n'
            '      "confidence": 0.0\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "如果没有任何可合并的组，返回 {\"groups\": []}。\n"
            "source_ids 必须来自上面列出的 id，每组至少 2 个 id。"
        )

    @staticmethod
    def _extract_llm_text(result: Any) -> str:
        """容错取 LLM 文本（沿用 F102 _generate_summary_llm 三路兜底范式）。"""
        if isinstance(result, str):
            return result
        content = getattr(result, "content", None)
        if isinstance(content, str):
            return content
        text_attr = getattr(result, "text", None)
        if isinstance(text_attr, str):
            return text_attr
        if isinstance(result, dict):
            for key in ("content", "text"):
                val = result.get(key)
                if isinstance(val, str):
                    return val
        return ""

    def _parse_groups(self, text: str, valid_ids: set[str]) -> list[_MergeGroup]:
        """解析 LLM JSON 输出为 _MergeGroup 列表。

        复用 F065 ``parse_llm_json_array``（处理 markdown code fence + 正则兜底 + dict 包裹
        数组提取）拿到原始组列表 → 逐组做 F127 专属校验（source_ids 必须全在 valid_ids 内 +
        至少 MIN_GROUP_SOURCE_COUNT 条 + merged_content 非空）。解析失败/结构不符返回空列表
        （保守：不产候选好过产坏候选）。**不抛**（FR-B6 不崩）。

        注：prompt 要求 ``{"groups": [...]}``——``parse_llm_json_array`` 对 dict 输入会提取
        其首个 list value（即 groups 数组），与本服务结构契合。
        """
        raw_groups = parse_llm_json_array(text)
        if raw_groups is None:
            logger.warning("consolidation_parse_groups_json_failed")
            return []

        groups: list[_MergeGroup] = []
        for rg in raw_groups:
            if not isinstance(rg, dict):
                continue
            source_ids = rg.get("source_ids")
            if not isinstance(source_ids, list):
                continue
            # 去重 + 只保留 valid（防 LLM 幻觉 id / 重复 id）
            clean_ids = []
            seen: set[str] = set()
            for sid in source_ids:
                if isinstance(sid, str) and sid in valid_ids and sid not in seen:
                    clean_ids.append(sid)
                    seen.add(sid)
            if len(clean_ids) < MIN_GROUP_SOURCE_COUNT:
                continue
            merged_content = rg.get("merged_content")
            if not isinstance(merged_content, str) or not merged_content.strip():
                continue
            subject_key = rg.get("subject_key")
            subject_key = subject_key.strip() if isinstance(subject_key, str) else ""
            rationale = rg.get("rationale")
            rationale = rationale.strip() if isinstance(rationale, str) else ""
            confidence_raw = rg.get("confidence", 0.0)
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            groups.append(
                _MergeGroup(
                    source_sor_ids=clean_ids,
                    merged_content=merged_content.strip(),
                    subject_key=subject_key,
                    rationale=rationale,
                    confidence=confidence,
                )
            )
        return groups

    # ============================================================
    # Step 3+4：propose MERGE（不 commit）→ validate → 写候选（FR-B3/B4 + C4）
    # ============================================================

    async def _propose_group(
        self,
        *,
        run_id: str,
        scope_id: str,
        root_task_id: str,
        group: _MergeGroup,
        facts_by_id: dict[str, SorRecord],
    ) -> str | None:
        """一个合并组 → propose MERGE（autocommit=False）→ validate → 写 PENDING 候选。

        **C4 红线**：``autocommit=False`` + 只 validate **不 commit**——既有事实的 MERGE
        绝不在发现端自主执行，落到 Phase D 人审后才 commit。

        Returns:
            candidate_id（成功）或 None（去重跳过 / validate 失败 / 异常）。
        """
        # 推断合并目标 partition（取组内多数源事实的 partition；混合则取第一条）
        partition = self._infer_partition(group, facts_by_id)

        # ★ 纵深防御（codex P1-1/P1-2 第二层；第一层是 _pull_window 窗口排除）：
        # 敏感性按 **any 语义** 判定——目标 partition 敏感 **或任一源事实 partition 敏感**
        # 即拒绝产候选。不能只看推断目标（P1-1 根因）：LLM 把 HEALTH 事实混进普通组时
        # 众数 partition 是非敏感 → 敏感内容会以 is_sensitive=False 走普通 SOR 明文存储。
        # 也不能产 is_sensitive=True 的 MERGE 候选（P1-2 根因）：commit 走 _commit_add，
        # _safe_sor_content 会把 content 换成 rationale 且 MERGE 不建 vault——accept 后
        # 源 SUPERSEDED、新事实只剩合并理由，毁掉敏感记忆。v0.1 一律不产（v0.2 vault-aware
        # MERGE 再放开，spec §2.2 deferred）。正常管道此分支不可达（窗口已排除敏感事实，
        # valid_ids 白名单挡住幻觉 id）——防的是窗口逻辑演化/数据侧漏网。
        group_sensitive = partition in SENSITIVE_PARTITIONS or any(
            facts_by_id[sid].partition in SENSITIVE_PARTITIONS
            for sid in group.source_sor_ids
            if sid in facts_by_id
        )
        if group_sensitive:
            logger.warning(
                "consolidation_sensitive_group_blocked",
                run_id=run_id,
                partition=partition.value,
                source_count=len(group.source_sor_ids),
            )
            return None
        is_sensitive = False  # 走到这里必非敏感（上方已拒绝）；字段保留供 v0.2 护栏

        # WriteProposal 校验要求非 NONE proposal 必须有非空 subject_key
        # （proposal.py:39-40）。LLM 漏给 subject_key 时用确定性兜底（取首个源 id 派生），
        # 不让一个空 key 整组失败。
        subject_key = group.subject_key or f"consolidated_{group.source_sor_ids[0]}"

        # WriteProposal 校验还要求非 NONE proposal 必须有非空 evidence_refs
        # （proposal.py:41-42）。合并提议的天然证据 = 被合并的源 SOR（ref_type="sor"），
        # 与 F065 inference/consolidation_service.py:372-375 MERGE 收集源 evidence 同范式。
        evidence_refs = [
            EvidenceRef(ref_id=sid, ref_type="sor") for sid in group.source_sor_ids
        ]

        content_hash = hashlib.sha256(
            group.merged_content.encode("utf-8")
        ).hexdigest()

        # NFR-4 幂等账本：同 scope 已有 pending/applying/applied 同 content_hash 候选 → 跳过
        if await self._is_duplicate_candidate(scope_id, content_hash):
            logger.info(
                "consolidation_duplicate_candidate_skipped",
                run_id=run_id,
                content_hash=content_hash[:12],
            )
            return None

        # FR-B3：转 WriteProposal[MERGE]，merge_source_ids 进 metadata（accept 后标 SUPERSEDED）
        try:
            proposal = await self._memory_service.propose_write(
                scope_id=scope_id,
                partition=partition,
                action=WriteAction.MERGE,
                subject_key=subject_key,
                content=group.merged_content,
                rationale=group.rationale or "F127 sleep-time consolidation merge proposal",
                confidence=group.confidence,
                evidence_refs=evidence_refs,
                is_sensitive=is_sensitive,
                # ★ merge_source_ids 必须是 **list**（write_service.py:177-178 commit 时
                # `for src_id in metadata.get("merge_source_ids", [])` 直接迭代——传 JSON 串
                # 会迭代成字符 char。WriteProposal.metadata 是 dict[str, Any]，list 合法；
                # 既有 F065 inference/consolidation_service.py:378 同样传 list）。
                metadata={"merge_source_ids": group.source_sor_ids},
                autocommit=False,  # ★ C4：不 commit 事务
            )
        except Exception:
            logger.exception("consolidation_propose_failed", run_id=run_id)
            return None

        # FR-B4：validate（通过但不 commit）。validate 失败 → 跳过本组（不写候选）。
        try:
            validation = await self._memory_service.validate_proposal(
                proposal.proposal_id, autocommit=False
            )
        except Exception:
            logger.exception("consolidation_validate_failed", run_id=run_id)
            return None
        if not validation.accepted:
            logger.info(
                "consolidation_proposal_rejected_by_validate",
                run_id=run_id,
                errors=validation.errors,
            )
            return None

        # 写 PENDING 候选（status 默认 PENDING）
        candidate = ConsolidationCandidate(
            candidate_id=str(ULID()),
            run_id=run_id,
            scope_id=scope_id,
            partition=partition,
            subject_key=subject_key,
            source_sor_ids=group.source_sor_ids,
            merged_content=group.merged_content,
            rationale=group.rationale,
            proposal_id=proposal.proposal_id,
            confidence=group.confidence,
            is_sensitive=is_sensitive,
            status=ConsolidationCandidateStatus.PENDING,
            content_hash=content_hash,
            created_at=datetime.now(UTC),
        )
        try:
            await self._consolidation_store.insert_candidate(candidate)
        except Exception:
            logger.exception("consolidation_insert_candidate_failed", run_id=run_id)
            return None

        # **先 commit 候选 + 提议，再 emit 事件**：emit 走 append_event_committed，其 task_seq
        # 冲突重试（PROPOSED 与 Phase B TRIGGERED / 后续 PROPOSED 都挂同一 root_task，task_seq=0
        # 会撞 UNIQUE(task_id,task_seq)）会 rollback 整个连接事务——若 insert_candidate +
        # propose/validate 未先 commit，重试 rollback 会把候选与提议一起回滚丢失。
        conn = getattr(self._consolidation_store, "_conn", None)
        if conn is not None and hasattr(conn, "commit"):
            try:
                await conn.commit()
            except Exception:
                logger.exception("consolidation_candidate_commit_failed", run_id=run_id)
                return None

        # C2：emit PROPOSED（PII 防护——candidate_id + content_hash，不含原文）。
        await self._emit_proposed(
            run_id=run_id,
            root_task_id=root_task_id,
            candidate=candidate,
        )
        return candidate.candidate_id

    @staticmethod
    def _infer_partition(
        group: _MergeGroup, facts_by_id: dict[str, SorRecord]
    ) -> MemoryPartition:
        """推断合并目标 partition：取组内源事实多数 partition；空则 CORE 兜底。"""
        from collections import Counter

        partitions = [
            facts_by_id[sid].partition
            for sid in group.source_sor_ids
            if sid in facts_by_id
        ]
        if not partitions:
            return MemoryPartition.CORE
        # most_common 取众数（混合时取第一个出现最多的）
        return Counter(partitions).most_common(1)[0][0]

    #: 幂等账本**阻断白名单**（codex 复审 round2 P2 修复——白名单而非黑名单）：
    #: - PENDING/APPLYING：同内容已有待审/在途候选，重复提议无意义；
    #: - APPLIED：已生效，重复提议是重放。
    #: 不阻断的终态显式排除在外：
    #: - REJECTED：用户拒过的内容 LLM 再提议 = 新提议让用户重新决定（既有语义）；
    #: - CONFLICT：候选因源过期/敏感防御失效——下次巩固基于**新 current 源**重新提议
    #:   必须放行，否则 accept 409 引导"等下次巩固重新提议"的恢复主流程被旧 conflict
    #:   候选吞掉。白名单式写法保证未来新增终态默认不阻断（不再重蹈黑名单覆辙）。
    _DUP_BLOCKING_STATUSES: frozenset[ConsolidationCandidateStatus] = frozenset(
        {
            ConsolidationCandidateStatus.PENDING,
            ConsolidationCandidateStatus.APPLYING,
            ConsolidationCandidateStatus.APPLIED,
        }
    )

    async def _is_duplicate_candidate(self, scope_id: str, content_hash: str) -> bool:
        """同 scope 是否已有阻断态（pending/applying/applied）同 content_hash 候选
        （NFR-4 幂等账本）。

        rejected / conflict 不阻断——前者是用户决策可重提（用户重新决定），后者是
        系统检测失效（源已变更），新一轮巩固基于新源的同内容提议必须放行（恢复主流程）。
        """
        try:
            existing = await self._consolidation_store.list_candidates(
                scope_id=scope_id, limit=500
            )
        except Exception:
            logger.exception("consolidation_dup_check_failed", scope_id=scope_id)
            return False  # 查询失败放行（宁可能产重复也不阻断巩固）
        for cand in existing:
            if (
                cand.content_hash == content_hash
                and cand.status in self._DUP_BLOCKING_STATUSES
            ):
                return True
        return False

    # ============================================================
    # 事件 emit（C2）
    # ============================================================

    async def _emit_proposed(
        self,
        *,
        run_id: str,
        root_task_id: str,
        candidate: ConsolidationCandidate,
    ) -> None:
        payload = ConsolidationProposedPayload(
            run_id=run_id,
            candidate_id=candidate.candidate_id,
            partition=candidate.partition.value,
            source_count=len(candidate.source_sor_ids),
            content_hash=candidate.content_hash,
            is_sensitive=candidate.is_sensitive,
        )
        event = Event(
            event_id=f"mcons-{ULID()}",
            task_id=root_task_id,
            task_seq=0,
            ts=datetime.now(UTC),
            type=EventType.MEMORY_CONSOLIDATION_PROPOSED,
            actor=ActorType.SYSTEM,
            payload=payload.model_dump(),
            trace_id="",
        )
        try:
            # append_event_committed：task_seq 冲突自动重试（MAX+1）+ commit 整个连接事务
            # （含 insert_candidate + propose/validate）→ 候选与提议原子落盘。update_task_pointer
            # =False：root task 是 SUCCEEDED 系统占位，不动其 pointer。
            append_committed = getattr(
                self._event_store, "append_event_committed", None
            )
            if append_committed is not None:
                await append_committed(event, update_task_pointer=False)
            else:
                await self._event_store.append_event(event)
        except Exception:
            logger.exception("consolidation_emit_proposed_failed", run_id=run_id)


__all__ = [
    "CONSOLIDATION_MODEL_ALIAS",
    "LLM_INPUT_CHAR_BUDGET",
    "LLM_OUTPUT_TOKEN_BUDGET",
    "MAX_PROPOSALS_PER_RUN",
    "ConsolidationDiscoveryService",
    "ConsolidationLLMClient",
    "DiscoveryOutcome",
]
