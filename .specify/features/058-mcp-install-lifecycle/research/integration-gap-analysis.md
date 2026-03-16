# 设计方案接缝分析：MCP Installer 与现有系统整合

**分析日期**: 2026-03-16
**分析范围**: 完整代码库扫描（19,000+ 行 MCP 相关代码）

---

## 0. 关键发现：Pydantic AI MCPServer Toolset 路径与现有架构不兼容

**产研汇总中的推荐方案有一个重大接缝。**

产研汇总推荐"直接复用 Pydantic AI MCPServer 作为 Toolset 注入 Agent"，但代码库扫描发现：

> **OctoAgent 不直接使用 Pydantic AI Agent 类。**
> 工具执行链路是：`LiteLLMSkillClient → ToolBroker → handler`，不经过 Pydantic AI Agent 的 `toolsets=[]` 参数。

这意味着 Pydantic AI 的 Toolset 组合体系（Filter/Prefix/Approval）**无法直接插入**现有链路。ToolBroker 自身已经实现了等价的治理能力。

---

## 1. 现有 MCP 架构完整图

```
                        Frontend (McpProviderCenter.tsx)
                            │
                  submitAction("mcp_provider.save/delete")
                            │
                            v
                   ControlPlaneService
                   ├── _handle_mcp_provider_save()  → McpRegistryService.save_config()
                   ├── _handle_mcp_provider_delete() → McpRegistryService.delete_config()
                   └── get_mcp_provider_catalog_document()  → 读 registry 状态
                            │
                            v
                    McpRegistryService (477 行)
                    ├── save_config() / delete_config()     → data/ops/mcp-servers.json
                    ├── startup() / refresh()               → _discover_server_tools()
                    │     └── _open_session()               → stdio_client → list_tools
                    ├── _build_tool_meta() + _build_tool_handler()
                    │     └── ToolBroker.register("mcp.{server}.{tool}", handler)
                    └── call_tool()                         → _open_session → session.call_tool
                            │
                            v
                       ToolBroker (packages/tooling/)
                       ├── Profile 过滤 (MINIMAL/STANDARD/PRIVILEGED)
                       ├── PolicyCheckpoint (IRREVERSIBLE 审批)
                       ├── Before/After Hook 链
                       ├── Event 记录 (TOOL_CALL_STARTED/COMPLETED/FAILED)
                       └── Timeout 控制
                            │
                            v
                  SkillRunner + LiteLLMSkillClient
                  ├── ToolBroker.discover(profile) → 获取可用工具列表
                  ├── 转换为 OpenAI function definitions
                  ├── 发送给 LiteLLM Proxy (SSE)
                  ├── 解析 tool_calls
                  └── ToolBroker.execute(tool_name, args, context) → handler → call_tool
```

### 现有系统已经覆盖的能力

| 能力 | 状态 | 实现位置 |
|------|------|---------|
| MCP 配置持久化 | ✅ 已有 | `data/ops/mcp-servers.json` |
| MCP server stdio 连接 | ✅ 已有 | McpRegistryService._open_session() |
| 工具发现 (list_tools) | ✅ 已有 | McpRegistryService._discover_server_tools() |
| ToolBroker 注册 | ✅ 已有 | `mcp.{server}.{tool}` 命名空间 |
| Profile 权限控制 | ✅ 已有 | mount_policy → ToolProfile 映射 |
| Side-effect 治理 | ✅ 已有 | MCP annotations → SideEffectLevel |
| Policy 审批门禁 | ✅ 已有 | ToolBroker + PolicyCheckpoint |
| Event 记录 | ✅ 已有 | ToolBroker 事件链 |
| 前端 CRUD UI | ✅ 已有 | McpProviderCenter.tsx |
| Control Plane 集成 | ✅ 已有 | actions + catalog document |
| Governance 集成 | ✅ 已有 | SkillGovernanceItem for MCP |

### 现有系统缺失的能力（真正需要新建的）

| 能力 | 状态 | 说明 |
|------|------|------|
| **远程安装（npm/pip）** | ❌ 缺失 | 从 registry 拉取并部署到本地 |
| **依赖隔离** | ❌ 缺失 | 每个 server 独立 node_modules/venv |
| **安装注册表** | ❌ 缺失 | 记录 source/version/integrity/install_path |
| **版本管理/更新** | ❌ 缺失 | 检查更新 + 升级 |
| **卸载（文件清理）** | ❌ 缺失 | 删除安装文件 + 依赖 |
| **持久 session** | ❌ 缺失 | 当前 per-operation，每次启动子进程 |
| **健康检查** | ❌ 缺失 | 主动探测 server 存活状态 |
| **自动重连** | ❌ 缺失 | server 崩溃后恢复 |
| **安装向导 UI** | ❌ 缺失 | 包名输入 → 一键安装流程 |

---

## 2. 接缝清单

### 接缝 #1: Pydantic AI MCPServer 路径不可用（严重）

