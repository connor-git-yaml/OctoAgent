# OctoAgent 分层复用需求文档

> 版本：v1.0 | 作者：Connor + Claude | 日期：2026-04-05

## 1. 背景与动机

### 1.1 当前痛点

OctoAgent 已具备 **LLM 编排、工具治理、Memory 系统、Policy 审批、行为文件分层** 等核心能力，但这些能力全部锁在 `apps/gateway/`（FastAPI Web 服务）中，无法在以下场景复用：

- **CLI Agent**：开发者想在终端跑一个简单 Agent
- **Python SDK**：开发者想在自己的应用中嵌入 OctoAgent 能力
- **Jupyter Notebook**：数据科学家想交互式使用 Agent
- **后台 Worker**：定时任务、CI/CD pipeline 中的自动化 Agent
- **嵌入式场景**：IoT、边缘设备上的轻量 Agent

### 1.2 循环依赖阻塞

```
provider/pyproject.toml → 依赖 "octoagent-gateway"
gateway/pyproject.toml  → 依赖 "octoagent-provider"
```

`provider` 包（LLM 调用 + 配置管理）错误地依赖了 `gateway`，导致：
- 任何使用 LLM 的场景都必须拉起 FastAPI + uvicorn
- `pip install octoagent-provider` 会引入整个 Web 框架
- 无法独立测试 Provider 层

### 1.3 编排层不可复用

`OrchestratorService`、`DelegationPlaneService`、`WorkerRuntime` 等核心编排能力全在 `apps/gateway/src/` 中，不在可复用的 `packages/` 中。

## 2. 目标

### 2.1 分层架构目标

```
Layer 0 - 基础设施（零外部依赖，任何 Python 项目可用）
├── octoagent-core       # 数据模型 + Event Store + Behavior 文件系统
├── octoagent-protocol   # A2A 消息协议
└── octoagent-tooling    # ToolBroker + ToolContract + Schema 反射

Layer 1 - 智能能力（依赖 Layer 0，pip install 即可用）
├── octoagent-memory     # SoR + 向量检索 + 自动提取
├── octoagent-skills     # SkillRunner + Pipeline DAG + 上下文压缩
├── octoagent-policy     # 审批引擎 + 二阶段门
└── octoagent-provider   # LLM 调用 + Auth + Fallback（去掉 gateway 依赖）

Layer 2 - 编排层（新建，依赖 Layer 0+1）
└── octoagent-agent      # Orchestrator + Delegation + WorkerRuntime + EventBus

Layer 3 - 应用层（依赖 Layer 0-2）
├── octoagent-gateway    # FastAPI Web 服务 + Control Plane
├── octoagent-cli        # CLI 工具（原 provider.dx 的一部分）
└── octoagent            # 顶层 SDK 包（re-export + 简化 API）
```

### 2.2 开发者体验目标

#### 最简使用（3 行代码）
```python
from octoagent import Agent

agent = Agent(model="gpt-4o")
result = await agent.run("帮我查深圳今天天气")
```

#### 带工具（5 行代码）
```python
from octoagent import Agent, tool

@tool
async def search_weather(city: str) -> str:
    """查询城市天气"""
    return f"{city} 今天 26°C 多云"

agent = Agent(model="gpt-4o", tools=[search_weather])
result = await agent.run("深圳天气")
```

#### 带 Memory（渐进式）
```python
from octoagent import Agent
from octoagent.memory import SqliteMemoryStore

memory = SqliteMemoryStore("./agent_memory.db")
agent = Agent(model="gpt-4o", memory=memory)

# 第一次对话
await agent.run("我叫 Connor，住在深圳")
# 第二次对话
result = await agent.run("我住在哪里？")  # → "深圳"
```

#### 带 Policy 审批
```python
from octoagent import Agent
from octoagent.policy import ApprovalPolicy

policy = ApprovalPolicy(
    dangerous_commands=["rm", "drop", "delete"],
    approval_callback=lambda action: input(f"允许 {action}？(y/n)") == "y"
)
agent = Agent(model="gpt-4o", policy=policy)
```

#### 多 Agent 编排
```python
from octoagent import Agent, Orchestrator

researcher = Agent(model="gpt-4o", name="研究员", tools=[web_search])
developer = Agent(model="gpt-4o", name="开发者", tools=[code_exec])

orchestrator = Orchestrator(agents=[researcher, developer])
result = await orchestrator.run("调研 Rust 的 async 生态并写一个 demo")
```

## 3. 分层设计详述

### 3.1 Layer 0：基础设施

#### octoagent-core
**职责**：数据模型 + 存储 + 行为文件系统
**当前状态**：✅ 已基本独立
**依赖**：pydantic, aiosqlite, structlog
**暴露接口**：
- `StoreGroup` — 统一的 SQLite 存储访问
- `Event`/`Task`/`Work` — 领域模型
- `BehaviorWorkspace` — 行为文件分层系统

#### octoagent-protocol
**职责**：A2A 消息协议
**当前状态**：✅ 已独立
**依赖**：octoagent-core
**暴露接口**：
- `NormalizedMessage` — 消息标准化
- `A2AMessage` — Agent 间通信协议

