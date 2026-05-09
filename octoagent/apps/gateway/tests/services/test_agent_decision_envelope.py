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
    build_default_behavior_workspace_files,
    ensure_filesystem_skeleton,
    get_profile_allowlist,
    materialize_agent_behavior_files,
    resolve_behavior_agent_slug,
)
from octoagent.core.models.agent_context import AgentProfile
from octoagent.core.models.behavior import (
    BehaviorLayerKind,
    BehaviorPack,
    BehaviorPackFile,
    BehaviorVisibility,
)
from octoagent.gateway.services.agent_decision import (
    _generate_behavior_pack_id,
    build_behavior_layers,
    build_behavior_slice_envelope,
    invalidate_behavior_pack_cache,
    make_behavior_pack_loaded_payload,
    resolve_behavior_pack,
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

    def test_envelope_excludes_bootstrap_md(self) -> None:
        """spec §6.2 永久守护：BOOTSTRAP.md 不应进 Worker envelope（主 Agent 用户首次见面脚本）。"""
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)
        assert "BOOTSTRAP.md" not in envelope.shared_file_ids, (
            "BOOTSTRAP.md 永远不在 Worker envelope（主 Agent 用户首次见面脚本，违反 H1）"
        )

    def test_envelope_includes_files_in_worker_allowlist_dynamic(self) -> None:
        """envelope 应包含 WORKER allowlist 中所有 BehaviorPack 内存在的文件。

        动态断言（不硬编码 5/8 文件）：避免 Phase C 白名单扩展导致此测试失效。
        """
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)
        worker_allowlist = get_profile_allowlist(BehaviorLoadProfile.WORKER)
        pack_ids = {f.file_id for f in pack.files}
        expected = pack_ids & worker_allowlist
        assert set(envelope.shared_file_ids) == expected, (
            f"envelope shared_file_ids 应等于 pack ∩ allowlist = {sorted(expected)}, "
            f"实际 {sorted(envelope.shared_file_ids)}"
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
        """AC-3: shared_file_ids 语义 = "WORKER 白名单内文件 ID 列表"，而不是"share_with_workers=True 列表"。

        断言验证：IDENTITY.md（share_with_workers=False）必出现在 envelope（修复 baseline bug）；
        envelope 集合 = pack ∩ WORKER_allowlist。

        动态断言：随 Phase C 扩白名单时仍正确，不需手改 expected。
        """
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)
        worker_allowlist = get_profile_allowlist(BehaviorLoadProfile.WORKER)
        pack_ids = {f.file_id for f in pack.files}

        # 关键断言 1：IDENTITY 必在内（baseline bug 修复）
        assert "IDENTITY.md" in envelope.shared_file_ids, (
            "IDENTITY.md（share_with_workers=False）应在 envelope 内（v0.2 语义守护）"
        )

        # 关键断言 2：集合相等于 pack ∩ allowlist（动态语义）
        assert set(envelope.shared_file_ids) == pack_ids & worker_allowlist

    def test_envelope_layers_contain_role_layer_with_identity(self) -> None:
        """AC-1 + AC-2a: envelope.layers 含 ROLE layer，其内容含 IDENTITY 文件 marker。"""
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)

        role_layers = [layer for layer in envelope.layers if layer.layer == BehaviorLayerKind.ROLE]
        assert role_layers, "Phase A 后 envelope 必含 ROLE layer（IDENTITY + AGENTS）"
        role_content = role_layers[0].content
        assert "[IDENTITY.md" in role_content, "ROLE layer 内容必含 IDENTITY.md marker"


