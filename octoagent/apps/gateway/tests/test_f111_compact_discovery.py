"""F111 Phase B — BehaviorCompactDiscoveryService 发现端单测。

覆盖 `[@test]` 绑定（spec §6）：
- AC-1 发现端产候选绝不落盘（discovery 后行为文件字节不变 + 候选 PENDING）
- AC-2 C9 无硬编码判重（grep 源码断言）
- AC-3 H1 变小护栏（输出 ≥ 原文 → SKIPPED(not_smaller)）
- AC-4 H2 占位符护栏（违反 → SKIPPED(protected_violation)；正常路径字节级保留）
- AC-5 C6 fallback（LLM None/异常/空/缺分隔符 → 0 候选不崩）
- AC-6 禁区第一层（非 eligible 不产候选）
- AC-7 静态断言发现端不 import 写核 commit
- AC-9 H6 USER.md config parity（config_drift）
- 资源护栏（too_small/too_large）+ 输入幂等（duplicate）+ no_change
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from octoagent.core.behavior_workspace import (
    PROTECTED_CLOSE_MARKER,
    PROTECTED_OPEN_MARKER,
    resolve_write_path_by_file_id,
)
from octoagent.core.models import RequesterInfo, Task
from octoagent.core.models.behavior_compact import BehaviorCompactCandidateStatus
from octoagent.core.models.enums import EventType, TaskStatus
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.services import behavior_compact_discovery as discovery_module
from octoagent.gateway.services.behavior_compact_discovery import (
    COMPACT_INPUT_CHAR_BUDGET,
    COMPACTED_DELIMITER,
    RATIONALE_DELIMITER,
    BehaviorCompactDiscoveryService,
)

_ROOT_TASK = "_behavior_compact_root_test"


class _ScriptedLLM:
    """按预置文本返回的发现端 LLM stub（记录调用现场供断言）。"""

    def __init__(self, text: str | None = None, exc: Exception | None = None) -> None:
        self._text = text
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        self.calls.append({"messages": messages, **kwargs})
        if self._exc is not None:
            raise self._exc

        class _R:
            content = self._text

        return _R()


def _contract(compacted: str, rationale: str = "合并了重复规则") -> str:
    return f"{COMPACTED_DELIMITER}\n{compacted}\n{RATIONALE_DELIMITER}\n{rationale}"


#: 含冗余规则的 AGENTS.md 测试原文（> MIN_COMPACT_SOURCE_CHARS）
_ORIGINAL = (
    "# AGENTS\n\n"
    "- 回复保持简洁，不要冗长\n"
    "- 回答用户时尽量简短，避免长篇大论\n"
    "- 输出务必精炼，不要啰嗦重复\n"
    "- commit message 用中文\n"
    "- 提交说明必须使用中文书写\n"
    "- 遇到不确定的事情要先问用户\n" * 4
)

_COMPACTED_SMALLER = "# AGENTS\n\n- 回复简洁精炼\n- commit message 用中文\n- 不确定先问用户\n"


@pytest_asyncio.fixture
async def env(tmp_path: Path):
    """真 StoreGroup + behavior 文件盘上就位 + root task（events FK）。"""
    store_group = await create_store_group(
        str(tmp_path / "test.db"), str(tmp_path / "artifacts")
    )
    project_root = tmp_path / "root"
    now = datetime.now(UTC)
    await store_group.task_store.create_task(
        Task(
            task_id=_ROOT_TASK,
            created_at=now,
            updated_at=now,
            status=TaskStatus.SUCCEEDED,
            title="F111 测试 root",
            requester=RequesterInfo(channel="system", sender_id="test"),
        )
    )
    await store_group.conn.commit()
    yield store_group, project_root
    await store_group.close()


def _write_behavior_file(
    project_root: Path, file_id: str, content: str
) -> Path:
    resolved = resolve_write_path_by_file_id(project_root, file_id)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return resolved


def _service(
    store_group: StoreGroup, project_root: Path, llm: Any
) -> BehaviorCompactDiscoveryService:
    return BehaviorCompactDiscoveryService(
        project_root=project_root,
        compact_store=store_group.behavior_compact_store,
        event_store=store_group.event_store,
        llm_client=llm,
    )


async def _skip_reasons(store_group: StoreGroup) -> list[str]:
    events = await store_group.event_store.get_events_for_task(_ROOT_TASK)
    return [
        e.payload.get("reason")
        for e in events
        if e.type == EventType.BEHAVIOR_COMPACT_SKIPPED
    ]


# ============================================================
# AC-1：产候选不落盘
# ============================================================


@pytest.mark.asyncio
async def test_discover_proposes_without_write(env):
    store_group, project_root = env
    resolved = _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    svc = _service(store_group, project_root, _ScriptedLLM(_contract(_COMPACTED_SMALLER)))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )

    assert outcome.status == "proposed"
    assert outcome.candidate_id
    # ★ C4：行为文件字节不变（发现端绝不落盘）
    assert resolved.read_text(encoding="utf-8") == _ORIGINAL
    cand = await store_group.behavior_compact_store.get_candidate(outcome.candidate_id)
    assert cand is not None
    assert cand.status is BehaviorCompactCandidateStatus.PENDING
    assert cand.size_after < cand.size_before
    assert cand.rationale == "合并了重复规则"
    # PROPOSED 事件（payload 无原文全文）
    events = await store_group.event_store.get_events_for_task(_ROOT_TASK)
    proposed = [e for e in events if e.type == EventType.BEHAVIOR_COMPACT_PROPOSED]
    assert len(proposed) == 1
    assert proposed[0].payload["candidate_id"] == outcome.candidate_id
    assert _COMPACTED_SMALLER not in str(proposed[0].payload)


# ============================================================
# AC-2 + AC-7：C9 边界 / 无自主 commit（静态断言）
# ============================================================


def test_no_hardcoded_dedup_rules():
    """C9：发现端源码不写相似度/编辑距离/阈值判重规则——判断归 LLM。"""
    source = inspect.getsource(discovery_module)
    for forbidden in (
        "SequenceMatcher",
        "difflib",
        "levenshtein",
        "similarity_threshold",
        "jaccard",
        "cosine",
    ):
        assert forbidden not in source, f"发现端源码不得含判重规则关键件：{forbidden}"


def test_no_autonomous_commit_path():
    """C4 红线：发现端模块不 import 写核 commit / 不做磁盘写入。"""
    source = inspect.getsource(discovery_module)
    for forbidden in (
        "commit_behavior_file_write",
        "prepare_behavior_file_write",
        "write_text",
        "record_behavior_version",
    ):
        assert forbidden not in source, f"发现端不得触达落盘路径：{forbidden}"


# ============================================================
# AC-3：H1 变小护栏
# ============================================================


@pytest.mark.asyncio
async def test_larger_output_rejected(env):
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    bigger = _ORIGINAL + "\n- LLM 扩写出来的新规则\n"
    svc = _service(store_group, project_root, _ScriptedLLM(_contract(bigger)))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )

    assert outcome.status == "skipped"
    assert outcome.reason == "not_smaller"
    assert await _skip_reasons(store_group) == ["not_smaller"]
    assert await store_group.behavior_compact_store.list_candidates() == []


# ============================================================
# AC-4：H2 占位符护栏
# ============================================================

_PROTECTED_SECTION = (
    f"{PROTECTED_OPEN_MARKER}\n- 核心红线：绝不删库\n{PROTECTED_CLOSE_MARKER}"
)
_ORIGINAL_WITH_PROTECTED = f"# AGENTS\n\n{_PROTECTED_SECTION}\n\n{_ORIGINAL}"


@pytest.mark.asyncio
async def test_protected_placeholder_roundtrip(env):
    """正常路径：LLM 保留占位符 → 候选内容含 PROTECTED 区段字节级原文。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL_WITH_PROTECTED)
    compacted = f"# AGENTS\n\n<<<PROTECTED_0>>>\n\n{_COMPACTED_SMALLER}"
    llm = _ScriptedLLM(_contract(compacted))
    svc = _service(store_group, project_root, llm)

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )

    assert outcome.status == "proposed"
    cand = await store_group.behavior_compact_store.get_candidate(outcome.candidate_id)
    assert cand is not None
    assert _PROTECTED_SECTION in cand.compacted_content
    assert "<<<PROTECTED_" not in cand.compacted_content
    # LLM 输入里绝无受保护内容（占位符方案核心）
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "绝不删库" not in prompt
    assert "<<<PROTECTED_0>>>" in prompt


