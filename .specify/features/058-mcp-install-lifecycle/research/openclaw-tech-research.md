# 技术调研报告: OpenClaw 插件系统完整实现流程

**调研日期**: 2026-03-16
**调研模式**: 离线（基于源码阅读）
**调研对象**: OpenClaw 开源项目 `_references/opensource/openclaw/`

> [独立模式] 本次技术调研未参考产品调研结论，直接基于需求描述和源码分析执行。

## 1. 调研目标

**核心问题**:
- OpenClaw 如何实现插件（含 MCP server）的安装、配置、发现与生命周期管理？
- OpenClaw 的插件架构有哪些值得 OctoAgent 借鉴的设计决策？
- OpenClaw 与 MCP 协议的关系是什么？它是否原生支持 MCP server？

**关键发现前置**:
OpenClaw 有一套**自研的完整插件系统**，不直接等同于 MCP server 管理。它通过 ACP（Agent Client Protocol）而非 MCP 与外部 Agent 通信。插件系统是 OpenClaw 自己定义的 `OpenClawPlugin` 规范，包含 tool/hook/channel/provider/service/command 等多种注册能力。以下分析基于 OpenClaw 插件系统的完整源码。

---

## 2. 架构概述

### 2.1 整体架构

```
用户操作（CLI/Web UI/Wizard）
        |
        v
  ┌─────────────────────────────┐
  │  安装层 (install.ts)         │  npm install / archive / dir / file
  │  → 写入 extensions/ 目录     │
  │  → 更新 config.plugins.installs │
  └─────────────┬───────────────┘
                v
  ┌─────────────────────────────┐
  │  发现层 (discovery.ts)       │  扫描 bundled + global + workspace + config paths
  │  → 生成 PluginCandidate[]   │  安全检查（ownership/symlink/world-writable）
  └─────────────┬───────────────┘
                v
  ┌─────────────────────────────┐
  │  清单注册 (manifest-registry)│  读取 openclaw.plugin.json
  │  → 提取 id/configSchema/kind │  校验 JSON Schema
  └─────────────┬───────────────┘
                v
  ┌─────────────────────────────┐
  │  加载层 (loader.ts)          │  Jiti 动态加载 TS/JS 模块
  │  → 执行 register(api)       │  注册 tools/hooks/channels/providers/services
  │  → 构建 PluginRegistry      │  enable/disable 状态管理
  └─────────────┬───────────────┘
                v
  ┌─────────────────────────────┐
  │  运行时层 (runtime/)         │  PluginRuntime 提供运行时 API
  │  → tools/hooks/channels 可用 │  Gateway/Agent 按需调用
  └─────────────────────────────┘
```

### 2.2 与 MCP 的关系

OpenClaw **不直接使用 MCP（Model Context Protocol）** 作为其插件系统的通信协议。关键区别：

| 维度 | OpenClaw | MCP 标准 |
|------|----------|----------|
| 插件协议 | 自研 `OpenClawPlugin` API（in-process 注册） | stdio/SSE JSON-RPC 2.0 |
| 外部 Agent 协议 | ACP（Agent Client Protocol） | 无（MCP 是 tool 层） |
| 工具注册 | `api.registerTool(factory)` | `tools/list` RPC 自动发现 |
| 进程模型 | 插件作为 JS 模块加载到同一进程 | 独立进程（server/client 模型） |
| 配置中的 MCP 引用 | `newSession({ mcpServers: [] })` -- 目前忽略不处理 | 核心配置对象 |

OpenClaw 的 ACP translator 中明确忽略了 MCP servers：
```typescript
// translator.ts, line 146-148
if (params.mcpServers.length > 0) {
  this.log(`ignoring ${params.mcpServers.length} MCP servers`);
}
```

OpenClaw 的 `memory.qmd.mcporter` 是一个 MCP-to-QMD 桥接器（McPorter），用于将 QMD 记忆系统暴露为 MCP server 供外部 Agent 调用，但这是一个**输出适配器**而非 MCP server 管理系统。

---

## 3. 安装流程

### 3.1 安装来源（5 种）

OpenClaw 支持多种安装来源，全部收口到 `install.ts`：