**问题**: 产研汇总推荐 "直接复用 Pydantic AI MCPServer 作为 Toolset"，但 OctoAgent 的工具链是 ToolBroker 驱动的，不走 Pydantic AI Agent 的 toolsets 参数。

**影响**: 如果强行引入 MCPServer Toolset 路径，需要维护两套工具注入链路（ToolBroker 路径 + Toolset 路径），增加复杂度且与现有治理机制冲突。

**修正方案**: **不替换 ToolBroker 路径，而是在 McpRegistryService 内部改进连接管理。** 具体：
- 将 per-operation session 改为 **持久 session 池**（从 Pydantic AI 借鉴引用计数思路，但实现在 McpRegistryService 内部）
- ToolBroker 的 hook/policy/event 治理链路完整保留
- MCP 工具继续通过 `mcp.{server}.{tool}` 注册到 ToolBroker

### 接缝 #2: 安装能力注入点不明确

**问题**: McpRegistryService 当前只有 save_config() 和 delete_config()，配置中的 command/args 假设用户已经自行安装好了 MCP server 可执行文件。

**修正方案**: 新增 `McpInstallerService`，与 McpRegistryService 协作：
```
McpInstallerService.install(spec)
  → 下载/安装文件到 ~/.octoagent/mcp-servers/{id}/
  → 生成 McpServerConfig (command, args, cwd 自动填充)
  → 调用 McpRegistryService.save_config(config)
  → 触发 McpRegistryService.refresh() 发现工具
```

### 接缝 #3: 配置格式差异

**问题**:
- 现有格式: `{"servers": [{"name": "x", "command": "...", ...}]}` (数组)
- 行业标准: `{"mcpServers": {"x": {"command": "...", ...}}}` (dict)
- Pydantic AI: 使用 mcpServers dict 格式

**修正方案**:
- McpRegistryService 内部保持现有 `servers` list 格式（避免破坏性变更）
- 新增 `import_claude_desktop_config(path)` 方法，支持从 Claude Desktop 格式导入
- 安装向导 UI 不暴露格式差异，用户只需输入包名

### 接缝 #4: 前端安装流程缺失

**问题**: McpProviderCenter.tsx 当前只有 "新建" 按钮（手动填写 command/args），没有"安装"流程。

**修正方案**:
- 在 McpProviderCenter 顶栏增加 "安装" 按钮（与 "新建" 并列）
- "安装" 打开安装向导 modal：
  1. 选择来源（npm/pip/自定义命令）
  2. 输入包名（如 `@anthropic/mcp-server-files`）
  3. 配置环境变量（可选）
  4. 一键安装 → 后台执行 → 显示进度 → 完成后自动出现在列表中
- 现有 "新建" 按钮保留，用于已安装但需手动配置的场景

### 接缝 #5: 安装注册表存储

**问题**: 现有配置存储在 `data/ops/mcp-servers.json`，只有运行时配置（command/args/env），没有安装元数据（source/version/install_path/integrity）。

**修正方案**:
- 新增 `data/ops/mcp-installs.json`（或扩展到 SQLite）
- 格式参考 OpenClaw 的 entries/installs 分离：
  ```json
  {
    "installs": {
      "mcp-server-files": {
        "source": "npm",
        "package": "@anthropic/mcp-server-files",
        "version": "1.2.3",
        "install_path": "~/.octoagent/mcp-servers/mcp-server-files/",
        "integrity": "sha256-...",
        "installed_at": "2026-03-16T10:00:00Z",
        "auto_generated_config": true
      }
    }
  }
  ```
- McpRegistryService 的 `mcp-servers.json` 继续只存运行时配置
- McpInstallerService 管理 `mcp-installs.json`

### 接缝 #6: 持久 session 改造路径

**问题**: 当前 `_open_session()` 每次创建新的 stdio_client 连接（启动子进程），调用完立即关闭。

**修正方案**: 在 McpRegistryService 内部引入 session 池：
```python
class _McpSessionPool:
    """管理 MCP server 的持久连接"""
    _sessions: dict[str, ClientSession]
    _processes: dict[str, AsyncExitStack]
    _lock: asyncio.Lock

    async def get_session(self, server_name: str, config: McpServerConfig) -> ClientSession:
        """获取或创建持久 session"""
        if server_name in self._sessions:
            return self._sessions[server_name]
        # 创建新连接并缓存
        ...

    async def close(self, server_name: str): ...
    async def close_all(self): ...
    async def health_check(self, server_name: str) -> bool: ...
```

**改造范围**: 仅修改 McpRegistryService 内部实现，外部 API（save_config/delete_config/call_tool/list_tools）保持不变。

---

## 3. 模块变更清单

### 需要新建的文件

| 文件 | 职责 | 估算行数 |
|------|------|---------|
| `gateway/services/mcp_installer.py` | 安装/卸载/更新服务 | ~400 |
| `gateway/services/mcp_session_pool.py` | 持久 session 管理 | ~200 |
| `frontend/src/components/McpInstallWizard.tsx` | 安装向导 modal | ~250 |

### 需要修改的文件

