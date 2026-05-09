"""F095 Phase A: build_behavior_slice_envelope 双过滤收敛 + IDENTITY 修复测试。

覆盖：
- AC-3 envelope 仅按白名单过滤，不再叠加 share_with_workers AND
- AC-2a IDENTITY.md 进 Worker envelope（baseline bug 修复）
- AC-2b prompt 拼接顺序断言（IDENTITY layer 不被静默覆盖）
- shared_file_ids 字段语义变更（"白名单内文件 ID 列表"，不再是"share_with_workers=True 列表"）
"""

from __future__ import annotations

from octoagent.core.behavior_workspace import (
    BehaviorLoadProfile,
    get_profile_allowlist,
)
from octoagent.core.models.behavior import (
    BehaviorLayerKind,
    BehaviorPack,
    BehaviorPackFile,
    BehaviorVisibility,
)
from octoagent.gateway.services.agent_decision import (
    build_behavior_layers,
    build_behavior_slice_envelope,
)


def _make_pack_file(
    *,
    file_id: str,
    layer: BehaviorLayerKind,
    share_with_workers: bool,
    content: str | None = None,
) -> BehaviorPackFile:
    """构造测试用 BehaviorPackFile。"""
    body = content if content is not None else f"[{file_id}] body"
    return BehaviorPackFile(
        file_id=file_id,
        title=file_id.replace(".md", ""),
        layer=layer,
        content=body,
        visibility=(
            BehaviorVisibility.SHARED if share_with_workers else BehaviorVisibility.PRIVATE
        ),
        share_with_workers=share_with_workers,
        source_kind="test_template",
        budget_chars=4096,
        original_char_count=len(body),
        effective_char_count=len(body),
        truncated=False,
    )


def _full_pack_baseline() -> BehaviorPack:
    """构造 9 文件 BehaviorPack（与 _build_file_templates 主-Agent FULL 等效）。"""
    files = [
        _make_pack_file(file_id="AGENTS.md", layer=BehaviorLayerKind.ROLE, share_with_workers=True),
        _make_pack_file(file_id="USER.md", layer=BehaviorLayerKind.COMMUNICATION, share_with_workers=True),
        _make_pack_file(file_id="PROJECT.md", layer=BehaviorLayerKind.SOLVING, share_with_workers=True),
        _make_pack_file(file_id="KNOWLEDGE.md", layer=BehaviorLayerKind.SOLVING, share_with_workers=True),
        _make_pack_file(file_id="TOOLS.md", layer=BehaviorLayerKind.TOOL_BOUNDARY, share_with_workers=True),
        _make_pack_file(file_id="BOOTSTRAP.md", layer=BehaviorLayerKind.BOOTSTRAP, share_with_workers=True),
        _make_pack_file(file_id="SOUL.md", layer=BehaviorLayerKind.COMMUNICATION, share_with_workers=False),
        _make_pack_file(file_id="IDENTITY.md", layer=BehaviorLayerKind.ROLE, share_with_workers=False),
        _make_pack_file(file_id="HEARTBEAT.md", layer=BehaviorLayerKind.BOOTSTRAP, share_with_workers=False),
    ]
    return BehaviorPack(
        pack_id="behavior-pack:test",
        profile_id="test-main",
        scope="agent",
        source_chain=["test:default_templates"],
        files=files,
        layers=build_behavior_layers(files),
        clarification_policy={},
        metadata={},
    )