| 来源 | 函数 | 说明 |
|------|------|------|
| npm registry | `installPluginFromNpmSpec()` | `npm pack` 下载 → 解压 → 安装依赖 |
| 本地 archive | `installPluginFromArchive()` | `.tgz/.tar.gz/.zip` 文件 |
| 本地目录 | `installPluginFromDir()` | 直接复制目录 |
| 单文件 | `installPluginFromFile()` | `.ts/.js` 文件复制 |
| 路径分派 | `installPluginFromPath()` | 自动检测（文件/目录/archive） |

### 3.2 安装流程（以 npm 为例）

```
1. 验证 npm spec 格式 (validateRegistryNpmSpec)
2. npm pack 下载 tarball
3. 解压到临时目录
4. 读取 package.json
5. 检查 openclaw.extensions 字段（入口文件列表）
6. 读取 openclaw.plugin.json（Manifest，含 id + configSchema）
7. 安全扫描 (skillScanner.scanDirectoryWithSummary)
   - 检测危险代码模式（warn-only，不阻止安装）
8. 解析目标安装路径: ~/.openclaw/extensions/<pluginId>/
9. 复制文件到目标目录
10. npm install 安装依赖（如有 dependencies）
11. 返回 InstallPluginResult { ok, pluginId, targetDir, extensions }
```

### 3.3 安装目标位置

```
~/.openclaw/
  extensions/         # 全局插件安装目录
    <plugin-id>/      # 每个插件一个目录
      package.json
      openclaw.plugin.json   # Manifest
      dist/
        index.js       # 入口文件
      node_modules/    # 依赖
```

### 3.4 安装后配置更新

安装完成后，`installs.ts` 的 `recordPluginInstall()` 将安装记录写入配置：

```json
{
  "plugins": {
    "installs": {
      "my-plugin": {
        "source": "npm",
        "installPath": "~/.openclaw/extensions/my-plugin",
        "resolvedName": "@scope/my-plugin",
        "resolvedVersion": "1.2.3",
        "integrity": "sha512-...",
        "installedAt": "2024-01-01T00:00:00.000Z"
      }
    }
  }
}
```

---

## 4. 配置管理

### 4.1 配置文件

- **路径**: `~/.openclaw/openclaw.json`（可通过 `OPENCLAW_CONFIG_PATH` 覆盖）
- **格式**: JSON
- **Schema 验证**: Zod schema (`zod-schema.ts`)，启动时严格验证

### 4.2 插件配置结构

```typescript
// 完整配置结构 (zod-schema.ts)
{
  plugins: {
    enabled: boolean,              // 全局开关
    allow: string[],               // 白名单（只允许这些插件加载）
    deny: string[],                // 黑名单
    load: {
      paths: string[]              // 额外加载路径
    },
    slots: {
      memory: string               // 记忆插件选择（只能一个）
    },
    entries: {                     // 每个插件的独立配置
      [pluginId]: {
        enabled: boolean,
        hooks: {
          allowPromptInjection: boolean
        },
        config: Record<string, unknown>  // 插件自定义配置
      }
    },
    installs: {                    // 安装记录（机器维护）
      [pluginId]: {
        source: "npm" | "path" | "archive",
        installPath: string,
        sourcePath: string,
        resolvedName: string,
        resolvedVersion: string,
        integrity: string,
        installedAt: string
      }
    }
  }
}
```

### 4.3 Enable/Disable 机制

`config-state.ts` 中的 `resolveEffectiveEnableState()` 实现了多层优先级：

```
1. plugins.enabled === false → 全局禁用
2. plugins.deny 包含 pluginId → 黑名单禁用
3. plugins.entries[pluginId].enabled === false → 个体禁用
4. plugins.allow 非空但不包含 pluginId → 白名单过滤
5. bundled 插件有默认启用列表 (BUNDLED_ENABLED_BY_DEFAULT)
```

---

## 5. 插件发现与注册

### 5.1 发现顺序（discovery.ts）