class TestPhaseCWorkerAllowlistExpansion:
    """F095 Phase C: WORKER 白名单扩到 8 文件（USER + SOUL + HEARTBEAT 进，BOOTSTRAP 不进）。"""

    def test_envelope_includes_user_soul_heartbeat_post_phase_c(self) -> None:
        """Phase C 后：USER/SOUL/HEARTBEAT 必出现在 Worker envelope。"""
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)
        for fid in ("USER.md", "SOUL.md", "HEARTBEAT.md"):
            assert fid in envelope.shared_file_ids, (
                f"{fid} 应在 Phase C Worker envelope（v0.2 修订决策）"
            )

    def test_envelope_total_file_count_post_phase_c(self) -> None:
        """Phase C 后：Worker envelope shared_file_ids 应有 8 项（与 _PROFILE_ALLOWLIST[WORKER] 等大）。"""
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)
        assert len(envelope.shared_file_ids) == 8, (
            f"Phase C 后 Worker envelope 应含 8 项，实际 {len(envelope.shared_file_ids)}"
        )

    def test_envelope_layers_cover_4_h2_core_plus_bootstrap(self) -> None:
        """Phase C: envelope 覆盖 ROLE/COMMUNICATION/SOLVING/TOOL_BOUNDARY 4 层 H2 核心 + BOOTSTRAP lifecycle layer。"""
        pack = _full_pack_baseline()
        envelope = build_behavior_slice_envelope(pack)
        layer_kinds = {layer.layer for layer in envelope.layers}

        for required in (
            BehaviorLayerKind.ROLE,
            BehaviorLayerKind.COMMUNICATION,
            BehaviorLayerKind.SOLVING,
            BehaviorLayerKind.TOOL_BOUNDARY,
            BehaviorLayerKind.BOOTSTRAP,
        ):
            assert required in layer_kinds, (
                f"Phase C envelope 必含 {required.value} layer"
            )
        # COMMUNICATION layer 应含 USER + SOUL（PROJECT 和 KNOWLEDGE 走 SOLVING）
        comm_layers = [layer for layer in envelope.layers if layer.layer == BehaviorLayerKind.COMMUNICATION]
        assert comm_layers, "Phase C envelope 必含 COMMUNICATION layer"
        comm_source_ids = set(comm_layers[0].source_file_ids)
        assert comm_source_ids == {"USER.md", "SOUL.md"}, (
            f"Phase C COMMUNICATION layer source_file_ids 应等于 {{USER, SOUL}}，"
            f"实际 {sorted(comm_source_ids)}"
        )
        # BOOTSTRAP layer 应只含 HEARTBEAT（BOOTSTRAP.md 不在白名单）
        bootstrap_layers = [
            layer for layer in envelope.layers if layer.layer == BehaviorLayerKind.BOOTSTRAP
        ]
        assert bootstrap_layers, "Phase C envelope 必含 BOOTSTRAP layer（仅 HEARTBEAT）"
        bs_source_ids = set(bootstrap_layers[0].source_file_ids)
        assert bs_source_ids == {"HEARTBEAT.md"}, (
            f"Phase C BOOTSTRAP layer 仅含 HEARTBEAT（BOOTSTRAP.md 不在白名单），"
            f"实际 {sorted(bs_source_ids)}"
        )

    def test_end_to_end_worker_pack_to_envelope_with_worker_variants(self) -> None:
        """端到端集成（覆盖 plan §0.6 Worker 创建入口）：

        kind="worker" AgentProfile → build_default_behavior_workspace_files(include_advanced=True)
        → 转 BehaviorPack → build_behavior_slice_envelope → 验证 SOUL.worker.md / HEARTBEAT.worker.md
        内容真进入 Worker LLM context（envelope.layers 内容含 worker variant 标志短语）。
        """
        worker_profile = AgentProfile(
            profile_id="prod-worker-e2e",
            name="Prod Worker E2E",
            kind="worker",
        )
        workspace_files = build_default_behavior_workspace_files(
            agent_profile=worker_profile,
            project_name="atom",
            project_slug="atom",
            include_advanced=True,
        )

        # 转换 BehaviorWorkspaceFile → BehaviorPackFile
        pack_files = [
            BehaviorPackFile(
                file_id=wf.file_id,
                title=wf.title,
                layer=wf.layer,
                content=wf.content,
                visibility=wf.visibility,
                share_with_workers=wf.share_with_workers,
                source_kind=wf.source_kind,
                budget_chars=wf.budget_chars,
                original_char_count=wf.original_char_count,
                effective_char_count=wf.effective_char_count,
                truncated=wf.truncated,
                truncation_reason=wf.truncation_reason,
            )
            for wf in workspace_files
        ]
        pack = BehaviorPack(
            pack_id="behavior-pack:prod-worker-e2e",
            profile_id=worker_profile.profile_id,
            scope="agent",
            source_chain=["test:default_templates"],
            files=pack_files,
            layers=build_behavior_layers(pack_files),
            clarification_policy={},
            metadata={},
        )

        envelope = build_behavior_slice_envelope(pack)

        # 端到端断言：worker variant 内容真进入 envelope.layers
        comm_layers = [layer for layer in envelope.layers if layer.layer == BehaviorLayerKind.COMMUNICATION]
        assert comm_layers, "端到端 envelope 必含 COMMUNICATION layer"
        comm_content = comm_layers[0].content
        # SOUL.worker.md 特征短语必出现
        assert "服务对象 = 主 Agent" in comm_content, (
            "端到端 envelope COMMUNICATION layer 必含 SOUL.worker.md 特征短语"
        )

        bootstrap_layers = [layer for layer in envelope.layers if layer.layer == BehaviorLayerKind.BOOTSTRAP]
        assert bootstrap_layers, "端到端 envelope 必含 BOOTSTRAP layer"
        bs_content = bootstrap_layers[0].content
        # HEARTBEAT.worker.md 特征短语
        assert "通过当前 Worker 回报通道" in bs_content, (
            "端到端 envelope BOOTSTRAP layer 必含 HEARTBEAT.worker.md 特征短语"
        )


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


