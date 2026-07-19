"""F140 L1 场景脚本脑：prompt-marker 路由（memU FakeChatClient 范式）。

与 F138 ``octoagent.skills.testing.ScriptedModelClient``（队列版）实现同一
``StructuredModelClientProtocol``，但**按输入路由而非按序出队**——L1 服务器
长驻、跨多条 Playwright 测试消费，队列会被后台/额外 LLM 调用 desync；
prompt-marker 让「哪条消息得到哪个脚本」与调用次序解耦（确定性属于消息本身）。

轮次判定用 ``feedback``（决策环第 2 轮携带工具执行 feedback，spike 实测）而非
内部计数——同因：不依赖跨请求共享状态。

场景表（marker 出现在用户消息里即命中）：

- ``L1-WRITE``：第 1 轮调 ``filesystem.write_text`` 写 ``l1_e2e/note.md``
  （内容 = :data:`L1_WRITE_FILE_CONTENT`），第 2 轮回 :data:`L1_WRITE_REPLY`。
- 其余消息：直接回 :data:`L1_DEFAULT_REPLY`（完成）。

常量被 Playwright 侧断言复用语义（TS 侧有对应字面量），改动需同步
``frontend/e2e/support.ts``。
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import (
    SkillExecutionContext,
    SkillOutputEnvelope,
    ToolCallSpec,
)

# --- 场景契约常量（TS 侧 frontend/e2e/support.ts 同步字面量） ---
L1_WRITE_MARKER = "L1-WRITE"
L1_WRITE_FILE_RELPATH = "l1_e2e/note.md"
L1_WRITE_FILE_CONTENT = "F140-L1-MARKER：这行内容由脚本决策环真实写盘"
L1_WRITE_REPLY = "文件已写好（L1 场景①）"
L1_DEFAULT_REPLY = "L1 默认回复"

# --- 场景③（F145 审批中心）契约常量（TS 侧同步字面量） ---
L1_COMPACT_FILE_ID = "AGENTS.md"
L1_COMPACT_ORIGINAL = (
    "# AGENTS\n\n"
    "- 回复保持简洁，不要冗长啰嗦\n"
    "- 回答用户时尽量简短，避免长篇大论\n"
    "- commit message 用中文书写\n"
    "- 提交说明必须使用中文\n"
)
L1_COMPACT_COMPACTED = (
    "# AGENTS\n\n"
    "- 回复简洁精炼（L1-COMPACT-MARKER）\n"
    "- commit message 用中文\n"
)


async def provision_approval_center_scenario(root: Any, store_group: Any) -> None:
    """场景③（F145）：写盘 AGENTS.md + 直插一条 PENDING 规则精简候选。

    不重跑 discovery 链（``test_e2e_scripted_behavior_compact`` L3 已全覆盖
    discovery→候选）——L1 只验「UI 点接受 → 真 REST accept → 真落盘 + F107 版本」
    的接线。``source_hash`` 按盘上真实内容计算，保证 accept 走 APPLIED 而非
    CONFLICT（新鲜度锚对账，behavior_compact_approval._verify_for_apply）。

    launcher wipe 重建实例 → 每次 server 启动候选恢复 PENDING；本地
    ``reuseExistingServer`` 下场景为一次性消费（spec 侧带已消费守卫）。
    """
    import hashlib
    from datetime import datetime
    from pathlib import Path

    from octoagent.core.behavior_workspace import resolve_write_path_by_file_id
    from octoagent.core.models.behavior_compact import BehaviorCompactCandidate

    target = resolve_write_path_by_file_id(Path(root), L1_COMPACT_FILE_ID)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(L1_COMPACT_ORIGINAL, encoding="utf-8")

    candidate = BehaviorCompactCandidate(
        candidate_id="l1-compact-cand",
        run_id="l1-compact-run",
        file_id=L1_COMPACT_FILE_ID,
        source_hash=hashlib.sha256(L1_COMPACT_ORIGINAL.encode("utf-8")).hexdigest(),
        compacted_content=L1_COMPACT_COMPACTED,
        rationale="合并了简洁性与中文提交两组语义重复规则（L1 场景③注入）",
        size_before=len(L1_COMPACT_ORIGINAL),
        size_after=len(L1_COMPACT_COMPACTED),
        created_at=datetime.now(UTC),
    )
    await store_group.behavior_compact_store.insert_candidate(candidate)


class L1ScenarioModelClient:
    """按 prompt marker 路由的确定性脚本脑。

    Attributes:
        calls: ``generate`` 被调用总次数（launcher 日志用，不做断言依赖——
            长驻 server 上后台调用会使其跨场景不可预测）。
    """

    def __init__(self) -> None:
        self.calls = 0

    async def generate(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        prompt: str,
        feedback: list[Any],
        attempt: int,
        step: int,
    ) -> SkillOutputEnvelope:
        self.calls += 1
        if L1_WRITE_MARKER in prompt:
            if not feedback:
                return SkillOutputEnvelope(
                    content="",
                    tool_calls=[
                        ToolCallSpec(
                            tool_name="filesystem.write_text",
                            arguments={
                                "path": L1_WRITE_FILE_RELPATH,
                                "content": L1_WRITE_FILE_CONTENT,
                            },
                        )
                    ],
                )
            return SkillOutputEnvelope(content=L1_WRITE_REPLY, complete=True)
        return SkillOutputEnvelope(content=L1_DEFAULT_REPLY, complete=True)


__all__ = [
    "L1ScenarioModelClient",
    "L1_WRITE_MARKER",
    "L1_WRITE_FILE_RELPATH",
    "L1_WRITE_FILE_CONTENT",
    "L1_WRITE_REPLY",
    "L1_DEFAULT_REPLY",
    "L1_COMPACT_FILE_ID",
    "L1_COMPACT_ORIGINAL",
    "L1_COMPACT_COMPACTED",
    "provision_approval_center_scenario",
]