```
1. 配置路径 (plugins.load.paths) → origin: "config"
2. 工作区目录 (.openclaw/extensions/) → origin: "workspace"
3. 内置插件目录 (bundled/) → origin: "bundled"
4. 全局插件目录 (~/.openclaw/extensions/) → origin: "global"
```

每个目录的扫描逻辑：
- 读取子目录/文件
- 解析 `package.json` 中的 `openclaw.extensions` 字段确定入口
- 回退到 `index.{ts,js,mjs,cjs}`
- 安全检查：symlink 逃逸检测、world-writable 检测、ownership 检测

### 5.2 Manifest 结构（openclaw.plugin.json）

```typescript
type PluginManifest = {
  id: string;                              // 插件唯一标识
  configSchema: Record<string, unknown>;   // JSON Schema
  kind?: "memory";                         // 插件类型（目前只有 memory）
  channels?: string[];                     // 声明的 channel
  providers?: string[];                    // 声明的 provider
  skills?: string[];                       // 声明的 skill
  name?: string;
  description?: string;
  version?: string;
  uiHints?: Record<string, PluginConfigUiHint>;  // UI 表单提示
};
```

### 5.3 模块加载（loader.ts）

使用 **Jiti**（TypeScript/ESM 即时加载器）加载插件：

```typescript
// 创建 Jiti 加载器，支持 .ts/.js/.mjs/.cjs 等多种格式
const jitiLoader = createJiti(import.meta.url, {
  interopDefault: true,
  extensions: [".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs", ".json"],
  alias: { "openclaw/plugin-sdk": pluginSdkAlias },
});

// 加载插件模块
const mod = jitiLoader(safeSource);

// 调用 register/activate
register(api);
```

### 5.4 注册 API（OpenClawPluginApi）

插件通过 `register(api)` 获得注册 API，可以注册：

| 方法 | 说明 |
|------|------|
| `api.registerTool(tool, opts)` | 注册 Agent 可调用的工具 |
| `api.registerHook(events, handler)` | 注册生命周期 hook |
| `api.registerChannel(plugin)` | 注册消息通道（Telegram/Discord 等） |
| `api.registerProvider(provider)` | 注册 LLM 提供商 |
| `api.registerGatewayMethod(method, handler)` | 注册 Gateway RPC 方法 |
| `api.registerHttpRoute(params)` | 注册 HTTP 路由 |
| `api.registerCli(registrar)` | 注册 CLI 子命令 |
| `api.registerService(service)` | 注册后台服务 |
| `api.registerCommand(command)` | 注册聊天命令（`/xxx`） |
| `api.on(hookName, handler)` | 注册类型化 hook |

### 5.5 工具发现与解析（tools.ts）

工具在 Agent 运行时按需解析：

```typescript
// resolvePluginTools() -- 每次 Agent 运行时调用
1. 检查 plugins.enabled
2. loadOpenClawPlugins() 获取 registry
3. 遍历 registry.tools
4. 调用 entry.factory(context) 实例化工具
5. 名称冲突检测（核心工具优先）
6. 可选工具白名单过滤（toolAllowlist）
7. 返回 AnyAgentTool[]
```

---

## 6. 运行时生命周期

### 6.1 服务启动（services.ts）

```typescript
// Gateway 启动时调用
const handle = await startPluginServices({
  registry,
  config,
  workspaceDir,
});

// 每个 service 按注册顺序启动
for (const entry of registry.services) {
  await service.start(serviceContext);
}

// 关停时逆序停止
await handle.stop();
```

### 6.2 Hook 生命周期（26 种 hook）

OpenClaw 定义了 26 种生命周期 hook，覆盖完整的 Agent 运行周期：

```
启动阶段:    gateway_start
Agent 阶段:  before_model_resolve → before_prompt_build → before_agent_start
LLM 阶段:   llm_input → llm_output
工具阶段:    before_tool_call → after_tool_call → tool_result_persist
消息阶段:    message_received → message_sending → message_sent → before_message_write
会话阶段:    session_start → session_end
压缩阶段:    before_compaction → after_compaction → before_reset
子代理:      subagent_spawning → subagent_spawned → subagent_ended → subagent_delivery_target
关停阶段:    gateway_stop
```

### 6.3 状态监控（status.ts）

