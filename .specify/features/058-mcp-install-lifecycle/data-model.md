# 数据模型: MCP 安装与生命周期管理

**Feature**: 058-mcp-install-lifecycle
**Date**: 2026-03-16

---

## 实体关系总览

```
McpInstallRecord (mcp-installs.json)     McpServerConfig (mcp-servers.json)
      |                                        |
      | server_id == name                      |
      +----------------------------------------+
      |
      v
McpProviderItem (ControlPlane 合并展示)
      |
      v
McpProviderCatalogDocument (前端 snapshot)


McpSessionEntry (内存)
      |
      | server_name == McpServerConfig.name
      v
McpSessionPool (运行时连接管理)


InstallTask (内存)
      |
      | server_id == McpInstallRecord.server_id
      v
McpInstallerService (安装任务管理)
```

---

## 1. McpInstallRecord -- 安装注册表条目

**存储位置**: `data/ops/mcp-installs.json`
**生命周期**: 持久化到磁盘，跨进程重启保留
**所有者**: McpInstallerService

```python
class InstallSource(StrEnum):
    """安装来源类型。"""
    NPM = "npm"
    PIP = "pip"
    DOCKER = "docker"     # P2 预留
    MANUAL = "manual"     # 手动配置（无安装记录）

class InstallStatus(StrEnum):
    """安装状态。"""
    INSTALLING = "installing"       # 安装进行中
    INSTALLED = "installed"         # 安装成功
    FAILED = "failed"               # 安装失败
    UNINSTALLING = "uninstalling"   # 卸载进行中

class McpInstallRecord(BaseModel):
    """描述一个已安装 MCP server 的元数据。"""
    server_id: str                              # 唯一标识，== McpServerConfig.name
    install_source: InstallSource               # npm / pip / docker / manual
    package_name: str                           # 原始包名（如 @anthropic/mcp-server-files）
    version: str = ""                           # 安装版本号
    install_path: str = ""                      # 安装目录绝对路径
    integrity: str = ""                         # 完整性校验值（sha256 hash）
    installed_at: datetime                      # 首次安装时间
    updated_at: datetime                        # 最后更新时间
    status: InstallStatus                       # 当前状态
    auto_generated_config: bool = True          # 配置是否自动生成
    error: str = ""                             # 错误信息（status=failed 时有值）
```

**JSON 存储格式**:

```json
{
  "installs": {
    "mcp_server_files": {
      "server_id": "mcp_server_files",
      "install_source": "npm",
      "package_name": "@anthropic/mcp-server-files",
      "version": "1.2.3",
      "install_path": "/Users/xxx/.octoagent/mcp-servers/mcp_server_files",
      "integrity": "sha256-abcdef1234567890",
      "installed_at": "2026-03-16T10:00:00Z",
      "updated_at": "2026-03-16T10:00:00Z",
      "status": "installed",
      "auto_generated_config": true,
      "error": ""
    }
  },
  "schema_version": 1
}
```

---

## 2. McpServerConfig -- 运行时配置（现有，不修改）

**存储位置**: `data/ops/mcp-servers.json`
**生命周期**: 持久化到磁盘
**所有者**: McpRegistryService

```python
class McpServerConfig(BaseModel):
    """MCP server 运行时配置（保持不变）。"""
    name: str                                    # 唯一标识
    command: str                                 # 可执行命令
    args: list[str] = []                         # 命令参数
    env: dict[str, str] = {}                     # 环境变量
    cwd: str = ""                                # 工作目录
    enabled: bool = True                         # 是否启用
    mount_policy: str = "auto_readonly"          # 挂载策略
```

**关联关系**: McpInstallRecord.server_id == McpServerConfig.name

---

## 3. McpSessionEntry -- 持久连接条目

**存储位置**: 内存（McpSessionPool._entries）
**生命周期**: 与 OctoAgent 进程一致
**所有者**: McpSessionPool

```python
@dataclass
class McpSessionEntry:
    """一个 MCP server 的持久连接条目。"""
    server_name: str                             # == McpServerConfig.name
    config: McpServerConfig                      # 关联的运行时配置快照
    session: ClientSession | None = None         # MCP ClientSession 实例
    exit_stack: AsyncExitStack | None = None     # 资源清理栈
    status: Literal[
        "connected",      # 连接正常
        "disconnected",   # 连接断开
        "reconnecting",   # 正在重连
    ] = "disconnected"
    created_at: datetime | None = None           # 连接建立时间
    last_active_at: datetime | None = None       # 最后活跃时间
    error: str = ""                              # 最后错误信息
    reconnect_count: int = 0                     # 累计重连次数
```