@pytest.mark.asyncio
async def test_protected_violation_rejected(env):
    """LLM 丢占位符 → SKIPPED(protected_violation)，零候选。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL_WITH_PROTECTED)
    svc = _service(
        store_group, project_root, _ScriptedLLM(_contract(_COMPACTED_SMALLER))
    )

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )

    assert outcome.status == "skipped"
    assert outcome.reason == "protected_violation"
    assert await store_group.behavior_compact_store.list_candidates() == []


@pytest.mark.asyncio
async def test_placeholder_collision_rejected(env):
    """自查①：原文本身含占位符字面量 → SKIPPED(placeholder_collision)。"""
    store_group, project_root = env
    content = _ORIGINAL + "\n<<<PROTECTED_0>>> 用户手写的怪行\n"
    _write_behavior_file(project_root, "AGENTS.md", content)
    llm = _ScriptedLLM(_contract(_COMPACTED_SMALLER))
    svc = _service(store_group, project_root, llm)

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )

    assert outcome.status == "skipped"
    assert outcome.reason == "placeholder_collision"
    assert llm.calls == []  # 根本不该送 LLM


@pytest.mark.asyncio
async def test_protected_malformed_rejected(env):
    store_group, project_root = env
    content = f"{_ORIGINAL}\n{PROTECTED_OPEN_MARKER}\n没有闭标记\n"
    _write_behavior_file(project_root, "AGENTS.md", content)
    svc = _service(store_group, project_root, _ScriptedLLM(_contract("x")))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )

    assert outcome.status == "skipped"
    assert outcome.reason == "protected_malformed"


# ============================================================
# AC-5：C6 fallback
# ============================================================


@pytest.mark.asyncio
async def test_llm_unavailable_fallback(env):
    store_group, project_root = env
    resolved = _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    svc = _service(store_group, project_root, None)

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )

    assert outcome.status == "fallback"
    assert resolved.read_text(encoding="utf-8") == _ORIGINAL
    assert await store_group.behavior_compact_store.list_candidates() == []


@pytest.mark.asyncio
async def test_llm_exception_fallback(env):
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    svc = _service(store_group, project_root, _ScriptedLLM(exc=RuntimeError("boom")))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "fallback"


@pytest.mark.asyncio
async def test_empty_response_fallback(env):
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    svc = _service(store_group, project_root, _ScriptedLLM("   "))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "fallback"


@pytest.mark.asyncio
async def test_missing_delimiter_fallback(env):
    """缺 COMPACTED 分隔符 → fallback（LLM 忘格式）。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    svc = _service(store_group, project_root, _ScriptedLLM(_COMPACTED_SMALLER))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "fallback"
    assert outcome.reason == "contract_parse_failed"


