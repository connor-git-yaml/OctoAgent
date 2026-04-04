# Claude Code — 源码架构深度分析

> **源码版本**：2026-04 快照（`_references/opensource/claude-code/`）
> **分析日期**：2026-04-04
> **分析目的**：提取可供 OctoAgent 借鉴的架构模式和设计决策

## 1. 系统概览

### 技术栈
- **语言**：TypeScript + React + JSX
- **运行时**：Bun（非 Node.js）
- **UI 框架**：自定义 Ink TUI（251KB `ink.tsx`，非 npm 包）
- **状态管理**：Zustand + React Context
- **模型 SDK**：@anthropic-ai/sdk (v0.80.0+)
- **MCP**：@modelcontextprotocol/sdk (v1.29.0+)
- **类型验证**：Zod v4

### 整体架构图

```
用户输入 (Terminal / VS Code / Web)
    ↓
bin/claude-haha → cli.tsx → main.tsx (4690 行)
    ↓
QueryEngine.ts (1295 行)  ←→  query.ts (1729 行)
    ├─ System Prompt 构建 (context.ts + CLAUDE.md)
    ├─ AutoCompact 检查 (compact/)
    ├─ 模型调用 (services/api/claude.ts)
    ├─ 流式工具执行 (StreamingToolExecutor)
    └─ 停止钩子 (stopHooks)
         ↓
47 个内置工具 (tools/)
    ├─ BashTool / FileEditTool / FileReadTool
    ├─ GlobTool / GrepTool
    ├─ MCPTool (MCP 工具调用)
    ├─ AgentTool (子 Agent 编排)
    └─ SkillTool / TaskTools / ...
```

### 代码规模

| 维度 | 数量 |
|------|------|
| src/ 核心代码 | ~5000+ 行核心文件 |
| 内置工具 | 47 个（每个 1-20 个文件） |
| React 组件 | 146 个 |
| 自定义 Hook | 68 个 |
| 工具函数 | 331 个 |
| 斜杠命令 | 104 个 |
| 服务模块 | 38+ 个 |
| 特性门 | 40+ 个 |

## 2. 用户消息完整执行路径

```
Step 1: 用户输入
  cli.tsx → main.tsx → REPL 循环
  ↓
Step 2: 消息规范化
  query.ts → normalizeMessagesForAPI()
  构建 UserMessage（支持附件、图像）
  ↓
Step 3: System Prompt 构建
  context.ts → getSystemContext() + getUserContext()
  组装：基础提示(48KB) + Git 状态 + CLAUDE.md + 工具描述 + 权限说明
  ↓
Step 4: AutoCompact 检查
  compact/autoCompact.ts → threshold = effectiveContextWindow - 13K tokens
  超限时触发：Microcompact → AutoCompact → 会话内存压缩
  ↓
Step 5: 模型调用
  services/api/claude.ts → queryModelWithStreaming()
  Anthropic SDK 流式调用，含重试策略（exponential backoff）
  ↓
Step 6: 流式响应处理
  QueryEngine.ts → 解析 assistant response
  检测工具调用 → StreamingToolExecutor 并发执行
  ↓
Step 7: 工具权限检查
  permissions/ → 四层决策：alwaysAllow → alwaysDeny → autoClassify → ask
  ↓
Step 8: 工具执行
  toolOrchestration.ts → runTools()
  执行钩子 (pre/post_tool_call) → tool.execute() → 结果处理
  ↓
Step 9: 循环或终止
  有工具调用 → 回到 Step 5
  无工具调用 → 会话持久化 → 等待下一条输入
```

## 3. 编排层详解

### 查询循环（query.ts → QueryEngine.ts）

核心是 `query()` 函数驱动的单 Agent 循环：

1. **消息规范化**：将用户输入转为 `UserMessage`，支持附件和图像
2. **Token 预算管理**：`tokenBudget.ts` 计算剩余空间
3. **AutoCompact 触发**：Token 超过阈值时压缩历史
4. **模型调用**：`queryModelWithStreaming()` 流式调用
5. **工具调用分派**：检测 `tool_use` block → 权限检查 → 执行
6. **停止钩子**：`executeStopFailureHooks()` 在失败时触发

### 消息类型系统

```typescript
Message =
  | UserMessage              // 用户输入（支持附件、图像）
  | AssistantMessage          // 模型回复（含工具调用）
  | SystemLocalCommandMessage // 本地命令输出
  | SystemCompactBoundaryMessage  // 压缩边界标记
  | SystemPermissionRetryMessage  // 权限重试
  | SystemScheduledTaskFireMessage // 计划任务触发
  | AttachmentMessage         // 附件（CLAUDE.md、文件）
  | ProgressMessage           // 进度指示
  | TombstoneMessage          // 已删除消息标记（用于追踪）
```

### 子 Agent 机制（AgentTool）

- 通过 `AgentTool` 工具实现子 Agent 派发
- 内置 Agent 类型：Plan（规划）、Explore（探索）
- 支持自定义 Agent 定义（`.claude/agents/` 目录下的 Markdown）
- 子 Agent 运行在独立上下文中，完成后返回结果
- 支持 worktree 隔离模式

