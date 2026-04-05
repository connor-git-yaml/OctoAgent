"""Feature 063: Behavior workspace 生命周期管理与差异化加载测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from octoagent.core.behavior_workspace import (
    _PROFILE_ALLOWLIST,
    ALL_BEHAVIOR_FILE_IDS,
    BEHAVIOR_FILE_BUDGETS,
    BehaviorLoadProfile,
    OnboardingState,
    _default_content_for_file,
    _onboarding_state_path,
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


class TestLegacyCompatibility:
    """T1.7: Legacy 兼容检测。"""

    def test_legacy_with_modified_identity(self, tmp_path: Path) -> None:
        """无 state 文件 + IDENTITY.md 已修改 → 自动标记完成。"""
        # 手动创建目录结构，不创建 state 文件
        identity_dir = tmp_path / "behavior" / "agents" / "main"
        identity_dir.mkdir(parents=True)
        identity_path = identity_dir / "IDENTITY.md"
        # 写入自定义内容（不含默认标记）
        identity_path.write_text("我是 Connor 的私人助理，代号 ATM。", encoding="utf-8")

        state = load_onboarding_state(tmp_path)
        assert state.is_completed()

    def test_legacy_with_session_history(self, tmp_path: Path) -> None:
        """无 state 文件 + data/ 非空 → 自动标记完成。"""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "some_session.db").touch()

        state = load_onboarding_state(tmp_path)
        assert state.is_completed()

    def test_fresh_install_not_legacy(self, tmp_path: Path) -> None:
        """全新安装（无 state、无 identity、无 data）→ 不自动标记。"""
        state = load_onboarding_state(tmp_path)
        assert not state.is_completed()


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
