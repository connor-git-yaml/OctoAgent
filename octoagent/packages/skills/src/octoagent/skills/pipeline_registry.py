"""Feature 065: PipelineRegistry — PIPELINE.md 文件系统扫描、解析、DAG 验证、缓存服务。

复用 BaseFilesystemRegistry 泛型基类提供的三级目录扫描模式（内置 > 用户 > 项目），
解析 YAML frontmatter + Markdown body，验证 DAG 拓扑，按优先级去重，构建内存缓存。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from octoagent.core.models.pipeline import (
    PipelineNodeType,
    SkillPipelineDefinition,
    SkillPipelineNode,
)

from .discovery import parse_frontmatter, split_frontmatter
from .pipeline_models import (
    PipelineInputField,
    PipelineListItem,
    PipelineManifest,
    PipelineOutputField,
    PipelineParseError,
    PipelineSource,
)
from .registry_base import AssetSource, BaseFilesystemRegistry

logger = structlog.get_logger(__name__)

# v0.1 仅支持 1.x.x 版本
_SUPPORTED_VERSION_PREFIX = "1."

# v0.1 不支持 delegation 节点类型
_UNSUPPORTED_NODE_TYPES = {PipelineNodeType.DELEGATION}


# ============================================================
# PIPELINE.md 解析函数
# ============================================================


def parse_pipeline_file(
    file_path: Path,
    source: PipelineSource,
) -> PipelineManifest | PipelineParseError:
    """解析单个 PIPELINE.md 文件。

    Args:
        file_path: PIPELINE.md 文件路径
        source: Pipeline 来源分类

    Returns:
        成功返回 PipelineManifest，失败返回 PipelineParseError
    """
    # 1. 读取文件
    try:
        raw = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return PipelineParseError(
            file_path=str(file_path),
            error_type="io_error",
            message="非 UTF-8 编码，无法读取",
        )
    except OSError as exc:
        return PipelineParseError(
            file_path=str(file_path),
            error_type="io_error",
            message=f"文件读取失败: {exc}",
        )

    # 2. 分离 frontmatter 和 body
    frontmatter_str, body = split_frontmatter(raw)
    if not frontmatter_str:
        return PipelineParseError(
            file_path=str(file_path),
            error_type="yaml_error",
            message="缺少 YAML frontmatter（文件应以 --- 开头）",
        )

    # 3. 解析 YAML
    try:
        data = parse_frontmatter(frontmatter_str)
    except Exception as exc:
        return PipelineParseError(
            file_path=str(file_path),
            error_type="yaml_error",
            message=f"YAML 解析失败: {exc}",
        )

    # 4. 验证必填字段
    missing = _check_required_fields(data)
    if missing:
        return PipelineParseError(
            file_path=str(file_path),
            error_type="missing_field",
            message=f"缺少必填字段: {', '.join(missing)}",
            details={"missing_fields": missing},
        )

    # 5. 版本检查
    version = str(data["version"]).strip()
    if not version.startswith(_SUPPORTED_VERSION_PREFIX):
        return PipelineParseError(
            file_path=str(file_path),
            error_type="unsupported_version",
            message=f"不支持的 Pipeline 版本 '{version}'。v0.1 仅支持 1.x.x 版本。",
            details={"version": version},
        )

    # 6. 解析节点列表
    nodes_raw = data["nodes"]
    if not isinstance(nodes_raw, list) or len(nodes_raw) == 0:
        return PipelineParseError(
            file_path=str(file_path),
            error_type="missing_field",
            message="nodes 必须是非空列表",
        )

    nodes_result = _parse_nodes(nodes_raw, str(file_path))
    if isinstance(nodes_result, PipelineParseError):
        return nodes_result

    nodes: list[SkillPipelineNode] = nodes_result

    # 7. 验证 entry_node
    entry_node = str(data["entry_node"]).strip()
    node_ids = {n.node_id for n in nodes}
    if entry_node not in node_ids:
        return PipelineParseError(
            file_path=str(file_path),
            error_type="invalid_reference",
            message=f"entry_node '{entry_node}' 不存在于节点列表中",
            details={"entry_node": entry_node, "available_nodes": sorted(node_ids)},
        )

    # 8. DAG 验证
    dag_error = validate_dag(nodes, entry_node, str(file_path))
    if dag_error is not None:
        return dag_error

    # 9. 构建 SkillPipelineDefinition
    pipeline_name = str(data["name"]).strip()
    definition = SkillPipelineDefinition(
        pipeline_id=pipeline_name,
        label=str(data["description"]).strip(),
        version=version,
        entry_node_id=entry_node,
        nodes=nodes,
        metadata=data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {},
    )

    # 10. 解析可选字段
    input_schema = _parse_schema_fields(data.get("input_schema"), PipelineInputField)
    output_schema = _parse_schema_fields(data.get("output_schema"), PipelineOutputField)

    tags = data.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip() for t in tags if str(t).strip()]

    manifest = PipelineManifest(
        pipeline_id=pipeline_name,
        description=str(data["description"]).strip(),
        version=version,
        author=str(data.get("author", "")).strip(),
        tags=tags,
        trigger_hint=str(data.get("trigger_hint", "")).strip(),
        input_schema=input_schema,
        output_schema=output_schema,
        source=source,
        source_path=str(file_path.resolve()),
        content=body,
        definition=definition,
        raw_frontmatter=data,
    )
    return manifest


# ============================================================
# DAG 验证
# ============================================================


def validate_dag(
    nodes: list[SkillPipelineNode],
    entry_node: str,
    file_path: str,
) -> PipelineParseError | None:
    """验证节点构成的有向图：next 引用存在性 + DFS 环检测 + 孤立节点检查。

    Args:
        nodes: 节点列表
        entry_node: 入口节点 ID
        file_path: 文件路径（用于错误报告）

    Returns:
        验证通过返回 None，失败返回 PipelineParseError
    """
    node_ids = {n.node_id for n in nodes}

    # 1. 验证所有 next 引用存在
    for node in nodes:
        if node.next_node_id and node.next_node_id not in node_ids:
            return PipelineParseError(
                file_path=file_path,
                error_type="invalid_reference",
                message=f"节点 '{node.node_id}' 的 next '{node.next_node_id}' 不存在",
                details={
                    "node_id": node.node_id,
                    "invalid_next": node.next_node_id,
                    "available_nodes": sorted(node_ids),
                },
            )

    # 2. DFS 环检测
    adjacency: dict[str, str | None] = {
        n.node_id: n.next_node_id for n in nodes
    }
    cycle_error = _detect_cycle(adjacency, file_path)
    if cycle_error is not None:
        return cycle_error

    # 3. 孤立节点检查：每个节点必须从 entry_node 可达
    reachable = _find_reachable(entry_node, adjacency)
    orphans = node_ids - reachable
    if orphans:
        return PipelineParseError(
            file_path=file_path,
            error_type="orphan_node",
            message=(
                f"存在从 entry_node '{entry_node}' "
                f"不可达的孤立节点: {', '.join(sorted(orphans))}"
            ),
            details={"orphan_nodes": sorted(orphans), "entry_node": entry_node},
        )

    return None


def _detect_cycle(
    adjacency: dict[str, str | None],
    file_path: str,
) -> PipelineParseError | None:
    """DFS 环检测。

    由于每个节点最多只有一个 next_node_id（单链拓扑），
    可以用快慢指针或简单路径追踪。这里用路径追踪。
    """
    # 对于每个节点，沿 next 链走，如果走到已在路径中的节点则有环
    visited: set[str] = set()

    for start_node in adjacency:
        if start_node in visited:
            continue

        path: list[str] = []
        path_set: set[str] = set()
        current: str | None = start_node

        while current is not None:
            if current in path_set:
                # 找到环，提取环路径
                cycle_start_idx = path.index(current)
                cycle_path = path[cycle_start_idx:] + [current]
                return PipelineParseError(
                    file_path=file_path,
                    error_type="cycle_detected",
                    message=f"检测到环: {' -> '.join(cycle_path)}",
                    details={"cycle_path": cycle_path},
                )

            if current in visited:
                # 此节点已被处理过且无环，安全
                break

            path.append(current)
            path_set.add(current)
            current = adjacency.get(current)

        visited.update(path_set)

    return None


def _find_reachable(entry_node: str, adjacency: dict[str, str | None]) -> set[str]:
    """从 entry_node 沿 next 链找出所有可达节点。"""
    reachable: set[str] = set()
    current: str | None = entry_node
    while current is not None and current not in reachable:
        reachable.add(current)
        current = adjacency.get(current)
    return reachable


# ============================================================
# 内部辅助函数
# ============================================================


def _check_required_fields(data: dict[str, Any]) -> list[str]:
    """检查 PIPELINE.md 必填字段。"""
    required = ["name", "description", "version", "entry_node", "nodes"]
    return [f for f in required if not data.get(f)]


def _parse_nodes(
    nodes_raw: list[Any],
    file_path: str,
) -> list[SkillPipelineNode] | PipelineParseError:
    """解析节点列表。"""
    nodes: list[SkillPipelineNode] = []
    seen_ids: set[str] = set()

    for i, raw_node in enumerate(nodes_raw):
        if not isinstance(raw_node, dict):
            return PipelineParseError(
                file_path=file_path,
                error_type="missing_field",
                message=f"nodes[{i}] 必须是 dict",
            )

        # 检查节点必填字段
        node_id = str(raw_node.get("id", "")).strip()
        if not node_id:
            return PipelineParseError(
                file_path=file_path,
                error_type="missing_field",
                message=f"nodes[{i}] 缺少必填字段 'id'",
            )

        if node_id in seen_ids:
            return PipelineParseError(
                file_path=file_path,
                error_type="invalid_reference",
                message=f"节点 ID 重复: '{node_id}'",
                details={"duplicate_node_id": node_id},
            )
        seen_ids.add(node_id)

        node_type_str = str(raw_node.get("type", "")).strip()
        if not node_type_str:
            return PipelineParseError(
                file_path=file_path,
                error_type="missing_field",
                message=f"nodes[{i}] (id='{node_id}') 缺少必填字段 'type'",
            )

        # 验证节点类型
        try:
            node_type = PipelineNodeType(node_type_str)
        except ValueError:
            return PipelineParseError(
                file_path=file_path,
                error_type="missing_field",
                message=f"nodes[{i}] (id='{node_id}') 的 type '{node_type_str}' 无效。"
                f"有效值: {', '.join(t.value for t in PipelineNodeType)}",
            )

        # v0.1 不支持 delegation
        if node_type in _UNSUPPORTED_NODE_TYPES:
            return PipelineParseError(
                file_path=file_path,
                error_type="unsupported_node_type",
                message="delegation 节点类型在 v0.1 中不支持，"
                "请使用 Subagent 工具替代不确定性子任务",
                details={"node_id": node_id, "node_type": node_type_str},
            )

        handler_id = str(raw_node.get("handler_id", "")).strip()
        if not handler_id:
            return PipelineParseError(
                file_path=file_path,
                error_type="missing_field",
                message=f"nodes[{i}] (id='{node_id}') 缺少必填字段 'handler_id'",
            )

        # 可选字段
        next_node_id = str(raw_node.get("next", "")).strip() or None
        label = str(raw_node.get("label", "")).strip()
        retry_limit = int(raw_node.get("retry_limit", 0))
        timeout_raw = raw_node.get("timeout_seconds")
        timeout_seconds = float(timeout_raw) if timeout_raw is not None else None

        # 节点级 metadata（从节点定义中提取非标准字段）
        node_metadata: dict[str, Any] = {}
        reserved_keys = {
            "id", "type", "handler_id", "next",
            "label", "retry_limit", "timeout_seconds",
        }
        for key, value in raw_node.items():
            if key not in reserved_keys:
                node_metadata[key] = value

        nodes.append(
            SkillPipelineNode(
                node_id=node_id,
                label=label,
                node_type=node_type,
                handler_id=handler_id,
                next_node_id=next_node_id,
                retry_limit=retry_limit,
                timeout_seconds=timeout_seconds,
                metadata=node_metadata,
            )
        )

    return nodes


def _parse_schema_fields(
    raw: Any,
    field_class: type[PipelineInputField] | type[PipelineOutputField],
) -> dict[str, PipelineInputField] | dict[str, PipelineOutputField]:
    """解析 input_schema / output_schema 字段。"""
    if not isinstance(raw, dict):
        return {}

    result = {}
    for key, value in raw.items():
        key_str = str(key).strip()
        if not key_str:
            continue
        if isinstance(value, dict):
            try:
                result[key_str] = field_class(**value)
            except Exception:
                # 解析单个字段失败时跳过该字段
                result[key_str] = field_class()
        else:
            result[key_str] = field_class()

    return result


# ============================================================
# PipelineRegistry 核心服务
# ============================================================


class PipelineRegistry(BaseFilesystemRegistry[PipelineManifest]):
    """PIPELINE.md 文件系统扫描与缓存服务。

    继承 BaseFilesystemRegistry 泛型基类，只实现 Pipeline 特有的解析逻辑。

    三级目录优先级（从低到高）：
    - builtin_dir: 仓库 pipelines/ 目录（内置）
    - user_dir: ~/.octoagent/pipelines/ 目录（用户全局）
    - project_dir: {project_root}/pipelines/ 目录（项目级）

    同名 Pipeline 按优先级覆盖。
    """

    # ------------------------------------------------------------------
    # BaseFilesystemRegistry 抽象实现
    # ------------------------------------------------------------------

    @property
    def _marker_filename(self) -> str:
        return "PIPELINE.md"

    @property
    def _log_prefix(self) -> str:
        return "pipeline_registry"

    def _entry_key(self, entry: PipelineManifest) -> str:
        return entry.pipeline_id

    def _parse_file(self, file_path: Path, source: AssetSource) -> PipelineManifest | None:
        """解析单个 PIPELINE.md 文件。"""
        # AssetSource -> PipelineSource 映射
        pipeline_source = PipelineSource(source.value)

        result = parse_pipeline_file(file_path, pipeline_source)

        if isinstance(result, PipelineParseError):
            logger.warning(
                "pipeline_registry_parse_error",
                file_path=result.file_path,
                error_type=result.error_type,
                message=result.message,
            )
            return None

        return result

    # ------------------------------------------------------------------
    # Pipeline 特有的公共 API
    # ------------------------------------------------------------------

    def list_items(self) -> list[PipelineListItem]:
        """返回所有缓存 Pipeline 的摘要投影列表（按 pipeline_id 排序）。"""
        items = [entry.to_list_item() for entry in self._cache.values()]
        items.sort(key=lambda x: x.pipeline_id)
        return items