class TestBuildBehaviorSliceEnvelope:
    """F095 Phase A — envelope 仅按 WORKER 白名单过滤，不再叠加 share_with_workers AND。"""

    def test_envelope_includes_identity_md_post_phase_a(self) -> None:
        """AC-2a: IDENTITY.md（share_with_workers=False）现在出现在 Worker envelope。

        baseline bug：share_with_workers AND 子句剥离 IDENTITY，导致 IDENTITY.worker.md
        模板渲染了 Worker LLM 永远看不到。Phase A 修复后必须可见。
        """
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)

        assert "IDENTITY.md" in envelope.shared_file_ids, (
            "IDENTITY.md 应在 envelope 内（Phase A 修复后），baseline 中被 share_with_workers AND 剥离"
        )

    def test_envelope_does_not_strip_share_with_workers_false(self) -> None:
        """AC-3: envelope 不再叠加 share_with_workers AND；white-list 内的文件无视该字段。"""
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)

        identity_in_envelope = "IDENTITY.md" in envelope.shared_file_ids
        identity_share_flag = next(
            f.share_with_workers for f in pack.files if f.file_id == "IDENTITY.md"
        )
        assert identity_in_envelope, "IDENTITY 应在 envelope 内"
        assert identity_share_flag is False, "前置条件：IDENTITY share_with_workers=False"

    def test_envelope_excludes_files_outside_worker_allowlist(self) -> None:
        """Phase A 阶段（白名单未扩展前）：USER/SOUL/HEARTBEAT/BOOTSTRAP 不在 envelope。"""
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)

        worker_allowlist = get_profile_allowlist(BehaviorLoadProfile.WORKER)
        # Phase A baseline allowlist = {AGENTS, TOOLS, IDENTITY, PROJECT, KNOWLEDGE}
        for excluded in ("USER.md", "SOUL.md", "HEARTBEAT.md", "BOOTSTRAP.md"):
            assert excluded not in envelope.shared_file_ids, (
                f"{excluded} 不应在 Phase A envelope 内（不在白名单 {sorted(worker_allowlist)})"
            )

    def test_envelope_metadata_counts_match_whitelist(self) -> None:
        """metadata shared_file_count + private_file_count = total。"""
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)

        shared = envelope.metadata["shared_file_count"]
        private = envelope.metadata["private_file_count"]
        assert shared + private == len(pack.files)
        assert shared == len(envelope.shared_file_ids)

    def test_envelope_shared_file_ids_semantics_v2(self) -> None:
        """AC-3: shared_file_ids 现在是"WORKER 白名单内文件 ID 列表"，不再是"share_with_workers=True 列表"。

        断言验证：如果按旧语义（share_with_workers=True 子集），结果会少 IDENTITY；
        按新语义（白名单子集），IDENTITY 必须在内。
        """
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)

        # 旧语义：share_with_workers=True 子集 = AGENTS, USER, PROJECT, KNOWLEDGE, TOOLS, BOOTSTRAP（6 个）
        # 与白名单交集 = AGENTS, PROJECT, KNOWLEDGE, TOOLS（4 个）+ IDENTITY 不会出现 ← baseline bug
        # 新语义（v0.2）：白名单子集 = AGENTS, IDENTITY, PROJECT, KNOWLEDGE, TOOLS（5 个）
        expected_v2 = {"AGENTS.md", "IDENTITY.md", "PROJECT.md", "KNOWLEDGE.md", "TOOLS.md"}
        assert set(envelope.shared_file_ids) == expected_v2, (
            f"Phase A envelope shared_file_ids 应等于白名单子集 {expected_v2}，"
            f"实际 {set(envelope.shared_file_ids)}"
        )

    def test_envelope_layers_contain_role_layer_with_identity(self) -> None:
        """AC-1 + AC-2a: envelope.layers 含 ROLE layer，其内容含 IDENTITY 文件 marker。"""
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)

        role_layers = [layer for layer in envelope.layers if layer.layer == BehaviorLayerKind.ROLE]
        assert role_layers, "Phase A 后 envelope 必含 ROLE layer（IDENTITY + AGENTS）"
        role_content = role_layers[0].content
        assert "[IDENTITY.md" in role_content, "ROLE layer 内容必含 IDENTITY.md marker"


