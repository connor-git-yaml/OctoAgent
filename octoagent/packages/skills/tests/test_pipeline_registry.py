"""Feature 065 Phase 1: Pipeline 解析器 + DAG 验证 + Registry 单元测试。

覆盖范围：
- PIPELINE.md frontmatter 正常解析
- 必填字段缺失检测
- 版本不支持检测
- delegation 节点类型拒绝
- DAG 环检测
- 孤立节点检测
- next 引用不存在检测
- 节点 ID 重复检测
- YAML 格式错误处理
- 三级目录扫描
- 优先级覆盖
- 缓存 + refresh
- 单文件失败隔离
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octoagent.core.models.pipeline import PipelineNodeType
from octoagent.skills.pipeline_models import (
    PipelineInputField,
    PipelineListItem,
    PipelineManifest,
    PipelineOutputField,
    PipelineParseError,
    PipelineSource,
)
from octoagent.skills.pipeline_registry import (
    PipelineRegistry,
    parse_pipeline_file,
    validate_dag,
)


# ============================================================
# 辅助函数：创建测试用 PIPELINE.md
# ============================================================

_VALID_PIPELINE = """\
---
name: test-pipeline
description: "测试 Pipeline"
version: 1.0.0
author: Tester
tags:
  - test
  - demo
trigger_hint: "当需要测试时使用"
input_schema:
  message:
    type: string
    description: "测试消息"
    required: true
  debug:
    type: boolean
    description: "调试模式"
    default: false
output_schema:
  result:
    type: string
    description: "测试结果"
nodes:
  - id: start
    label: "开始"
    type: transform
    handler_id: transform.passthrough
    next: end
  - id: end
    label: "结束"
    type: transform
    handler_id: transform.passthrough
entry_node: start
---

# Test Pipeline

这是一个测试 Pipeline。
"""


def _write_pipeline(tmp_path: Path, name: str, content: str) -> Path:
    """在 tmp_path/{name}/PIPELINE.md 创建文件。"""
    pipeline_dir = tmp_path / name
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    md_file = pipeline_dir / "PIPELINE.md"
    md_file.write_text(content, encoding="utf-8")
    return md_file


# ============================================================
# 解析器测试
# ============================================================


class TestParsePipelineFile:
    """parse_pipeline_file 测试。"""

    def test_valid_pipeline(self, tmp_path: Path) -> None:
        """正常 PIPELINE.md 解析。"""
        md_file = _write_pipeline(tmp_path, "test-pipeline", _VALID_PIPELINE)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)

        assert isinstance(result, PipelineManifest)
        assert result.pipeline_id == "test-pipeline"
        assert result.description == "测试 Pipeline"
        assert result.version == "1.0.0"
        assert result.author == "Tester"
        assert result.tags == ["test", "demo"]
        assert result.trigger_hint == "当需要测试时使用"
        assert result.source == PipelineSource.BUILTIN
        assert "Test Pipeline" in result.content

        # input_schema
        assert "message" in result.input_schema
        assert result.input_schema["message"].type == "string"
        assert result.input_schema["message"].required is True
        assert "debug" in result.input_schema
        assert result.input_schema["debug"].type == "boolean"
        assert result.input_schema["debug"].default is False

        # output_schema
        assert "result" in result.output_schema
        assert result.output_schema["result"].type == "string"

        # definition
        defn = result.definition
        assert defn.pipeline_id == "test-pipeline"
        assert defn.entry_node_id == "start"
        assert len(defn.nodes) == 2
        assert defn.nodes[0].node_id == "start"
        assert defn.nodes[0].next_node_id == "end"
        assert defn.nodes[1].node_id == "end"
        assert defn.nodes[1].next_node_id is None

    def test_missing_name(self, tmp_path: Path) -> None:
        """缺少必填字段 name。"""
        content = """\
---
description: "测试"
version: 1.0.0
entry_node: start
nodes:
  - id: start
    type: transform
    handler_id: transform.passthrough
---
"""
        md_file = _write_pipeline(tmp_path, "no-name", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineParseError)
        assert result.error_type == "missing_field"
        assert "name" in result.message

    def test_missing_nodes(self, tmp_path: Path) -> None:
        """缺少必填字段 nodes。"""
        content = """\
