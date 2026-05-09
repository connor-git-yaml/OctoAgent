"""Feature 063: Behavior workspace 生命周期管理与差异化加载测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from octoagent.core.behavior_workspace import (
    _BEHAVIOR_TEMPLATE_VARIANTS,
    _PROFILE_ALLOWLIST,
    ALL_BEHAVIOR_FILE_IDS,
    BEHAVIOR_FILE_BUDGETS,
    BehaviorLoadProfile,
    OnboardingState,
    _default_content_for_file,
    _is_worker_behavior_profile,
    _onboarding_state_path,
    _template_name_for_file,
    build_default_behavior_workspace_files,
    ensure_filesystem_skeleton,
    load_onboarding_state,
    mark_onboarding_completed,
    measure_behavior_total_size,
    resolve_behavior_workspace,
    save_onboarding_state,
    truncate_behavior_content,
)
from octoagent.core.models.agent_context import AgentProfile


def _make_main_profile() -> AgentProfile:
    """创建测试用 Main Agent AgentProfile。"""
    return AgentProfile(
        profile_id="test-main",
        name="Main Agent",
    )


def _make_worker_profile() -> AgentProfile:
    """创建测试用 Worker AgentProfile。"""
    return AgentProfile(
        profile_id="test-worker",
        name="Worker",
        metadata={
            "source_kind": "worker_profile_mirror",
            "source_worker_profile_id": "wp-001",
        },
    )


def _setup_skeleton(tmp_path: Path) -> Path:
    """创建 behavior 目录骨架，返回 project_root。"""
    ensure_filesystem_skeleton(tmp_path, project_slug="default", agent_slug="main")
    return tmp_path


# ============================================================================
# Feature 090 D2: AgentProfile.kind 显式标记 worker
# ============================================================================


class TestAgentProfileKindFlag:
    """F090 D2: 验证 _is_worker_behavior_profile 优先读 kind 字段。"""

    def test_kind_worker_recognized(self) -> None:
        """新建 AgentProfile(kind="worker") 直接被识别为 worker。"""
        profile = AgentProfile(
            profile_id="test-w-001",
            name="Worker",
            kind="worker",
        )
        assert _is_worker_behavior_profile(profile) is True

    def test_kind_main_not_recognized_as_worker(self) -> None:
        """默认 kind="main" 的 AgentProfile 不被识别为 worker。"""
        profile = AgentProfile(
            profile_id="test-m-001",
            name="Main",
        )
        assert profile.kind == "main"
        assert _is_worker_behavior_profile(profile) is False

    def test_metadata_fallback_for_legacy_data(self) -> None:
        """老数据：kind 字段未填（默认 main）但 metadata 携带 worker_profile_mirror，仍走 fallback 识别为 worker。"""
        profile = AgentProfile(
            profile_id="test-legacy-001",
            name="Legacy Worker",
            metadata={
                "source_kind": "worker_profile_mirror",
                "source_worker_profile_id": "wp-001",
            },
        )
        assert profile.kind == "main"
        assert _is_worker_behavior_profile(profile) is True

    def test_kind_worker_overrides_metadata(self) -> None:
        """显式 kind=worker 优先（无需 metadata 携带 source_kind）。"""
        profile = AgentProfile(
            profile_id="test-w-002",
            name="Pure Kind Worker",
            kind="worker",
        )
        assert profile.metadata == {}
        assert _is_worker_behavior_profile(profile) is True


# ============================================================================
# Phase 1: Bootstrap 生命周期管理
# ============================================================================


class TestOnboardingState:
    """T1.1: OnboardingState 模型 + 原子读写。"""

    def test_save_and_load_state(self, tmp_path: Path) -> None:
        """保存后读取，数据一致。"""
        # 创建 BOOTSTRAP.md 避免 Path B 被触发
        bootstrap_dir = tmp_path / "behavior" / "system"
        bootstrap_dir.mkdir(parents=True)
        (bootstrap_dir / "BOOTSTRAP.md").write_text("placeholder", encoding="utf-8")

        state = OnboardingState(
            bootstrap_seeded_at="2026-03-18T00:00:00+00:00",
        )
        save_onboarding_state(tmp_path, state)

        loaded = load_onboarding_state(tmp_path)
        assert loaded.bootstrap_seeded_at == "2026-03-18T00:00:00+00:00"
        assert loaded.onboarding_completed_at is None
        assert not loaded.is_completed()

    def test_state_persistence_across_reads(self, tmp_path: Path) -> None:
        """状态持久化：写入后多次读取结果不变。"""
        state = OnboardingState(
            bootstrap_seeded_at="2026-03-18T00:00:00+00:00",
            onboarding_completed_at="2026-03-18T01:00:00+00:00",
        )
        save_onboarding_state(tmp_path, state)

        for _ in range(3):
            loaded = load_onboarding_state(tmp_path)
            assert loaded.is_completed()
            assert loaded.onboarding_completed_at == "2026-03-18T01:00:00+00:00"

    def test_atomic_write_creates_valid_json(self, tmp_path: Path) -> None:
        """原子写入产生有效 JSON。"""
        state = OnboardingState(bootstrap_seeded_at="2026-03-18T00:00:00+00:00")
        save_onboarding_state(tmp_path, state)

        state_path = _onboarding_state_path(tmp_path)
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        assert raw["bootstrap_seeded_at"] == "2026-03-18T00:00:00+00:00"
        assert raw["onboarding_completed_at"] is None


class TestBootstrapSeeding:
    """T1.2: ensure_filesystem_skeleton 创建 BOOTSTRAP.md 时写入 bootstrap_seeded_at。"""

    def test_skeleton_creates_bootstrap_and_seeds_state(self, tmp_path: Path) -> None:
        ensure_filesystem_skeleton(tmp_path)
        # BOOTSTRAP.md 应该被创建
        bootstrap_path = tmp_path / "behavior" / "system" / "BOOTSTRAP.md"
        assert bootstrap_path.exists()

        # onboarding state 应该有 seeded_at
        state = load_onboarding_state(tmp_path)
        assert state.bootstrap_seeded_at is not None

    def test_skeleton_idempotent_no_double_seed(self, tmp_path: Path) -> None:
        """第二次调用不覆盖已有的 seeded_at。"""
        ensure_filesystem_skeleton(tmp_path)
        state1 = load_onboarding_state(tmp_path)

        ensure_filesystem_skeleton(tmp_path)
        state2 = load_onboarding_state(tmp_path)

        assert state1.bootstrap_seeded_at == state2.bootstrap_seeded_at


class TestBootstrapCompletionPathA:
    """T1.4: 路径 A — `<!-- COMPLETED -->` 标记触发完成。"""

    def test_mark_onboarding_completed(self, tmp_path: Path) -> None:
        """手动调用 mark_onboarding_completed 标记完成。"""
        _setup_skeleton(tmp_path)
        state = mark_onboarding_completed(tmp_path)
        assert state.is_completed()
        assert state.onboarding_completed_at is not None

    def test_mark_idempotent(self, tmp_path: Path) -> None:
        """重复标记不改变 completed_at 时间戳。"""
        _setup_skeleton(tmp_path)
        state1 = mark_onboarding_completed(tmp_path)
        state2 = mark_onboarding_completed(tmp_path)
        assert state1.onboarding_completed_at == state2.onboarding_completed_at


class TestBootstrapCompletionPathB:
    """T1.5: 路径 B — 文件删除触发完成。"""

    def test_deleted_bootstrap_triggers_completion(self, tmp_path: Path) -> None:
        """seeded 但 BOOTSTRAP.md 已删除 → 自动标记完成。"""
        _setup_skeleton(tmp_path)
        bootstrap_path = tmp_path / "behavior" / "system" / "BOOTSTRAP.md"
        assert bootstrap_path.exists()

        # 删除 BOOTSTRAP.md
        bootstrap_path.unlink()

        # 重新加载 → 应检测到删除并自动完成
        state = load_onboarding_state(tmp_path)
        assert state.is_completed()

    def test_existing_bootstrap_no_auto_completion(self, tmp_path: Path) -> None:
        """BOOTSTRAP.md 仍存在 → 不自动完成。"""
        _setup_skeleton(tmp_path)
        state = load_onboarding_state(tmp_path)
        assert not state.is_completed()


class TestBootstrapSkipInjection:
    """T1.3: 完成后 resolve_behavior_workspace 不包含 BOOTSTRAP.md。"""

    def test_bootstrap_included_before_completion(self, tmp_path: Path) -> None:
        """未完成时 BOOTSTRAP.md 被包含在 workspace files 中。"""
        _setup_skeleton(tmp_path)
        profile = _make_main_profile()

        workspace = resolve_behavior_workspace(
            project_root=tmp_path,
            agent_profile=profile,
        )
        file_ids = [f.file_id for f in workspace.files]
        assert "BOOTSTRAP.md" in file_ids

    def test_bootstrap_excluded_after_completion(self, tmp_path: Path) -> None:
        """完成后 BOOTSTRAP.md 不再包含在 workspace files 中。"""
        _setup_skeleton(tmp_path)
        mark_onboarding_completed(tmp_path)
        profile = _make_main_profile()

        workspace = resolve_behavior_workspace(
            project_root=tmp_path,
            agent_profile=profile,
        )
        file_ids = [f.file_id for f in workspace.files]
        assert "BOOTSTRAP.md" not in file_ids


class TestBootstrapTemplate:
    """T1.6: BOOTSTRAP.md 模板包含"完成引导"指令段。"""

    def test_default_template_contains_completion_instructions(self, tmp_path: Path) -> None:
        _setup_skeleton(tmp_path)
        bootstrap_path = tmp_path / "behavior" / "system" / "BOOTSTRAP.md"
        content = bootstrap_path.read_text(encoding="utf-8")
        assert "完成引导" in content
        assert "<!-- COMPLETED -->" in content


# F084 T002：删除 TestLegacyCompatibility 类。
# 历史背景：F082 P1 引入 _detect_legacy_onboarding_completion 做"双证据"检测
# （IDENTITY.md 改 + USER.md 实质填充 → legacy 完成）。F084 退役该函数后，
# 整个 legacy 检测概念由 SnapshotStore 接管（Phase 2 实现）—— SnapshotStore 直接
# 读 USER.md 是 SoT，无需"探测"是否 legacy 完成。

# ============================================================================
# Phase 2: BehaviorLoadProfile 差异化加载
# ============================================================================


class TestBehaviorLoadProfile:
    """T2.1: BehaviorLoadProfile 枚举 + 白名单常量。"""

    def test_full_profile_includes_all_files(self) -> None:
        assert _PROFILE_ALLOWLIST[BehaviorLoadProfile.FULL] == frozenset(ALL_BEHAVIOR_FILE_IDS)

    def test_worker_profile_includes_5_files(self) -> None:
        worker_set = _PROFILE_ALLOWLIST[BehaviorLoadProfile.WORKER]
        expected = {"AGENTS.md", "TOOLS.md", "IDENTITY.md", "PROJECT.md", "KNOWLEDGE.md"}
        assert worker_set == expected
        assert len(worker_set) == 5

    def test_worker_profile_excludes_private(self) -> None:
        worker_set = _PROFILE_ALLOWLIST[BehaviorLoadProfile.WORKER]
        for excluded in ("USER.md", "SOUL.md", "HEARTBEAT.md", "BOOTSTRAP.md"):
            assert excluded not in worker_set

    def test_minimal_profile_includes_4_files(self) -> None:
        minimal_set = _PROFILE_ALLOWLIST[BehaviorLoadProfile.MINIMAL]
        expected = {"AGENTS.md", "TOOLS.md", "IDENTITY.md", "USER.md"}
        assert minimal_set == expected
        assert len(minimal_set) == 4


class TestResolveWithLoadProfile:
    """T2.2: resolve_behavior_workspace 接受 load_profile 参数。"""

    def test_full_profile_returns_all_default_files(self, tmp_path: Path) -> None:
        _setup_skeleton(tmp_path)
        profile = _make_main_profile()

        workspace = resolve_behavior_workspace(
            project_root=tmp_path,
            agent_profile=profile,
            load_profile=BehaviorLoadProfile.FULL,
        )
        file_ids = {f.file_id for f in workspace.files}
        # FULL 默认应包含 6 个核心文件（不含 advanced，除非 agent_private 文件存在）
        for fid in (
            "AGENTS.md",
            "USER.md",
            "PROJECT.md",
            "KNOWLEDGE.md",
            "TOOLS.md",
            "BOOTSTRAP.md",
        ):
            assert fid in file_ids, f"{fid} 应在 FULL profile 中"

    def test_worker_profile_returns_subset(self, tmp_path: Path) -> None:
        _setup_skeleton(tmp_path)
        profile = _make_main_profile()

        workspace = resolve_behavior_workspace(
            project_root=tmp_path,
            agent_profile=profile,
            load_profile=BehaviorLoadProfile.WORKER,
        )
        file_ids = {f.file_id for f in workspace.files}
        # WORKER 应只包含白名单文件
        for fid in ("AGENTS.md", "TOOLS.md", "PROJECT.md", "KNOWLEDGE.md"):
            assert fid in file_ids, f"{fid} 应在 WORKER profile 中"
        for excluded in ("USER.md", "SOUL.md", "HEARTBEAT.md", "BOOTSTRAP.md"):
            assert excluded not in file_ids, f"{excluded} 不应在 WORKER profile 中"

    def test_minimal_profile_returns_minimal_subset(self, tmp_path: Path) -> None:
        _setup_skeleton(tmp_path)
        profile = _make_main_profile()

        workspace = resolve_behavior_workspace(
            project_root=tmp_path,
            agent_profile=profile,
            load_profile=BehaviorLoadProfile.MINIMAL,
        )
        file_ids = {f.file_id for f in workspace.files}
        for fid in ("AGENTS.md", "TOOLS.md", "USER.md"):
            assert fid in file_ids, f"{fid} 应在 MINIMAL profile 中"
        for excluded in ("PROJECT.md", "KNOWLEDGE.md", "SOUL.md", "HEARTBEAT.md", "BOOTSTRAP.md"):
            assert excluded not in file_ids, f"{excluded} 不应在 MINIMAL profile 中"

    def test_backward_compat_default_is_full(self, tmp_path: Path) -> None:
        """不传 load_profile 等同 FULL。"""
        _setup_skeleton(tmp_path)
        profile = _make_main_profile()

        ws_default = resolve_behavior_workspace(
            project_root=tmp_path,
            agent_profile=profile,
        )
        ws_full = resolve_behavior_workspace(
            project_root=tmp_path,
            agent_profile=profile,
            load_profile=BehaviorLoadProfile.FULL,
        )
        default_ids = {f.file_id for f in ws_default.files}
        full_ids = {f.file_id for f in ws_full.files}
        assert default_ids == full_ids

    def test_full_profile_with_completed_onboarding_excludes_bootstrap(
        self, tmp_path: Path,
    ) -> None:
        """FULL profile + onboarding 已完成 → BOOTSTRAP.md 也被跳过。"""
        _setup_skeleton(tmp_path)
        mark_onboarding_completed(tmp_path)
        profile = _make_main_profile()

        workspace = resolve_behavior_workspace(
            project_root=tmp_path,
            agent_profile=profile,
            load_profile=BehaviorLoadProfile.FULL,
        )
        file_ids = {f.file_id for f in workspace.files}
        assert "BOOTSTRAP.md" not in file_ids


class TestTruncateBehaviorContent:
    """T2.3: head/tail 截断策略。"""

    def test_short_content_not_truncated(self) -> None:
        content = "Hello world"
        result = truncate_behavior_content(content, 100)
        assert result == content

    def test_exact_budget_not_truncated(self) -> None:
        content = "A" * 100
        result = truncate_behavior_content(content, 100)
        assert result == content

    def test_over_budget_truncated(self) -> None:
        content = "A" * 500
        result = truncate_behavior_content(content, 200)
        assert len(result) <= 200
        # 应包含截断标记
        assert "截断" in result or "truncat" in result.lower()

    def test_head_tail_preservation(self) -> None:
        """截断后保留文件开头和结尾。"""
        head = "HEAD_MARKER_" * 10  # 120 chars
        middle = "MIDDLE_" * 100  # 700 chars
        tail = "TAIL_MARKER_" * 10  # 120 chars
        content = head + middle + tail

        result = truncate_behavior_content(content, 300)
        assert result.startswith("HEAD_MARKER_")
        assert result.endswith("TAIL_MARKER_")

    def test_below_min_budget_returns_empty(self) -> None:
        """预算低于 64 字符 → 返回空字符串。"""
        result = truncate_behavior_content("A" * 100, 30)
        assert result == ""

    def test_truncation_marker_contains_size_info(self) -> None:
        content = "A" * 1000
        result = truncate_behavior_content(content, 200)
        assert "1000" in result  # 原文大小
        assert "200" in result  # 预算大小


class TestMeasureBehaviorTotalSize:
    """T3.1（部分）: 行为文件总大小测量。"""

    def test_fresh_skeleton_has_reasonable_size(self, tmp_path: Path) -> None:
        _setup_skeleton(tmp_path)
        sizes = measure_behavior_total_size(tmp_path)
        assert sizes["__total__"] > 0
        assert "AGENTS.md" in sizes
        assert sizes["AGENTS.md"] > 0

    def test_empty_project_has_zero_total(self, tmp_path: Path) -> None:
        sizes = measure_behavior_total_size(tmp_path)
        assert sizes["__total__"] == 0


# ============================================================================
# Feature 065: 默认模板内容改进 -- 预算合规与内容域覆盖测试
# ============================================================================


class TestDefaultTemplateBudgetCompliance:
    """T014: 全量字符预算合规参数化测试。"""

    @pytest.mark.parametrize(
        "file_id,is_worker",
        [
            ("AGENTS.md", False),
            ("USER.md", False),
            ("PROJECT.md", False),
            ("KNOWLEDGE.md", False),
            ("TOOLS.md", False),
            ("BOOTSTRAP.md", False),
            ("SOUL.md", False),
            ("IDENTITY.md", False),
            ("IDENTITY.md", True),
            ("HEARTBEAT.md", False),
        ],
    )
    def test_default_template_within_budget(self, file_id: str, is_worker: bool) -> None:
        """每个默认模板的字符数 <= 预算上限且 >= 30% 下限。"""
        content = _default_content_for_file(
            file_id=file_id,
            is_worker_profile=is_worker,
            agent_name="TestAgent",
            project_label="test-project",
        )
        budget = BEHAVIOR_FILE_BUDGETS[file_id]
        char_count = len(content)
        variant = "Worker" if is_worker else "Main Agent"
        assert char_count <= budget, (
            f"{file_id}({variant}) 超预算: {char_count} > {budget}"
        )
        assert char_count >= int(budget * 0.3), (
            f"{file_id}({variant}) 内容过少: {char_count} < 30% of {budget}"
        )


class TestDefaultTemplateContentDomains:
    """T015: 内容域覆盖关键词测试。"""

    def test_agents_content_domains(self) -> None:
        """共享版 AGENTS.md 包含文件用途、协作规则、存储边界和治理。"""
        content = _default_content_for_file(
            file_id="AGENTS.md",
            is_worker_profile=False,
            agent_name="Agent",
            project_label="default",
        )
        assert "文件用途" in content
        assert "共享协作规则" in content
        assert "Memory" in content or "记忆" in content or "内存" in content
        assert "project_path_manifest" in content
        assert "安全" in content or "红线" in content

    def test_tools_content_domains(self) -> None:
        """TOOLS.md 包含优先级、secrets 安全、delegate 规范、读写指引。"""
        content = _default_content_for_file(
            file_id="TOOLS.md",
            is_worker_profile=False,
            agent_name="Agent",
            project_label="default",
        )
        assert "优先级" in content or "优先" in content
        assert "secret" in content.lower() or "SecretService" in content
        assert "delegate" in content.lower() or "委派" in content
        assert "filesystem" in content or "读" in content

    def test_bootstrap_content_domains(self) -> None:
        """BOOTSTRAP.md 包含完成引导关键词、COMPLETED 标记、称呼引导步骤。"""
        content = _default_content_for_file(
            file_id="BOOTSTRAP.md",
            is_worker_profile=False,
            agent_name="Agent",
            project_label="default",
        )
        assert "完成引导" in content
        assert "<!-- COMPLETED -->" in content
        assert "称呼" in content or "名称" in content

    def test_soul_content_domains(self) -> None:
        """SOUL.md 包含价值观/原则和认知边界。"""
        content = _default_content_for_file(
            file_id="SOUL.md",
            is_worker_profile=False,
            agent_name="Agent",
            project_label="default",
        )
        assert "价值观" in content or "原则" in content
        assert "边界" in content or "不确定" in content

    def test_identity_main_content_domains(self) -> None:
        """Main Agent 版 IDENTITY.md 包含 agent_name 插值、主 Agent 角色、proposal 权限。"""
        content = _default_content_for_file(
            file_id="IDENTITY.md",
            is_worker_profile=False,
            agent_name="TestMain",
            project_label="default",
        )
        assert "TestMain" in content
        assert "默认会话" in content or "Main Agent" in content
        assert "proposal" in content

    def test_identity_worker_content_domains(self) -> None:
        """Worker 版 IDENTITY.md 包含 agent_name 插值、specialist/worker、proposal 权限。"""
        content = _default_content_for_file(
            file_id="IDENTITY.md",
            is_worker_profile=True,
            agent_name="TestWorker",
            project_label="default",
        )
        assert "TestWorker" in content
        assert "specialist" in content or "worker" in content.lower()
        assert "proposal" in content

    def test_user_content_domains(self) -> None:
        """USER.md 包含 Memory/记忆存储边界提示和偏好/习惯框架。"""
        content = _default_content_for_file(
            file_id="USER.md",
            is_worker_profile=False,
            agent_name="Agent",
            project_label="default",
        )
        assert "Memory" in content or "记忆" in content
        assert "偏好" in content or "习惯" in content

    def test_project_content_domains(self) -> None:
        """PROJECT.md 包含 project_label 插值、术语/目录/验收框架。"""
        content = _default_content_for_file(
            file_id="PROJECT.md",
            is_worker_profile=False,
            agent_name="Agent",
            project_label="my-awesome-project",
        )
        assert "my-awesome-project" in content
        assert "术语" in content or "目录" in content or "验收" in content

    def test_knowledge_content_domains(self) -> None:
        """KNOWLEDGE.md 包含引用/入口原则、canonical 引用、更新触发。"""
        content = _default_content_for_file(
            file_id="KNOWLEDGE.md",
            is_worker_profile=False,
            agent_name="Agent",
            project_label="default",
        )
        assert "引用" in content or "入口" in content
        assert "canonical" in content
        assert "更新" in content

    def test_heartbeat_content_domains(self) -> None:
        """HEARTBEAT.md 包含自检/检查、进度/报告、收口标准。"""
        content = _default_content_for_file(
            file_id="HEARTBEAT.md",
            is_worker_profile=False,
            agent_name="Agent",
            project_label="default",
        )
        assert "自检" in content or "检查" in content
        assert "进度" in content or "报告" in content
        assert "收口" in content


# ============================================================================
# F095 Phase B: Worker 私有模板（SOUL.worker.md / HEARTBEAT.worker.md）+ variant 注册
# ============================================================================


class TestWorkerVariantTemplates:
    """F095 Phase B: _BEHAVIOR_TEMPLATE_VARIANTS 含 IDENTITY/SOUL/HEARTBEAT 共 3 个 worker variant。"""

    def test_variants_contain_three_worker_entries(self) -> None:
        """Phase B 后 variant 表含 3 个 worker variant 条目。"""
        worker_entries = {
            file_id
            for (file_id, is_worker), _template_name in _BEHAVIOR_TEMPLATE_VARIANTS.items()
            if is_worker is True
        }
        assert worker_entries == {"IDENTITY.md", "SOUL.md", "HEARTBEAT.md"}, (
            f"Phase B worker variant 应为 {{IDENTITY, SOUL, HEARTBEAT}}，实际 {worker_entries}"
        )

    def test_template_dispatch_worker_profile(self) -> None:
        """is_worker_profile=True 时 IDENTITY/SOUL/HEARTBEAT 派发 worker variant。"""
        assert _template_name_for_file(file_id="IDENTITY.md", is_worker_profile=True) == "IDENTITY.worker.md"
        assert _template_name_for_file(file_id="SOUL.md", is_worker_profile=True) == "SOUL.worker.md"
        assert _template_name_for_file(file_id="HEARTBEAT.md", is_worker_profile=True) == "HEARTBEAT.worker.md"

    def test_template_dispatch_main_profile(self) -> None:
        """is_worker_profile=False 时派发主 variant（行为零变更守护）。"""
        assert _template_name_for_file(file_id="IDENTITY.md", is_worker_profile=False) == "IDENTITY.main.md"
        # SOUL/HEARTBEAT 主版没有 .main.md variant，fall through 到 file_id 自己
        assert _template_name_for_file(file_id="SOUL.md", is_worker_profile=False) == "SOUL.md"
        assert _template_name_for_file(file_id="HEARTBEAT.md", is_worker_profile=False) == "HEARTBEAT.md"

    def test_worker_variant_content_no_placeholder_leak(self) -> None:
        """worker variant 渲染后不漏 __AGENT_NAME__ / __PROJECT_LABEL__ 等 placeholder。"""
        for file_id in ("IDENTITY.md", "SOUL.md", "HEARTBEAT.md"):
            content = _default_content_for_file(
                file_id=file_id,
                is_worker_profile=True,
                agent_name="WorkerBot",
                project_label="atom",
            )
            for placeholder in ("__AGENT_NAME__", "__PROJECT_LABEL__"):
                assert placeholder not in content, (
                    f"{file_id} worker variant 渲染后不应残留 placeholder {placeholder}"
                )

    def test_worker_variant_h1_philosophy_guard(self) -> None:
        """worker variant 内容必含 H1 哲学守护关键词（主 Agent / A2A / 不主动对话用户）。"""
        # SOUL.worker.md: 必含"主 Agent" + "A2A"
        soul = _default_content_for_file(
            file_id="SOUL.md",
            is_worker_profile=True,
            agent_name="W",
            project_label="p",
        )
        assert "主 Agent" in soul, "SOUL.worker.md 必显式提及主 Agent（H1 哲学守护）"
        assert "A2A" in soul, "SOUL.worker.md 必显式提及 A2A 状态机回报通道"
        assert "不主动" in soul or "不直接" in soul, (
            "SOUL.worker.md 必显式约束不主动 / 不直接与用户对话（H1 守护）"
        )

        # HEARTBEAT.worker.md: 必含"主 Agent" + "A2A" + escalate
        hb = _default_content_for_file(
            file_id="HEARTBEAT.md",
            is_worker_profile=True,
            agent_name="W",
            project_label="p",
        )
        assert "主 Agent" in hb, "HEARTBEAT.worker.md 必显式提及主 Agent"
        assert "A2A" in hb, "HEARTBEAT.worker.md 必显式提及 A2A"
        assert "escalate" in hb.lower() or "回报" in hb, (
            "HEARTBEAT.worker.md 必含 escalate / 回报 关键路径"
        )


class TestWorkerWorkspaceFilesInit:
    """F095 Phase B: build_default_behavior_workspace_files 在 worker profile 派发 worker variant。"""

    def test_worker_advanced_files_use_worker_variants(self, tmp_path: Path) -> None:
        """Worker AgentProfile + include_advanced=True → IDENTITY/SOUL/HEARTBEAT 用 worker variant。"""
        worker_profile = _make_worker_profile()
        files = build_default_behavior_workspace_files(
            agent_profile=worker_profile,
            project_name="atom",
            project_slug="atom",
            include_advanced=True,
        )
        files_by_id = {f.file_id: f for f in files}

        for fid in ("IDENTITY.md", "SOUL.md", "HEARTBEAT.md"):
            assert fid in files_by_id, f"{fid} 应在 worker advanced files 中"

        # IDENTITY worker 内容必含 specialist / Worker / Butler 关键词
        assert "specialist" in files_by_id["IDENTITY.md"].content or "Worker" in files_by_id["IDENTITY.md"].content
        # SOUL worker 内容必含哲学守护关键词
        assert "主 Agent" in files_by_id["SOUL.md"].content
        # HEARTBEAT worker 内容必含 A2A / 主 Agent
        assert "A2A" in files_by_id["HEARTBEAT.md"].content

    def test_main_advanced_files_use_main_variants(self, tmp_path: Path) -> None:
        """Main AgentProfile + include_advanced=True → IDENTITY 用 main variant；SOUL/HEARTBEAT 用通用版（行为零变更）。

        Codex Phase B finding LOW2 闭环：用更稳定的 Worker-only 完整片段断言，而非通用词汇
        ("不主动" / "不直接")——避免通用 SOUL.md 未来合法演进时误警。
        """
        main_profile = _make_main_profile()
        files = build_default_behavior_workspace_files(
            agent_profile=main_profile,
            project_name="atom",
            project_slug="atom",
            include_advanced=True,
        )
        files_by_id = {f.file_id: f for f in files}

        # IDENTITY 必使用 main variant
        assert "IDENTITY.md" in files_by_id

        # SOUL 主版不应含 worker variant 特有的完整短语
        soul_main = files_by_id["SOUL.md"].content
        worker_only_fragments = (
            "服务对象 = 主 Agent",
            "通过当前 Worker 回报通道",
            "不主动与用户对话",
        )
        for fragment in worker_only_fragments:
            assert fragment not in soul_main, (
                f"主 Agent SOUL 不应含 Worker variant 专属片段 {fragment!r}；行为零变更守护"
            )

    def test_worker_variants_via_kind_attribute(self) -> None:
        """Codex Phase B finding LOW1 闭环：覆盖 production worker 创建路径（kind="worker"）。

        production 的 worker 创建路径（worker_service.py:1383 / agent_service.py:639）用
        `kind="worker"` 显式标记；前面的 `_make_worker_profile()` 用 metadata fallback——
        本测试单独覆盖 kind 显式路径 → build_default_behavior_workspace_files → worker variant 派发。
        """
        worker_via_kind = AgentProfile(
            profile_id="prod-worker-001",
            name="Prod Worker",
            kind="worker",
        )
        files = build_default_behavior_workspace_files(
            agent_profile=worker_via_kind,
            project_name="atom",
            project_slug="atom",
            include_advanced=True,
        )
        files_by_id = {f.file_id: f for f in files}

        for fid in ("IDENTITY.md", "SOUL.md", "HEARTBEAT.md"):
            assert fid in files_by_id, f"{fid} 应在 worker advanced files 中"

        assert "主 Agent" in files_by_id["SOUL.md"].content, (
            "kind=worker 路径必须派发 SOUL.worker.md（含 H1 哲学守护）"
        )
        assert "服务对象 = 主 Agent" in files_by_id["SOUL.md"].content
        assert "A2A" in files_by_id["HEARTBEAT.md"].content