class TestBehaviorPromptOrdering:
    """AC-2b: prompt 拼接顺序断言——IDENTITY layer 优先级稳定。

    F095 Phase A Codex review #1 finding 1 闭环：
    Phase A 阶段 WORKER allowlist 不含 USER/SOUL → envelope 没有 COMMUNICATION layer，
    用 `assert ROLE before COMMUNICATION` 是空断言。改为对实际存在的 layer 顺序断言：
    ROLE 在 SOLVING / TOOL_BOUNDARY 之前。
    """

    def test_role_layer_precedes_solving_and_tool_boundary(self) -> None:
        """Phase A 阶段：envelope 含 ROLE / SOLVING / TOOL_BOUNDARY 三层；ROLE 必先于其他两层。

        Worker decision loop 拼接 system prompt 按 BehaviorLayerKind 顺序：
        ROLE -> COMMUNICATION -> SOLVING -> TOOL_BOUNDARY -> MEMORY_POLICY -> BOOTSTRAP。
        IDENTITY layer 不允许被后注入指令静默覆盖。
        """
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)

        layer_kinds = [layer.layer for layer in envelope.layers]
        assert BehaviorLayerKind.ROLE in layer_kinds, "envelope 必含 ROLE layer"
        assert BehaviorLayerKind.SOLVING in layer_kinds, (
            "Phase A envelope 必含 SOLVING layer（PROJECT + KNOWLEDGE 在白名单）"
        )
        assert BehaviorLayerKind.TOOL_BOUNDARY in layer_kinds, (
            "Phase A envelope 必含 TOOL_BOUNDARY layer（TOOLS 在白名单）"
        )
        role_idx = layer_kinds.index(BehaviorLayerKind.ROLE)
        solving_idx = layer_kinds.index(BehaviorLayerKind.SOLVING)
        tool_idx = layer_kinds.index(BehaviorLayerKind.TOOL_BOUNDARY)
        assert role_idx < solving_idx, "ROLE layer 必须在 SOLVING 之前"
        assert role_idx < tool_idx, "ROLE layer 必须在 TOOL_BOUNDARY 之前"

    def test_role_layer_source_file_ids_contain_identity(self) -> None:
        """ROLE layer.source_file_ids 必含 IDENTITY.md（用于 F096 审计）。"""
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)

        role_layers = [layer for layer in envelope.layers if layer.layer == BehaviorLayerKind.ROLE]
        assert role_layers, "envelope 必含 ROLE layer"
        assert "IDENTITY.md" in role_layers[0].source_file_ids, (
            "ROLE layer source_file_ids 必含 IDENTITY.md（F096 审计接口）"
        )


class TestFullProfileBehaviorZeroChange:
    """AC-6 显式断言：Phase A 改动后 FULL / 主 Agent 行为零变更（Codex Phase A finding 2 闭环）。"""

    def test_full_profile_layers_unchanged(self) -> None:
        """FULL profile 不走 envelope，全 9 文件均参与 layers 渲染。

        agent_decision.py:495-508 中 `if load_profile == WORKER` 分支只对 Worker 用 envelope；
        FULL/MINIMAL 仍用 `build_behavior_layers(pack.files)`。Phase A 改动 envelope 不应
        影响 FULL profile 文件清单与 layer 拓扑。
        """
        pack = _full_pack_baseline()
        full_layers = build_behavior_layers(pack.files)

        # FULL 应渲染全部 9 文件
        all_file_ids: set[str] = set()
        for layer in full_layers:
            all_file_ids.update(layer.source_file_ids)
        expected_full = {
            "AGENTS.md", "USER.md", "PROJECT.md", "KNOWLEDGE.md",
            "TOOLS.md", "BOOTSTRAP.md", "SOUL.md", "IDENTITY.md", "HEARTBEAT.md",
        }
        assert all_file_ids == expected_full, (
            f"FULL profile 应渲染 9 文件，实际 {sorted(all_file_ids)}"
        )

        # FULL 的 ROLE layer 也应含 IDENTITY（与之前主 Agent 行为完全一致）
        role_layers = [layer for layer in full_layers if layer.layer == BehaviorLayerKind.ROLE]
        assert role_layers and "IDENTITY.md" in role_layers[0].source_file_ids

    def test_envelope_does_not_affect_full_profile_layer_call_path(self) -> None:
        """显式锁住 build_behavior_slice_envelope 不在 FULL 路径调用。

        前置：agent_decision.py:495-508 显示 envelope 仅在 load_profile==WORKER 时调用。
        本测试不直接 mock 调用栈（单元层面），改用对照断言：
        Worker envelope shared_file_ids 是 FULL layers source_file_ids 的真子集。
        """
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)
        full_layers = build_behavior_layers(pack.files)

        full_file_ids: set[str] = set()
        for layer in full_layers:
            full_file_ids.update(layer.source_file_ids)
        envelope_ids = set(envelope.shared_file_ids)

        assert envelope_ids < full_file_ids, (
            "Worker envelope 必为 FULL files 的真子集；不应包含 FULL 没有的文件"
        )