#### octoagent-tooling
**职责**：工具系统
**当前状态**：✅ 基本独立（需恢复 ToolProfile 兼容）
**依赖**：octoagent-core
**暴露接口**：
- `ToolBroker` — 工具发现 + 执行 + Hook 链
- `tool_contract` — 工具声明装饰器
- `ToolIndex` — 语义工具检索
- `reflect_tool_schema` — 从函数签名自动生成 JSON Schema

### 3.2 Layer 1：智能能力

#### octoagent-memory
**职责**：长期记忆系统
**当前状态**：✅ 已独立
**依赖**：octoagent-core, lancedb
**暴露接口**：
- `SqliteMemoryStore` — SoR 存储
- `MemoryRecallResult` — 向量检索结果
- `WriteAction` — propose → validate → commit 三步协议

#### octoagent-skills
**职责**：Skill 执行引擎
**当前状态**：✅ 已独立
**依赖**：octoagent-core, octoagent-tooling
**暴露接口**：
- `SkillRunner` — 多步工具调用循环
- `LiteLLMSkillClient` — LLM 调用 + SSE 流式
- `ContextCompactor` — 上下文压缩
- `SkillDiscovery` — SKILL.md 自动发现

#### octoagent-policy
**职责**：权限与审批
**当前状态**：✅ 已独立
**依赖**：octoagent-core, octoagent-tooling
**暴露接口**：
- `PermissionPreset` — 权限预设（FULL/NORMAL/MINIMAL）
- `check_permission()` — 单次权限检查
- `ApprovalManager` — 审批流管理

#### octoagent-provider（重构后）
**职责**：LLM 调用抽象
**当前状态**：❌ 需要拆分（去掉 gateway 依赖）
**依赖**：octoagent-core（不再依赖 gateway）
**暴露接口**：
- `LiteLLMClient` — LLM 调用（支持 Proxy 和直连）
- `FallbackManager` — 多模型降级
- `AliasRegistry` — 模型别名路由
- `CodexAuthClient` — OpenAI Codex OAuth

**拆分方案**：
- `provider/dx/setup_governance_adapter.py`（依赖 gateway）→ 移到 `octoagent-cli`
- `provider/dx/` 中与配置管理相关的模块 → 保留在 provider 但去掉 gateway import
- 使用延迟导入或接口抽象打断循环

### 3.3 Layer 2：编排层（新建）

#### octoagent-agent
**职责**：Agent 编排和执行
**当前状态**：❌ 需要从 gateway 中提取
**依赖**：octoagent-core, octoagent-provider, octoagent-tooling, octoagent-skills, octoagent-memory, octoagent-policy
**暴露接口**：
- `Agent` — 顶层 Agent 类（简化 API）
- `Orchestrator` — 多 Agent 编排
- `WorkerRuntime` — Worker 执行环境
- `EventBus` — 事件发布/订阅（从 SSEHub 抽象）

**从 gateway 提取的组件**：

| 组件 | 原位置 | 新位置 |
|------|--------|--------|
| `OrchestratorService` | `gateway/services/orchestrator.py` | `agent/orchestrator.py` |
| `DelegationPlaneService` | `gateway/services/delegation_plane.py` | `agent/delegation.py` |
| `WorkerRuntime` | `gateway/services/worker_runtime.py` | `agent/worker.py` |
| `AgentContextService` | `gateway/services/agent_context.py` | `agent/context.py` |
| `SSEHub` → `EventBus` | `gateway/services/sse_hub.py` | `agent/events.py` |
| `TaskService` | `gateway/services/task_service.py` | `agent/task.py` |
| `LLMService` | `gateway/services/llm_service.py` | `agent/llm.py` |

### 3.4 Layer 3：应用层

#### octoagent-gateway（瘦化后）
**职责**：Web 服务 + Control Plane UI
**依赖**：octoagent-agent + FastAPI
**保留内容**：
- HTTP 路由（`routes/`）
- Control Plane（`control_plane/` 包）
- SSE 端点
- Telegram 渠道适配
- Web UI 静态文件服务

#### octoagent-cli（新建）
**职责**：CLI 工具
**依赖**：octoagent-agent
**包含内容**：
- `octo` 命令行入口
- `setup_governance_adapter`（从 provider.dx 迁移）
- `config_wizard`
- `update_service`

#### octoagent（顶层 SDK）
**职责**：统一入口，re-export 简化 API
**依赖**：octoagent-agent
**包含内容**：
```python
# octoagent/__init__.py
from octoagent_agent import Agent, Orchestrator, tool
from octoagent_memory import SqliteMemoryStore
from octoagent_policy import ApprovalPolicy
```

## 4. 实施路线图

### Phase 1：打断循环依赖（1-2 天）
- [ ] `provider/dx/setup_governance_adapter.py` 的 gateway import 改为延迟导入或移到 CLI 包
- [ ] `provider/pyproject.toml` 删除 `octoagent-gateway` 依赖
- [ ] 验证 `pip install octoagent-provider` 不再拉起 FastAPI

