"""Feature 065/067 Phase 3: Butler DELEGATE_GRAPH 路由 + 规则匹配 单元测试。"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octoagent.core.models import (
    ButlerDecision,
    ButlerDecisionMode,
)
from octoagent.gateway.services.butler_behavior import (
    _build_butler_pipeline_context,
    _match_pipeline_trigger,
    _parse_butler_decision_payload,
    build_butler_decision_messages,
    decide_butler_decision,
)


# ============================================================
# 辅助工具
# ============================================================


class _FakePipelineListItem:
    """模拟 PipelineListItem 的最小替身。"""

    def __init__(
        self,
        pipeline_id: str = "deploy-staging",
        description: str = "部署到 staging",
        trigger_hint: str = "当用户要求部署到 staging 时使用",
        input_schema: dict[str, Any] | None = None,
    ):
        self.pipeline_id = pipeline_id
        self.description = description
        self.trigger_hint = trigger_hint
        self.input_schema = input_schema or {}


class _FakeInputField:
    """模拟 PipelineInputField 的最小替身。"""

    def __init__(self, type: str = "string", required: bool = False):
        self.type = type
        self.required = required


# ============================================================
# T-065-027: ButlerDecisionMode 新增 DELEGATE_GRAPH
# ============================================================


def test_butler_decision_mode_has_delegate_graph() -> None:
    """DELEGATE_GRAPH 枚举值存在且值正确。"""
    assert hasattr(ButlerDecisionMode, "DELEGATE_GRAPH")
    assert ButlerDecisionMode.DELEGATE_GRAPH == "delegate_graph"


def test_butler_decision_mode_preserves_existing_values() -> None:
    """新增枚举值不破坏现有 6 个值。"""
    expected = {
        "direct_answer",
        "ask_once",
        "delegate_research",
        "delegate_dev",
        "delegate_ops",
        "best_effort_answer",
        "delegate_graph",
    }
    actual = {m.value for m in ButlerDecisionMode}
    assert expected == actual


# ============================================================
# T-065-028: ButlerDecision 新增 pipeline_id / pipeline_params
# ============================================================


def test_butler_decision_pipeline_fields_default() -> None:
    """pipeline_id 和 pipeline_params 有正确的默认值。"""
    d = ButlerDecision()
    assert d.pipeline_id == ""
    assert d.pipeline_params == {}


def test_butler_decision_pipeline_fields_populated() -> None:
    """DELEGATE_GRAPH 模式下可填充 pipeline_id 和 pipeline_params。"""
    d = ButlerDecision(
        mode=ButlerDecisionMode.DELEGATE_GRAPH,
        pipeline_id="deploy-staging",
        pipeline_params={"branch": "main", "skip_tests": True},
    )
    assert d.mode == ButlerDecisionMode.DELEGATE_GRAPH
    assert d.pipeline_id == "deploy-staging"
    assert d.pipeline_params == {"branch": "main", "skip_tests": True}


def test_butler_decision_serialization_roundtrip() -> None:
    """ButlerDecision 序列化/反序列化正确保留 pipeline 字段。"""
    d = ButlerDecision(
        mode=ButlerDecisionMode.DELEGATE_GRAPH,
        pipeline_id="data-migration",
        pipeline_params={"source": "prod", "target": "staging"},
        rationale="用户请求数据迁移",
    )
    dumped = d.model_dump()
    restored = ButlerDecision.model_validate(dumped)
    assert restored.pipeline_id == "data-migration"
    assert restored.pipeline_params == {"source": "prod", "target": "staging"}
    assert restored.mode == ButlerDecisionMode.DELEGATE_GRAPH


# ============================================================
# T-065-029: Butler system prompt 注入 Pipeline 列表
# ============================================================


def test_build_butler_pipeline_context_empty_list() -> None:
    """Pipeline 列表为空时返回空字符串。"""
    assert _build_butler_pipeline_context([]) == ""
    assert _build_butler_pipeline_context(None) == ""


def test_build_butler_pipeline_context_with_items() -> None:
    """非空 Pipeline 列表正确格式化。"""
    items = [
        _FakePipelineListItem(
            pipeline_id="deploy-staging",
            description="部署到 staging",
            trigger_hint="部署 staging 时使用",
        ),
        _FakePipelineListItem(
            pipeline_id="data-migration",
            description="数据迁移",
            trigger_hint="",
        ),
    ]
    result = _build_butler_pipeline_context(items)
    assert "Available Pipelines for delegation:" in result
    assert "deploy-staging" in result
    assert "部署到 staging" in result
    assert "(trigger: 部署 staging 时使用)" in result
    assert "data-migration" in result
    # 没有 trigger_hint 的不应包含 (trigger: ...)
    assert result.count("(trigger:") == 1


def test_build_butler_pipeline_context_with_input_schema() -> None:
    """Pipeline 有 input_schema 时注入字段摘要。"""
    items = [
        _FakePipelineListItem(
            pipeline_id="deploy-staging",
            description="部署到 staging",
            trigger_hint="部署时使用",
            input_schema={
                "branch": _FakeInputField(type="string", required=True),
                "skip_tests": _FakeInputField(type="boolean", required=False),
            },
        ),
    ]
    result = _build_butler_pipeline_context(items)
    assert "input: branch (string, required), skip_tests (boolean)" in result


def test_build_butler_decision_messages_no_pipeline_params() -> None:
    """Feature 067: build_butler_decision_messages 不再接受 pipeline_items 参数。"""
    messages = build_butler_decision_messages(
        user_text="你好",
        behavior_system_block="test_behavior",
        runtime_hint_block="test_hints",
    )
    # schema 中不包含 delegate_graph（Pipeline 匹配已迁移到规则层）
    user_msg = messages[-1]["content"]
    assert "delegate_graph" not in user_msg
    assert "pipeline_id" not in user_msg


# ============================================================
# Feature 067: _match_pipeline_trigger 规则匹配
# ============================================================


def test_match_pipeline_trigger_none_items() -> None:
    """pipeline_items 为 None 时返回 None。"""
    assert _match_pipeline_trigger("部署到 staging", None) is None


def test_match_pipeline_trigger_empty_items() -> None:
    """pipeline_items 为空列表时返回 None。"""
    assert _match_pipeline_trigger("部署到 staging", []) is None


def test_match_pipeline_trigger_by_pipeline_id() -> None:
    """用户输入包含 pipeline_id 时匹配成功。"""
    items = [_FakePipelineListItem(pipeline_id="deploy-staging")]
    result = _match_pipeline_trigger("请执行 deploy-staging", items)
    assert result is not None
    assert result.mode == ButlerDecisionMode.DELEGATE_GRAPH
    assert result.pipeline_id == "deploy-staging"
    assert result.metadata.get("match_type") == "pipeline_id"


def test_match_pipeline_trigger_by_trigger_hint() -> None:
    """用户输入匹配 trigger_hint 关键词时成功。"""
    items = [
        _FakePipelineListItem(
            pipeline_id="deploy-staging",
            trigger_hint="deploy staging",
        ),
    ]
    result = _match_pipeline_trigger("please deploy to staging now", items)
    assert result is not None
    assert result.mode == ButlerDecisionMode.DELEGATE_GRAPH
    assert result.pipeline_id == "deploy-staging"
    assert result.metadata.get("match_type") == "trigger_hint"


def test_match_pipeline_trigger_no_match() -> None:
    """用户输入不匹配任何 Pipeline 时返回 None。"""
    items = [
        _FakePipelineListItem(
            pipeline_id="deploy-staging",
            trigger_hint="部署 staging 时使用",
        ),
    ]
    result = _match_pipeline_trigger("今天天气怎么样", items)
    assert result is None


def test_decide_butler_decision_with_pipeline_match() -> None:
    """Feature 067: decide_butler_decision 传入 pipeline_items 时能匹配返回 DELEGATE_GRAPH。"""
    items = [
        _FakePipelineListItem(
            pipeline_id="deploy-staging",
            trigger_hint="deploy staging",
        ),
    ]
    decision = decide_butler_decision(
        "please deploy to staging now",
        pipeline_items=items,
    )
    assert decision.mode == ButlerDecisionMode.DELEGATE_GRAPH
    assert decision.pipeline_id == "deploy-staging"


def test_decide_butler_decision_without_pipeline_match() -> None:
    """Feature 067: 不匹配 Pipeline 时 decide_butler_decision 正常走其他规则。"""
    items = [_FakePipelineListItem(pipeline_id="deploy-staging", trigger_hint="deploy staging")]
    decision = decide_butler_decision(
        "hello world",
        pipeline_items=items,
    )
    # 不匹配 Pipeline 时不应该返回 DELEGATE_GRAPH
    assert decision.mode != ButlerDecisionMode.DELEGATE_GRAPH


# ============================================================
# T-065-030: _parse_butler_decision_payload 支持 delegate_graph
# ============================================================


def test_parse_delegate_graph_decision() -> None:
    """delegate_graph mode 可被正确解析。"""
    payload = {
        "mode": "delegate_graph",
        "pipeline_id": "deploy-staging",
        "pipeline_params": {"branch": "main"},
        "rationale": "精确匹配 Pipeline trigger_hint",
    }
    decision = _parse_butler_decision_payload(payload)
    assert decision is not None
    assert decision.mode == ButlerDecisionMode.DELEGATE_GRAPH
    assert decision.pipeline_id == "deploy-staging"
    assert decision.pipeline_params == {"branch": "main"}


def test_parse_delegate_graph_without_pipeline_id() -> None:
    """delegate_graph 不带 pipeline_id 也能解析（fallback 在路由层处理）。"""
    payload = {
        "mode": "delegate_graph",
        "rationale": "用户意图模糊",
    }
    decision = _parse_butler_decision_payload(payload)
    assert decision is not None
    assert decision.mode == ButlerDecisionMode.DELEGATE_GRAPH
    assert decision.pipeline_id == ""


# ============================================================
# T-065-031: DELEGATE_GRAPH fallback
# ============================================================


def test_fallback_delegate_graph_to_ops() -> None:
    """Pipeline tags 包含 deploy/ops 时 fallback 到 DELEGATE_OPS。"""
    from octoagent.gateway.services.orchestrator import OrchestratorService

    decision = ButlerDecision(
        mode=ButlerDecisionMode.DELEGATE_GRAPH,
        pipeline_id="deploy-staging",
        rationale="Pipeline 不可用",
        metadata={"pipeline_tags": ["deploy", "ci-cd"]},
    )
    result = OrchestratorService._fallback_delegate_graph_decision(decision)
    assert result.mode == ButlerDecisionMode.DELEGATE_OPS
    assert "fallback" in result.rationale.lower()


def test_fallback_delegate_graph_to_dev() -> None:
    """Pipeline tags 包含 dev/code 时 fallback 到 DELEGATE_DEV。"""
    from octoagent.gateway.services.orchestrator import OrchestratorService

    decision = ButlerDecision(
        mode=ButlerDecisionMode.DELEGATE_GRAPH,
        pipeline_id="build-project",
        rationale="Pipeline 不可用",
        metadata={"pipeline_tags": ["dev", "build"]},
    )
    result = OrchestratorService._fallback_delegate_graph_decision(decision)
    assert result.mode == ButlerDecisionMode.DELEGATE_DEV


def test_fallback_delegate_graph_to_research() -> None:
    """Pipeline tags 不匹配时 fallback 到 DELEGATE_RESEARCH。"""
    from octoagent.gateway.services.orchestrator import OrchestratorService

    decision = ButlerDecision(
        mode=ButlerDecisionMode.DELEGATE_GRAPH,
        pipeline_id="unknown-pipeline",
        rationale="Pipeline 不可用",
        metadata={"pipeline_tags": ["analysis"]},
    )
    result = OrchestratorService._fallback_delegate_graph_decision(decision)
    assert result.mode == ButlerDecisionMode.DELEGATE_RESEARCH


def test_fallback_delegate_graph_no_tags() -> None:
    """没有 pipeline_tags 时 fallback 到 DELEGATE_RESEARCH。"""
    from octoagent.gateway.services.orchestrator import OrchestratorService

    decision = ButlerDecision(
        mode=ButlerDecisionMode.DELEGATE_GRAPH,
        pipeline_id="some-pipeline",
        rationale="Pipeline 不可用",
    )
    result = OrchestratorService._fallback_delegate_graph_decision(decision)
    assert result.mode == ButlerDecisionMode.DELEGATE_RESEARCH


# ============================================================
# T-065-032: Worker/Subagent system prompt 注入 Pipeline 列表
# ============================================================


def test_llm_service_build_pipeline_catalog_no_registry() -> None:
    """没有 pipeline_registry 时返回空字符串。"""
    from octoagent.gateway.services.llm_service import LLMService

    svc = LLMService()
    assert svc._build_pipeline_catalog_context() == ""


def test_llm_service_build_pipeline_catalog_empty_registry() -> None:
    """pipeline_registry 为空时返回空字符串。"""
    from octoagent.gateway.services.llm_service import LLMService

    mock_registry = MagicMock()
    mock_registry.list_items.return_value = []

    svc = LLMService(pipeline_registry=mock_registry)
    assert svc._build_pipeline_catalog_context() == ""


def test_llm_service_build_pipeline_catalog_with_items() -> None:
    """pipeline_registry 有内容时正确构建注入文本。"""
    from octoagent.gateway.services.llm_service import LLMService

    mock_registry = MagicMock()
    mock_registry.list_items.return_value = [
        _FakePipelineListItem(
            pipeline_id="deploy-staging",
            description="部署到 staging",
            trigger_hint="部署时使用",
        ),
        _FakePipelineListItem(
            pipeline_id="data-migration",
            description="数据迁移",
            trigger_hint="需要迁移数据时使用",
        ),
    ]

    svc = LLMService(pipeline_registry=mock_registry)
    result = svc._build_pipeline_catalog_context()

    assert "## Available Pipelines" in result
    assert "deploy-staging" in result
    assert "data-migration" in result
    assert "(trigger: 部署时使用)" in result
    assert "Pipelines vs Subagents" in result
    assert "graph_pipeline" in result
    assert "subagents" in result.lower() or "Subagent" in result


def test_llm_service_build_pipeline_catalog_exception_safe() -> None:
    """pipeline_registry.list_items() 抛异常时返回空字符串。"""
    from octoagent.gateway.services.llm_service import LLMService

    mock_registry = MagicMock()
    mock_registry.list_items.side_effect = RuntimeError("scan failed")

    svc = LLMService(pipeline_registry=mock_registry)
    assert svc._build_pipeline_catalog_context() == ""