# ============================================================================
# F095 Phase D: BEHAVIOR_PACK_LOADED + pack_id 改造（infrastructure ready）
# ============================================================================


class TestBehaviorPackIdGeneration:
    """F095 Phase D: pack_id 从 f"behavior-pack:{profile_id}" 改造为 hash 化。

    旧策略不区分 load_profile / content；F096 BEHAVIOR_PACK_USED 通过 pack_id
    引用具体 LOADED 实例必须区分。
    """

    def test_pack_id_format(self) -> None:
        """pack_id 包含 profile_id + load_profile + 16-char hex digest。"""
        files = [
            BehaviorPackFile(
                file_id="AGENTS.md",
                layer=BehaviorLayerKind.ROLE,
                content="x",
                visibility=BehaviorVisibility.SHARED,
                share_with_workers=True,
                source_kind="test",
                budget_chars=100,
                original_char_count=1,
                effective_char_count=1,
            ),
        ]
        pack_id = _generate_behavior_pack_id(
            profile_id="prof-1",
            load_profile=BehaviorLoadProfile.WORKER,
            source_chain=["chain-a"],
            files=files,
        )
        assert pack_id.startswith("behavior-pack:prof-1:worker:")
        digest = pack_id.split(":")[-1]
        assert len(digest) == 16, f"digest should be 16-char hex, got {digest!r}"

    def test_pack_id_deterministic(self) -> None:
        """同 input 同 output（cache 一致性）。"""
        files = [
            BehaviorPackFile(file_id="AGENTS.md", layer=BehaviorLayerKind.ROLE, content="x"),
        ]
        a = _generate_behavior_pack_id(profile_id="p", load_profile=BehaviorLoadProfile.WORKER, source_chain=["s"], files=files)
        b = _generate_behavior_pack_id(profile_id="p", load_profile=BehaviorLoadProfile.WORKER, source_chain=["s"], files=files)
        assert a == b, "同 input pack_id 必相同"

    def test_pack_id_changes_with_load_profile(self) -> None:
        """同 profile / 同 content / 不同 load_profile → 不同 pack_id（F096 USED 引用前提）。"""
        files = [
            BehaviorPackFile(file_id="AGENTS.md", layer=BehaviorLayerKind.ROLE, content="x"),
        ]
        a = _generate_behavior_pack_id(profile_id="p", load_profile=BehaviorLoadProfile.WORKER, source_chain=["s"], files=files)
        b = _generate_behavior_pack_id(profile_id="p", load_profile=BehaviorLoadProfile.FULL, source_chain=["s"], files=files)
        assert a != b, "同 profile 不同 load_profile 必不同 pack_id"

    def test_pack_id_changes_with_content(self) -> None:
        """同 profile / 同 load_profile / 不同 content → 不同 pack_id。"""
        f1 = [BehaviorPackFile(file_id="AGENTS.md", layer=BehaviorLayerKind.ROLE, content="x", original_char_count=1, effective_char_count=1)]
        f2 = [BehaviorPackFile(file_id="AGENTS.md", layer=BehaviorLayerKind.ROLE, content="y" * 100, original_char_count=100, effective_char_count=100)]
        a = _generate_behavior_pack_id(profile_id="p", load_profile=BehaviorLoadProfile.WORKER, source_chain=["s"], files=f1)
        b = _generate_behavior_pack_id(profile_id="p", load_profile=BehaviorLoadProfile.WORKER, source_chain=["s"], files=f2)
        assert a != b, "同 profile 不同 content 必不同 pack_id"

    def test_pack_id_changes_same_length_different_content(self) -> None:
        """Codex Phase D HIGH-1 闭环：同 file_id + 同 char_count + 不同 content → 不同 pack_id。

        baseline bug：原实现只用 file_id + char_count，同长度不同内容会撞 pack_id，
        破坏 F096 BEHAVIOR_PACK_USED → BEHAVIOR_PACK_LOADED 引用。修复后必须区分。
        """
        body_a = "AAAAAAAAAA"  # 10 chars
        body_b = "BBBBBBBBBB"  # 10 chars，相同长度
        assert len(body_a) == len(body_b)
        f1 = [BehaviorPackFile(
            file_id="AGENTS.md",
            layer=BehaviorLayerKind.ROLE,
            content=body_a,
            original_char_count=len(body_a),
            effective_char_count=len(body_a),
        )]
        f2 = [BehaviorPackFile(
            file_id="AGENTS.md",
            layer=BehaviorLayerKind.ROLE,
            content=body_b,
            original_char_count=len(body_b),
            effective_char_count=len(body_b),
        )]
        a = _generate_behavior_pack_id(profile_id="p", load_profile=BehaviorLoadProfile.WORKER, source_chain=["s"], files=f1)
        b = _generate_behavior_pack_id(profile_id="p", load_profile=BehaviorLoadProfile.WORKER, source_chain=["s"], files=f2)
        assert a != b, (
            "同 file_id + 同 char_count + 不同 content 必产生不同 pack_id "
            "（content 必进 sha256 摘要）"
        )


