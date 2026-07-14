"""F111 Behavior Compactor — 发现端（单文件整文件精简提议）。

compact "发现端" = 读单个 behavior 文件全文 → PROTECTED 占位符化（H2）→ LLM 识别
语义重复/矛盾规则并输出精简后全文（C9：判断完全归 LLM）→ 确定性护栏（H1 变小 /
H3 结构 / H6 config parity）→ 写 ``behavior_compact_candidates`` 候选（status=PENDING，
等用户 accept）→ emit ``BEHAVIOR_COMPACT_PROPOSED``。

★ 架构定位（仿 F127 ``ConsolidationDiscoveryService`` 归档偏离）：确定性编排组件，
``llm_client`` 注入（测试 stub / production ``ProviderRouterMessageAdapter``）——
``tool_profile="minimal"`` 挂不到工具，free-loop 发现端撞 F127 handoff 坑 7 同一堵墙，
F111 不重试。**不造 behavior 原语**——路径解析/预算复用 behavior_workspace；落盘
**绝不在本模块**（C4：唯一落盘入口是 ``BehaviorCompactApprovalService.accept``）。

与 F127 发现端的数据面差异（spec §0.2）：
- 输出契约 = 分隔符包裹全文（§_parse_contract），非 JSON 数组；
- 挡幻觉护栏 = H1/H2/H3/H6，非 source_ids 白名单；
- 输入超预算 = SKIP 不截断（整文件重写截断=丢内容）；
- 幂等 = 输入 (file, source_hash) 阻断 {PENDING,APPLYING}，非输出 content_hash；
- model alias = "main"（整文件重写质量敏感，非 F127 的 cheap）。

继承宪法：
- C2：每个候选 emit PROPOSED；每次护栏拒绝 emit SKIPPED(reason)（payload 无原文）。
- C4：本模块只产候选不落盘（AC-7 静态断言：不 import 写核 commit）。
- C6：LLM 不可用/异常/空响应/缺分隔符 → fallback（0 候选不崩）。
- C9：是否冗余/怎么合并由 LLM 判断——本模块不写关键词/相似度/行数阈值判重规则，
  确定性层只做护栏（大小对账 / 占位符 / 非空 / config parity / 禁区 / 资源闸）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from octoagent.core.behavior_workspace import (
    COMPACT_ELIGIBLE_FILE_IDS,
    SHARED_BEHAVIOR_FILE_IDS,
    PlaceholderCollision,
    ProtectedSectionMalformed,
    ProtectedSectionViolation,
    extract_protected_sections,
    resolve_write_path_by_file_id,
    verify_and_reinsert,
)
from octoagent.core.models.behavior_compact import (
    BehaviorCompactCandidate,
    BehaviorCompactCandidateStatus,
)
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.core.models.payloads import (
    BehaviorCompactProposedPayload,
    BehaviorCompactSkippedPayload,
)
from ulid import ULID

if TYPE_CHECKING:
    from octoagent.core.store.behavior_compact_store import SqliteBehaviorCompactStore
    from octoagent.core.store.event_store import SqliteEventStore


logger = structlog.get_logger(__name__)


# ============================================================
# 常量
# ============================================================

#: compact 发现端 model alias（spec §0.2 归档偏离 F127 的 cheap）：整文件重写质量
#: 直接决定候选可用率（写坏=用户全拒=功能死），nightly ≤3 文件成本可忽略。
COMPACT_MODEL_ALIAS: str = "main"

#: 输入字符预算（FR-11 自查③收紧）：预算内文件 ≤4000 + 2 倍手工编辑超限余量。
#: 超限 → SKIPPED(too_large)，**不截断**（整文件重写截断=丢内容，spec §0.2）。
COMPACT_INPUT_CHAR_BUDGET: int = 8000

#: 输出 token 预算：须容纳精简后整文件 + rationale。
COMPACT_OUTPUT_TOKEN_BUDGET: int = 8192

#: 太小不值得 compact（资源护栏非判重规则，同 F127 MIN_GROUP_SOURCE_COUNT 性质）。
MIN_COMPACT_SOURCE_CHARS: int = 200

#: 输出契约分隔符（spec §8 契约 A'）。
COMPACTED_DELIMITER: str = "===COMPACTED==="
#: 尾分隔符**必需**（自查③截断守卫）：截断的"半个文件"天然更小能骗过 H1——
#: 缺尾分隔符一律 fallback。
RATIONALE_DELIMITER: str = "===RATIONALE==="


class BehaviorCompactLLMClient(Protocol):
    """compact 发现端 LLM 客户端最小契约（注入式，测试可 stub）。

    匹配 ``ProviderRouterMessageAdapter.complete`` 签名——production 注入它，
    单测/e2e 注入返回固定 ``.content`` 的脚本 stub。**绝不**在本服务内构造 provider。
    """

    async def complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        **kwargs: Any,
    ) -> Any:  # 返回对象需有 .content: str（_extract_llm_text 容错）
        ...


@dataclass(slots=True)
class FileCompactOutcome:
    """单文件发现结果（供运行级审计聚合 + REST trigger 返回）。"""

    file_id: str
    status: str  # "proposed" / "skipped" / "fallback"
    reason: str = ""
    candidate_id: str = ""
    size_before: int = 0
    size_after: int = 0


@dataclass(slots=True)
class CompactDiscoveryOutcome:
    """发现端一次运行的聚合产出（供编排服务写 run 审计 + 通知判断）。"""

    files_reviewed: int = 0
    proposals_made: int = 0
    fallback: bool = False
    outcomes: list[FileCompactOutcome] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)


class BehaviorCompactDiscoveryService:
    """compact 发现端（确定性编排：读文件 → 占位 → LLM 精简 → 护栏 → 写候选）。

    依赖注入（全部测试可 stub）：
    - ``project_root``：behavior 文件路径解析根。
    - ``compact_store``：候选持久化 + 输入幂等账本。
    - ``event_store``：emit PROPOSED / SKIPPED。
    - ``llm_client``：精简判断（C9 LLM 决策）；None → 直接 fallback（无 LLM 不崩）。
    """

    def __init__(
        self,
        *,
        project_root: Path,
        compact_store: SqliteBehaviorCompactStore,
        event_store: SqliteEventStore,
        llm_client: BehaviorCompactLLMClient | None = None,
    ) -> None:
        self._project_root = project_root
        self._compact_store = compact_store
        self._event_store = event_store
        self._llm_client = llm_client

    # ============================================================
    # 主入口
    # ============================================================

    async def discover_files(
        self,
        *,
        run_id: str,
        file_ids: list[str],
        root_task_id: str,
        agent_slug: str = "main",
        project_slug: str = "default",
        respect_rejected: bool = False,
    ) -> CompactDiscoveryOutcome:
        """逐文件跑发现端并聚合结果。

        **C4**：只产 PENDING 候选，**绝不落盘**——覆写在用户 accept 后由
        ``BehaviorCompactApprovalService`` 执行。

        ``respect_rejected``（Opus 自审精化）：True = 同源已有 REJECTED 也跳过
        （cron 路径——文件不变时不为同一被拒源反复提议+通知；文件一编辑即自然
        重提）；手动路径 False（用户主动=显式重新决定）。
        """
        outcome = CompactDiscoveryOutcome()
        for file_id in file_ids:
            file_outcome = await self.discover_file(
                run_id=run_id,
                file_id=file_id,
                root_task_id=root_task_id,
                agent_slug=agent_slug,
                project_slug=project_slug,
                respect_rejected=respect_rejected,
            )
            outcome.outcomes.append(file_outcome)
            outcome.files_reviewed += 1
            if file_outcome.status == "proposed":
                outcome.proposals_made += 1
                outcome.candidate_ids.append(file_outcome.candidate_id)
            elif file_outcome.status == "fallback":
                outcome.fallback = True
        return outcome

    async def discover_file(
        self,
        *,
        run_id: str,
        file_id: str,
        root_task_id: str,
        agent_slug: str = "main",
        project_slug: str = "default",
        respect_rejected: bool = False,
    ) -> FileCompactOutcome:
        """单文件发现：读全文 → 占位 → LLM → 护栏 → 写候选（不落盘）。"""
        # FR-6 禁区第一层（根治）：非 eligible 不读不送 LLM 不产候选
        if file_id not in COMPACT_ELIGIBLE_FILE_IDS:
            return await self._skip(
                run_id, file_id, root_task_id, reason="not_eligible",
                agent_slug=agent_slug, project_slug=project_slug,
            )

        # slug 按 scope 归零（Codex round11 P2，`behavior_version_key_for` 同款
        # 原则）：SHARED 文件的落盘路径不含 slug——不归零则同一物理文件会因
        # 调用方传不同 project_slug 裂成多路候选（幂等账本 key 含 slug）。
        if file_id in SHARED_BEHAVIOR_FILE_IDS:
            agent_slug = "main"
            project_slug = "default"

        # 读盘（写路径解析同款——与 behavior.write_file / restore 一致的落盘位）
        try:
            resolved = resolve_write_path_by_file_id(
                self._project_root,
                file_id,
                agent_slug=agent_slug,
                project_slug=project_slug,
            )
            if not resolved.exists():
                return await self._skip(
                    run_id, file_id, root_task_id, reason="read_error",
                    agent_slug=agent_slug, project_slug=project_slug,
                )
            original = resolved.read_text(encoding="utf-8")
        except (ValueError, OSError):
            logger.warning(
                "behavior_compact_read_failed", file_id=file_id, run_id=run_id
            )
            return await self._skip(
                run_id, file_id, root_task_id, reason="read_error",
                agent_slug=agent_slug, project_slug=project_slug,
            )

        # 分隔符碰撞守卫（Codex P2 闭环，与占位符碰撞同性质）：原文本身含契约
        # 分隔符字面量时，LLM 原样保留会让 _parse_contract 在文中分隔符处截断——
        # 截断后的"半个文件"仍可能过 H1（更小）产生破坏性候选。保守整文件跳过。
        if COMPACTED_DELIMITER in original or RATIONALE_DELIMITER in original:
            return await self._skip(
                run_id, file_id, root_task_id, reason="delimiter_collision",
                agent_slug=agent_slug, project_slug=project_slug,
            )

        # H2 前半：PROTECTED 占位符化（LLM 看不到受保护内容）
        try:
            extraction = extract_protected_sections(original)
        except PlaceholderCollision:
            return await self._skip(
                run_id, file_id, root_task_id, reason="placeholder_collision",
                agent_slug=agent_slug, project_slug=project_slug,
            )
        except ProtectedSectionMalformed:
            return await self._skip(
                run_id, file_id, root_task_id, reason="protected_malformed",
                agent_slug=agent_slug, project_slug=project_slug,
            )

        # 资源护栏（C9 边界：成本闸非判重规则）。基准 = **占位后**文本（Codex
        # round12 P2）：LLM 只见 masked 内容，输入预算（prompt 体积）与输出预算
        # （精简后 masked 全文须装进 max_tokens）的语义基准都是它——大 PROTECTED
        # 块 + 小可编辑体的文件是 H2 明确支持的形态，不得按原文体积误拒。
        masked_len = len(extraction.masked_content)
        if masked_len < MIN_COMPACT_SOURCE_CHARS:
            return await self._skip(
                run_id, file_id, root_task_id, reason="too_small",
                agent_slug=agent_slug, project_slug=project_slug,
            )
        if masked_len > COMPACT_INPUT_CHAR_BUDGET:
            return await self._skip(
                run_id, file_id, root_task_id, reason="too_large",
                agent_slug=agent_slug, project_slug=project_slug,
            )

        # 输入幂等账本（同源已有待审提议 → 跳过；查询失败放行，宁可重复不阻断）
        source_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
        try:
            duplicated = await self._compact_store.has_blocking_candidate(
                file_id=file_id,
                agent_slug=agent_slug,
                project_slug=project_slug,
                source_hash=source_hash,
                include_rejected=respect_rejected,
            )
        except Exception:
            logger.exception("behavior_compact_dup_check_failed", file_id=file_id)
            duplicated = False
        if duplicated:
            return await self._skip(
                run_id, file_id, root_task_id, reason="duplicate",
                agent_slug=agent_slug, project_slug=project_slug,
            )

        # C9：LLM 精简（判断完全归 LLM；fallback 语义 C6）
        llm_text = await self._call_llm(file_id, extraction.masked_content)
        if llm_text is None:
            return FileCompactOutcome(
                file_id=file_id, status="fallback", reason="llm_unavailable"
            )

        parsed = self._parse_contract(
            llm_text, original_masked=extraction.masked_content
        )
        if parsed is None:
            logger.warning(
                "behavior_compact_contract_parse_failed",
                file_id=file_id,
                run_id=run_id,
            )
            return FileCompactOutcome(
                file_id=file_id, status="fallback", reason="contract_parse_failed"
            )
        compacted_masked, rationale = parsed

        # H2 后半：占位符 exactly-once 校验 + 确定性插回 + 终局字节断言
        try:
            final_content = verify_and_reinsert(compacted_masked, extraction.sections)
        except ProtectedSectionViolation:
            return await self._skip(
                run_id, file_id, root_task_id, reason="protected_violation",
                agent_slug=agent_slug, project_slug=project_slug,
            )

        # H3：非空
        if not final_content.strip():
            return await self._skip(
                run_id, file_id, root_task_id, reason="empty_output",
                agent_slug=agent_slug, project_slug=project_slug,
            )
        # 尾换行规范化：契约解析剥了分隔符边界换行——原文以 \n 结尾则补回，
        # 否则"只差一个尾换行"的垃圾候选会骗过 no_change（实测抓到）。
        if original.endswith("\n") and not final_content.endswith("\n"):
            final_content += "\n"
        # H3：与原文完全相同 → 无事可提（正常空运行非 fallback）
        if final_content == original:
            return await self._skip(
                run_id, file_id, root_task_id, reason="no_change",
                agent_slug=agent_slug, project_slug=project_slug,
            )

        # H1：合并后必须严格更小（去冗余的定义就是变小）
        if len(final_content) >= len(original):
            return await self._skip(
                run_id, file_id, root_task_id, reason="not_smaller",
                agent_slug=agent_slug, project_slug=project_slug,
            )

        # H6：USER.md 机器可读字段 parity（复用生产 extractors 契约级对账）
        if file_id == "USER.md" and self._user_md_config_drifted(
            original, final_content
        ):
            return await self._skip(
                run_id, file_id, root_task_id, reason="config_drift",
                agent_slug=agent_slug, project_slug=project_slug,
            )

        # 写 PENDING 候选（C4：不落盘）
        candidate = BehaviorCompactCandidate(
            candidate_id=str(ULID()),
            run_id=run_id,
            file_id=file_id,
            agent_slug=agent_slug,
            project_slug=project_slug,
            source_hash=source_hash,
            compacted_content=final_content,
            rationale=rationale,
            size_before=len(original),
            size_after=len(final_content),
            content_hash=hashlib.sha256(final_content.encode("utf-8")).hexdigest(),
            status=BehaviorCompactCandidateStatus.PENDING,
            created_at=datetime.now(UTC),
        )
        try:
            await self._compact_store.insert_candidate(candidate)
        except Exception:
            logger.exception(
                "behavior_compact_insert_candidate_failed", file_id=file_id
            )
            return await self._skip(
                run_id, file_id, root_task_id, reason="persist_error",
                agent_slug=agent_slug, project_slug=project_slug,
            )

        # **先 commit 候选再 emit**（F127 handoff 坑 1）：emit 走 append_event_committed，
        # 其 task_seq 冲突重试会 rollback 整个连接事务——候选必须先落。
        # Codex P2 闭环：commit 失败 → 候选不 durable，**绝不** emit PROPOSED /
        # 返回 proposed（否则审批面 404 幽灵候选）。补偿 DELETE 抵销共享连接事务里
        # 未提交的 INSERT（防后续任意 commit 把半插入行落成幽灵），降级 skipped。
        # 注意此路径**不 emit SKIPPED**——emit 的 append_event_committed 会提交整个
        # 连接事务，把刚失败的 INSERT 一并落盘，与补偿目的相反（log 承担审计）。
        if not await self._commit_tx():
            try:
                await self._compact_store.delete_candidate(candidate.candidate_id)
                # 补偿后再尝试提交关闭事务（Codex round14 P2：净零效果落盘 +
                # 释放共享连接写锁；两次都失败则事务留待后续提交，DELETE 已
                # 在事务内抵销 INSERT，无幽灵候选）。
                await self._commit_tx()
            except Exception:
                logger.exception(
                    "behavior_compact_persist_compensate_failed", file_id=file_id
                )
            logger.warning(
                "behavior_compact_persist_failed_downgraded",
                run_id=run_id,
                file_id=file_id,
            )
            return FileCompactOutcome(
                file_id=file_id, status="skipped", reason="persist_error"
            )

        await self._emit_proposed(run_id, root_task_id, candidate)
        logger.info(
            "behavior_compact_proposed",
            run_id=run_id,
            file_id=file_id,
            candidate_id=candidate.candidate_id,
            size_before=candidate.size_before,
            size_after=candidate.size_after,
        )
        return FileCompactOutcome(
            file_id=file_id,
            status="proposed",
            candidate_id=candidate.candidate_id,
            size_before=candidate.size_before,
            size_after=candidate.size_after,
        )

    # ============================================================
    # LLM 调用 + 输出契约解析
    # ============================================================

    async def _call_llm(self, file_id: str, masked_content: str) -> str | None:
        """调 LLM 精简。None = fallback（不可用/异常/空响应，C6）。"""
        if self._llm_client is None:
            logger.info("behavior_compact_llm_unavailable_fallback", file_id=file_id)
            return None
        prompt = self._build_prompt(file_id, masked_content)
        try:
            result = await self._llm_client.complete(
                messages=[{"role": "user", "content": prompt}],
                model_alias=COMPACT_MODEL_ALIAS,
                max_tokens=COMPACT_OUTPUT_TOKEN_BUDGET,
            )
        except Exception:
            logger.warning(
                "behavior_compact_llm_call_failed_fallback",
                file_id=file_id,
                exc_info=True,
            )
            return None
        text = self._extract_llm_text(result)
        if not text.strip():
            logger.warning(
                "behavior_compact_llm_empty_response_fallback", file_id=file_id
            )
            return None
        return text

    @staticmethod
    def _build_prompt(file_id: str, masked_content: str) -> str:
        """构造精简 prompt。

        C9：怎么判断冗余/矛盾完全交给 LLM（不给相似度阈值/关键词规则）；确定性
        要求（占位符原样保留 / 配置行逐字保留 / 更短）由 H1/H2/H6 护栏机械兜底，
        prompt 里写是为了提高一次通过率，不依赖 LLM 守约。
        """
        return (
            f"你是一个行为规则文件整理助手。下面是用户的行为规则文件 {file_id} 的完整内容。\n"
            "文中形如 <<<PROTECTED_n>>> 的是受保护区段占位符：必须原样保留（每个恰好出现一次，"
            "不得增删改写），位置放在你认为合适的结构处。\n\n"
            "请识别其中**语义重复或矛盾的规则**并智能合并去冗余，输出精简后的完整文件。\n"
            "判断标准完全由你决定，不要机械按字面相似度——要理解语义是否指向同一条规则。\n"
            "只合并你确信语义等价/冗余的内容，合并不得丢失任何独立语义的规则；"
            "拿不准就保留原文（宁缺毋滥，合并是破坏性操作要谨慎）。\n\n"
            "硬性要求：\n"
            "- 机器可读配置行（形如 key: value 的字段行）必须逐字保留；\n"
            "- 输出必须比原文更短（若确实无可精简，原样输出原文）；\n"
            "- 保持 markdown 结构与原文语言风格。\n\n"
            f"文件内容：\n{masked_content}\n\n"
            "请严格按以下格式输出（分隔符行必须原样出现，不要任何额外说明）：\n"
            f"{COMPACTED_DELIMITER}\n"
            "<精简后的完整文件内容>\n"
            f"{RATIONALE_DELIMITER}\n"
            "<你合并了什么、为什么（简短列点）>"
        )

    @staticmethod
    def _extract_llm_text(result: Any) -> str:
        """容错取 LLM 文本（沿用 F127 三路兜底范式）。"""
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

    @staticmethod
    def _looks_fence_wrapped(text: str) -> bool:
        """文本是否呈"首行 ``` 开、末行 ``` 收"的整体包裹形态。"""
        lines = text.strip("\n").split("\n")
        return (
            len(lines) >= 2
            and lines[0].startswith("```")
            and lines[-1].strip() == "```"
        )

    #: LLM 外包装的典型开栅栏形态（在"呈现一份 markdown 文档"时的常见 info string）。
    #: 其他 info string（```yaml/```python 等）视为内容自身的栅栏，绝不剥。
    _LLM_WRAPPER_FENCE_OPENERS: frozenset[str] = frozenset(
        {"```", "```markdown", "```md"}
    )

    def _strip_code_fence(self, text: str, *, original_masked: str) -> str:
        """剥 LLM 包的**外层** code fence（G-lite 实测怪癖），三重收窄判定。

        Codex round2 P2 + round7 P2 闭环：静默剥错行=内容损坏，判定必须收窄到
        "几乎确定是 LLM 包装"才剥——三个条件全满足：
        1. 输出呈整体包裹形态（首行栅栏开、末行 ``` 收）；
        2. **原文自身不是**该形态（原文是 → 栅栏属内容，不剥）；
        3. 开栅栏是 LLM 呈现 markdown 文档的典型形态（``` / ```markdown / ```md）
           ——```yaml 等带语言 info string 的视为内容栅栏（round7：模型合法产出
           单个 fenced block 文件的场景），不剥。
        判定不剥时若 LLM 真加了包装：多出的栅栏行体现在 H4 人审 diff 可见可拒
        ——失败模式恒为"可见加行"，绝不"静默删行"。

        Codex round11 P2：**不剥时原样返回**（不再 strip("\\n")）——首尾空行属
        用户内容，无条件剥会把回显变"更小"产生纯格式垃圾候选；契约自身的边界
        换行由 ``_parse_contract`` 单个剥除。剥包装时空行判定用局部 strip 副本，
        返回内层行（包装场景下边界即栅栏行，无内容空行损失）。
        """
        detection = text.strip("\n")
        if not self._looks_fence_wrapped(detection):
            return text
        if self._looks_fence_wrapped(original_masked):
            return text
        first_line = detection.split("\n", 1)[0].strip().lower()
        if first_line not in self._LLM_WRAPPER_FENCE_OPENERS:
            return text
        return "\n".join(detection.split("\n")[1:-1])

    def _parse_contract(
        self, text: str, *, original_masked: str
    ) -> tuple[str, str] | None:
        """解析契约 A'（spec §8）。返回 (compacted_masked, rationale) 或 None。

        ``===RATIONALE===`` 尾分隔符**必需**（自查③）：输出 token 截断产生的
        "半个文件"天然更小能骗过 H1——缺失一律按 fallback 保守丢弃，把"截断"
        与"忘格式"统一处理。
        """
        start = text.find(COMPACTED_DELIMITER)
        if start == -1:
            return None
        rest = text[start + len(COMPACTED_DELIMITER):]
        end = rest.find(RATIONALE_DELIMITER)
        if end == -1:
            return None  # 截断守卫
        segment = rest[:end]
        # 只剥契约自身的**单个**边界换行（分隔符行与内容之间的那一个）——
        # 更多的首尾空行属用户内容原样保留（Codex round11 P2：无条件 strip
        # 会把回显变"更小"产生纯格式垃圾候选）。
        if segment.startswith("\n"):
            segment = segment[1:]
        if segment.endswith("\n"):
            segment = segment[:-1]
        compacted = self._strip_code_fence(segment, original_masked=original_masked)
        rationale = rest[end + len(RATIONALE_DELIMITER):].strip()
        # Codex round10 P2：模型在正文中间自发产出分隔符 → 首个 ===RATIONALE===
        # 处早截断，被切走的正文尾巴落进 rationale——三个歧义信号任一命中即
        # fallback（宁缺毋滥）：①rationale 里再现任一分隔符（真分隔符在更后面）；
        # ②rationale 里出现 PROTECTED 占位符（占位符只属于正文）。残余不可判定
        # 场景（模型恰好只产一次中缝分隔符且此后无任何信号）诚实归档：H1/H2 先
        # 兜（截掉占位符即 protected_violation），最终 H4 人审 diff 的大段删除
        # 可见可拒。输入侧碰撞守卫（delimiter_collision）已挡"原文含分隔符"向量。
        if (
            RATIONALE_DELIMITER in rationale
            or COMPACTED_DELIMITER in rationale
            or "<<<PROTECTED_" in rationale
        ):
            logger.warning("behavior_compact_ambiguous_delimiter_in_rationale")
            return None
        return compacted, rationale

    # ============================================================
    # H6：USER.md 机器可读字段 parity
    # ============================================================

    @staticmethod
    def _user_md_config_drifted(original: str, compacted: str) -> bool:
        """对账 USER.md 机器可读字段提取值（original vs compacted）。

        复用生产 extractors（F102/F127/F111 单一事实源解析函数），任一提取值不一致
        → True（config_drift，丢弃提议）。这是契约级确定性对账，不是关键词判重
        （C9 合规）；未来新增 config 字段未覆盖 = 少保护不误伤（fail-open 到 H4
        人审 diff，spec §0.1.3 归档）。
        """
        from .behavior_compact_config import (
            extract_compact_active_from_user_md,
            extract_compact_time_from_user_md,
        )
        from .consolidation_config import (
            extract_consolidation_active_from_user_md,
            extract_consolidation_max_facts_from_user_md,
            extract_consolidation_time_from_user_md,
            extract_consolidation_window_days_from_user_md,
        )
        from .daily_routine_config import (
            extract_daily_summary_time_from_user_md,
            extract_routine_active_from_user_md,
            extract_summary_channels_from_user_md,
            extract_user_timezone_from_user_md,
        )
        from .notification import extract_active_hours_from_user_md

        extractors = (
            extract_user_timezone_from_user_md,
            extract_daily_summary_time_from_user_md,
            extract_routine_active_from_user_md,
            extract_summary_channels_from_user_md,
            # Codex round17 P1：quiet hours 语义字段（NotificationService 消费）
            extract_active_hours_from_user_md,
            extract_consolidation_active_from_user_md,
            extract_consolidation_time_from_user_md,
            extract_consolidation_window_days_from_user_md,
            extract_consolidation_max_facts_from_user_md,
            extract_compact_active_from_user_md,
            extract_compact_time_from_user_md,
        )
        for extractor in extractors:
            if extractor(original) != extractor(compacted):
                logger.warning(
                    "behavior_compact_user_md_config_drift",
                    extractor=extractor.__name__,
                )
                return True
        return False

    # ============================================================
    # 事件 emit（C2）+ 事务提交
    # ============================================================

    async def _commit_tx(self) -> bool:
        """提交共享连接事务。False = 提交失败（调用方必须降级，Codex P2）。"""
        conn = getattr(self._compact_store, "_conn", None)
        if conn is None or not hasattr(conn, "commit"):
            return True  # 测试 stub 无连接语义时视为成功
        try:
            await conn.commit()
            return True
        except Exception:
            logger.exception("behavior_compact_candidate_commit_failed")
            return False

    async def _skip(
        self,
        run_id: str,
        file_id: str,
        root_task_id: str,
        *,
        reason: str,
        agent_slug: str = "",
        project_slug: str = "",
    ) -> FileCompactOutcome:
        """单文件护栏跳过：emit SKIPPED(reason) + 返回 outcome（文件零触碰）。"""
        payload = BehaviorCompactSkippedPayload(
            reason=reason,
            run_id=run_id,
            file_id=file_id,
            project_slug=project_slug,
            agent_slug=agent_slug,
        )
        await self._safe_emit(
            EventType.BEHAVIOR_COMPACT_SKIPPED, payload.model_dump(), root_task_id
        )
        logger.info(
            "behavior_compact_file_skipped",
            run_id=run_id,
            file_id=file_id,
            reason=reason,
        )
        return FileCompactOutcome(file_id=file_id, status="skipped", reason=reason)

    async def _emit_proposed(
        self, run_id: str, root_task_id: str, candidate: BehaviorCompactCandidate
    ) -> None:
        payload = BehaviorCompactProposedPayload(
            run_id=run_id,
            candidate_id=candidate.candidate_id,
            file_id=candidate.file_id,
            project_slug=candidate.project_slug,
            agent_slug=candidate.agent_slug,
            size_before=candidate.size_before,
            size_after=candidate.size_after,
            content_hash=candidate.content_hash,
        )
        await self._safe_emit(
            EventType.BEHAVIOR_COMPACT_PROPOSED, payload.model_dump(), root_task_id
        )

    async def _safe_emit(
        self, event_type: EventType, payload: dict[str, Any], root_task_id: str
    ) -> None:
        """emit 优先 committed（task_seq 冲突自动重试 MAX+1）；失败静默降级（C6）。

        update_task_pointer=False：root task 是 SUCCEEDED 系统占位，不动其 pointer
        （F127 ``_emit_proposed`` 同款）。
        """
        event = Event(
            event_id=f"bcpt-{ULID()}",
            task_id=root_task_id,
            task_seq=0,
            ts=datetime.now(UTC),
            type=event_type,
            actor=ActorType.SYSTEM,
            payload=payload,
            trace_id="",
        )
        try:
            append_committed = getattr(self._event_store, "append_event_committed", None)
            if append_committed is not None:
                await append_committed(event, update_task_pointer=False)
            else:
                await self._event_store.append_event(event)
        except Exception:
            logger.exception(
                "behavior_compact_event_append_failed",
                event_type=(
                    event.type.value if hasattr(event.type, "value") else str(event.type)
                ),
            )


__all__ = [
    "COMPACTED_DELIMITER",
    "COMPACT_INPUT_CHAR_BUDGET",
    "COMPACT_MODEL_ALIAS",
    "COMPACT_OUTPUT_TOKEN_BUDGET",
    "MIN_COMPACT_SOURCE_CHARS",
    "RATIONALE_DELIMITER",
    "BehaviorCompactDiscoveryService",
    "BehaviorCompactLLMClient",
    "CompactDiscoveryOutcome",
    "FileCompactOutcome",
]