`buildPluginStatusReport()` 生成完整的插件状态报告：

```typescript
type PluginRecord = {
  id: string;
  status: "loaded" | "disabled" | "error";
  error?: string;
  toolNames: string[];
  hookNames: string[];
  channelIds: string[];
  providerIds: string[];
  services: string[];
  // ...
};
```

---

## 7. 安全与隔离

### 7.1 安装阶段安全

| 检查 | 实现 |
|------|------|
| 代码扫描 | `skillScanner.scanDirectoryWithSummary()` -- 检测危险模式（warn-only） |
| 路径遍历防护 | `resolveSafeInstallDir()` -- 防止 `../` 逃逸 |
| npm 完整性校验 | integrity (sha512) + shasum 验证 |
| Manifest 验证 | `openclaw.plugin.json` 必须有 `id` 和 `configSchema` |

### 7.2 加载阶段安全

| 检查 | 实现 |
|------|------|
| Symlink 逃逸 | `openBoundaryFileSync()` -- realpathSync 检查 |
| Hardlink 拒绝 | `rejectHardlinks: true`（非 bundled 插件） |
| World-writable 检查 | Unix mode 检查（mode & 0o002） |
| Ownership 验证 | UID 必须匹配当前用户或 root |
| 白名单/黑名单 | `plugins.allow` / `plugins.deny` |

### 7.3 运行时安全

| 检查 | 实现 |
|------|------|
| Prompt 注入控制 | `hooks.allowPromptInjection` 字段 |
| HTTP 路由认证 | `auth: "gateway" | "plugin"` |
| 名称冲突防护 | 核心工具名优先，插件工具名冲突时报错 |
| 可选工具白名单 | `tools.allow` 控制可选工具可见性 |

### 7.4 环境变量管理

OpenClaw 的配置中**没有**原生的插件级环境变量注入机制。相关观察：

- `skills.entries[skillId].apiKey` 使用 `SecretInputSchema`（支持 `{ env: "VAR_NAME" }` 或直接值）
- `skills.entries[skillId].env` 支持 `Record<string, string>` 环境变量映射
- `plugins.entries[pluginId].config` 只支持通用 `Record<string, unknown>` 配置
- Sensitive 字段通过 Zod 的 `.register(sensitive)` 标记，配置持久化时做特殊处理

---

## 8. Web UI 管理

### 8.1 UI 状态

基于源码分析，OpenClaw Web UI 中**没有独立的插件管理页面**。相关 UI 能力分布在：

- **Tools & Skills 面板** (`views/agents-panels-tools-skills.ts`): 展示 Agent 可用的工具列表（含插件工具），支持工具 profile 切换和 allow/deny 配置
- **Config 表单** (`views/config-form.render.ts`): 通用配置编辑器，可编辑 `plugins.entries` 等配置字段
- **Agents 控制器** (`controllers/agents.ts`): 管理 Agent 配置，间接涉及插件工具的 enable/disable

### 8.2 主要管理方式

OpenClaw 插件管理主要通过以下方式：

1. **CLI 命令**:
   - `openclaw plugins install <spec>` -- 安装插件
   - `openclaw plugins uninstall <pluginId>` -- 卸载插件
   - `openclaw plugins list` -- 列出插件状态
   - `openclaw security audit --deep` -- 安全审计

2. **配置文件直接编辑**:
   - 编辑 `~/.openclaw/openclaw.json` 中的 `plugins` 字段

3. **Wizard 引导**:
   - 初始设置向导中的 channel/provider 选择（间接安装插件）

---

## 9. 卸载流程

`uninstall.ts` 实现了完整的卸载逻辑：

```
1. 检查 pluginId 存在于 entries 或 installs
2. removePluginFromConfig():
   - 移除 plugins.entries[pluginId]
   - 移除 plugins.installs[pluginId]
   - 从 plugins.allow 移除
   - 从 plugins.load.paths 移除（source=path 类型）
   - 重置 plugins.slots.memory（如果是当前 memory 插件）
3. 删除安装目录（source=path 类型不删除源目录）
4. 返回 UninstallPluginResult { config, actions, warnings }
```

---