@pytest.mark.asyncio
async def test_truncated_output_fallback(env):
    """自查③截断守卫：缺 ===RATIONALE=== 尾分隔符（输出截断）→ fallback，
    绝不让'半个文件'骗过 H1。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    truncated = f"{COMPACTED_DELIMITER}\n# AGENTS\n- 只剩一半就被截"
    svc = _service(store_group, project_root, _ScriptedLLM(truncated))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "fallback"
    assert await store_group.behavior_compact_store.list_candidates() == []


@pytest.mark.asyncio
async def test_midbody_delimiter_emission_fallback(env):
    """Codex round10 P2 闭环：模型在正文中间自发产出 ===RATIONALE===（早截断，
    正文尾巴落进 rationale）→ 歧义信号命中 → fallback，绝不产截断候选。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    # 模型把分隔符写进了正文中间；真正的分隔符+理由在更后面
    ambiguous = (
        f"{COMPACTED_DELIMITER}\n# AGENTS\n- 前半段规则\n"
        f"{RATIONALE_DELIMITER}\n- 其实这还是正文后半段\n"
        f"{RATIONALE_DELIMITER}\n真正的合并理由"
    )
    svc = _service(store_group, project_root, _ScriptedLLM(ambiguous))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "fallback"
    assert outcome.reason == "contract_parse_failed"
    assert await store_group.behavior_compact_store.list_candidates() == []


@pytest.mark.asyncio
async def test_placeholder_in_rationale_fallback(env):
    """歧义信号②：rationale 尾巴含 PROTECTED 占位符（占位符只属正文）→ fallback。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL_WITH_PROTECTED)
    ambiguous = (
        f"{COMPACTED_DELIMITER}\n# AGENTS\n- 前半段\n"
        f"{RATIONALE_DELIMITER}\n<<<PROTECTED_0>>>\n- 被切走的正文\n"
    )
    svc = _service(store_group, project_root, _ScriptedLLM(ambiguous))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "fallback"


@pytest.mark.asyncio
async def test_code_fence_stripped(env):
    """LLM 包 code fence 的常见怪癖（F127 G-lite 实测）→ 剥离后正常提议。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    fenced = f"```markdown\n{_COMPACTED_SMALLER}\n```"
    svc = _service(store_group, project_root, _ScriptedLLM(_contract(fenced)))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "proposed"
    cand = await store_group.behavior_compact_store.get_candidate(outcome.candidate_id)
    assert cand is not None
    assert not cand.compacted_content.startswith("```")