---
name: no-nodes
description: "测试"
version: 1.0.0
entry_node: start
---
"""
        md_file = _write_pipeline(tmp_path, "no-nodes", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineParseError)
        assert result.error_type == "missing_field"
        assert "nodes" in result.message

    def test_missing_description(self, tmp_path: Path) -> None:
        """缺少必填字段 description。"""
        content = """\
---
name: no-desc
version: 1.0.0
entry_node: start
nodes:
  - id: start
    type: transform
    handler_id: transform.passthrough
---
"""
        md_file = _write_pipeline(tmp_path, "no-desc", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineParseError)
        assert result.error_type == "missing_field"
        assert "description" in result.message

    def test_unsupported_version(self, tmp_path: Path) -> None:
        """版本号不以 1. 开头。"""
        content = """\
---
name: bad-version
description: "测试"
version: 2.0.0
entry_node: start
nodes:
  - id: start
    type: transform
    handler_id: transform.passthrough
---
"""
        md_file = _write_pipeline(tmp_path, "bad-version", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineParseError)
        assert result.error_type == "unsupported_version"
        assert "2.0.0" in result.message

    def test_delegation_node_type_rejected(self, tmp_path: Path) -> None:
        """delegation 节点类型在 v0.1 中拒绝。"""
        content = """\
---
name: delegation-test
description: "测试"
version: 1.0.0
entry_node: start
nodes:
  - id: start
    type: delegation
    handler_id: some.handler
---
"""
        md_file = _write_pipeline(tmp_path, "delegation-test", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineParseError)
        assert result.error_type == "unsupported_node_type"
        assert "delegation" in result.message.lower()

    def test_invalid_node_type(self, tmp_path: Path) -> None:
        """无效节点类型。"""
        content = """\
---
name: bad-type
description: "测试"
version: 1.0.0
entry_node: start
nodes:
  - id: start
    type: invalid_type
    handler_id: some.handler
---
"""
        md_file = _write_pipeline(tmp_path, "bad-type", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineParseError)
        assert "invalid_type" in result.message

    def test_invalid_entry_node(self, tmp_path: Path) -> None:
        """entry_node 指向不存在的节点。"""
        content = """\
---
name: bad-entry
description: "测试"
version: 1.0.0
entry_node: nonexistent
nodes:
  - id: start
    type: transform
    handler_id: transform.passthrough
---
"""
        md_file = _write_pipeline(tmp_path, "bad-entry", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineParseError)
        assert result.error_type == "invalid_reference"
        assert "nonexistent" in result.message

    def test_yaml_error(self, tmp_path: Path) -> None:
        """YAML 语法错误。"""
        content = """\