### Phase 2：抽象 EventBus（1 天）
- [ ] 从 `SSEHub` 提取通用 `EventBus` 接口到 `octoagent-core`
- [ ] `SSEHub` 变为 `EventBus` 的 Web 实现
- [ ] 编排层服务改为依赖 `EventBus` 接口而非 `SSEHub`

### Phase 3：提取编排层（3-5 天）
- [ ] 新建 `packages/agent/` 包
- [ ] 迁移 `OrchestratorService`、`DelegationPlaneService`、`WorkerRuntime`
- [ ] 迁移 `AgentContextService`、`TaskService`、`LLMService`
- [ ] Gateway 改为依赖 `octoagent-agent` 包

### Phase 4：SDK API 设计（2-3 天）
- [ ] 设计 `Agent` 类 API（参考 Pydantic AI 的简洁性）
- [ ] 设计 `@tool` 装饰器（从 `tool_contract` 简化）
- [ ] 设计流式响应 API（`async for chunk in agent.run(...)`)
- [ ] 设计 Memory 集成 API

### Phase 5：CLI 包提取（1-2 天）
- [ ] 新建 `packages/cli/` 包
- [ ] 迁移 `setup_governance_adapter`、`config_wizard`、`update_service`
- [ ] `octo` 命令入口迁移

### Phase 6：文档与示例（2-3 天）
- [ ] SDK 快速入门文档
- [ ] API Reference 自动生成
- [ ] 5 个渐进式示例（hello → tool → memory → policy → multi-agent）
- [ ] PyPI 发布配置

## 5. 市场定位与差异化

### 5.1 目标用户

| 用户类型 | 使用方式 | 竞品替代 |
|---------|---------|---------|
| **个人开发者** | `pip install octoagent` + 3 行代码 | Pydantic AI, LangChain |
| **AI 应用开发者** | SDK 嵌入到自己的应用 | Claude SDK, CrewAI |
| **企业 IT** | 自部署 Agent OS + Policy 审批 | LangChain Enterprise |
| **个人 AI OS 用户** | Web UI + Telegram + CLI | Agent Zero, OpenClaw |

### 5.2 核心差异化

| 能力 | OctoAgent | Pydantic AI | Agent Zero | LangChain | CrewAI |
|------|-----------|-------------|-----------|-----------|--------|
| **工具治理** | 双维度审批 + Policy Engine | 基础 | 无 | 基础 | 无 |
| **Memory** | SoR + Vault + 自动提取 | 无 | FAISS 基础 | 简单 KV | 无 |
| **行为系统** | 9 层文件 + 4 层作用域 | 无 | 单文件 | 无 | 无 |
| **可观测** | Event Sourcing + Logfire | Logfire | 基础日志 | LangSmith | 无 |
| **多模型** | LiteLLM + Codex Auth + Fallback | 多 | LiteLLM | 多 | 多 |
| **SDK 独立性** | ✅（重构后） | ✅ 极好 | ❌ 单体 | ⚠️ 过重 | ⚠️ 耦合 |
| **Web UI** | ✅ 完整 | ❌ | ✅ | ❌ | ❌ |
| **Skill Pipeline** | ✅ DAG + Pipeline | ❌ | ❌ | ✅ | ✅ |

### 5.3 市场机会

1. **"企业级 Agent 基础设施"赛道**
   - Policy + Memory + Event Sourcing 组合是企业客户关心的
   - Pydantic AI 和 CrewAI 都缺乏完整的治理能力

2. **"个人 AI OS"赛道**
   - Agent Zero 验证了需求但代码质量差
   - OctoAgent 有更好的 Web UI + 多渠道 + Memory

3. **"Agent SDK"赛道**
   - Pydantic AI 证明了轻量 SDK 有市场
   - OctoAgent 的 Skills + Tooling + Memory 层可以独立竞争
   - 不跟 Claude Code/Cursor 直接竞争（它们是 IDE 产品）

## 6. 验证标准

### 6.1 技术验证
- [ ] `pip install octoagent` 不拉起 FastAPI/uvicorn
- [ ] `Agent(model="gpt-4o").run("hello")` 在纯 Python 环境中成功
- [ ] 每个 Layer 的包可以独立 `pip install` 和 `import`
- [ ] 现有 gateway 功能零回归

### 6.2 开发者体验验证
- [ ] 从 `pip install` 到 `agent.run()` 只需 3 行代码
- [ ] README 中的所有示例可以直接运行
- [ ] 渐进式复杂度：简单 → 高级 不需要重写代码

### 6.3 性能验证
- [ ] SDK 模式下首次响应时间 < 2 秒
- [ ] 内存占用 < 100MB（不含 LLM 模型）
- [ ] 工具执行不引入额外延迟

## 7. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 编排层提取引入回归 | 高 | 分阶段迁移 + 每步测试 |
| SDK API 设计不合理 | 中 | 先做 3-5 个内部用例验证 |
| 包发布管理复杂度 | 中 | Monorepo + 统一版本号 |
| 与 Pydantic AI 同质化 | 低 | 聚焦 Memory + Policy + Behavior 差异化 |