@pytest.mark.asyncio
async def test_language_fenced_output_not_stripped(env):
    """Codex round7 P2 闭环：模型合法产出单个带语言 info string 的 fenced block
    （```yaml 等）不被当 LLM 包装剥掉——只有 ```/```markdown/```md 典型包装
    形态才剥。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)  # 原文非 fence 包裹
    fenced_yaml = "```yaml\n- 合并后规则 A\n- 合并后规则 B\n```"
    svc = _service(store_group, project_root, _ScriptedLLM(_contract(fenced_yaml)))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "proposed"
    cand = await store_group.behavior_compact_store.get_candidate(outcome.candidate_id)
    assert cand is not None
    assert cand.compacted_content.startswith("```yaml")
    assert cand.compacted_content.rstrip("\n").endswith("```")


@pytest.mark.asyncio
async def test_legitimate_fence_wrapped_content_not_stripped(env):
    """Codex round2 P2 闭环：原文自身首尾就是栅栏行（合法 fenced 内容）时，
    LLM 原样保留的首尾栅栏**不得**被当外包装剥掉（静默删行=内容损坏）。"""
    store_group, project_root = env
    # 原文整体呈 fence 包裹形态（首行 ``` 开 + 末行 ``` 收），内容够长
    original = "```yaml\n" + "key_example: 值示例（填充行）\n" * 12 + "```\n"
    _write_behavior_file(project_root, "KNOWLEDGE.md", original)
    # LLM 忠实保留首尾栅栏、只精简中间
    compacted = "```yaml\n" + "key_example: 值示例（填充行）\n" * 6 + "```"
    svc = _service(store_group, project_root, _ScriptedLLM(_contract(compacted)))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="KNOWLEDGE.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "proposed"
    cand = await store_group.behavior_compact_store.get_candidate(outcome.candidate_id)
    assert cand is not None
    # 首尾栅栏行完整保留（没有被剥）
    assert cand.compacted_content.startswith("```yaml")
    assert cand.compacted_content.rstrip("\n").endswith("```")


# ============================================================
# AC-6：禁区第一层
# ============================================================


@pytest.mark.asyncio
async def test_excluded_files_skipped(env):
    store_group, project_root = env
    llm = _ScriptedLLM(_contract(_COMPACTED_SMALLER))
    svc = _service(store_group, project_root, llm)

    for file_id in ("SOUL.md", "IDENTITY.md", "BOOTSTRAP.md", "HEARTBEAT.md"):
        outcome = await svc.discover_file(
            run_id="run-1", file_id=file_id, root_task_id=_ROOT_TASK
        )
        assert outcome.status == "skipped"
        assert outcome.reason == "not_eligible"
    assert llm.calls == []  # 禁区文件根本不读不送 LLM
    assert await store_group.behavior_compact_store.list_candidates() == []


# ============================================================
# AC-9：H6 USER.md config parity
# ============================================================

_USER_MD_ORIGINAL = (
    "# USER\n\n"
    "- 称呼：Connor\n"
    "- user_timezone: Asia/Shanghai\n"
    "- compact_active: true\n"
    "- 喜好：简洁回复，不要客套话\n"
    "- 偏好：回答尽量简短精炼\n" * 8
)


@pytest.mark.asyncio
async def test_user_md_config_drift_rejected(env):
    """LLM 把机器可读配置行合并掉 → SKIPPED(config_drift)（自查②）。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "USER.md", _USER_MD_ORIGINAL)
    # 精简后丢了 compact_active 行
    drifted = "# USER\n\n- 称呼：Connor\n- user_timezone: Asia/Shanghai\n- 简洁回复\n"
    svc = _service(store_group, project_root, _ScriptedLLM(_contract(drifted)))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="USER.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "skipped"
    assert outcome.reason == "config_drift"


@pytest.mark.asyncio
async def test_user_md_active_hours_drift_rejected(env):
    """Codex round17 P1 闭环：active_hours（quiet hours 语义）被精简丢失 →
    SKIPPED(config_drift)。"""
    store_group, project_root = env
    original = (
        "# USER\n\n- 称呼：Connor\n- active_hours: 08:00-23:00\n"
        + "- 喜好：简洁回复，不要客套\n" * 8
    )
    _write_behavior_file(project_root, "USER.md", original)
    drifted = "# USER\n\n- 称呼：Connor\n- 简洁回复\n"  # active_hours 被合并掉
    svc = _service(store_group, project_root, _ScriptedLLM(_contract(drifted)))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="USER.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "skipped"
    assert outcome.reason == "config_drift"