## 4. Context 管理详解

### System Prompt 组装

组装顺序（`constants/prompts.ts` + `utils/queryContext.ts`）：

1. 基础系统提示（~48KB，包含安全规则、行为准则）
2. Git 状态（当前分支、status、最近 commit）
3. CLAUDE.md 内容（分层加载：项目根 → 子目录 → 用户全局）
4. 工具描述（47 个工具的 schema）
5. 权限模式说明
6. 思考模式说明（如启用 Extended Thinking）
7. 计划模式说明（如在 Plan Mode 中）

### CLAUDE.md 加载机制

- 自动发现（除非 `--bare` 模式）
- 分层加载：项目 `CLAUDE.md` → `.claude/CLAUDE.md` → `~/.claude/CLAUDE.md`
- 截断策略：200 行或 25KB
- 去重：`loadedNestedMemoryPaths` 追踪已加载路径
- 作为 system context 注入到消息序列

### Context Window 管理和压缩策略

三级渐进压缩：

1. **Microcompact**（增量）：合并相邻短消息，减少消息数量
2. **AutoCompact**（完整）：
   - 阈值：`effectiveContextWindow - 13K tokens`
   - 连续失败上限：`MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3`
   - 生成压缩总结替换历史
3. **会话内存压缩**：提取长期记忆保存到 `MEMORY.md`

### Memory 系统

- 持久化到 `~/.claude/projects/<project>/memory/MEMORY.md`
- 自动记忆：auto memory 系统在对话中自动识别和保存
- 内存类型：user（用户信息）、feedback（反馈）、project（项目）、reference（引用）
- 单文件索引 + 分文件存储（带 frontmatter）
- 截断上限：MEMORY.md 200 行

## 5. Tool 系统详解

### 工具分类（47 个）

| 类别 | 工具名 | 数量 |
|------|--------|------|
| 文件操作 | FileReadTool, FileEditTool, FileWriteTool, GlobTool, NotebookEditTool | 5 |
| 执行 | BashTool, PowerShellTool | 2 |
| 搜索 | GrepTool, WebSearchTool | 2 |
| Web | WebFetchTool, WebSearchTool | 2 |
| MCP | MCPTool, ListMcpResourcesTool, ReadMcpResourceTool | 3 |
| 任务 | TaskCreateTool, TaskUpdateTool, TaskStopTool, TaskListTool, TaskOutputTool | 5 |
| Agent | AgentTool（子 Agent 编排） | 1 |
| 计划 | EnterPlanModeTool, ExitPlanModeV2Tool | 2 |
| 其他 | SkillTool, AskUserQuestionTool, TodoWriteTool, SendMessageTool 等 | 10+ |

### 工具执行流程

```
1. 权限检查 (canUseTool)
   ├─ 规则匹配 (glob/regex/command pattern)
   ├─ 自动分类器 (bashClassifier 分析命令危险性)
   └─ 用户确认对话

2. 输入验证 (tool.validate)
   └─ Zod Schema 验证

3. 执行
   ├─ 沙盒检查 (@anthropic-ai/sandbox-runtime)
   ├─ 执行前钩子 (pre_tool_call)
   ├─ tool.execute(input, context)
   └─ 执行后钩子 (post_tool_call)

4. 结果处理
   ├─ StreamingToolExecutor 并发执行多个工具
   ├─ 进度更新回调
   ├─ 错误处理和重试
   └─ 大结果存储到磁盘
```

### MCP 集成

成熟的多传输支持：

- **传输协议**：stdio / SSE / HTTP / WebSocket / In-Process SDK
- **配置作用域**：local / user / project / dynamic / enterprise / claudeai / managed（7 种）
- **认证**：OAuth + XAA/SEP-990
- **环境变量扩展**：配置中的 `${VAR}` 自动替换

## 6. Permission / Safety

### 权限模型

```
PermissionMode:
  ├─ 'default'           # 每个操作询问用户
  ├─ 'plan'              # 计划模式，暂停自动执行
  ├─ 'acceptEdits'       # 自动接受文件编辑
  ├─ 'bypassPermissions' # 完全跳过检查
  ├─ 'dontAsk'           # 自动拒绝需确认的操作
  └─ 'auto' (内部)       # 自动分类器决策
```

### 四层决策流程

```
alwaysAllow 规则 → 命中则允许
   ↓ 未命中
alwaysDeny 规则 → 命中则拒绝
   ↓ 未命中
自动分类器 (bashClassifier/yoloClassifier) → 高置信度则自动决策
   ↓ 低置信度
用户确认对话 → allow / deny / always allow
```

### Hook 系统

- `pre_tool_call`：工具执行前
- `post_tool_call`：工具执行后
- `pre_compact`：压缩前
- `post_compact`：压缩后
- `session_start`：会话开始
- `pre_workspace_modification`：工作区修改前
- `post_workspace_modification`：工作区修改后

### 沙盒机制

