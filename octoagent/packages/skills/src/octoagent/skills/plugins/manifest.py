"""plugin.yaml manifest 数据模型 + 能力分类常量（F106 Phase A）。

PluginManifest 是 plugin.yaml 的解析结果；未知字段宽容（向后兼容）。
能力分类（declarative vs code-capable）基于实际文件存在（纯 stat，绝不 import），
而非 manifest 自报——防 manifest 谎报隐藏可执行制品（spec FR-1.4 / review H7）。
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# plugin name 合法性：复用 skill 的 kebab 约定（skill_models.py 范式）
_PLUGIN_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# behavior overlay allowlist：v0.1 仅 KNOWLEDGE.md（spec DP-11 / CL-7）。
# 不含 TOOLS.md（工具策略干预风险 #9/#10，推 v0.2）；禁 IDENTITY/SOUL/HEARTBEAT/
# BOOTSTRAP/AGENTS/PROJECT/USER（护 H1/H2，避免 plugin 篡改人格/首次见面脚本/用户偏好）。
PLUGIN_BEHAVIOR_ALLOWLIST: frozenset[str] = frozenset({"KNOWLEDGE.md"})

# code-capable 触发文件（review H7）：目录含任一即标记 code-capable。
# 覆盖 .py 之外的可执行载体（.so/.pyc/.pth 等）+ 构建 hook（setup.py/pyproject.toml）。
_CODE_FILE_SUFFIXES: frozenset[str] = frozenset(
    {".py", ".pyc", ".pyo", ".so", ".dylib", ".pyd", ".pyx", ".pth"}
)
_CODE_FILE_NAMES: frozenset[str] = frozenset(
    {"conftest.py", "setup.py", "setup.cfg", "pyproject.toml"}
)

# loader 管理的状态 marker（不计入 code_hash / 不算 code 触发）。
PLUGIN_DISABLED_MARKER = ".disabled"
PLUGIN_APPROVED_MARKER = ".approved"
PLUGIN_MANIFEST_FILE = "plugin.yaml"


class PluginCapability(StrEnum):
    """plugin 能力分类（基于实际文件，纯 stat 判定）。"""

    DECLARATIVE = "declarative"  # 仅声明式制品（skill/behavior markdown），无可执行代码
    CODE = "code"  # 含可执行制品（.py/.so/...），启用须审批


class PluginState(StrEnum):
    """plugin 运行态。"""

    ENABLED = "enabled"
    DISABLED = "disabled"
    PENDING_APPROVAL = "pending_approval"  # code-capable 未审批：发现+列出但代码不 import
    REJECTED = "rejected"


class PluginRejectedReason(StrEnum):
    """plugin 拒载原因（PLUGIN_REJECTED 事件 payload，审计一致性）。"""

    MANIFEST_INVALID = "manifest_invalid"
    NAME_MISMATCH = "name_mismatch"  # manifest name 与目录名不一致
    NAME_INVALID = "name_invalid"  # name 非 kebab / 含路径分隔符
    MISSING_ARTIFACT = "missing_artifact"  # provides 引用的制品不存在
    NAME_COLLISION = "name_collision"  # skill/tool 名与现有冲突（不覆盖）
    THREAT_FLAGGED = "threat_flagged"  # 声明式制品威胁扫描命中
    BEHAVIOR_NOT_ALLOWED = "behavior_not_allowed"  # behavior file 不在 allowlist
    IMPORT_ERROR = "import_error"  # code plugin import 抛错（Phase B）
    APPROVAL_MISSING = "approval_missing"  # code plugin 未审批/hash 不匹配（Phase B）
    PATH_ESCAPE = "path_escape"  # 路径逃逸 plugins_dir
    UNKNOWN = "unknown"


class PluginProvides(BaseModel):
    """plugin 声明提供的制品。未知字段宽容。"""

    model_config = ConfigDict(extra="ignore")

    skills: list[str] = Field(default_factory=list, description="skill 子目录名（各含 SKILL.md）")
    behavior: list[str] = Field(default_factory=list, description="behavior overlay 文件（allowlist 内）")
    tools: list[str] = Field(default_factory=list, description="工具模块（Phase B；如 tools.py）")
    hooks: bool = Field(default=False, description="是否含 hooks.py lifecycle（Phase B）")
    extensions: list[str] = Field(default_factory=list, description="extension 模块（Phase B+）")


class PluginManifest(BaseModel):
    """plugin.yaml 解析结果。未知字段宽容（向后兼容）。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=64, description="plugin 唯一标识，kebab，与目录名一致")
    version: str = Field(default="", description="版本号")
    description: str = Field(default="", description="描述")
    author: str = Field(default="", description="作者（可选）")
    repo: str = Field(default="", description="git 仓库（可选，audit-only 非信任凭证）")
    provides: PluginProvides = Field(default_factory=PluginProvides)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _PLUGIN_NAME_PATTERN.match(v):
            msg = f"plugin name {v!r} 非法：须满足 ^[a-z0-9]+(-[a-z0-9]+)*$（kebab-case）"
            raise ValueError(msg)
        return v


class PluginRecord(BaseModel):
    """plugin 注册/拒载结果（REST 列表 + 内部状态）。"""

    name: str
    version: str = ""
    description: str = ""
    state: PluginState
    capability: PluginCapability
    source: str = "local"  # "local" 或 "git:<repo>"
    provides: PluginProvides = Field(default_factory=PluginProvides)
    code_hash: str | None = None
    reject_reason: PluginRejectedReason | None = None
    scanner_skipped: bool = False
    path: str = ""
