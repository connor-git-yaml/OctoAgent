# OpenClaw 技术架构深度分析

> 基于 OpenClaw 源码（2025-03 快照）的完整架构逆向分析。
> 源码位置：`_references/opensource/openclaw/`

---

## 1. 系统概览

### 1.1 技术栈

| 层级 | 技术选型 |
|------|----------|
| 语言 | TypeScript (Node.js 22+) |
| 包管理 | pnpm workspace (monorepo) |
| LLM 集成 | `@mariozechner/pi-agent-core` + `@mariozechner/pi-ai`（Pi Coding Agent） |
| 渠道 | Telegram / Discord / Slack / Signal / iMessage / WhatsApp / Web / Matrix / MS Teams |
| MCP 集成 | `@modelcontextprotocol/sdk`（StdioClientTransport） |
| 数据库 | SQLite（向量检索：sqlite-vec；FTS5 全文） |
| 向量嵌入 | OpenAI / Gemini / Voyage / Mistral / Ollama / 本地 ONNX |
| 执行隔离 | Docker sandbox（可选） |
| 调度 | 内置 Cron Service（非 APScheduler） |
| 移动端 | iOS / Android / macOS 原生 app |
| 构建 | tsdown (tsup fork) + vitest |

### 1.2 整体架构图

```
┌─────────────────────────────────────────────────────────┐
│                     User Channels                        │
│  Telegram │ Discord │ Slack │ Signal │ Web │ Mobile App  │
└────────┬────────────┬───────────────┬───────────────────┘
         │            │               │
         ▼            ▼               ▼
┌─────────────────────────────────────────────────────────┐
│              Channel Plugins (src/channels/)             │
│  registry.ts → session.ts → run-state-machine.ts        │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│           Gateway Server (src/gateway/server.impl.ts)    │
│  ┌─────────────────────────────────────────────────┐    │
│  │  HTTP + WebSocket Server (express-ws)            │    │
│  │  ├─ REST API  (server-methods.ts)               │    │
│  │  ├─ WS handlers (server-ws-runtime.ts)          │    │
│  │  ├─ Chat flow  (server-chat.ts)                 │    │
│  │  ├─ Agent event bus (server-node-events.ts)     │    │
│  │  └─ Control UI (control-ui.ts)                  │    │
│  └─────────────────────────────────────────────────┘    │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│          Auto-Reply / Agent Runner                       │
│  src/auto-reply/reply/agent-runner-execution.ts          │
│  → resolves session → builds prompt → calls Pi Agent    │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│      Pi Embedded Runner (src/agents/pi-embedded-runner/) │
│  ┌──────────────────────────────────────────────────┐   │
│  │ run.ts  → attempt.ts  → pi-agent-core loop       │   │
│  │    ├─ system-prompt.ts  (prompt 组装)             │   │
│  │    ├─ tool-split.ts     (工具注册)                │   │
│  │    ├─ compaction.ts     (上下文压缩)              │   │
│  │    └─ subscribe.ts      (流式回调)                │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────────┘
                       │
         ┌─────────────┼──────────────┐
         ▼             ▼              ▼
┌──────────────┐ ┌──────────┐ ┌──────────────┐
│   LLM APIs   │ │  Tools   │ │   Memory     │
│ Anthropic    │ │ read     │ │ MEMORY.md    │
│ OpenAI       │ │ write    │ │ memory/*.md  │
│ Gemini       │ │ edit     │ │ sqlite-vec   │
│ Ollama       │ │ exec     │ │ hybrid search│
│ ...          │ │ web_*    │ │ embedding    │
│              │ │ MCP      │ │              │
└──────────────┘ │ browser  │ └──────────────┘
                 │ cron     │
                 │ message  │
                 │ subagents│
                 └──────────┘
```

### 1.3 核心模块关系