| 文件 | 变更内容 | 影响范围 |
|------|---------|---------|
| `gateway/services/mcp_registry.py` | 注入 session pool 替代 per-operation session；新增 install_path 字段 | 内部重构，外部 API 不变 |
| `gateway/services/control_plane.py` | 新增 `mcp_provider.install` / `mcp_provider.uninstall` action handler | 新增 action，不影响现有 |
| `gateway/main.py` | 初始化 McpInstallerService，绑定到 CapabilityPackService | 新增初始化代码 |
| `core/models/control_plane.py` | McpProviderItem 新增 install_source/install_version/install_path 字段 | 向后兼容扩展 |
| `frontend/src/pages/McpProviderCenter.tsx` | 新增"安装"按钮 + 集成安装向导 | UI 扩展 |
| `frontend/src/types/index.ts` | McpProviderItem 新增字段 | 类型扩展 |

### 不需要修改的文件

| 文件 | 理由 |
|------|------|
| `packages/tooling/broker.py` | ToolBroker 完全不动，MCP 工具继续走现有注册链路 |
| `packages/skills/runner.py` | SkillRunner 不动，工具调用继续走 ToolBroker |
| `packages/skills/litellm_client.py` | LLM 客户端不动 |
| `gateway/services/capability_pack.py` | 已通过 McpRegistryService 间接集成，无需修改 |
| `gateway/routes/control_plane.py` | 现有 action dispatch 机制自动路由新 action |

### 需要删除的代码

| 位置 | 内容 | 理由 |
|------|------|------|
| 无 | — | 本次 Feature 不删除任何现有代码，只做扩展 |

---

## 4. 修正后的架构图

```
                        Frontend (McpProviderCenter.tsx)
                        ├── "安装" → McpInstallWizard ─────────┐
                        ├── "新建" → McpProviderModal (现有)    │
                        └── submitAction("mcp_provider.*")     │
                            │                                  │
                            v                                  v
                   ControlPlaneService              McpInstallerService (新建)
                   ├── mcp_provider.save (现有)      ├── install(source, package)
                   ├── mcp_provider.delete (现有)    │   → npm install / pip install
                   ├── mcp_provider.install (新增)   │   → 部署到 ~/.octoagent/mcp-servers/
                   └── mcp_provider.uninstall (新增) │   → 写入 mcp-installs.json
                            │                       │   → 调用 McpRegistryService.save_config()
                            v                       └── uninstall(server_id)
                    McpRegistryService (改进)
                    ├── save_config() / delete_config()  (不变)
                    ├── startup() / refresh()            (不变)
                    ├── _session_pool (新增)              → 持久连接管理
                    │     └── get_session() → 复用连接
                    ├── _discover_server_tools()          (改用持久 session)
                    ├── call_tool()                       (改用持久 session)
                    └── ToolBroker.register()             (不变)
                            │
                            v
                       ToolBroker (完全不动)
                       └── 现有完整治理链路
```

---

## 5. Constitution 合规性检查

| 约束 | 现有系统 | 修正方案 | 状态 |
|------|---------|---------|------|
| Durability First | ✅ 配置持久化到 JSON | ✅ 安装记录也持久化 | 合规 |
| Everything is an Event | ✅ ToolBroker 已有事件链 | ✅ 安装/卸载也生成事件 | 合规 |
| Tools are Contracts | ✅ MCP schema → ToolMeta | ✅ 不变 | 合规 |
| Side-effect Two-Phase | ✅ PolicyCheckpoint 已有 | ✅ 安装需审批（首次） | 合规 |
| Least Privilege | ✅ mount_policy 已有 | ✅ env 隔离 + Vault | 合规 |
| Degrade Gracefully | ⚠️ per-op 故障影响 | ✅ session pool + 重连 | 改善 |
| User-in-Control | ✅ 前端 enable/disable | ✅ 安装审批 + 卸载 | 合规 |
| Observability | ⚠️ 仅 ToolBroker 事件 | ✅ 增加安装/健康事件 | 改善 |

---

## 6. 结论

### 原始方案的三个错误假设

1. **❌ "直接复用 Pydantic AI MCPServer Toolset"** → OctoAgent 不用 Pydantic AI Agent，走 ToolBroker
2. **❌ "淘汰 ToolBroker MCP 路径"** → ToolBroker 是治理核心，不能淘汰
3. **❌ "McpRegistryService 退化为配置管理"** → 它同时负责发现+注册+执行，是 MCP 的脊柱

### 修正后的方案核心思路

**不替换，只扩展。**

- McpRegistryService **保留并改进**（session pool 替代 per-operation）
- ToolBroker 路径 **完全不动**
- 新增 McpInstallerService **专注安装能力**
- 前端新增安装向导，不影响现有编辑功能
- 配置格式向后兼容，新增导入能力

### 变更最小化

- **新建 3 个文件**（installer service + session pool + install wizard）
- **修改 6 个文件**（均为向后兼容扩展）
- **不删除任何代码**
- **ToolBroker/SkillRunner/LiteLLMClient 零改动**
