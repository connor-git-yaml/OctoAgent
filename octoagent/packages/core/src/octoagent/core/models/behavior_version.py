"""F107 文件工作台 v0.2 W1 -- behavior_versions 历史表返回契约模型。

behavior 文件（USER.md/IDENTITY.md/SOUL.md 等）版本历史（append-only，record-after + 首版 baseline）。
与 F104 artifact_versions **同存储/隔离模式**（versionable_conn + 共用写锁 + SAVEPOINT 重试），
但 **key 不同**：`(scope, agent_slug, project_slug, file_id)` 而非 `(task_id, logical_file_id)`——
是 sibling 而非 mirror（Codex MED-4）。behavior 文件均为小 md → 恒 inline（无 storage_ref/oversize 分支）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BehaviorFileKey(BaseModel):
    """behavior 逻辑文件身份（覆盖 4 scope；GLOBAL scope 的 agent_slug/project_slug 取约定空值）。

    scope 字段作 discriminator：同 file_id 在不同 scope 下是不同逻辑文件（SD-7）。
    """

    scope: str = Field(description="BehaviorWorkspaceScope 值（system_shared/agent_private/...）")
    agent_slug: str = Field(
        default="", description="AGENT_PRIVATE/PROJECT_AGENT 的 agent slug；GLOBAL 取 ''"
    )
    project_slug: str = Field(
        default="", description="PROJECT_* 的 project slug；GLOBAL 取 ''"
    )
    file_id: str = Field(description="behavior 文件标识，如 USER.md / IDENTITY.md")


class BehaviorVersionMeta(BaseModel):
    """单个版本元信息（不含内容），服务版本时间线 + Advanced 区。"""

    version_no: int = Field(description="该 key 内单调递增版本号")
    ts: str = Field(description="版本写入时间（ISO 8601），兜底排序键")
    size: int = Field(default=0, description="内容大小（字节）")
    hash: str = Field(default="", description="SHA-256 哈希")


class BehaviorVersionContent(BaseModel):
    """单个版本内容，服务任意两版 diff。behavior 恒 inline → availability 恒 available。"""

    version_no: int = Field(description="版本号")
    content: str | None = Field(default=None, description="UTF-8 内容；缺失版本为 None")
    availability: str = Field(
        default="available", description="内容可用性（behavior 恒 available；缺失版本由调用方判 None）"
    )
    size: int = Field(default=0, description="内容大小（字节）")
    hash: str = Field(default="", description="SHA-256 哈希")


class BehaviorFileSummary(BaseModel):
    """有版本历史的 behavior 文件摘要，服务 Agent 中心版本历史入口。"""

    scope: str = Field(description="BehaviorWorkspaceScope 值")
    agent_slug: str = Field(default="")
    project_slug: str = Field(default="")
    file_id: str = Field(description="behavior 文件标识")
    version_count: int = Field(description="该文件的版本数量")
    display_name: str | None = Field(
        default=None, description="友好展示名（前端兜底 file_id）"
    )