- **entry.ts / index.ts**：CLI 入口，路由到 `cli/run-main.ts`
- **gateway/**：长驻 daemon 进程，管理所有 channel 连接和 agent session
- **agents/**：Agent 运行时核心——prompt 构建、工具注册、LLM 调用、compaction
- **channels/**：渠道抽象层，处理消息收发、session 绑定、typing 状态
- **auto-reply/**：消息触发逻辑，决定是否回复、如何路由到 Agent
- **memory/**：向量记忆系统，SQLite + embedding provider
- **cron/**：定时任务 + heartbeat 系统
- **plugins/**：插件加载器、hook runner、provider 运行时

---

## 2. 用户消息完整执行路径

以 Telegram 用户发一条消息为例，追踪完整执行路径：

### Step 1: Channel 接收消息

```
Telegram Bot API → aiogram/polling
  → extensions/telegram/src/channel.ts（或 src/channels/plugins/）
  → 触发 inbound message event
```

### Step 2: Channel Session 绑定

```
src/channels/session.ts :: recordInboundSession()
  → 根据 sender + chat 计算 sessionKey
  → 写入 session store (JSON 文件)
  → 更新 lastRoute（用于回复路由）
```

文件：`src/channels/session.ts`

### Step 3: Auto-Reply 触发判定

```
src/auto-reply/reply/agent-runner-execution.ts
  → 检查 allowlist/mention/command gating
  → 判断是否需要 Agent 回复（非 command、非静默）
  → 构建 reply context
```

关键文件：
- `src/channels/allowlist-match.ts` — 发送者白名单匹配
- `src/channels/mention-gating.ts` — @提及门控
- `src/channels/command-gating.ts` — 命令门控

### Step 4: Agent Runner 准备

```
src/auto-reply/reply/agent-runner-payloads.ts
  → 解析 sessionKey → 确定 agentId
  → 加载 config + workspace
  → 准备 run payload
```

### Step 5: Pi Embedded Runner 启动

```
src/agents/pi-embedded-runner/run.ts :: runEmbeddedPiAgent()
  → 获取 API key（auth profile 轮转）
  → 解析 context window 大小
  → 准备 system prompt + tools + session history
  → 进入重试循环（failover + profile rotation）
```

关键函数：`runEmbeddedPiAgent()` 在 `src/agents/pi-embedded-runner/run.ts`

### Step 6: 单次 Attempt 执行

```
src/agents/pi-embedded-runner/run/attempt.ts :: runEmbeddedAttempt()
  → 加载 bootstrap files (SOUL.md, AGENTS.md, TOOLS.md, USER.md, etc.)
  → buildEmbeddedSystemPrompt() → 组装完整 system prompt
  → createOpenClawCodingTools() → 注册所有工具
  → 创建 SessionManager（transcript 持久化）
  → 调用 pi-agent-core 的 streamSimple()
```

### Step 7: LLM 调用与工具循环

```
@mariozechner/pi-ai :: streamSimple()
  → 发送 messages + tools 到 LLM API
  → 流式接收 response
  → 如果 response 包含 tool_use：
      → 执行工具 → 将 result 追加到 messages
      → 再次调用 LLM（循环直到 stop_reason != tool_use）
  → 如果触发 context overflow：
      → compaction.ts :: generateSummary() 压缩历史
      → 重新调用 LLM
```

### Step 8: 流式响应回调

```
src/agents/pi-embedded-subscribe.ts :: subscribeEmbeddedPiSession()
  → 拦截 assistant text delta → 分块推送到 channel
  → 拦截 tool_use events → 格式化工具摘要
  → 拦截 reasoning blocks → 按配置转发或隐藏
  → 处理 messaging tool 去重（避免重复发送）
```

### Step 9: 回复投递

```
Channel reply dispatcher
  → 根据 lastRoute 确定目标 channel + chat
  → 格式化 markdown → 发送到对应渠道
  → 处理 silent reply (NO_REPLY token)
  → 处理 heartbeat ACK
```

---

## 3. 编排层详解

### 3.1 消息路由

OpenClaw 的消息路由基于 **sessionKey** 机制：

```typescript
// src/routing/session-key.ts
// sessionKey 格式示例：
// "telegram:dm:12345"       — Telegram 私聊
// "discord:guild:111:222"   — Discord 服务器频道
// "sub:agent-research:xxx"  — 子 Agent session
// "cron:heartbeat:main"     — Cron heartbeat session
```

路由流程：
1. Channel plugin 将原始消息转化为 `NormalizedMessage`
2. `session.ts` 根据 sender + chat 计算 `sessionKey`
3. Gateway 将消息投递到对应 session 的 Agent runner
4. Agent 回复通过 `lastRoute` 回溯原始渠道

文件：`src/routing/session-key.ts`、`src/channels/session.ts`

### 3.2 Agent 主循环

OpenClaw 的 Agent 主循环**不是自己实现的**，而是委托给 `@mariozechner/pi-agent-core`（Pi Coding Agent SDK）：

```typescript
// src/agents/pi-embedded-runner/run/attempt.ts
import { createAgentSession, SessionManager } from "@mariozechner/pi-coding-agent";
import { streamSimple } from "@mariozechner/pi-ai";

// Pi Agent Core 的 streamSimple 实现了标准的工具调用循环：
// 1. 发送 system + history + user message 到 LLM
// 2. 流式接收 response
// 3. 如果 stop_reason == "tool_use" → 执行工具 → 追加 tool_result → goto 1
// 4. 如果 stop_reason == "end_turn" → 结束
```

OpenClaw 在这个循环之上增加了：
- **Auth profile 轮转**：多个 API key 自动切换（`model-auth.ts`、`auth-profiles.ts`）
- **Failover**：遇到 rate limit / billing error / context overflow 自动切换 provider（`failover-error.ts`）
- **Compaction**：context 超限时自动压缩历史（`compaction.ts`）
- **Tool result 截断**：过大的工具输出自动截断（`tool-result-truncation.ts`）
- **Session 写锁**：防止并发写入同一 session 的 transcript（`session-write-lock.ts`）

重试循环上限：

```typescript
// src/agents/pi-embedded-runner/run.ts
const BASE_RUN_RETRY_ITERATIONS = 24;
const RUN_RETRY_ITERATIONS_PER_PROFILE = 8;
const MIN_RUN_RETRY_ITERATIONS = 32;
const MAX_RUN_RETRY_ITERATIONS = 160;
```

### 3.3 多 Agent 和 Subordinate 机制

OpenClaw 支持两种多 Agent 模式：

**1. Sub-agent（sessions_spawn 工具）**

```typescript
// src/agents/subagent-spawn.ts
export type SpawnSubagentParams = {
  task: string;           // 子任务描述
  label?: string;         // 显示名称
  agentId?: string;       // 使用哪个 agent profile
  model?: string;         // 模型覆盖
  mode?: "run" | "session"; // run=一次性, session=持久
  thread?: boolean;       // 是否绑定到 channel thread
  sandbox?: "inherit" | "require";
  attachments?: Array<{name: string; content: string}>;
};
```

子 Agent 的生命周期：
- 主 Agent 通过 `sessions_spawn` 工具创建
- 子 Agent 在独立 session 中运行
- 完成后通过 **auto-announce** 机制推送结果回主 Agent
- 主 Agent **不需要轮询**，结果是推送模式

关键文件：
- `src/agents/subagent-spawn.ts` — 创建逻辑
- `src/agents/subagent-registry.ts` — 注册和状态管理
- `src/agents/subagent-announce.ts` — 完成通知
- `src/agents/subagent-depth.ts` — 深度限制

**2. ACP (Agent Communication Protocol)**

```typescript
// ACP 模式通过 sessions_spawn 的 runtime="acp" 参数触发
// 支持外部 coding agent harness（如 Claude Code / Codex）
```

---

## 4. Context 管理详解

### 4.1 System Prompt 组装

System prompt 由 `buildAgentSystemPrompt()` 函数组装，位于 `src/agents/system-prompt.ts`。

组装顺序（从上到下）：

```
1. Identity line      — "You are a personal assistant running inside OpenClaw."
2. ## Tooling          — 可用工具列表 + 工具摘要
3. ## Tool Call Style  — 工具调用风格指导
4. ## Safety           — 安全规则
5. ## CLI Reference    — OpenClaw 命令行参考
6. ## Skills           — 可用 Skills 列表
7. ## Memory Recall    — Memory 搜索指导
8. ## Self-Update      — 自更新指导
9. ## Model Aliases    — 模型别名
10. ## Workspace       — 工作目录信息
11. ## Documentation   — 文档路径
12. ## Sandbox         — 沙箱信息（如果启用）
13. ## Authorized Senders — 授权发送者
14. ## Current Date    — 时区信息
15. ## Workspace Files — 注入的 bootstrap 文件说明
16. ## Reply Tags      — 回复标签
17. ## Messaging       — 消息路由指导
18. ## Voice           — TTS 提示
19. ## Reactions       — 反应指导
20. ## Reasoning       — 推理格式
21. # Project Context  — 注入 SOUL.md / AGENTS.md 等内容
22. ## Silent Replies  — NO_REPLY 机制
```

关键参数：

```typescript
// src/agents/system-prompt.ts
export function buildAgentSystemPrompt(params: {
  workspaceDir: string;
  toolNames?: string[];
  toolSummaries?: Record<string, string>;
  contextFiles?: EmbeddedContextFile[];   // bootstrap 文件内容
  skillsPrompt?: string;                   // Skills 列表
  heartbeatPrompt?: string;                // Heartbeat 指导
  promptMode?: "full" | "minimal" | "none"; // 子 Agent 用 minimal
  sandboxInfo?: EmbeddedSandboxInfo;
  // ...更多参数
})
```

### 4.2 行为文件（MD 文件）加载机制

OpenClaw 使用 **workspace bootstrap files** 机制将用户编辑的 Markdown 文件注入 system prompt。

**文件列表**（定义在 `src/agents/workspace.ts`）：

| 文件名 | 用途 |
|--------|------|
| `AGENTS.md` | Agent 行为和能力描述 |
| `SOUL.md` | 人格/语气/风格定义 |
| `TOOLS.md` | 用户自定义工具使用指导 |
| `IDENTITY.md` | 身份定义 |
| `USER.md` | 用户信息（偏好、联系方式） |
| `HEARTBEAT.md` | Heartbeat 任务列表 |
| `BOOTSTRAP.md` | 额外启动上下文 |
| `MEMORY.md` | 长期记忆存储 |

**加载流程**：

```typescript
// src/agents/bootstrap-files.ts
export async function resolveBootstrapContextForRun(params) {
  // 1. 从 workspace 目录加载所有 bootstrap 文件
  const bootstrapFiles = await resolveBootstrapFilesForRun(params);
  // 2. 根据 maxChars 限制截断内容
  const contextFiles = buildBootstrapContextFiles(bootstrapFiles, {
    maxChars: resolveBootstrapMaxChars(params.config),     // 默认 ~50K chars
    totalMaxChars: resolveBootstrapTotalMaxChars(params.config), // 总限制
  });
  return { bootstrapFiles, contextFiles };
}
```

**文件读取安全**：

```typescript
// src/agents/workspace.ts :: readWorkspaceFileWithGuards()
// 使用 boundary-file-read 防止路径穿越：
// 1. 检查文件是否在 workspace root 内
// 2. 通过 inode/dev/size/mtime 缓存，避免重复读取
// 3. 2MB 文件大小上限
```

**子 Agent 的 bootstrap 过滤**：

- 子 Agent 使用 `promptMode: "minimal"`，跳过大部分 bootstrap 段落
- Heartbeat session 只加载 `HEARTBEAT.md`
- Cron session 在 lightweight 模式下不加载任何 bootstrap 文件

关键设计：**TOOLS.md 不控制工具可用性**——system prompt 中明确写道：

> "TOOLS.md does not control tool availability; it is user guidance for how to use external tools."

这意味着 TOOLS.md 只是给 LLM 的参考文档，实际工具可用性由 tool policy 引擎决定。

### 4.3 上下文压缩和 Token 管理

**Compaction（上下文压缩）**：

当 session 的 token 使用接近 context window 上限时，自动触发 compaction：

```typescript
// src/agents/compaction.ts
export const BASE_CHUNK_RATIO = 0.4;  // 压缩目标：保留 40%
export const MIN_CHUNK_RATIO = 0.15;  // 最小保留 15%
export const SAFETY_MARGIN = 1.2;     // 20% 安全余量
```

Compaction 流程：
1. 将历史消息分成 N 个 chunk
2. 对每个 chunk 调用 LLM 生成摘要
3. 合并所有摘要为一条 "summary" 消息
4. 用 summary 替换原始历史

摘要指令要求保留：
- 活跃任务及状态
- 批量操作进度
- 用户最后请求
- 决策及理由
- TODO 和开放问题
- 所有不透明标识符（UUID、hash、URL 等）

**Context Window Guard**：

```typescript
// src/agents/context-window-guard.ts
export const CONTEXT_WINDOW_HARD_MIN_TOKENS = 8_192;    // 硬性最小值
export const CONTEXT_WINDOW_WARN_BELOW_TOKENS = 16_384; // 警告阈值
```

Token 计数使用 Pi Coding Agent SDK 的 `estimateTokens()` 函数。

---

## 5. Tool 系统详解

### 5.1 工具注册机制

工具注册分为三层：

**Layer 1: Pi SDK 内置工具**

```typescript
// src/agents/pi-tools.ts
import { codingTools, createReadTool, readTool } from "@mariozechner/pi-coding-agent";
// Pi SDK 提供基础的 read/write/edit/grep/find/ls/exec/process 工具
```

**Layer 2: OpenClaw 自定义工具**

```typescript
// src/agents/openclaw-tools.ts :: createOpenClawTools()
// 注册所有 OpenClaw 特有工具：
const tools = [
  createWebSearchTool(),      // web_search (Brave API)
  createWebFetchTool(),       // web_fetch
  createBrowserTool(),        // browser
  createCanvasTool(),         // canvas
  createNodesTool(),          // nodes (移动端控制)
  createCronTool(),           // cron
  createMessageTool(),        // message (跨渠道发送)
  createGatewayTool(),        // gateway (自管理)
  createAgentsListTool(),     // agents_list
  createSessionsListTool(),   // sessions_list
  createSessionsHistoryTool(),// sessions_history
  createSessionsSendTool(),   // sessions_send
  createSessionsSpawnTool(),  // sessions_spawn (子 Agent)
  createSubagentsTool(),      // subagents (管理子 Agent)
  createSessionStatusTool(),  // session_status
  createImageTool(),          // image
  createImageGenerateTool(),  // image_generate
  createTtsTool(),            // tts
  createPdfTool(),            // pdf
  // + plugin tools
];
```

**Layer 3: MCP 工具**

```typescript
// src/agents/pi-bundle-mcp-tools.ts
// 通过 @modelcontextprotocol/sdk 的 StdioClientTransport 连接外部 MCP server
// 每个 MCP server 的工具被发现后注册为 Agent tool
```

**工具组装**（`src/agents/pi-tools.ts :: createOpenClawCodingTools()`）：

```typescript
// 完整的工具组装流程：
// 1. 获取 Pi SDK 基础工具 (codingTools)
// 2. 添加 OpenClaw 工具 (createOpenClawTools)
// 3. 添加 MCP 工具 (createBundleMcpToolRuntime)
// 4. 添加 LSP 工具 (createBundleLspToolRuntime)
// 5. 添加 Plugin 工具 (resolvePluginTools)
// 6. 应用工具策略过滤 (applyToolPolicyPipeline)
// 7. 应用 sandbox 策略
// 8. 应用 workspace-only 文件系统策略
// 9. 包装 before-tool-call hook
// 10. 包装 abort signal
```

### 5.2 MCP 集成

MCP 工具通过 Stdio transport 集成：

```typescript
// src/agents/pi-bundle-mcp-tools.ts
async function createBundleMcpToolRuntime(params): BundleMcpToolRuntime {
  // 1. 从 config 加载 MCP server 配置
  const mcpConfig = loadEmbeddedPiMcpConfig(cfg);
  // 2. 对每个 server，启动 StdioClientTransport
  const transport = new StdioClientTransport({
    command: serverConfig.command,
    args: serverConfig.args,
    env: serverConfig.env,
  });
  // 3. 创建 MCP Client 并连接
  const client = new Client({ name: "openclaw", version: "1.0.0" });
  await client.connect(transport);
  // 4. 列出所有工具并转换为 Agent tool 格式
  const mcpTools = await listAllTools(client);
  // 5. 包装每个工具的 execute 函数
  return { tools: wrappedTools, dispose };
}
```

MCP tool result 转换：

```typescript
// src/agents/pi-bundle-mcp-tools.ts :: toAgentToolResult()
// MCP CallToolResult → AgentToolResult：
// - content 数组直接传递
// - structuredContent 序列化为 JSON text
// - 附加 mcpServer + mcpTool 到 details
```

### 5.3 权限模型（Security Profiles）

OpenClaw 使用三级安全策略：

**配置级别**（`config/zod-schema.agent-runtime.ts`）：

```typescript
// tools.exec.security 枚举值：
z.enum(["deny", "allowlist", "full"])
// - deny: 禁止所有 exec 调用
// - allowlist: 只允许配置的命令
// - full: 允许所有命令（需要审批机制）
```

**Tool Profile**（`src/agents/tool-catalog.ts`）：

```typescript
export type ToolProfileId = "minimal" | "coding" | "messaging" | "full";
// - minimal: 最少工具集
// - coding: 文件操作 + exec
// - messaging: 消息相关工具
// - full: 所有工具
```

**Tool Policy Pipeline**（`src/agents/tool-policy-pipeline.ts`）：

```typescript
// 工具过滤管线，按顺序应用：
// 1. Tool Profile Policy — 根据 profile 决定基础工具集
// 2. Owner-Only Policy — 某些工具只有 owner 可用
// 3. Allowlist/Deny — 显式允许/禁止列表
// 4. Message Provider Policy — 某些 provider 不支持某些工具
// 5. Model Provider Policy — 某些模型有原生工具冲突
// 6. Subagent Policy — 子 Agent 工具受限
// 7. Workspace-Only FS Policy — 文件操作限制在 workspace 内
```

**Owner-Only 工具**：

```typescript
// src/agents/tool-policy.ts
const OWNER_ONLY_TOOL_NAME_FALLBACKS = new Set([
  "whatsapp_login",
  "cron",
  "gateway",
  "nodes",
]);
// 只有 owner sender 才能使用这些工具
```

**Exec 审批机制**：

```typescript
// src/gateway/exec-approval-manager.ts
// 高风险 exec 命令需要用户审批：
// - 用户在 channel 中看到 /approve 命令
// - 支持 allow-once / allow-always / deny
// - 审批有时效性
```

### 5.4 工具执行和结果截断

工具结果截断策略：

```typescript
// src/agents/pi-embedded-runner/tool-result-truncation.ts
// 当工具输出过大时自动截断，避免 context overflow
// truncateOversizedToolResultsInSession()
```

Tool Loop Detection：

```typescript
// src/agents/tool-loop-detection.ts
// 检测 Agent 是否陷入工具调用死循环
// 例如反复读取同一文件或反复执行同一命令
```

---

## 6. Memory 系统详解

### 6.1 MEMORY.md 长期记忆

`MEMORY.md` 是 OpenClaw 的主要长期记忆存储：

- 位置：`~/.openclaw/workspace/MEMORY.md`
- 格式：纯 Markdown，用户可直接编辑
- 注入方式：作为 bootstrap file 注入 system prompt 的 Project Context 段

System prompt 中的 Memory Recall 指导：

```
## Memory Recall
Before answering anything about prior work, decisions, dates, people,
preferences, or todos: run memory_search on MEMORY.md + memory/*.md;
then use memory_get to pull only the needed lines.
```

Memory 工具：
- `memory_search`：混合检索（向量 + FTS5）
- `memory_get`：按行号读取指定内容

### 6.2 向量记忆索引

```typescript
// src/memory/manager.ts :: MemoryIndexManager
// 核心实现：
// - SQLite 数据库 + sqlite-vec 向量扩展 + FTS5 全文索引
// - 支持 5 种 embedding provider：OpenAI / Gemini / Voyage / Mistral / Ollama
// - 混合检索：向量相似度 + BM25 文本匹配 + 时间衰减
// - 支持 MMR（Maximal Marginal Relevance）去重
```

Memory 数据源（`src/memory/types.ts`）：

```typescript
export type MemorySource = "memory" | "sessions";
// "memory" — MEMORY.md + memory/*.md 文件
// "sessions" — 历史 session transcript（实验性）
```

### 6.3 Daily Notes 机制

OpenClaw 的 memory 目录支持 `memory/*.md` 格式的文件：

```
~/.openclaw/workspace/
  MEMORY.md              ← 主记忆文件
  memory/
    2024-01-15.md        ← 日记文件
    projects.md          ← 主题文件
    ...
```

这些文件通过 `memory_search` 工具被检索，通过 `resolveMemorySearchConfig()` 配置。

### 6.4 Heartbeat 主动记忆维护

Heartbeat 是 OpenClaw 的定时自省机制：

```typescript
// src/auto-reply/heartbeat.ts
export const HEARTBEAT_PROMPT =
  "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. " +
  "Do not infer or repeat old tasks from prior chats. " +
  "If nothing needs attention, reply HEARTBEAT_OK.";
export const DEFAULT_HEARTBEAT_EVERY = "30m";
```

Heartbeat 工作流：
1. Cron service 每 30 分钟触发一次 heartbeat
2. 创建一个 lightweight session，只加载 `HEARTBEAT.md`
3. Agent 读取 `HEARTBEAT.md` 中的任务列表
4. 执行待办任务（发消息、检查状态、更新记忆等）
5. 无事可做则回复 `HEARTBEAT_OK`（被静默处理，不发送给用户）

关键文件：
- `src/cron/service.ts` — Cron 调度器
- `src/cron/isolated-agent/isolated-agent.ts` — Heartbeat 独立 Agent runner
- `src/auto-reply/heartbeat.ts` — Heartbeat prompt 和 token 处理

---

## 7. LLM 调用链详解

### 7.1 Provider 抽象

OpenClaw 通过 Pi Agent Core SDK 抽象了 LLM provider：

```typescript
// src/agents/pi-embedded-runner/run/attempt.ts
import { streamSimple } from "@mariozechner/pi-ai";
// streamSimple() 是核心的 LLM 调用函数
// 支持 Anthropic / OpenAI / Google / Ollama 等 API
```

模型选择和 Auth：

```typescript
// src/agents/model-auth.ts
// 管理多个 auth profile，每个 profile 对应一个 API key
// 支持自动轮转、cooldown 冷却、profile 优先级

// src/agents/auth-profiles.ts :: resolveAuthProfileOrder()
// 决定使用哪个 auth profile：
// 1. 检查 cooldown 状态
// 2. 按优先级排序
// 3. 标记使用/成功/失败
```

模型 failover：

```typescript
// src/agents/model-fallback.ts
// 当主模型不可用时自动切换到备用模型
// 支持配置 fallback 链
```

### 7.2 工具调用循环

Pi Agent Core 的工具调用循环是标准的 ReAct 模式：

```
while true:
  response = await streamSimple(messages, tools, systemPrompt)

  if response.stopReason == "end_turn":
    break  // Agent 完成回复

  if response.stopReason == "tool_use":
    for each toolCall in response.toolCalls:
      result = await tool.execute(toolCall.params)
      messages.append(toolResult(toolCall.id, result))
    continue  // 带着工具结果继续调用 LLM
```

OpenClaw 在此基础上增加了：

```typescript
// src/agents/pi-embedded-runner/run.ts
// 外层重试循环：
for (let attempt = 0; attempt < maxRetries; attempt++) {
  try {
    result = await runEmbeddedAttempt(params);
    if (result.ok) break;

    // 处理各种错误：
    if (isRateLimitError(result.error)) {
      await rotateAuthProfile();  // 切换 API key
      continue;
    }
    if (isContextOverflowError(result.error)) {
      await compact(session);     // 压缩历史
      continue;
    }
    if (isBillingError(result.error)) {
      await failoverToNextProvider();
      continue;
    }
  } catch (e) {
    handleFailover(e);
  }
}
```

### 7.3 流式响应

流式响应通过 `subscribeEmbeddedPiSession()` 处理：

```typescript
// src/agents/pi-embedded-subscribe.ts
export function subscribeEmbeddedPiSession(params) {
  // 维护状态：
  const state = {
    assistantTexts: [],     // 累积的 assistant 文本
    toolMetas: [],           // 工具调用元数据
    compactionInFlight: false, // 是否正在压缩
    // ...
  };

  // 注册事件处理器：
  // - onAssistantText: 文本 delta → 分块推送到 channel
  // - onToolCall: 工具调用开始 → 显示 thinking indicator
  // - onToolResult: 工具完成 → 格式化摘要
  // - onReasoning: 推理文本 → 按模式处理
  // - onCompaction: 上下文压缩事件
  // - onUsage: token 使用统计
}
```

分块策略（Block Chunking）：

```typescript
// src/agents/pi-embedded-block-chunker.ts
// 将连续的文本流按段落/代码块切分
// 避免在 markdown 代码块中间切割
// 确保 channel 显示的消息格式正确
```

---

## 8. 与 OctoAgent 的关键差异

### 8.1 架构差异

| 维度 | OpenClaw | OctoAgent |
|------|----------|-----------|
| **运行模型** | 单体 Node.js 进程（Gateway daemon） | 分层微服务（Gateway + Kernel + Workers） |
| **编排层** | 无独立 Orchestrator，Channel → Agent 直连 | Orchestrator 层做路由和监督 |
| **Agent 循环** | 委托 Pi Agent Core SDK（streamSimple） | 自研 Free Loop（Worker 自治） |
| **Session 存储** | JSON 文件 + SQLite | Event Store（Event Sourcing） |
| **配置管理** | 单 YAML 文件 (`config.yaml`) | 四层 BehaviorWorkspaceScope |
| **进程模型** | 单进程多协程 | 多进程/多 Worker |

**关键差异解读**：OpenClaw 是一个**单体架构**——所有 channel、agent runner、cron 在同一个 Node.js 进程中运行。OctoAgent 采用分层架构，Orchestrator 和 Worker 可以独立运行。

### 8.2 工具系统差异

| 维度 | OpenClaw | OctoAgent |
|------|----------|-----------|
| **工具注册** | 硬编码 + SDK 内置 + MCP 动态发现 | Pydantic Schema 反射 + Tool Broker |
| **MCP 集成** | Stdio transport，per-session 生命周期 | 待实现（计划中） |
| **工具调用** | Pi Agent Core 内置循环 | Pydantic Skill + 确定性 Pipeline |
| **审批机制** | Exec 级别的 /approve 命令 | Policy Engine 双维度（PolicyAction x ApprovalDecision） |
| **隔离方式** | Docker sandbox（可选） | Docker 默认 |

**关键差异**：OpenClaw 的工具集是**开放式**的——Agent 可以直接读写文件系统、执行 shell 命令、控制浏览器。OctoAgent 计划采用更严格的 Policy Engine 控制。

### 8.3 Memory 模型差异

| 维度 | OpenClaw | OctoAgent |
|------|----------|-----------|
| **长期记忆** | MEMORY.md 文件（用户手动编辑） | SoR propose/validate/commit 编辑 |
| **语义检索** | SQLite + sqlite-vec + FTS5 混合 | LanceDB 向量数据库 |
| **Session 记忆** | 文件系统 JSON transcript | Event Store 事件流 |
| **记忆维护** | Heartbeat 定时自省 | 待实现 |
| **事实提取** | Agent 自主写入 MEMORY.md | 结构化事实提取 pipeline |
| **敏感分区** | 无 | 显式 vault + 额外授权 |

**关键差异**：OpenClaw 的 MEMORY.md 是**非结构化**的纯文本文件，由 Agent 自主读写，也允许用户直接编辑。OctoAgent 采用结构化的事实记忆系统，有明确的 schema 和编辑流程。

### 8.4 权限模型差异

| 维度 | OpenClaw | OctoAgent |
|------|----------|-----------|
| **工具访问** | Tool Profile (minimal/coding/messaging/full) + allow/deny 列表 | Policy Engine 双维度模型 |
| **文件访问** | workspaceOnly 开关 + boundary file guard | 计划：Policy-Driven Access |
| **Exec 审批** | /approve CLI 命令 + allow-once/allow-always/deny | Two-Phase (Plan→Gate→Execute) |
| **Owner 特权** | owner sender 独享某些工具 | 用户审批流 |
| **源码保护** | 无特殊保护（但 TOOLS.md 明确指导不读源码） | 禁止 Agent 访问系统源码目录 |

### 8.5 关于 "为什么 OpenClaw 的 Agent 不会去读源码"

OpenClaw 通过**行为指导而非硬编码限制**来防止 Agent 读取自身源码：

1. **TOOLS.md 明确告知**：system prompt 中写道 "TOOLS.md does not control tool availability; it is user guidance for how to use external tools"——将 TOOLS.md 定位为"使用指南"而非"能力声明"

2. **workspaceOnly 限制**：当 `tools.fs.workspaceOnly: true` 时，文件操作工具被限制在 workspace 目录内，无法访问 OpenClaw 安装目录

3. **workspace 目录隔离**：默认 workspace 是 `~/.openclaw/workspace/`，与 OpenClaw 源码完全分离

4. **Docker sandbox**：启用 sandbox 后，Agent 在 Docker 容器内运行，物理隔离

5. **Prompt 设计哲学**：system prompt 从不提及"你是 OpenClaw 系统"的内部实现细节。Agent 被告知的是"你有这些工具可用"，而不是"你的实现在这个目录"

这与 OctoAgent 的 Feature 067 形成对比——OctoAgent 选择通过 Policy Engine 在**系统层面**硬性禁止 Agent 通过 filesystem/terminal 工具访问源码目录，而非依赖 LLM 的行为遵从。

两种方案各有取舍：
- OpenClaw 方案更灵活，但依赖 LLM 遵守行为指导
- OctoAgent 方案更安全，但增加了系统复杂度，需要维护路径拦截策略

---

## 附录 A：核心文件索引

| 模块 | 关键文件 |
|------|----------|
| CLI 入口 | `src/entry.ts`、`src/index.ts`、`src/cli/run-main.ts` |
| Gateway | `src/gateway/server.impl.ts`、`src/gateway/server-chat.ts` |
| Agent Runner | `src/agents/pi-embedded-runner/run.ts`、`src/agents/pi-embedded-runner/run/attempt.ts` |
| System Prompt | `src/agents/system-prompt.ts`、`src/agents/system-prompt-params.ts` |
| Bootstrap Files | `src/agents/workspace.ts`、`src/agents/bootstrap-files.ts` |
| Tool 注册 | `src/agents/pi-tools.ts`、`src/agents/openclaw-tools.ts` |
| Tool Policy | `src/agents/tool-policy.ts`、`src/agents/tool-catalog.ts`、`src/agents/tool-policy-pipeline.ts` |
| MCP 集成 | `src/agents/pi-bundle-mcp-tools.ts`、`src/agents/mcp-stdio.ts` |
| Compaction | `src/agents/compaction.ts` |
| 流式响应 | `src/agents/pi-embedded-subscribe.ts` |
| Auth/Failover | `src/agents/model-auth.ts`、`src/agents/auth-profiles.ts`、`src/agents/failover-error.ts` |
| Memory | `src/memory/manager.ts`、`src/agents/memory-search.ts` |
| Session | `src/channels/session.ts`、`src/routing/session-key.ts` |
| Cron/Heartbeat | `src/cron/service.ts`、`src/auto-reply/heartbeat.ts` |
| Sub-agent | `src/agents/subagent-spawn.ts`、`src/agents/subagent-registry.ts` |
| Security | `src/agents/tool-fs-policy.ts`、`src/security/audit.ts` |
| Config Schema | `src/config/zod-schema.agent-runtime.ts` |