class TestResolveBehaviorPackCacheStateMarking:
    """F095 Phase D: resolve_behavior_pack 三条 cache miss 路径标 cache_state + pack_source。"""

    def test_empty_dir_marked_as_default_via_source_kind(self, tmp_path: Path) -> None:
        """空目录场景 → 所有 file.source_kind == 'default_template' → pack_source='default'。

        Codex Phase D MED-2 闭环：原 _resolve_filesystem_behavior_pack 不返回 None，
        无论是否真有磁盘文件都标 'filesystem'，导致 F096 audit source 失真。修复方案：
        按 file.source_kind 实测区分——所有文件 source_kind='default_template' →
        pack_source='default'；任一 filesystem 来源 → 'filesystem'。
        """
        invalidate_behavior_pack_cache()
        profile = AgentProfile(profile_id="phase-d-default", name="P", kind="main")
        pack = resolve_behavior_pack(
            agent_profile=profile,
            project_root=tmp_path,  # 空目录
            load_profile=BehaviorLoadProfile.MINIMAL,
        )
        assert pack.metadata.get("cache_state") == "miss", "首次 resolve cache miss"
        assert pack.metadata.get("pack_source") == "default", (
            f"空目录所有 file.source_kind='default_template' → pack_source='default'；"
            f"实际 pack_source={pack.metadata.get('pack_source')!r}"
        )
        # 显式断言：所有 file.source_kind 真的都是 default_template
        for f in pack.files:
            assert f.source_kind == "default_template", (
                f"空目录所有 file.source_kind 应是 default_template，{f.file_id} 实际 {f.source_kind!r}"
            )

    def test_filesystem_path_marked_as_miss_with_filesystem_source(self, tmp_path: Path) -> None:
        """filesystem 路径（已 ensure_filesystem_skeleton + materialize）标 pack_source='filesystem'。"""
        invalidate_behavior_pack_cache()
        ensure_filesystem_skeleton(tmp_path, project_slug="default", agent_slug="main")
        profile = AgentProfile(profile_id="phase-d-fs", name="Main Agent", kind="main")
        agent_slug = resolve_behavior_agent_slug(profile)
        materialize_agent_behavior_files(
            tmp_path, agent_slug=agent_slug, agent_name=profile.name, is_worker_profile=False
        )
        pack = resolve_behavior_pack(
            agent_profile=profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.WORKER,
        )
        assert pack.metadata.get("cache_state") == "miss"
        assert pack.metadata.get("pack_source") == "filesystem", (
            f"filesystem skeleton 已就位应走 filesystem 路径，实际 pack_source={pack.metadata.get('pack_source')!r}"
        )

    def test_mtime_invalidation_re_marks_miss_with_new_pack_id(self, tmp_path: Path) -> None:
        """Codex Phase D LOW-4 闭环：filesystem 文件修改后 cache 失效，重新 resolve 应：

        1. 再次返回 cache_state="miss"（caller 据此重新 emit）
        2. 新 pack_id 与旧不同（content 变更后 hash digest 不同）
        """
        invalidate_behavior_pack_cache()
        ensure_filesystem_skeleton(tmp_path, project_slug="default", agent_slug="main")
        worker_profile = AgentProfile(profile_id="phase-d-mtime", name="W", kind="worker")
        agent_slug = resolve_behavior_agent_slug(worker_profile)
        materialize_agent_behavior_files(
            tmp_path, agent_slug=agent_slug, agent_name=worker_profile.name, is_worker_profile=True,
        )

        # 第一次 resolve：cache miss
        first = resolve_behavior_pack(
            agent_profile=worker_profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.WORKER,
        )
        first_pack_id = first.pack_id
        assert first.metadata.get("cache_state") == "miss"

        # 第二次 resolve：cache hit（无 cache_state="miss"）
        second = resolve_behavior_pack(
            agent_profile=worker_profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.WORKER,
        )
        assert second.metadata.get("cache_state") != "miss"

        # 修改一个 filesystem 文件 mtime + 内容（触发 cache 失效）
        from octoagent.core.behavior_workspace import behavior_agent_dir
        soul_path = behavior_agent_dir(tmp_path.resolve(), agent_slug) / "SOUL.md"
        assert soul_path.exists(), "前置：materialize 已写入 SOUL.md"
        # 修改内容使 sha256 变化
        original = soul_path.read_text(encoding="utf-8")
        soul_path.write_text(original + "\n# F095 Phase D LOW-4 mtime test marker\n", encoding="utf-8")
        # 强制 mtime 推进（在 macOS 上某些 fs 可能精度问题）
        import os
        import time
        time.sleep(0.01)
        os.utime(soul_path, None)

        # 第三次 resolve：cache 失效后再次 miss + 新 pack_id
        third = resolve_behavior_pack(
            agent_profile=worker_profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.WORKER,
        )
        assert third.metadata.get("cache_state") == "miss", (
            "filesystem 文件修改后 cache 失效，重新 resolve 应再次标 miss"
        )
        assert third.pack_id != first_pack_id, (
            f"content 改变后 pack_id 必变；first={first_pack_id!r} third={third.pack_id!r}"
        )

    def test_cache_hit_does_not_mark_cache_state_miss(self, tmp_path: Path) -> None:
        """cache hit 时返回的 pack 不应有 cache_state='miss'（caller 据此跳 emit）。"""
        invalidate_behavior_pack_cache()
        profile = AgentProfile(profile_id="phase-d-cache", name="C", kind="main")
        # 第一次：miss
        first = resolve_behavior_pack(
            agent_profile=profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.MINIMAL,
        )
        assert first.metadata.get("cache_state") == "miss"
        # 第二次：hit
        second = resolve_behavior_pack(
            agent_profile=profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.MINIMAL,
        )
        assert second.metadata.get("cache_state") != "miss", (
            f"cache hit 时不应标 cache_state='miss'，实际 {second.metadata.get('cache_state')!r}"
        )
        assert "pack_source" not in second.metadata or second.metadata.get("pack_source") != "filesystem"