---
name: [invalid yaml
---
"""
        md_file = _write_pipeline(tmp_path, "yaml-error", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineParseError)
        assert result.error_type == "yaml_error"

    def test_no_frontmatter(self, tmp_path: Path) -> None:
        """没有 frontmatter。"""
        content = "Just some markdown without frontmatter."
        md_file = _write_pipeline(tmp_path, "no-fm", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineParseError)
        assert result.error_type == "yaml_error"

    def test_missing_handler_id(self, tmp_path: Path) -> None:
        """节点缺少 handler_id。"""
        content = """\
---
name: no-handler
description: "测试"
version: 1.0.0
entry_node: start
nodes:
  - id: start
    type: transform
---
"""
        md_file = _write_pipeline(tmp_path, "no-handler", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineParseError)
        assert result.error_type == "missing_field"
        assert "handler_id" in result.message

    def test_duplicate_node_id(self, tmp_path: Path) -> None:
        """节点 ID 重复。"""
        content = """\
---
name: dup-id
description: "测试"
version: 1.0.0
entry_node: start
nodes:
  - id: start
    type: transform
    handler_id: transform.passthrough
    next: start
  - id: start
    type: transform
    handler_id: transform.passthrough
---
"""
        md_file = _write_pipeline(tmp_path, "dup-id", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineParseError)
        assert result.error_type == "invalid_reference"
        assert "重复" in result.message

    def test_optional_fields_default(self, tmp_path: Path) -> None:
        """可选字段缺失时使用空默认值。"""
        content = """\
---
name: minimal
description: "最小"
version: 1.0.0
entry_node: start
nodes:
  - id: start
    type: transform
    handler_id: transform.passthrough
---
"""
        md_file = _write_pipeline(tmp_path, "minimal", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineManifest)
        assert result.author == ""
        assert result.tags == []
        assert result.trigger_hint == ""
        assert result.input_schema == {}
        assert result.output_schema == {}

    def test_node_metadata_preserved(self, tmp_path: Path) -> None:
        """节点定义中非标准字段保存到 metadata。"""
        content = """\
---
name: meta-test
description: "测试"
version: 1.0.0
entry_node: start
nodes:
  - id: start
    type: tool
    handler_id: terminal.exec
    command: "echo hello"
    custom_key: custom_value
---
"""
        md_file = _write_pipeline(tmp_path, "meta-test", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineManifest)
        node = result.definition.nodes[0]
        assert node.metadata.get("command") == "echo hello"
        assert node.metadata.get("custom_key") == "custom_value"

    def test_node_timeout_seconds(self, tmp_path: Path) -> None:
        """节点定义中 timeout_seconds 和 retry_limit 正确解析。"""
        content = """\
---
name: timeout-test
description: "测试"
version: 1.0.0
entry_node: start
nodes:
  - id: start
    type: tool
    handler_id: terminal.exec
    timeout_seconds: 30.5
    retry_limit: 3
---
"""
        md_file = _write_pipeline(tmp_path, "timeout-test", content)
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineManifest)
        node = result.definition.nodes[0]
        assert node.timeout_seconds == 30.5
        assert node.retry_limit == 3

    def test_file_not_found(self, tmp_path: Path) -> None:
        """文件不存在。"""
        md_file = tmp_path / "nonexistent" / "PIPELINE.md"
        result = parse_pipeline_file(md_file, PipelineSource.BUILTIN)
        assert isinstance(result, PipelineParseError)
        assert result.error_type == "io_error"


# ============================================================
# DAG 验证测试
# ============================================================


class TestValidateDag:
    """validate_dag 测试。"""

    def test_valid_linear_dag(self) -> None:
        """有效的线性 DAG。"""
        from octoagent.core.models.pipeline import SkillPipelineNode

        nodes = [
            SkillPipelineNode(
                node_id="a", node_type=PipelineNodeType.TRANSFORM,
                handler_id="h", next_node_id="b",
            ),
            SkillPipelineNode(
                node_id="b", node_type=PipelineNodeType.TRANSFORM,
                handler_id="h", next_node_id="c",
            ),
            SkillPipelineNode(
                node_id="c", node_type=PipelineNodeType.TRANSFORM,
                handler_id="h",
            ),
        ]
        result = validate_dag(nodes, "a", "test.md")
        assert result is None

    def test_cycle_detection(self) -> None:
        """检测环路。"""
        from octoagent.core.models.pipeline import SkillPipelineNode

        nodes = [
            SkillPipelineNode(
                node_id="a", node_type=PipelineNodeType.TRANSFORM,
                handler_id="h", next_node_id="b",
            ),
            SkillPipelineNode(
                node_id="b", node_type=PipelineNodeType.TRANSFORM,
                handler_id="h", next_node_id="c",
            ),
            SkillPipelineNode(
                node_id="c", node_type=PipelineNodeType.TRANSFORM,
                handler_id="h", next_node_id="a",
            ),
        ]
        result = validate_dag(nodes, "a", "test.md")
        assert result is not None
        assert result.error_type == "cycle_detected"
        assert "a" in result.message

    def test_self_loop(self) -> None:
        """自循环检测。"""
        from octoagent.core.models.pipeline import SkillPipelineNode

        nodes = [
            SkillPipelineNode(
                node_id="a", node_type=PipelineNodeType.TRANSFORM,
                handler_id="h", next_node_id="a",
            ),
        ]
        result = validate_dag(nodes, "a", "test.md")
        assert result is not None
        assert result.error_type == "cycle_detected"

    def test_orphan_node(self) -> None:
        """检测孤立节点。"""
        from octoagent.core.models.pipeline import SkillPipelineNode

        nodes = [
            SkillPipelineNode(
                node_id="a", node_type=PipelineNodeType.TRANSFORM,
                handler_id="h", next_node_id="b",
            ),
            SkillPipelineNode(
                node_id="b", node_type=PipelineNodeType.TRANSFORM,
                handler_id="h",
            ),
            SkillPipelineNode(
                node_id="orphan", node_type=PipelineNodeType.TRANSFORM,
                handler_id="h",
            ),
        ]
        result = validate_dag(nodes, "a", "test.md")
        assert result is not None
        assert result.error_type == "orphan_node"
        assert "orphan" in result.message

    def test_invalid_next_reference(self) -> None:
        """next 引用不存在的节点。"""
        from octoagent.core.models.pipeline import SkillPipelineNode

        nodes = [
            SkillPipelineNode(
                node_id="a", node_type=PipelineNodeType.TRANSFORM,
                handler_id="h", next_node_id="nonexistent",
            ),
        ]
        result = validate_dag(nodes, "a", "test.md")
        assert result is not None
        assert result.error_type == "invalid_reference"
        assert "nonexistent" in result.message

    def test_single_node_is_valid(self) -> None:
        """单个节点（entry_node = 终止节点）有效。"""
        from octoagent.core.models.pipeline import SkillPipelineNode

        nodes = [
            SkillPipelineNode(
                node_id="only", node_type=PipelineNodeType.TRANSFORM,
                handler_id="h",
            ),
        ]
        result = validate_dag(nodes, "only", "test.md")
        assert result is None


# ============================================================
# PipelineRegistry 测试
# ============================================================


class TestPipelineRegistry:
    """PipelineRegistry 测试。"""

    def test_scan_builtin_dir(self, tmp_path: Path) -> None:
        """扫描内置目录。"""
        _write_pipeline(tmp_path / "builtin", "test-pipeline", _VALID_PIPELINE)
        registry = PipelineRegistry(builtin_dir=tmp_path / "builtin")
        manifests = registry.scan()

        assert len(manifests) == 1
        assert manifests[0].pipeline_id == "test-pipeline"
        assert manifests[0].source == PipelineSource.BUILTIN

    def test_scan_empty_dir(self, tmp_path: Path) -> None:
        """扫描空目录。"""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        registry = PipelineRegistry(builtin_dir=empty_dir)
        manifests = registry.scan()
        assert manifests == []

    def test_scan_nonexistent_dir(self, tmp_path: Path) -> None:
        """目录不存在时安全降级。"""
        registry = PipelineRegistry(builtin_dir=tmp_path / "nonexistent")
        manifests = registry.scan()
        assert manifests == []

    def test_priority_override_user_over_builtin(self, tmp_path: Path) -> None:
        """用户级覆盖内置级。"""
        builtin_dir = tmp_path / "builtin"
        user_dir = tmp_path / "user"

        _write_pipeline(builtin_dir, "test-pipeline", _VALID_PIPELINE)

        user_pipeline = _VALID_PIPELINE.replace(
            'description: "测试 Pipeline"',
            'description: "用户覆盖版本"',
        )
        _write_pipeline(user_dir, "test-pipeline", user_pipeline)

        registry = PipelineRegistry(builtin_dir=builtin_dir, user_dir=user_dir)
        manifests = registry.scan()

        assert len(manifests) == 1
        assert manifests[0].description == "用户覆盖版本"
        assert manifests[0].source == PipelineSource.USER

    def test_priority_override_project_over_user(self, tmp_path: Path) -> None:
        """项目级覆盖用户级。"""
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"

        _write_pipeline(user_dir, "test-pipeline", _VALID_PIPELINE)

        project_pipeline = _VALID_PIPELINE.replace(
            'description: "测试 Pipeline"',
            'description: "项目覆盖版本"',
        )
        _write_pipeline(project_dir, "test-pipeline", project_pipeline)

        registry = PipelineRegistry(user_dir=user_dir, project_dir=project_dir)
        manifests = registry.scan()

        assert len(manifests) == 1
        assert manifests[0].description == "项目覆盖版本"
        assert manifests[0].source == PipelineSource.PROJECT

    def test_get_by_pipeline_id(self, tmp_path: Path) -> None:
        """按 pipeline_id 从缓存获取。"""
        _write_pipeline(tmp_path / "builtin", "test-pipeline", _VALID_PIPELINE)
        registry = PipelineRegistry(builtin_dir=tmp_path / "builtin")
        registry.scan()

        manifest = registry.get("test-pipeline")
        assert manifest is not None
        assert manifest.pipeline_id == "test-pipeline"

        # 不存在的 ID 返回 None
        assert registry.get("nonexistent") is None

    def test_list_items(self, tmp_path: Path) -> None:
        """list_items 返回摘要投影列表。"""
        _write_pipeline(tmp_path / "builtin", "test-pipeline", _VALID_PIPELINE)

        second = _VALID_PIPELINE.replace("name: test-pipeline", "name: another-pipeline")
        _write_pipeline(tmp_path / "builtin", "another-pipeline", second)

        registry = PipelineRegistry(builtin_dir=tmp_path / "builtin")
        registry.scan()

        items = registry.list_items()
        assert len(items) == 2
        # 按 pipeline_id 排序
        assert items[0].pipeline_id == "another-pipeline"
        assert items[1].pipeline_id == "test-pipeline"
        # 验证是 PipelineListItem 类型
        assert isinstance(items[0], PipelineListItem)

    def test_refresh_updates_cache(self, tmp_path: Path) -> None:
        """refresh 重新扫描并更新缓存。"""
        builtin_dir = tmp_path / "builtin"
        _write_pipeline(builtin_dir, "test-pipeline", _VALID_PIPELINE)

        registry = PipelineRegistry(builtin_dir=builtin_dir)
        registry.scan()
        assert len(registry.list_items()) == 1

        # 添加新 Pipeline
        second = _VALID_PIPELINE.replace("name: test-pipeline", "name: new-pipeline")
        _write_pipeline(builtin_dir, "new-pipeline", second)

        manifests = registry.refresh()
        assert len(manifests) == 2

    def test_single_file_failure_isolation(self, tmp_path: Path) -> None:
        """单文件解析失败不影响其他 Pipeline。"""
        builtin_dir = tmp_path / "builtin"

        # 有效 Pipeline
        _write_pipeline(builtin_dir, "valid-pipeline", _VALID_PIPELINE)

        # 无效 Pipeline（缺少 entry_node）
        bad_content = """\
---
name: bad-pipeline
description: "坏的"
version: 1.0.0
nodes:
  - id: start
    type: transform
    handler_id: transform.passthrough
---
"""
        _write_pipeline(builtin_dir, "bad-pipeline", bad_content)

        registry = PipelineRegistry(builtin_dir=builtin_dir)
        manifests = registry.scan()

        # 只有有效的被加载
        assert len(manifests) == 1
        assert manifests[0].pipeline_id == "test-pipeline"

    def test_multiple_sources(self, tmp_path: Path) -> None:
        """三级目录都有不同 Pipeline 时全部发现。"""
        builtin_dir = tmp_path / "builtin"
        user_dir = tmp_path / "user"
        project_dir = tmp_path / "project"

        _write_pipeline(builtin_dir, "builtin-only", _VALID_PIPELINE)

        user_pipeline = _VALID_PIPELINE.replace("name: test-pipeline", "name: user-only")
        _write_pipeline(user_dir, "user-only", user_pipeline)

        project_pipeline = _VALID_PIPELINE.replace("name: test-pipeline", "name: project-only")
        _write_pipeline(project_dir, "project-only", project_pipeline)

        registry = PipelineRegistry(
            builtin_dir=builtin_dir,
            user_dir=user_dir,
            project_dir=project_dir,
        )
        manifests = registry.scan()

        ids = {m.pipeline_id for m in manifests}
        assert ids == {"test-pipeline", "user-only", "project-only"}

    def test_to_list_item_projection(self, tmp_path: Path) -> None:
        """PipelineManifest.to_list_item() 投影正确。"""
        _write_pipeline(tmp_path / "builtin", "test-pipeline", _VALID_PIPELINE)
        registry = PipelineRegistry(builtin_dir=tmp_path / "builtin")
        registry.scan()

        manifest = registry.get("test-pipeline")
        assert manifest is not None

        item = manifest.to_list_item()
        assert item.pipeline_id == manifest.pipeline_id
        assert item.description == manifest.description
        assert item.version == manifest.version
        assert item.tags == manifest.tags
        assert item.trigger_hint == manifest.trigger_hint
        assert item.source == manifest.source