## 10. 与 OctoAgent 的对比与借鉴

### 10.1 架构差异

| 维度 | OpenClaw | OctoAgent 现状 | 差异分析 |
|------|----------|---------------|---------|
| 语言 | TypeScript (Node.js) | Python 3.12+ | 生态差异大 |
| 插件模型 | In-process JS 模块 | [推断] 尚无插件系统 | OpenClaw 无进程隔离 |
| 配置存储 | JSON 文件 | SQLite WAL | OctoAgent 更适合复杂状态管理 |
| 工具注册 | 插件 API 注册 | Pydantic Skill + Tool Contract | OctoAgent 已有强类型基础 |
| 外部 Agent | ACP 协议 | A2A-Lite | 方向类似 |
| MCP 支持 | 明确不支持 MCP server 管理 | 仅有配置管理 | 两者都未实现完整 MCP |

### 10.2 可借鉴设计

#### A. 多来源安装机制

OpenClaw 的 5 种安装来源设计（npm/archive/dir/file/path）值得参考。OctoAgent 可以实现：

```python
# 建议的 OctoAgent MCP 安装来源
class InstallSource(str, Enum):
    NPM = "npm"          # npx / npm 全局安装
    PIP = "pip"          # pip install
    DOCKER = "docker"    # Docker 镜像
    PATH = "path"        # 本地路径（开发模式）
    ARCHIVE = "archive"  # 下载的归档文件
```

#### B. 分层安全检查

OpenClaw 的安全检查分为安装、加载、运行时三个阶段，每阶段有不同的安全关注点：

- **安装**: 代码扫描 + 完整性校验
- **加载**: 路径安全 + 权限检查
- **运行时**: 工具白名单 + hook 注入控制

这与 OctoAgent Constitution 的 "Least Privilege by Default" 和 "Side-effect Must be Two-Phase" 原则高度一致。

#### C. 声明式 Manifest

`openclaw.plugin.json` 的声明式清单设计值得借鉴：

```json
{
  "id": "my-mcp-server",
  "configSchema": { /* JSON Schema */ },
  "kind": "tool",
  "uiHints": {
    "apiKey": { "label": "API Key", "sensitive": true }
  }
}
```

OctoAgent 可用 Pydantic 模型实现等价能力，且更强（运行时验证 + IDE 支持）。

#### D. 配置-安装分离

OpenClaw 将 `entries`（用户配置）和 `installs`（机器维护的安装记录）分开存储，避免了配置污染。

#### E. 插件发现优先级

`config paths > workspace > bundled > global` 的优先级设计，让用户可以用 config 覆盖 bundled 行为，同时保持默认安全。

### 10.3 不建议借鉴的设计

#### A. In-process 加载模型

OpenClaw 将所有插件加载到同一 Node.js 进程，没有进程隔离。这与 OctoAgent 的 Constitution 第 6 条 "Degrade Gracefully" 冲突——一个插件崩溃可能影响整个系统。

OctoAgent 应采用 **进程隔离模型**（与 MCP 标准的 stdio/SSE 模型一致），通过独立进程运行 MCP server。

#### B. 忽略 MCP 标准

OpenClaw 选择自研插件协议而非采用 MCP，导致其插件生态与 Claude/Cursor 等主流 AI 工具的 MCP 生态不兼容。

OctoAgent 应直接支持 MCP 标准协议，确保与现有 MCP server 生态兼容。

#### C. 无 Web UI 管理

OpenClaw 的插件管理主要靠 CLI 和手动编辑配置文件，缺少可视化管理界面。OctoAgent 应在 Web UI 中提供 MCP server 的安装/配置/监控能力。

---

## 11. 对 OctoAgent MCP 实现的建议

### 11.1 建议架构