@pytest.mark.asyncio
async def test_user_md_config_preserved_proposes(env):
    store_group, project_root = env
    _write_behavior_file(project_root, "USER.md", _USER_MD_ORIGINAL)
    kept = (
        "# USER\n\n- 称呼：Connor\n- user_timezone: Asia/Shanghai\n"
        "- compact_active: true\n- 简洁精炼回复\n"
    )
    svc = _service(store_group, project_root, _ScriptedLLM(_contract(kept)))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="USER.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "proposed"


# ============================================================
# 资源护栏 + 幂等 + no_change
# ============================================================


@pytest.mark.asyncio
async def test_too_small_skipped(env):
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", "# 短\n")
    llm = _ScriptedLLM(_contract("x"))
    svc = _service(store_group, project_root, llm)

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "skipped"
    assert outcome.reason == "too_small"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_too_large_skipped_not_truncated(env):
    """超输入预算 → SKIP 不截断（spec §0.2 归档偏离：截断=丢内容）。"""
    store_group, project_root = env
    _write_behavior_file(
        project_root, "AGENTS.md", "- 规则\n" * (COMPACT_INPUT_CHAR_BUDGET // 4)
    )
    llm = _ScriptedLLM(_contract("x"))
    svc = _service(store_group, project_root, llm)

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "skipped"
    assert outcome.reason == "too_large"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_large_protected_small_body_passes_size_guard(env):
    """Codex round12 P2 闭环：尺寸闸基准=占位后文本——大 PROTECTED 块 + 小可
    编辑体（原文超预算但 masked 远小于）是 H2 明确支持的形态，不得误拒。"""
    store_group, project_root = env
    big_protected = (
        f"{PROTECTED_OPEN_MARKER}\n"
        + "- 受保护长内容行\n" * (COMPACT_INPUT_CHAR_BUDGET // 10)
        + f"{PROTECTED_CLOSE_MARKER}"
    )
    original = f"# AGENTS\n\n{big_protected}\n\n{_ORIGINAL}"
    assert len(original) > COMPACT_INPUT_CHAR_BUDGET  # 原文体积超预算
    _write_behavior_file(project_root, "AGENTS.md", original)
    compacted_masked = f"# AGENTS\n\n<<<PROTECTED_0>>>\n\n{_COMPACTED_SMALLER}"
    llm = _ScriptedLLM(_contract(compacted_masked))
    svc = _service(store_group, project_root, llm)

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "proposed", f"masked 基准下不该 too_large：{outcome}"
    cand = await store_group.behavior_compact_store.get_candidate(outcome.candidate_id)
    assert cand is not None
    assert big_protected in cand.compacted_content  # H2 字节级保留


@pytest.mark.asyncio
async def test_no_change_skipped(env):
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    svc = _service(store_group, project_root, _ScriptedLLM(_contract(_ORIGINAL)))

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "skipped"
    assert outcome.reason == "no_change"


@pytest.mark.asyncio
async def test_duplicate_source_skipped(env):
    """输入幂等：同源已有 PENDING 候选 → 第二次不重复提议（不再调 LLM）。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    llm = _ScriptedLLM(_contract(_COMPACTED_SMALLER))
    svc = _service(store_group, project_root, llm)

    first = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert first.status == "proposed"
    second = await svc.discover_file(
        run_id="run-2", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert second.status == "skipped"
    assert second.reason == "duplicate"
    assert len(llm.calls) == 1
    assert len(await store_group.behavior_compact_store.list_candidates()) == 1


@pytest.mark.asyncio
async def test_delimiter_collision_skipped(env):
    """Codex P2 闭环：原文含契约分隔符字面量 → 整文件保守跳过（不送 LLM），
    防 LLM 原样保留后 _parse_contract 在文中分隔符处截断产生破坏性候选。"""
    store_group, project_root = env
    content = _ORIGINAL + f"\n用户手写的怪行 {RATIONALE_DELIMITER} 嵌在正文里\n"
    _write_behavior_file(project_root, "AGENTS.md", content)
    llm = _ScriptedLLM(_contract(_COMPACTED_SMALLER))
    svc = _service(store_group, project_root, llm)

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "skipped"
    assert outcome.reason == "delimiter_collision"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_persist_failure_downgrades_no_ghost(env, monkeypatch):
    """Codex P2 闭环：候选 commit 失败 → 降级 skipped(persist_error) + 不 emit
    PROPOSED + 补偿 DELETE（无幽灵候选可被后续 commit 落盘）。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    svc = _service(store_group, project_root, _ScriptedLLM(_contract(_COMPACTED_SMALLER)))

    async def _fail_commit() -> bool:
        return False

    monkeypatch.setattr(svc, "_commit_tx", _fail_commit)
    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )

    assert outcome.status == "skipped"
    assert outcome.reason == "persist_error"
    # 不 emit PROPOSED（emit 会提交整个连接事务把幽灵行落盘）
    events = await store_group.event_store.get_events_for_task(_ROOT_TASK)
    assert [
        e for e in events if e.type == EventType.BEHAVIOR_COMPACT_PROPOSED
    ] == []
    # 补偿 DELETE 抵销未提交 INSERT——同连接读不到候选
    assert await store_group.behavior_compact_store.list_candidates() == []


@pytest.mark.asyncio
async def test_shared_file_slug_canonicalized(env):
    """Codex round11 P2 闭环：SHARED 文件带非 default slug 触发 → 候选按 scope
    归零（main/default），同一物理文件不因调用方 slug 裂成多路候选。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    llm = _ScriptedLLM(_contract(_COMPACTED_SMALLER))
    svc = _service(store_group, project_root, llm)

    first = await svc.discover_file(
        run_id="run-1",
        file_id="AGENTS.md",
        root_task_id=_ROOT_TASK,
        project_slug="foo",  # 调用方误传
    )
    assert first.status == "proposed"
    cand = await store_group.behavior_compact_store.get_candidate(first.candidate_id)
    assert cand is not None
    assert cand.project_slug == "default"
    assert cand.agent_slug == "main"
    # 换个 slug 再触发 → 输入幂等账本命中（同一物理文件不裂多路）
    second = await svc.discover_file(
        run_id="run-2",
        file_id="AGENTS.md",
        root_task_id=_ROOT_TASK,
        project_slug="bar",
    )
    assert second.status == "skipped"
    assert second.reason == "duplicate"


@pytest.mark.asyncio
async def test_user_blank_lines_preserved_no_junk_candidate(env):
    """Codex round11 P2 闭环：文件首尾空行属用户内容——回显不被 strip 变'更小'
    产生纯格式垃圾候选（no_change 正常命中）。"""
    store_group, project_root = env
    content_with_blanks = "\n\n" + _ORIGINAL + "\n\n"
    _write_behavior_file(project_root, "AGENTS.md", content_with_blanks)
    svc = _service(
        store_group, project_root, _ScriptedLLM(_contract(content_with_blanks))
    )

    outcome = await svc.discover_file(
        run_id="run-1", file_id="AGENTS.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "skipped"
    assert outcome.reason == "no_change"


@pytest.mark.asyncio
async def test_missing_file_read_error(env):
    store_group, project_root = env
    svc = _service(store_group, project_root, _ScriptedLLM(_contract("x")))
    outcome = await svc.discover_file(
        run_id="run-1", file_id="KNOWLEDGE.md", root_task_id=_ROOT_TASK
    )
    assert outcome.status == "skipped"
    assert outcome.reason == "read_error"


@pytest.mark.asyncio
async def test_discover_files_aggregates(env):
    """discover_files 聚合：proposed / skipped / fallback 计数正确。"""
    store_group, project_root = env
    _write_behavior_file(project_root, "AGENTS.md", _ORIGINAL)
    _write_behavior_file(project_root, "TOOLS.md", "# 短\n")
    svc = _service(store_group, project_root, _ScriptedLLM(_contract(_COMPACTED_SMALLER)))

    outcome = await svc.discover_files(
        run_id="run-1",
        file_ids=["AGENTS.md", "TOOLS.md", "USER.md"],
        root_task_id=_ROOT_TASK,
    )
    assert outcome.files_reviewed == 3
    assert outcome.proposals_made == 1  # AGENTS 提议；TOOLS too_small；USER 缺文件
    assert len(outcome.candidate_ids) == 1
    statuses = {o.file_id: o.status for o in outcome.outcomes}
    assert statuses == {
        "AGENTS.md": "proposed",
        "TOOLS.md": "skipped",
        "USER.md": "skipped",
    }