class TestMakeBehaviorPackLoadedPayload:
    """F095 Phase D: make_behavior_pack_loaded_payload helper（F096 实施时调用）。"""

    def test_payload_fields_completeness(self, tmp_path: Path) -> None:
        """payload 含全部 10 字段，与 BehaviorPackLoadedPayload schema 对齐。"""
        invalidate_behavior_pack_cache()
        profile = AgentProfile(profile_id="phase-d-payload", name="P", kind="worker")
        pack = resolve_behavior_pack(
            agent_profile=profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.WORKER,
        )
        payload = make_behavior_pack_loaded_payload(
            pack, agent_profile=profile, load_profile=BehaviorLoadProfile.WORKER
        )

        # 10 个字段全验证
        assert payload.pack_id == pack.pack_id
        assert payload.agent_id == "phase-d-payload"
        assert payload.agent_kind == "worker"
        assert payload.load_profile == "worker"
        assert payload.pack_source in {"default", "filesystem", "metadata_raw_pack"}
        assert payload.file_count == len(pack.files)
        assert payload.file_ids == [f.file_id for f in pack.files]
        assert payload.source_chain == list(pack.source_chain)
        assert payload.cache_state == "miss"
        # is_advanced_included：default fallback path 在 WORKER profile 不含 SOUL/HEARTBEAT/IDENTITY 文件吗？
        # 实际看 build_default_behavior_pack_files 走 include_advanced=False，所以 default 路径无 ADVANCED 文件
        assert isinstance(payload.is_advanced_included, bool)

    def test_ac_7b_double_agent_id_consistency(self) -> None:
        """AC-7b partial 验证：F095 helper 生成的 payload.agent_id == agent_profile.profile_id。

        Codex Phase D MED-3 闭环：F094 RecallFrame schema 实际**没有 agent_id 字段**
        （它使用 ``agent_runtime_id`` / ``agent_session_id`` / ``task_id``）。AC-7b
        双 agent 一致性的真实路径是间接关联：
            payload.agent_id (= AgentProfile.profile_id)
            → AgentRuntime.profile_id
            → RecallFrame.agent_runtime_id
        本测试只验证 F095 自身：payload.agent_id 来自 AgentProfile.profile_id；
        间接关联完整验证由 F096 集成测覆盖（用 AgentRuntime 表的 profile_id ↔
        runtime_id 映射对齐两侧 audit）。
        """
        profile = AgentProfile(profile_id="prod-worker-9999", name="W", kind="worker")
        files = [BehaviorPackFile(file_id="AGENTS.md", layer=BehaviorLayerKind.ROLE, content="x")]
        pack = BehaviorPack(
            pack_id="behavior-pack:prod-worker-9999:worker:abcd1234deadbeef",
            profile_id="prod-worker-9999",
            scope="agent",
            source_chain=["test"],
            files=files,
            layers=build_behavior_layers(files),
            metadata={"cache_state": "miss", "pack_source": "default"},
        )
        payload = make_behavior_pack_loaded_payload(
            pack, agent_profile=profile, load_profile=BehaviorLoadProfile.WORKER
        )
        # AC-7b：双 agent_id 一致性 — F095 payload.agent_id 与 F094 RecallFrame.agent_id 同 source
        assert payload.agent_id == profile.profile_id, (
            f"AC-7b 守护：payload.agent_id 必须来自 AgentProfile.profile_id，"
            f"以保证与 F094 RecallFrame.agent_id 一致；payload={payload.agent_id!r} vs "
            f"profile={profile.profile_id!r}"
        )

    def test_advanced_included_flag_filesystem_worker_path(self, tmp_path: Path) -> None:
        """worker profile + filesystem 路径 + materialize advanced → is_advanced_included=True。"""
        invalidate_behavior_pack_cache()
        worker_profile = AgentProfile(profile_id="phase-d-adv", name="W", kind="worker")
        ensure_filesystem_skeleton(tmp_path, project_slug="default", agent_slug="main")
        agent_slug = resolve_behavior_agent_slug(worker_profile)
        materialize_agent_behavior_files(
            tmp_path, agent_slug=agent_slug, agent_name=worker_profile.name, is_worker_profile=True
        )
        pack = resolve_behavior_pack(
            agent_profile=worker_profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.WORKER,
        )
        payload = make_behavior_pack_loaded_payload(
            pack, agent_profile=worker_profile, load_profile=BehaviorLoadProfile.WORKER
        )
        assert payload.is_advanced_included is True, (
            f"materialize 后 advanced 文件已写入 → payload.is_advanced_included 必为 True；"
            f"实际 file_ids={payload.file_ids}"
        )