---

## 4. InstallTask -- 安装任务状态

**存储位置**: 内存（McpInstallerService._install_tasks）
**生命周期**: 安装完成/失败后保留 10 分钟供查询，之后清理
**所有者**: McpInstallerService

```python
class InstallTaskStatus(StrEnum):
    """安装任务执行状态。"""
    PENDING = "pending"         # 等待执行
    RUNNING = "running"         # 执行中
    COMPLETED = "completed"     # 成功完成
    FAILED = "failed"           # 执行失败

class InstallTask(BaseModel):
    """描述一个进行中的安装任务。"""
    task_id: str                                 # 任务唯一 ID (uuid4)
    server_id: str                               # 目标 server ID
    install_source: InstallSource                # 安装来源
    package_name: str                            # 包名
    status: InstallTaskStatus = InstallTaskStatus.PENDING
    progress_message: str = ""                   # 当前进度描述
    error: str = ""                              # 错误信息
    result: dict[str, Any] = {}                  # 完成后的结果
    created_at: datetime                         # 任务创建时间
```

**result 字段结构（安装完成时）**:

```json
{
  "server_id": "mcp_server_files",
  "version": "1.2.3",
  "install_path": "/Users/xxx/.octoagent/mcp-servers/mcp_server_files",
  "command": "/Users/xxx/.octoagent/mcp-servers/mcp_server_files/node_modules/.bin/mcp-server-files",
  "tools_count": 5,
  "tools": [
    {"name": "read_file", "description": "Read a file from the filesystem"},
    {"name": "write_file", "description": "Write content to a file"}
  ]
}
```

---

## 5. McpProviderItem 扩展 -- 合并展示模型

**存储位置**: ControlPlane snapshot（运行时生成）
**生命周期**: 每次 snapshot 生成时重建
**所有者**: ControlPlaneService

```python
class McpProviderItem(BaseModel):
    """MCP Provider 展示模型（在 ControlPlane catalog document 中）。"""

    # ---- 现有字段（保持不变） ----
    provider_id: str
    label: str
    description: str = ""
    editable: bool = True
    removable: bool = True
    enabled: bool = True
    status: str = "unconfigured"
    command: str = ""
    args: list[str] = []
    cwd: str = ""
    env: dict[str, str] = {}
    mount_policy: str = "auto_readonly"
    tool_count: int = 0
    selection_item_id: str = ""
    install_hint: str = ""
    error: str = ""
    warnings: list[str] = []
    details: dict[str, Any] = {}

    # ---- 新增字段（安装信息） ----
    install_source: str = ""       # "npm" | "pip" | "docker" | "manual" | ""
    install_version: str = ""      # 安装版本号
    install_path: str = ""         # 安装目录路径
    installed_at: str = ""         # 安装时间 ISO 字符串
```

**数据来源映射**:

| McpProviderItem 字段 | 数据来源 |
|---------------------|---------|
| provider_id, label, command, args, cwd, env, enabled, mount_policy | McpServerConfig |
| status, tool_count, error | McpServerRecord (来自 McpRegistryService) |
| install_source, install_version, install_path, installed_at | McpInstallRecord (来自 McpInstallerService) |
| selection_item_id, install_hint, warnings | SkillGovernanceItem (来自 ControlPlaneService) |

---

## 6. Event 类型

安装/卸载/连接变更操作生成的事件类型（写入 Event Store）:

| event_type | 触发时机 | payload 主要字段 |
|------------|---------|-----------------|
| `mcp.server.installed` | 安装成功完成 | server_id, install_source, package_name, version, tools_count |
| `mcp.server.install_failed` | 安装失败 | server_id, install_source, package_name, error |
| `mcp.server.uninstalled` | 卸载完成 | server_id, install_source, package_name |
| `mcp.session.connected` | 持久连接建立 | server_name, transport="stdio" |
| `mcp.session.disconnected` | 持久连接断开 | server_name, reason, error |
| `mcp.session.reconnected` | 自动重连成功 | server_name, reconnect_count |
| `mcp.session.health_check_failed` | 健康检查失败 | server_name, error |
