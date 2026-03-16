# 产研汇总: MCP 安装与生命周期管理

**特性编号**: 058-mcp-install-lifecycle
**特性分支**: `claude/festive-meitner`
**汇总日期**: 2026-03-16
**调研来源**: Agent Zero × OpenClaw × Pydantic AI 三方交叉分析

---

## 1. 调研概览

| 项目 | 核心发现 | 借鉴等级 |
|------|---------|---------|
| **Agent Zero** | "声明即启动"模式，JSON 配置兼容 Claude Desktop，per-operation session，无安装管理 | 中 — 配置格式可复用，架构需改进 |
| **OpenClaw** | 自研插件系统（非 MCP），5 种安装来源，分层安全，entries/installs 分离 | 高 — 安装/安全架构可借鉴 |
| **Pydantic AI** | MCPServer is-a Toolset，持久连接+引用计数，Toolset 组合体系，process_tool_call 钩子 | 最高 — 运行时直接复用 |

## 2. 交叉分析矩阵

### 2.1 安装能力对比

| 维度 | Agent Zero | OpenClaw | Pydantic AI | OctoAgent 建议 |
|------|-----------|----------|-------------|---------------|
| **安装机制** | 无（依赖 npx/uvx 自动下载） | 完整（npm/archive/dir/file/path 5 种来源） | 无（纯运行时框架） | 参考 OpenClaw 5 来源 + npm/pip/docker/git/path |
| **安装位置** | 系统全局缓存（npm cache） | `~/.openclaw/extensions/{id}/` | 无 | `~/.octoagent/mcp-servers/{server-id}/` |
| **依赖隔离** | 无（共享系统依赖） | npm install 每插件独立 node_modules | 无 | 每 server 独立 venv/node_modules |
| **安装记录** | 无 | `installs` 字段（source/version/integrity/date） | 无 | SQLite 安装注册表 |
| **完整性校验** | 无 | sha512 integrity + shasum | 无 | sha256 + 安装日志 |
| **卸载** | 无（手动删除配置条目） | 完整（config 清理 + 文件删除） | 无 | 完整卸载（配置+文件+依赖） |

### 2.2 运行时生命周期对比

| 维度 | Agent Zero | OpenClaw | Pydantic AI | OctoAgent 建议 |
|------|-----------|----------|-------------|---------------|
| **连接模型** | Per-operation（每次新建 session） | In-process（同进程加载） | 持久连接+引用计数 | 持久连接（复用 Pydantic AI MCPServer） |
| **进程隔离** | 无（共享容器） | 无（同进程） | stdio 子进程 | stdio 子进程 + 可选 Docker |
| **健康检查** | 无（仅 status 轮询） | 无 | 无（依赖连接状态） | 主动 heartbeat + tools/list 探测 |
| **自动重连** | 无 | 无 | 自动（async with self:） | 指数退避重连 + 事件通知 |
| **工具缓存** | 内存缓存 | 无 | 缓存+通知失效 | 复用 Pydantic AI 缓存机制 |
| **热更新** | 全量重建 | 不支持 | 引用计数保护 | 增量更新（仅重建变更 server） |

### 2.3 配置模型对比

| 维度 | Agent Zero | OpenClaw | Pydantic AI | OctoAgent 建议 |
|------|-----------|----------|-------------|---------------|
| **格式** | JSON 字符串（mcpServers 对象/数组） | JSON 文件（Zod 验证） | JSON 文件（Pydantic 验证） | Pydantic 模型 + SQLite 持久化 |
| **entries/installs 分离** | 无 | 是 | 无 | 是（参考 OpenClaw） |
| **enable/disable** | disabled 字段 | 多层优先级（全局/白名单/黑名单/个体） | 无内建 | 多层优先级 |
| **env 管理** | 明文 JSON | SecretInputSchema | 隔离+展开语法 | Vault 加密存储 + env 隔离 |
| **配置兼容** | Claude Desktop 格式 | 自研格式 | Claude Desktop 格式 | Claude Desktop 格式（mcpServers dict） |

### 2.4 安全模型对比

| 维度 | Agent Zero | OpenClaw | Pydantic AI | OctoAgent 建议 |
|------|-----------|----------|-------------|---------------|
| **API Key 存储** | 明文 | SecretInputSchema | env 展开（不存储） | Vault 加密存储 |
| **代码扫描** | 无 | skillScanner（warn-only） | 无 | 安装时扫描 + 策略阻断 |
| **路径安全** | 无 | symlink/hardlink/ownership 检查 | 无 | 参考 OpenClaw 路径安全 |
| **进程沙箱** | Docker 容器共享 | 无 | 无 | 可选 Docker 沙箱 |
| **工具权限** | 无限制 | 工具白名单 | FilteredToolset | Policy Engine 审批 |
| **prompt 注入** | 无防护 | allowPromptInjection 控制 | 无内建 | 工具输出净化 |

## 3. 可行性评估

### 3.1 技术可行性: 高

**有利因素**:
- Pydantic AI 已提供完整的 MCP 运行时支持（MCPServer + Toolset 组合），无需自建连接层
- Agent Zero 的配置格式（mcpServers）已成为事实标准，可直接复用
- OpenClaw 的安装架构提供了成熟的参考模式
- OctoAgent 现有 McpRegistryService 可渐进演化

**风险因素**:
- npm/pip 安装的供应链安全需要投入
- 多 server 进程管理增加系统复杂度
- 配置格式迁移（现有 servers list → mcpServers dict）需要向后兼容

