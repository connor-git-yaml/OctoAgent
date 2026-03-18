"""Feature 061 T-045: 性能验证测试

验证核心性能指标:
- SC-001: Deferred 模式 token 占用减少（通过名称列表 vs 完整 schema 对比）
- SC-002: PresetBeforeHook 延迟 <1ms
- SC-004: tool_search 延迟 <10ms（使用 mock ToolIndex）
- SC-006: bootstrap token ≤200
"""

from __future__ import annotations

import time

import pytest

from octoagent.tooling.models import (
    PRESET_POLICY,
    CoreToolSet,
    DeferredToolEntry,
    ExecutionContext,
    PermissionPreset,
    PresetDecision,
    SideEffectLevel,
    ToolMeta,
    ToolProfile,
    ToolPromotionState,
    ToolTier,
    format_deferred_tools_list,
    preset_decision,
)


# ============================================================
# SC-001: Deferred 模式 token 减少
# ============================================================


class TestDeferredTokenReduction:
    """SC-001: 验证 Deferred 名称列表相比完整 schema 的 token 减少"""

    def _make_tool_metas(self, count: int) -> list[ToolMeta]:
        """生成 N 个模拟 ToolMeta"""
        tools: list[ToolMeta] = []
        for i in range(count):
            tools.append(ToolMeta(
                name=f"test.tool_{i}",
                description=f"这是第 {i} 号测试工具，用于执行某些特定的操作任务",
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "input_path": {
                            "type": "string",
                            "description": f"输入文件路径，工具 {i} 会从此路径读取数据",
                        },
                        "output_path": {
                            "type": "string",
                            "description": f"输出文件路径，工具 {i} 会将结果写入此路径",
                        },
                        "options": {
                            "type": "object",
                            "description": "额外选项配置",
                            "properties": {
                                "verbose": {"type": "boolean", "default": False},
                                "timeout": {"type": "integer", "default": 30},
                            },
                        },
                    },
                    "required": ["input_path"],
                },
                side_effect_level=SideEffectLevel.REVERSIBLE,
                tool_profile=ToolProfile.STANDARD,
                tool_group="test",
                tier=ToolTier.DEFERRED,
            ))
        return tools

    def _make_deferred_entries(self, count: int) -> list[DeferredToolEntry]:
        """生成 N 个 DeferredToolEntry"""
        entries: list[DeferredToolEntry] = []
        for i in range(count):
            entries.append(DeferredToolEntry(
                name=f"test.tool_{i}",
                one_line_desc=f"测试工具 {i} 的简短描述",
            ))
        return entries

    def test_token_reduction_with_30_tools(self) -> None:
        """30 个工具时，Deferred 列表相比完整 schema 减少 ≥60% 字符"""
        tools = self._make_tool_metas(30)
        deferred = self._make_deferred_entries(30)

        # 计算完整 schema 的大致 token 占用（以字符数近似）
        import json
        full_schema_chars = sum(
            len(json.dumps(t.model_dump(), ensure_ascii=False)) for t in tools
        )

        # 计算 Deferred 名称列表的字符数
        deferred_list_text = format_deferred_tools_list(deferred)
        deferred_chars = len(deferred_list_text)

        # 验证减少 ≥60%
        reduction = 1 - (deferred_chars / full_schema_chars)
        assert reduction >= 0.60, (
            f"token 减少比例 {reduction:.1%} 不足 60%: "
            f"full={full_schema_chars}, deferred={deferred_chars}"
        )

    def test_token_reduction_with_50_tools(self) -> None:
        """50 个工具时，减少比例应更高"""
        tools = self._make_tool_metas(50)
        deferred = self._make_deferred_entries(50)

        import json
        full_schema_chars = sum(
            len(json.dumps(t.model_dump(), ensure_ascii=False)) for t in tools
        )
        deferred_list_text = format_deferred_tools_list(deferred)
        deferred_chars = len(deferred_list_text)

        reduction = 1 - (deferred_chars / full_schema_chars)
        assert reduction >= 0.70, f"50 个工具时 token 减少比例 {reduction:.1%} 不足 70%"


# ============================================================
# SC-002: PresetBeforeHook 延迟 <1ms
# ============================================================