```
OctoAgent MCP 管理架构:

┌─────────────────────────────────────┐
│  Web UI / Telegram 命令             │  用户操作入口
└─────────────┬───────────────────────┘
              v
┌─────────────────────────────────────┐
│  MCP Manager (packages/tooling/)    │  安装 + 配置 + 生命周期
│  ├── installer.py                   │  多来源安装（npm/pip/docker/path）
│  ├── config_store.py                │  配置持久化（SQLite）
│  ├── process_manager.py             │  进程启停 + 健康检查
│  └── tool_registry.py              │  工具发现 + schema 注册
└─────────────┬───────────────────────┘
              v
┌─────────────────────────────────────┐
│  MCP Server 进程池                   │  独立进程运行
│  ├── server-a (stdio)               │  MCP JSON-RPC 通信
│  ├── server-b (SSE)                 │
│  └── server-c (Docker)              │  隔离运行
└─────────────────────────────────────┘
```

### 11.2 关键设计决策建议

| 决策 | 建议 | 理由 |
|------|------|------|
| 进程模型 | 独立进程 + stdio/SSE | 与 MCP 标准对齐，故障隔离 |
| 配置存储 | SQLite（复用现有 Event Store） | 与 Constitution #1 Durability First 一致 |
| 安装机制 | npm/pip/docker/path 多来源 | 覆盖 MCP 生态主流安装方式 |
| 安全模型 | env 加密存储 + 进程沙箱 | Constitution #5 Least Privilege |
| 健康检查 | heartbeat + tools/list 定期探测 | Constitution #8 Observability |
| 工具注册 | tools/list 自动发现 + schema 缓存 | Constitution #3 Tools are Contracts |
| 审批 | 首次安装需审批，高危工具需门禁 | Constitution #7 User-in-Control |

### 11.3 从 OpenClaw 借鉴的实现清单

- [x] 多来源安装（参考 `install.ts` 的 5 种来源）
- [x] 声明式 Manifest 配置（参考 `openclaw.plugin.json`）
- [x] 分层安全检查（安装/加载/运行时）
- [x] 配置与安装记录分离
- [x] 发现优先级机制
- [x] enable/disable 多层优先级
- [x] 工具名称冲突检测
- [ ] ~~In-process 加载~~（不采用，使用进程隔离）
- [ ] ~~自研协议~~（不采用，使用 MCP 标准）

---

## 12. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| 1 | MCP server 进程管理复杂度（启动/重启/crash recovery） | 高 | 高 | 借鉴 OpenClaw 的 service lifecycle 模式，增加 watchdog |
| 2 | npm/pip 安装的安全性（供应链攻击） | 中 | 高 | 安装时代码扫描 + integrity 校验（参考 OpenClaw） |
| 3 | 环境变量泄露（API key 等） | 中 | 高 | 加密存储 + 不进 LLM 上下文（Constitution #5） |
| 4 | 工具 schema 版本不兼容 | 中 | 中 | 缓存 + 版本号追踪 + 启动时重新发现 |
| 5 | 多 MCP server 资源消耗 | 低 | 中 | 按需启动 + 空闲超时关闭 |

---

## 13. 结论

### 总结

OpenClaw 拥有一套成熟的、完全自研的插件系统，涵盖安装、配置、发现、加载、运行时管理和卸载的完整生命周期。然而，它**不直接支持 MCP 标准**，而是使用自研的 in-process 插件 API。

对 OctoAgent 而言，OpenClaw 的架构在以下方面提供了可借鉴的成熟模式：
1. **多来源安装** -- 5 种安装来源的统一抽象
2. **分层安全** -- 安装/加载/运行时三层安全检查
3. **声明式配置** -- Manifest + JSON Schema + UI Hints
4. **发现优先级** -- 多层来源的优先级覆盖
5. **状态管理** -- enable/disable/error 的完整状态机

但 OctoAgent 应避免其 in-process 加载和自研协议的设计，转而采用 MCP 标准协议 + 进程隔离模型，以确保与主流 AI 工具生态兼容、满足 Constitution 的安全和可靠性约束。

### 对后续设计的建议

- MCP Manager 应作为 `packages/tooling/` 的核心模块
- 配置模型应参考 OpenClaw 的 entries/installs 分离设计
- 进程管理应增加 watchdog 和 crash recovery（参考 OpenClaw 的 service lifecycle）
- Web UI 应从一开始就提供可视化管理（OpenClaw 的缺失是一个教训）
- 安全模型应超越 OpenClaw，增加 Docker 沙箱隔离选项