### 3.2 架构可行性: 高

MCP Installer 可清晰分层，各层职责明确：

```
┌─────────────────────────────────────────────────┐
│ Web UI / CLI / Telegram                          │ 用户入口
├─────────────────────────────────────────────────┤
│ McpInstallerService                              │ 安装/卸载/更新
│   ├── NpmStrategy / PipStrategy / DockerStrategy │
│   ├── integrity 校验 + 代码扫描                   │
│   └── 安装注册表（SQLite）                        │
├─────────────────────────────────────────────────┤
│ McpServerPool                                    │ 生命周期管理
│   ├── MCPServer 实例池（Pydantic AI 原生）         │
│   ├── 健康检查 + 自动重连                         │
│   └── 增量热更新                                 │
├─────────────────────────────────────────────────┤
│ Pydantic AI Agent(toolsets=[...mcp_servers])     │ 工具注入
│   ├── process_tool_call → Event + Policy + Cost  │
│   └── Toolset 组合（Filter/Prefix/Approval）      │
└─────────────────────────────────────────────────┘
```

## 4. 风险矩阵

| # | 风险 | 概率 | 影响 | 来源 | 缓解策略 |
|---|------|------|------|------|---------|
| 1 | stdio 子进程异常退出 | 高 | 高 | Agent Zero/Pydantic AI 共同暴露 | Pydantic AI 自动重建 + watchdog 监控 |
| 2 | npm/pip 供应链攻击 | 中 | 高 | OpenClaw 经验 | integrity 校验 + 代码扫描 + 首次安装审批 |
| 3 | API Key 泄露 | 中 | 高 | Agent Zero 明文存储教训 | Vault 加密 + env 隔离（Pydantic AI 默认） |
| 4 | 配置格式迁移兼容性 | 中 | 中 | 三方格式差异 | 双向适配层 + 渐进迁移 |
| 5 | 多 server 资源消耗 | 低 | 中 | Agent Zero 全量重建问题 | 按需启动 + 空闲超时 + 增量更新 |
| 6 | MCP SDK API 演进 | 中 | 中 | 协议标准仍在发展 | Adapter 层隔离 SDK 变化 |

## 5. 推荐方案

### 5.1 核心架构决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 运行时框架 | 直接复用 Pydantic AI MCPServer | 已选型框架，Toolset 原生集成，避免重复建设 |
| 安装来源 | npm + pip + docker + git + path（5 种） | 覆盖 MCP 生态主流安装方式（参考 OpenClaw） |
| 安装位置 | `~/.octoagent/mcp-servers/{server-id}/` | 独立目录隔离依赖 |
| 配置格式 | mcpServers dict（Claude Desktop 兼容） | 行业标准（Agent Zero + Pydantic AI 共同验证） |
| 配置存储 | entries（用户配置）+ installs（安装记录）分离 | 参考 OpenClaw，避免配置污染 |
| 安全模型 | Vault 加密 + env 隔离 + 可选 Docker 沙箱 | 满足 Constitution #5 Least Privilege |
| 连接管理 | 持久连接 + 引用计数 | Pydantic AI 原生支持，性能最优 |
| 工具治理 | process_tool_call + Toolset 组合 | 注入 Event/Policy/Cost，满足 Constitution #2/#7 |

### 5.2 MVP 范围建议

**P0（必须实现）**:
- McpInstallerService：npm + pip 两种安装来源
- 安装注册表（SQLite）：server-id / source / version / install_path / status
- McpServerPool：持久连接管理（复用 Pydantic AI MCPServer）
- 配置格式迁移：现有 McpServerConfig → mcpServers dict
- Web UI 安装向导：包名输入 → 一键安装 → 配置 env → 启用

**P1（应该实现）**:
- Docker 安装来源
- Vault 集成（API Key 加密存储）
- 健康检查 + 自动重连
- process_tool_call 事件记录
- 卸载/更新能力

**P2（可以推迟）**:
- git clone 安装来源
- 代码扫描安全检查
- 工具权限细粒度控制
- MCP server 版本管理

### 5.3 与现有系统的集成点

| 现有模块 | 集成方式 |
|---------|---------|
| McpRegistryService | 保留配置管理，安装能力委托给 McpInstallerService |
| McpProviderCenter.tsx | 扩展：添加"安装来源"选择、安装进度展示 |
| ToolBroker | 渐进淘汰 MCP 路径，Worker 直接使用 MCPServer Toolset |
| Event Store | process_tool_call 注入工具调用事件 |
| Policy Engine | ApprovalRequiredToolset 对接高危工具审批 |
| Vault/Secrets | API Key 加密存储，env 安全注入 |

## 6. 结论

三方调研形成了完整的技术视角：

- **Agent Zero** 提供了配置格式标准和反面教训（per-operation session、无安装管理、明文存储）
- **OpenClaw** 提供了安装架构蓝图（多来源、分层安全、entries/installs 分离）
- **Pydantic AI** 提供了运行时最优解（MCPServer Toolset、持久连接、组合治理）

OctoAgent 的 MCP Installer 应当是三者优势的交叉融合：
**OpenClaw 的安装体系 + Agent Zero 的配置标准 + Pydantic AI 的运行时框架**

这一架构完全满足 OctoAgent Constitution 的八条硬约束，且与已选型技术栈（Pydantic AI、FastAPI、SQLite）无缝集成。