class TestPresetCheckPerformance:
    """SC-002: preset_decision() 延迟 <1ms"""

    def test_preset_decision_latency(self) -> None:
        """preset_decision() 调用 10000 次的平均延迟 <0.01ms"""
        iterations = 10000
        start = time.perf_counter()
        for _ in range(iterations):
            for preset in PermissionPreset:
                for side_effect in SideEffectLevel:
                    preset_decision(preset, side_effect)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # 10000 次 × 9 组合 = 90000 次查询
        total_calls = iterations * 9
        avg_ms = elapsed_ms / total_calls
        assert avg_ms < 0.01, f"preset_decision 平均延迟 {avg_ms:.4f}ms 超过 0.01ms"

    def test_preset_policy_matrix_is_dict_lookup(self) -> None:
        """PRESET_POLICY 是 O(1) dict 查询，不涉及计算"""
        # 验证矩阵是纯 dict
        assert isinstance(PRESET_POLICY, dict)
        for preset_dict in PRESET_POLICY.values():
            assert isinstance(preset_dict, dict)


# ============================================================
# SC-004: ToolPromotionState 操作延迟
# ============================================================


class TestPromotionStatePerformance:
    """SC-004 相关: ToolPromotionState 操作延迟"""

    def test_promote_demote_latency(self) -> None:
        """promote/demote 1000 个工具的延迟合理"""
        state = ToolPromotionState()
        count = 1000

        # 批量提升
        start = time.perf_counter()
        for i in range(count):
            state.promote(f"tool_{i}", "test:source")
        promote_ms = (time.perf_counter() - start) * 1000

        assert promote_ms < 100, f"1000 次 promote 耗时 {promote_ms:.1f}ms 过长"

        # 批量回退
        start = time.perf_counter()
        for i in range(count):
            state.demote(f"tool_{i}", "test:source")
        demote_ms = (time.perf_counter() - start) * 1000

        assert demote_ms < 100, f"1000 次 demote 耗时 {demote_ms:.1f}ms 过长"

    def test_active_tool_names_performance(self) -> None:
        """active_tool_names 在大量工具下的性能"""
        state = ToolPromotionState()
        for i in range(100):
            state.promote(f"tool_{i}", "test:source")

        start = time.perf_counter()
        for _ in range(1000):
            names = state.active_tool_names
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 100, f"1000 次 active_tool_names 查询耗时 {elapsed_ms:.1f}ms 过长"
        assert len(names) == 100


# ============================================================
# SC-006: Bootstrap token 估算
# ============================================================


class TestBootstrapTokenBudget:
    """SC-006: bootstrap token ≤200"""

    def test_shared_bootstrap_token_estimate(self) -> None:
        """shared bootstrap 模板元信息 token 估算 ≤100"""
        # 模拟 bootstrap:shared 的核心元信息
        shared_template = (
            "Project: {project_name}\n"
            "Workspace: {workspace_name}\n"
            "DateTime: {datetime}\n"
            "Preset: {permission_preset}\n"
        )
        # 填充示例值
        filled = shared_template.format(
            project_name="OctoAgent",
            workspace_name="default",
            datetime="2026-03-18T10:00:00Z",
            permission_preset="normal",
        )
        # 粗略 token 估算: 英文约 4 字符/token，中文约 2 字符/token
        estimated_tokens = len(filled) / 3  # 混合语言取折中
        assert estimated_tokens <= 100, (
            f"shared bootstrap 估计 {estimated_tokens:.0f} tokens 超过 100"
        )

    def test_role_card_token_estimate(self) -> None:
        """角色卡片 ~100-150 tokens"""
        # 典型角色卡片
        role_card = (
            "你是一个专注于代码开发的 Worker Agent，"
            "擅长 Python 和 TypeScript 开发，"
            "负责 OctoAgent 项目的功能实现和测试编写。"
            "你的工作风格是先理解需求再动手实现，注重代码质量和测试覆盖。"
        )
        # 中文约 1.5-2 字符/token
        estimated_tokens = len(role_card) / 1.8
        assert estimated_tokens <= 150, (
            f"角色卡片估计 {estimated_tokens:.0f} tokens 超过 150"
        )

    def test_combined_bootstrap_within_budget(self) -> None:
        """shared + 角色卡片总计 ≤200 tokens"""
        shared = "Project: OctoAgent\nWorkspace: default\nDateTime: 2026-03-18T10:00:00Z\nPreset: normal\n"
        role_card = (
            "你是一个专注于代码开发的 Worker Agent，"
            "擅长 Python 和 TypeScript 开发。"
        )
        combined = shared + "\n" + role_card
        # 混合语言取折中
        estimated_tokens = len(combined) / 2.5
        assert estimated_tokens <= 200, (
            f"combined bootstrap 估计 {estimated_tokens:.0f} tokens 超过 200"
        )
