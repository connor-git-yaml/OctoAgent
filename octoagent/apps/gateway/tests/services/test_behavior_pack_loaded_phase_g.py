"""F097 Phase G: BEHAVIOR_PACK_LOADED agent_kind=subagent 验证 + AC-AUDIT-1 + AC-COMPAT-1。

本测试文件专注于验证 Phase C 的自动副产品：
- AC-G1：ephemeral subagent profile 派生 BEHAVIOR_PACK_LOADED.agent_kind == "subagent"
- AC-AUDIT-1：四层 audit chain 一致性（AgentProfile.profile_id → BEHAVIOR_PACK_LOADED.agent_id
              → AgentRuntime.profile_id → RecallFrame.agent_runtime_id）
- AC-COMPAT-1：main / worker 路径的 agent_kind 不受 F097 影响

测试策略（plan Phase G §2 指定）：
- 直接调用 `make_behavior_pack_loaded_payload` 配合不同 kind 的 AgentProfile 构造验证
- 对于 subagent 路径，使用 `_build_ephemeral_subagent_profile` 静态方法直接构造 ephemeral profile
  （plan P2-2 闭环：该方法已提为 staticmethod，测试可直接调用真实 helper）
- 无需走完整 dispatch 路径（Phase C 端到端已在 test_agent_context_phase_c.py 验证）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octoagent.core.behavior_workspace import BehaviorLoadProfile
from octoagent.core.models.agent_context import AgentProfile
from octoagent.gateway.services.agent_context import AgentContextService
from octoagent.gateway.services.agent_decision import (
    invalidate_behavior_pack_cache,
    make_behavior_pack_loaded_payload,
    resolve_behavior_pack,
)


class TestAcG1BehaviorPackLoadedAgentKindSubagent:
    """AC-G1: Subagent 路径 BEHAVIOR_PACK_LOADED.agent_kind == "subagent"。"""

    def test_ephemeral_subagent_profile_yields_agent_kind_subagent(self, tmp_path: Path) -> None:
        """AC-G1 核心：make_behavior_pack_loaded_payload 读 str(agent_profile.kind)，
        当 profile.kind == "subagent" 时，payload.agent_kind 必须为 "subagent"。

        Phase C 实施的 _build_ephemeral_subagent_profile 返回 kind="subagent" 的 AgentProfile，
        make_behavior_pack_loaded_payload 以 str(agent_profile.kind) 填充 agent_kind 字段——
        两者协同确保 BEHAVIOR_PACK_LOADED 事件中 agent_kind == "subagent"。
        """
        invalidate_behavior_pack_cache()

        # 使用 Phase C 引入的 _build_ephemeral_subagent_profile 静态方法构造 ephemeral profile
        # project=None 时 profile.project_id=""，不影响 agent_kind 派生
        ephemeral_profile = AgentContextService._build_ephemeral_subagent_profile(project=None)

        # 验证 ephemeral profile 本身的 kind 正确（Phase C AC-C1/C2 的前置条件）
        assert str(ephemeral_profile.kind) == "subagent", (
            f"_build_ephemeral_subagent_profile 必须返回 kind='subagent'，"
            f"实际得到 kind={ephemeral_profile.kind!r}"
        )
        assert ephemeral_profile.profile_id.startswith("agent-prf-subagent-"), (
            f"ephemeral profile_id 必须以 'agent-prf-subagent-' 开头，"
            f"实际为 {ephemeral_profile.profile_id!r}"
        )

        # Subagent 走 MINIMAL load_profile（4 文件：AGENTS+TOOLS+IDENTITY+USER）
        pack = resolve_behavior_pack(
            agent_profile=ephemeral_profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.MINIMAL,
        )

        # 核心断言：AC-G1
        payload = make_behavior_pack_loaded_payload(
            pack,
            agent_profile=ephemeral_profile,
            load_profile=BehaviorLoadProfile.MINIMAL,
        )

        assert payload.agent_kind == "subagent", (
            f"AC-G1 失败：BEHAVIOR_PACK_LOADED.agent_kind 应为 'subagent'，"
            f"实际为 {payload.agent_kind!r}"
        )
        # agent_id 来自 ephemeral profile 的 profile_id（AC-AUDIT-1 四层链路第一层）
        assert payload.agent_id == ephemeral_profile.profile_id, (
            f"AC-AUDIT-1（前两层）：payload.agent_id 必须等于 AgentProfile.profile_id，"
            f"payload={payload.agent_id!r} vs profile={ephemeral_profile.profile_id!r}"
        )

    def test_subagent_agent_kind_not_worker_or_main(self, tmp_path: Path) -> None:
        """AC-G1 加固：subagent 路径的 agent_kind 既不是 'worker' 也不是 'main'。

        确认 F097 引入 subagent 值后没有被意外映射到其他值。
        """
        invalidate_behavior_pack_cache()

        ephemeral_profile = AgentContextService._build_ephemeral_subagent_profile(project=None)
        pack = resolve_behavior_pack(
            agent_profile=ephemeral_profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.MINIMAL,
        )
        payload = make_behavior_pack_loaded_payload(
            pack,
            agent_profile=ephemeral_profile,
            load_profile=BehaviorLoadProfile.MINIMAL,
        )

        assert payload.agent_kind not in ("worker", "main"), (
            f"AC-G1: subagent 路径的 agent_kind 不应为 'worker' 或 'main'，"
            f"实际为 {payload.agent_kind!r}"
        )
        assert payload.agent_kind == "subagent"

    def test_subagent_load_profile_is_minimal(self, tmp_path: Path) -> None:
        """Phase C Codex P2-1 闭环验证：subagent 走 MINIMAL profile（4 文件）。

        plan §2 Phase C 指出 subagent 走 MINIMAL 而非 WORKER，避免加载 SOUL/HEARTBEAT
        等 Worker 专用行为文件——此测试确认 MINIMAL profile 的 load_profile 字段正确写入事件。
        """
        invalidate_behavior_pack_cache()

        ephemeral_profile = AgentContextService._build_ephemeral_subagent_profile(project=None)
        pack = resolve_behavior_pack(
            agent_profile=ephemeral_profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.MINIMAL,
        )
        payload = make_behavior_pack_loaded_payload(
            pack,
            agent_profile=ephemeral_profile,
            load_profile=BehaviorLoadProfile.MINIMAL,
        )

        assert payload.load_profile == "minimal", (
            f"Subagent 路径应使用 MINIMAL load_profile，实际为 {payload.load_profile!r}"
        )
        assert payload.agent_kind == "subagent"


class TestAcAudit1FourLayerChain:
    """AC-AUDIT-1: 四层 audit chain 一致性验证。

    四层对齐：
    AgentProfile.profile_id
    → BEHAVIOR_PACK_LOADED.agent_id（= profile_id）
    → AgentRuntime.profile_id（运行时绑定）
    → RecallFrame.agent_runtime_id（通过 AgentRuntime 间接关联）

    本测试覆盖前两层的直接可验证部分（层 1 ↔ 层 2）；
    层 3/4 的运行时关联需要完整 dispatch 路径，已在 test_task_service_context_integration.py 覆盖。
    """

    def test_subagent_payload_agent_id_equals_profile_id(self, tmp_path: Path) -> None:
        """AC-AUDIT-1 层 1↔2：payload.agent_id 必须等于 AgentProfile.profile_id。

        这是 audit chain 第一个可验证的不变量：BEHAVIOR_PACK_LOADED.agent_id
        来自 AgentProfile.profile_id，后续通过 AgentRuntime.profile_id 将事件
        与 RecallFrame.agent_runtime_id 关联。
        """
        invalidate_behavior_pack_cache()

        ephemeral_profile = AgentContextService._build_ephemeral_subagent_profile(project=None)
        pack = resolve_behavior_pack(
            agent_profile=ephemeral_profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.MINIMAL,
        )
        payload = make_behavior_pack_loaded_payload(
            pack,
            agent_profile=ephemeral_profile,
            load_profile=BehaviorLoadProfile.MINIMAL,
        )

        # AC-AUDIT-1 层 1 ↔ 层 2：这是 audit chain 的根不变量
        assert payload.agent_id == ephemeral_profile.profile_id, (
            f"AC-AUDIT-1 audit chain 断裂：\n"
            f"  AgentProfile.profile_id = {ephemeral_profile.profile_id!r}\n"
            f"  BEHAVIOR_PACK_LOADED.agent_id = {payload.agent_id!r}\n"
            f"  两者必须相等，以保证通过 AgentRuntime.profile_id 桥接 RecallFrame.agent_runtime_id"
        )

    def test_subagent_profile_id_has_expected_prefix(self, tmp_path: Path) -> None:
        """AC-AUDIT-1 辅助：ephemeral profile_id 带 'agent-prf-subagent-' 前缀，
        可通过前缀区分 subagent 审计记录。

        plan §2 Phase C 规定：profile_id 命名前缀 `agent-prf-subagent-` + ULID。
        """
        ephemeral_profile = AgentContextService._build_ephemeral_subagent_profile(project=None)

        # 前缀区分 subagent（不与持久化 Worker/main profile 混淆）
        assert ephemeral_profile.profile_id.startswith("agent-prf-subagent-"), (
            f"ephemeral subagent profile_id 必须带 'agent-prf-subagent-' 前缀，"
            f"实际为 {ephemeral_profile.profile_id!r}"
        )
        # profile_id 非空（基础不变量）
        assert len(ephemeral_profile.profile_id) > len("agent-prf-subagent-"), (
            "ephemeral profile_id 前缀后必须有 ULID 部分"
        )

    def test_each_ephemeral_profile_has_unique_profile_id(self) -> None:
        """AC-AUDIT-1 辅助：每次调用 _build_ephemeral_subagent_profile 返回不同 profile_id。

        确保并发 subagent dispatch 时 audit chain 不会因 profile_id 冲突而混淆。
        """
        profile_a = AgentContextService._build_ephemeral_subagent_profile(project=None)
        profile_b = AgentContextService._build_ephemeral_subagent_profile(project=None)

        assert profile_a.profile_id != profile_b.profile_id, (
            "每次调用 _build_ephemeral_subagent_profile 必须生成不同的 profile_id（ULID 唯一性）"
        )


class TestAcCompat1MainAndWorkerUnchanged:
    """AC-COMPAT-1: main / worker 路径的 agent_kind 不受 F097 影响。

    F097 引入 subagent ephemeral profile 后，main/worker 路径必须保持原有行为：
    - main 路径：AgentProfile(kind="main") → payload.agent_kind == "main"
    - worker 路径：AgentProfile(kind="worker") → payload.agent_kind == "worker"

    对应 tasks.md TG.2 确认 test_agent_decision_envelope.py:640 的 Worker 路径测试继续通过。
    """

    def test_main_path_agent_kind_unchanged(self, tmp_path: Path) -> None:
        """AC-COMPAT-1：main 路径 BEHAVIOR_PACK_LOADED.agent_kind == 'main'，不受 F097 影响。"""
        invalidate_behavior_pack_cache()

        main_profile = AgentProfile(
            profile_id="test-main-profile-compat-1",
            name="Main Agent",
            kind="main",
        )
        pack = resolve_behavior_pack(
            agent_profile=main_profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.FULL,
        )
        payload = make_behavior_pack_loaded_payload(
            pack,
            agent_profile=main_profile,
            load_profile=BehaviorLoadProfile.FULL,
        )

        assert payload.agent_kind == "main", (
            f"AC-COMPAT-1 失败：main 路径的 agent_kind 应为 'main'，"
            f"实际为 {payload.agent_kind!r}（F097 不应改变 main 路径行为）"
        )
        # payload.agent_id 也来自 profile_id（audit chain 不变量）
        assert payload.agent_id == "test-main-profile-compat-1"

    def test_worker_path_agent_kind_unchanged(self, tmp_path: Path) -> None:
        """AC-COMPAT-1：worker 路径 BEHAVIOR_PACK_LOADED.agent_kind == 'worker'，不受 F097 影响。

        对应 test_agent_decision_envelope.py:640 的原有断言（line 640: assert payload.agent_kind == "worker"）。
        F097 实施后此断言必须继续成立。
        """
        invalidate_behavior_pack_cache()

        worker_profile = AgentProfile(
            profile_id="test-worker-profile-compat-1",
            name="Worker Agent",
            kind="worker",
        )
        pack = resolve_behavior_pack(
            agent_profile=worker_profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.WORKER,
        )
        payload = make_behavior_pack_loaded_payload(
            pack,
            agent_profile=worker_profile,
            load_profile=BehaviorLoadProfile.WORKER,
        )

        assert payload.agent_kind == "worker", (
            f"AC-COMPAT-1 失败：worker 路径的 agent_kind 应为 'worker'，"
            f"实际为 {payload.agent_kind!r}（F097 不应改变 worker 路径行为）"
        )
        assert payload.agent_id == "test-worker-profile-compat-1"

    def test_three_agent_kinds_are_distinct(self, tmp_path: Path) -> None:
        """AC-COMPAT-1 加固：三种 agent_kind（main/worker/subagent）互相不同，无意外映射。

        确认 F097 引入 subagent 后，三条路径产生的 agent_kind 值严格区分，
        不存在任意两条路径产生相同值的情况。
        """
        invalidate_behavior_pack_cache()

        main_profile = AgentProfile(profile_id="distinct-main", name="Main", kind="main")
        worker_profile = AgentProfile(profile_id="distinct-worker", name="Worker", kind="worker")
        subagent_profile = AgentContextService._build_ephemeral_subagent_profile(project=None)

        main_pack = resolve_behavior_pack(
            agent_profile=main_profile, project_root=tmp_path, load_profile=BehaviorLoadProfile.FULL
        )
        worker_pack = resolve_behavior_pack(
            agent_profile=worker_profile, project_root=tmp_path, load_profile=BehaviorLoadProfile.WORKER
        )
        subagent_pack = resolve_behavior_pack(
            agent_profile=subagent_profile,
            project_root=tmp_path,
            load_profile=BehaviorLoadProfile.MINIMAL,
        )

        main_payload = make_behavior_pack_loaded_payload(
            main_pack, agent_profile=main_profile, load_profile=BehaviorLoadProfile.FULL
        )
        worker_payload = make_behavior_pack_loaded_payload(
            worker_pack, agent_profile=worker_profile, load_profile=BehaviorLoadProfile.WORKER
        )
        subagent_payload = make_behavior_pack_loaded_payload(
            subagent_pack, agent_profile=subagent_profile, load_profile=BehaviorLoadProfile.MINIMAL
        )

        # 三值各不相同
        kinds = {main_payload.agent_kind, worker_payload.agent_kind, subagent_payload.agent_kind}
        assert len(kinds) == 3, (
            f"AC-COMPAT-1: 三种路径的 agent_kind 应互不相同，实际得到 {kinds}"
        )
        assert main_payload.agent_kind == "main"
        assert worker_payload.agent_kind == "worker"
        assert subagent_payload.agent_kind == "subagent"