- 使用 `@anthropic-ai/sandbox-runtime`
- BashTool 中 `shouldUseSandbox.ts` 决策
- 限制：文件访问、网络、进程

## 7. Session / State

### 会话管理

- **会话 ID**：UUID
- **存储路径**：`~/Library/Application Support/Claude Code/sessions/`（macOS）
- **会话数据**：消息历史、文件状态缓存、工具结果、git commit tracking

### 状态管理（Zustand）

```typescript
AppState {
  messages: Message[]
  toolPermissionContext: ToolPermissionContext
  thinkingEnabled: boolean
  fastModeEnabled: boolean
  session: SessionMetadata
  background: BackgroundState  // 后台任务
}
```

### 持久化和恢复

- `sessionStorage.ts`：消息记录和持久化
- `sessionRestore.ts`：列出和恢复过去会话
- `fileHistory.ts`：文件编辑快照和撤销支持

## 8. LLM 调用链详解

### API 调用（services/api/claude.ts）

- 使用 Anthropic SDK 的 `messages.create()` 流式调用
- 重试策略：exponential backoff（`withRetry.ts`）
- 缓存提示：`cache_control` header 优化
- Token 追踪：输入/输出/缓存读写分别计数

### 成本追踪（cost-tracker.ts）

- 每次调用累计 Token 使用
- 实时 USD 成本计算
- 支持多模型定价

### 多模型支持

- 环境变量：`ANTHROPIC_MODEL`
- 命令行：`claude --model <model>`
- 回退逻辑：`getMainLoopModel()`
- Extended Thinking 模式：8000 tokens 上限

## 9. 扩展性

### 技能系统（skills/）

- 内置技能（bundled）+ 用户自定义
- 通过 `SkillTool` 工具调用
- 支持文件路径加载和监视变化

### 插件系统（plugins/）

- 内置插件 + 用户自定义
- 插件可注册工具、命令和 Hook

### 配置系统

优先级从高到低：
1. 命令行参数
2. `.claude/config.json`（项目级）
3. `~/.claude/config.json`（用户级）
4. 内置默认值

## 10. 与 OctoAgent 的关键差异

### 架构对比

| 维度 | Claude Code | OctoAgent |
|------|-------------|-----------|
| **定位** | 个人 CLI 开发工具 | 个人 AI OS |
| **运行时** | Bun (TypeScript) | Python 3.12 + FastAPI |
| **UI** | React/Ink TUI + IDE 扩展 | Web UI (React + Vite) |
| **编排模式** | 单 Agent 循环 + 子 Agent | Orchestrator + Worker 委派 |
| **状态管理** | Zustand 内存 + 磁盘持久化 | Event Sourcing + SQLite WAL |
| **权限模型** | 实时 TUI 对话确认 | Policy Engine 双维度审批 |
| **模型网关** | 直连 Anthropic SDK | LiteLLM Proxy（多厂商） |
| **MCP** | 成熟的多传输支持 | 基础 stdio 集成 |
| **工具执行** | StreamingToolExecutor 并发 | 同步执行 + SkillRunner 循环 |
| **Context 压缩** | 三级渐进（Micro/Auto/Memory） | Rolling Summary |
| **Memory** | MEMORY.md 文件 + auto memory | SoR 三步协议 + 向量检索 |

### OctoAgent 可借鉴的设计

1. **权限四层决策**：alwaysAllow → alwaysDeny → autoClassify → ask，比当前 PolicyAction × ApprovalDecision 模型更直观
2. **StreamingToolExecutor**：并发工具执行，提升多工具场景效率
3. **AutoCompact 阈值**：`effectiveContextWindow - 13K` 的简单公式比动态 budget planner 更可预测
4. **消息类型系统**：12+ 种系统消息类型提供精细的 context 控制
5. **CLAUDE.md 分层加载**：项目 → 子目录 → 用户全局的三级发现机制
6. **Hook 系统**：pre/post_tool_call 等 7 种 hook 点，比当前 ToolBroker before/after 更丰富
7. **会话恢复**：完整的会话持久化和恢复流程

### Claude Code 的局限性

1. **UI 紧耦合**：Ink TUI 与核心逻辑混合（main.tsx 4690 行），难以 headless 使用
2. **单模型绑定**：仅支持 Anthropic 模型，无多厂商路由
3. **无长期 Agent 支持**：面向短 session 设计，无后台长任务恢复
4. **无多用户支持**：单用户 CLI 工具，无 multi-tenant 架构
5. **特性门碎片化**：40+ 个 `feature()` 门导致代码路径复杂

### OctoAgent 的独有优势

1. **多渠道接入**：Telegram / Web / API 统一网关
2. **Event Sourcing**：完整的事件溯源，支持崩溃恢复和审计
3. **Worker 委派**：独立 Worker 进程支持长任务
4. **LiteLLM Proxy**：多模型厂商路由和 fallback
5. **Behavior 四层分离**：system_shared / agent_private / project_shared / project_agent
6. **Memory SoR**：propose → validate → commit 的结构化记忆管理
