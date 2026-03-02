# 技术调研: Feature 006 — Policy Engine + Approvals + Chat UI

**Feature**: 006-policy-engine-approvals
**日期**: 2026-03-02
**模式**: [独立模式] 本次技术调研未参考产品调研结论，直接基于需求描述和代码上下文执行
**调研者**: Spec Driver 技术调研子代理

---

## 1. 调研背景与核心问题

### 1.1 需求上下文

Feature 006 目标是建立 OctoAgent 的安全治理层：

1. **多层 Policy Pipeline** — 层层过滤，纯函数管道，每层附 label 追溯决策来源
2. **Two-Phase Approval** — 幂等注册 + asyncio.Event 异步等待 + 原子消费 + 宽限期
3. **PolicyEngine 核心** — allow/ask/deny 决策，对接 ToolBroker before 钩子
4. **审批工作流** — APPROVAL_REQUESTED/APPROVED/REJECTED 事件 + WAITING_APPROVAL 状态
5. **Approvals REST API** — POST /api/approve/{approval_id}, GET /api/approvals
6. **前端 Approvals 面板 + 基础 Chat UI（SSE 流式输出）**

### 1.2 核心技术问题

| # | 问题 | 重要性 |
|---|------|--------|
| Q1 | Policy Pipeline 架构：纯函数 vs Provider 链 vs 混合？ | 高 |
| Q2 | Two-Phase Approval 在 Python asyncio 中如何实现幂等注册 + 异步等待？ | 高 |
| Q3 | 审批状态如何持久化？内存 + Event Store 还是纯 Event Store？ | 高 |
| Q4 | WAITING_APPROVAL 状态与现有 Task 状态机如何协调？ | 中 |
| Q5 | SSE 如何实时推送审批状态变更到前端？ | 中 |
| Q6 | 前端 Chat UI + Approvals 面板的技术选型？ | 中 |

### 1.3 前置依赖

Feature 004 (Tool Contract + ToolBroker) 已输出以下锁定契约（`contracts/tooling-api.md`），Feature 006 必须对齐：

- **PolicyCheckpoint Protocol**: `check(tool_meta, params, context) -> CheckResult`
- **BeforeHook Protocol**: `before_execute(tool_meta, args, context) -> BeforeHookResult`
- **CheckResult**: `{ allowed: bool, reason: str, requires_approval: bool }`
- **FailMode**: PolicyCheckpoint 强制 `fail_mode="closed"`
- **SideEffectLevel**: `none` / `reversible` / `irreversible`
- **ToolProfile**: `minimal` / `standard` / `privileged`

---

## 2. 竞品源码深度分析

### 2.1 OpenClaw Policy Pipeline（最高借鉴价值）

#### 2.1.1 7 层 Cascade 策略管道

**源码**: `_references/opensource/openclaw/src/agents/tool-policy-pipeline.ts`

OpenClaw 实现了 7 层策略过滤管道，核心设计：

```typescript
// 每层由 policy + label 组成
export type ToolPolicyPipelineStep = {
  policy: ToolPolicyLike | undefined;
  label: string;                          // 决策来源追溯
  stripPluginOnlyAllowlist?: boolean;     // 插件 allowlist 隔离
};

// 7 层执行顺序
const steps = [
  { policy: profilePolicy,         label: "tools.profile" },          // Layer 1: Profile 过滤
  { policy: providerProfilePolicy,  label: "tools.byProvider.profile" }, // Layer 2: Provider Profile
  { policy: globalPolicy,           label: "tools.allow" },            // Layer 3: 全局策略
  { policy: globalProviderPolicy,   label: "tools.byProvider.allow" }, // Layer 4: Provider 全局
  { policy: agentPolicy,            label: `agents.${id}.tools.allow` }, // Layer 5: Agent 策略
  { policy: agentProviderPolicy,    label: `agents.${id}.tools.byProvider.allow` }, // Layer 6: Agent Provider
  { policy: groupPolicy,            label: "group tools.allow" },      // Layer 7: Group 策略
];
```

**关键设计决策**：

1. **纯函数管道**: `applyToolPolicyPipeline()` 是纯函数，输入工具列表 + 策略步骤，输出过滤后的工具列表。无副作用，可单元测试。
2. **label 追踪**: 每层附带字符串 label，审计时可完整追溯"是哪一层规则过滤掉了该工具"。
3. **收紧原则**: 后续层只能进一步收紧（从可用集中移除），不可放松上层决策。
4. **跳过空策略**: `if (!step.policy) continue` — 未配置的层直接跳过，不阻塞管道。
5. **插件组展开**: `expandPolicyWithPluginGroups()` 将插件级别的 allowlist/blocklist 展开为具体工具名，避免名称冲突。

**OctoAgent 映射**: Blueprint 8.6.4 规划了 4 层 Pipeline（Profile -> Global -> Agent -> Group），M1 实现前 2 层。OpenClaw 的 7 层可压缩为 4 层满足需求，label 追踪机制直接采用。

#### 2.1.2 Command Gating

**源码**: `_references/opensource/openclaw/src/channels/command-gating.ts`

命令级门禁系统，核心逻辑：

```typescript
export type CommandGatingModeWhenAccessGroupsOff = "allow" | "deny" | "configured";

function resolveCommandAuthorizedFromAuthorizers(params) {
  if (!useAccessGroups) {
    if (mode === "allow") return true;
    if (mode === "deny") return false;
    // "configured" 模式：有配置就按配置走，无配置则 allow
    const anyConfigured = authorizers.some(e => e.configured);
    if (!anyConfigured) return true;
    return authorizers.some(e => e.configured && e.allowed);
  }
  // 启用 Access Groups 时，必须有显式授权
  return authorizers.some(e => e.configured && e.allowed);
}
```

**借鉴**: 三模式门禁（allow/deny/configured）可用于 OctoAgent 的渠道级策略覆盖。M1 阶段简化为 allow/deny 即可。

#### 2.1.3 安全门禁架构文档

**源码**: `_references/opensource/openclaw/docs/gateway/security/index.md`

关键架构原则：

- **Personal assistant trust model**: 单用户信任边界，不适用于多租户对抗场景
- **安全审计 CLI**: `openclaw security audit --deep` 定期检查配置安全性
- **分层防御**: 网络层（bind loopback）+ 认证层（token）+ 工具层（policy）+ 命令层（gating）

**OctoAgent 对齐**: 单用户场景完全匹配。但 OctoAgent 需额外关注 Web UI 的 CORS 和 JWT 认证（OpenClaw 主要依赖 macOS app 内嵌认证）。

### 2.2 OpenClaw Approval 系统

#### 2.2.1 ExecApprovalManager（核心组件）

**源码**: `_references/opensource/openclaw/src/gateway/exec-approval-manager.ts`

这是 Two-Phase Approval 的核心实现，173 行精炼代码：

```typescript
class ExecApprovalManager {
  private pending = new Map<string, PendingEntry>();

  // Phase 1: 注册（同步，幂等）
  register(record, timeoutMs): Promise<Decision | null> {
    const existing = this.pending.get(record.id);
    if (existing) {
      if (existing.record.resolvedAtMs === undefined) {
        return existing.promise;  // 幂等：返回已有 promise
      }
      throw new Error("already resolved");  // 已消费，不允许重注册
    }
    // 创建 Promise + 超时定时器
    const promise = new Promise((resolve, reject) => { ... });
    entry.timer = setTimeout(() => this.expire(record.id), timeoutMs);
    this.pending.set(record.id, entry);
    return promise;
  }

  // 决策解决（原子操作）
  resolve(recordId, decision, resolvedBy?): boolean {
    const pending = this.pending.get(recordId);
    if (!pending || pending.record.resolvedAtMs !== undefined) return false;  // 防双重解决
    clearTimeout(pending.timer);
    pending.record.resolvedAtMs = Date.now();
    pending.record.decision = decision;
    pending.resolve(decision);
    // 15s 宽限期后清理
    setTimeout(() => { if (this.pending.get(recordId) === pending) this.pending.delete(recordId); },
      RESOLVED_ENTRY_GRACE_MS);
    return true;
  }

  // 原子消费一次性审批令牌
  consumeAllowOnce(recordId): boolean {
    const entry = this.pending.get(recordId);
    if (!entry || entry.record.decision !== "allow-once") return false;
    entry.record.decision = undefined;  // 消费后清除，防重放
    return true;
  }
}
```

**关键设计模式**：

| 模式 | 实现 | OctoAgent 适用性 |
|------|------|-----------------|
| **幂等注册** | 同 ID 重复注册返回已有 promise | 直接采用 |
| **双阶段分离** | register() 同步 + awaitDecision() 异步 | 直接采用 |
| **原子消费** | consumeAllowOnce() 消费后清除 decision | 直接采用 |
| **15s 宽限期** | 解决后保留 15s，允许迟到的 await 找到结果 | 直接采用 |
| **超时过期** | setTimeout + expire() 自动超时 | 改用 asyncio.call_later |
| **内存存储** | Map<string, PendingEntry> 纯内存 | **需改进**: 必须持久化到 Event Store |

#### 2.2.2 ExecApprovalDecision 类型

**源码**: `_references/opensource/openclaw/src/infra/exec-approvals.ts`

```typescript
export type ExecApprovalDecision = "allow-once" | "allow-always" | "deny";
export type ExecSecurity = "deny" | "allowlist" | "full";
export type ExecAsk = "off" | "on-miss" | "always";
```

**决策矩阵**：

```typescript
function requiresExecApproval(params) {
  return (
    params.ask === "always" ||
    (params.ask === "on-miss" &&
      params.security === "allowlist" &&
      (!params.analysisOk || !params.allowlistSatisfied))
  );
}
```

**OctoAgent 映射**:

| OpenClaw 概念 | OctoAgent 概念 | 说明 |
|---------------|---------------|------|
| `ExecApprovalDecision` | `ApprovalDecision` | 三值：allow-once / allow-always / deny |
| `ExecSecurity` | `SideEffectLevel` | 已在 Feature 004 定义 |
| `ExecAsk` | Policy Pipeline 输出 | allow / ask / deny |
| `allowlist` | Safe Bins 白名单 | Blueprint 8.6.4 提及 |

#### 2.2.3 Two-Phase Approval Request Flow

**源码**: `_references/opensource/openclaw/src/agents/bash-tools.exec-approval-request.ts`

```typescript
// Phase 1: 注册审批（防竞态关键）
async function registerExecApprovalRequest(params): Promise<ExecApprovalRegistration> {
  const result = await callGatewayTool("exec.approval.request", {
    timeoutMs: DEFAULT_APPROVAL_REQUEST_TIMEOUT_MS
  }, { ...params, twoPhase: true }, { expectFinal: false });
  // 如果注册时已有决策（allow-always 白名单命中），直接返回
  if (decision.present) return { id, expiresAtMs, finalDecision: decision.value };
  return { id, expiresAtMs };
}

// Phase 2: 等待决策
async function waitForExecApprovalDecision(id): Promise<string | null> {
  const result = await callGatewayTool("exec.approval.waitDecision", {...}, { id });
  return parseDecision(result).value;
}
```

**关键洞见**: 注册阶段可能直接返回决策（如果命中 allow-always 白名单），这是一个优化路径——避免不必要的等待。OctoAgent 应采用此模式。

### 2.3 Agent Zero 干预系统

#### 2.3.1 InterventionException 机制

**源码**: `_references/opensource/agent-zero/agent.py` (L344-346, L830-849)

```python
# 定义
class InterventionException(Exception):
    pass

# 干预处理（在 monologue loop 的每个关键点调用）
async def handle_intervention(self, progress=""):
    while self.context.paused:
        await asyncio.sleep(0.1)  # [反模式] 轮询等待
    if self.intervention:
        msg = self.intervention
        self.intervention = None
        # 保存工具进度到历史
        if last_tool and last_tool.progress.strip():
            self.hist_add_tool_result(last_tool.name, tool_progress)
        # 追加干预消息到历史
        self.hist_add_user_message(msg, intervention=True)
        raise InterventionException(msg)  # 跳出当前迭代
```

**monologue loop 集成点**（L405-436 共 4 处调用）:

```python
async def monologue(self):
    while True:
        while True:
            await self.handle_intervention()  # (1) loop 开始
            prompt = await self.prepare_prompt(...)
            await self.handle_intervention()  # (2) LLM 调用前
            async def reasoning_callback(chunk, full):
                await self.handle_intervention()  # (3) 推理流中
            async def stream_callback(chunk, full):
                await self.handle_intervention()  # (4) 响应流中
```

**评价**:

| 方面 | 评价 | OctoAgent 借鉴 |
|------|------|---------------|
| 干预注入点 | 4 处检查点，覆盖全面 | 采用，但改为 Worker Free Loop 中的检查点 |
| 暂停机制 | `asyncio.sleep(0.1)` 轮询 | **反模式**，改用 `asyncio.Event` |
| 状态管理 | 纯内存 `self.intervention` | **反模式**，改为事件驱动 + Event Store |
| 历史保存 | 干预前保存工具进度 | 借鉴，确保审批时的上下文完整性 |

### 2.4 AgentStudio Pre-Send Guard

#### 2.4.1 Provider Pipeline 架构

**源码**: `_references/opensource/agentstudio/backend/src/services/preSendGuard/index.ts`

```typescript
// Provider Registry（可插拔）
const providerRegistry = new Map<string, PreSendGuardProvider>();
registerProvider(noopProvider);
registerProvider(httpAuditProvider);

async function evaluatePreSendGuard(input): Promise<EvaluationResult> {
  const providers = resolveProviders(globalConfig, agentConfig);
  let currentMessage = context.message;

  for (const providerConfig of providers) {
    const provider = providerRegistry.get(providerConfig.name);
    const result = await provider.evaluate({...context, message: currentMessage}, providerConfig);

    if (result.decision === 'rewrite') {
      currentMessage = result.rewrittenMessage;  // 消息改写
      continue;
    }
    if (result.decision === 'block' || result.decision === 'require_confirm') {
      return createBlockedResult(...);  // 短路返回
    }
  }
  return { decision: 'allow', message: currentMessage };
}
```

**四种决策类型**:

| Decision | 行为 | OctoAgent 映射 |
|----------|------|---------------|
| `allow` | 通过 | Policy `allow` |
| `block` | 拒绝 | Policy `deny` |
| `rewrite` | 改写消息后继续 | [推断] 可用于参数消毒，M2 考虑 |
| `require_confirm` | 需要用户确认 | Policy `ask` |

**Config Resolver**: 支持 default + rules 两级配置，rule 按 agentId / projectPath / channel 匹配。

**关键借鉴**:

1. **Provider 可插拔**: 通过 name 注册，运行时解析
2. **onError 策略**: 每个 Provider 声明 `onError: 'allow' | 'block'`
3. **步骤审计**: 每步记录 `{ provider, decision, reason, code, elapsedMs }`
4. **短路返回**: block/require_confirm 立即返回，不继续后续 Provider

**[推断] AgentStudio 未实现的**: `require_confirm` 虽然定义但在当前代码中未见实际的确认等待逻辑（仅返回 blocked），可能是前端直接处理。

### 2.5 Pydantic AI Approval 工具集

#### 2.5.1 ApprovalRequiredToolset

**源码**: `_references/opensource/pydantic-ai/pydantic_ai_slim/pydantic_ai/toolsets/approval_required.py`

```python
@dataclass
class ApprovalRequiredToolset(WrapperToolset[AgentDepsT]):
    approval_required_func: Callable[
        [RunContext[AgentDepsT], ToolDefinition, dict[str, Any]], bool
    ] = lambda ctx, tool_def, tool_args: True

    async def call_tool(self, name, tool_args, ctx, tool):
        if not ctx.tool_call_approved and self.approval_required_func(ctx, tool.tool_def, tool_args):
            raise ApprovalRequired
        return await super().call_tool(name, tool_args, ctx, tool)
```

**DeferredToolRequests 模式**（来自 `deferred-tools.md`）:

```python
# Agent run 遇到需要审批的工具时，run 结束并返回 DeferredToolRequests
result = agent.run_sync('...')
assert isinstance(result.output, DeferredToolRequests)

# 用户处理审批
results = DeferredToolResults()
results.approvals[call.tool_call_id] = True  # 或 ToolDenied('reason')

# 以审批结果继续 run
result = agent.run_sync('...', message_history=messages, deferred_tool_results=results)
```

**关键设计**:

| 方面 | Pydantic AI 方式 | OctoAgent 适用性 |
|------|-----------------|-----------------|
| 审批粒度 | 工具级（per-tool + per-call 可配） | 采用，对齐 ToolMeta 级别 |
| 审批判定 | `approval_required_func` 回调 | 对应 PolicyCheckpoint.check() |
| 等待机制 | Agent run 终止 + message_history 续传 | 不直接适用（OctoAgent 用 Free Loop，不重启 run） |
| 状态追踪 | RunContext.tool_call_approved 布尔值 | 简化，OctoAgent 需更丰富的审批状态 |

**借鉴**: `approval_required_func` 的可插拔判定回调设计优雅。OctoAgent 的 PolicyCheckpoint 可采用类似的回调注入模式，但需要将判定逻辑扩展为多层 Pipeline。

---

## 3. 架构方案选型

### 3.1 方案 A: 纯函数 Pipeline 模式（推荐）

**参考**: OpenClaw `tool-policy-pipeline.ts`

#### 架构描述

```
PolicyEngine
  ├── PolicyPipeline（纯函数，无状态）
  │   ├── Layer 1: ToolProfileFilter (Profile 过滤)
  │   ├── Layer 2: GlobalRuleEvaluator (side_effect 驱动的 allow/ask/deny)
  │   ├── Layer 3: AgentPolicyEvaluator (M2+)
  │   └── Layer 4: GroupPolicyEvaluator (M2+)
  │
  ├── ApprovalManager（有状态，内存 + Event Store 双写）
  │   ├── register() -> approval_id
  │   ├── wait_for_decision() -> asyncio.Event
  │   ├── resolve() -> 原子决策
  │   └── consume_allow_once() -> 原子消费
  │
  └── PolicyCheckHook（BeforeHook 包装器）
      └── before_execute() -> Pipeline + ApprovalManager 联动
```

#### 核心组件

**1. PolicyPipeline（纯函数）**

```python
@dataclass(frozen=True)
class PolicyStep:
    evaluator: Callable[[ToolMeta, dict, ExecutionContext], PolicyDecision]
    label: str

@dataclass(frozen=True)
class PolicyDecision:
    action: Literal["allow", "ask", "deny"]
    label: str
    reason: str = ""

def evaluate_pipeline(
    steps: list[PolicyStep],
    tool_meta: ToolMeta,
    params: dict[str, Any],
    context: ExecutionContext,
) -> PolicyDecision:
    """纯函数，无副作用，每层只能收紧不能放松"""
    current = PolicyDecision(action="allow", label="default")
    for step in steps:
        decision = step.evaluator(tool_meta, params, context)
        if _is_stricter(decision.action, current.action):
            current = decision
    return current
```

**2. ApprovalManager（有状态）**

```python
class ApprovalManager:
    def __init__(self, event_store: EventStoreProtocol):
        self._pending: dict[str, PendingApproval] = {}
        self._event_store = event_store

    async def register(self, request: ApprovalRequest) -> ApprovalRecord:
        """幂等注册，同 ID 返回已有 record"""
        ...

    async def wait_for_decision(self, approval_id: str, timeout_s: float = 120.0) -> ApprovalDecision | None:
        """asyncio.Event 异步等待"""
        ...

    def resolve(self, approval_id: str, decision: ApprovalDecision) -> bool:
        """原子决策，双写 Event Store"""
        ...

    def consume_allow_once(self, approval_id: str) -> bool:
        """原子消费一次性令牌"""
        ...
```

#### 优势

- **可测试性极佳**: Pipeline 是纯函数，可独立单元测试每一层
- **可追溯性**: 每个 PolicyDecision 附带 label，审计清晰
- **可扩展性**: 新增策略层只需实现 evaluator 函数 + 追加到 steps
- **与 Feature 004 契约完美对齐**: PolicyCheckHook 包装 Pipeline + ApprovalManager
- **竞品验证充分**: OpenClaw 在生产中验证了此模式

#### 劣势

- Pipeline 只处理**工具过滤**维度，不天然支持消息级 Guard（如 AgentStudio 的 rewrite 能力）
- 策略配置需要自行实现加载和热更新（无框架支持）

### 3.2 方案 B: Strategy + Provider 模式

**参考**: AgentStudio `preSendGuard`

#### 架构描述

```
PolicyEngine
  ├── ProviderRegistry（可插拔 Provider 注册表）
  │   ├── SideEffectProvider (副作用规则)
  │   ├── AllowlistProvider (白名单匹配)
  │   ├── HttpAuditProvider (外部审计服务)
  │   └── CustomProvider (用户自定义)
  │
  ├── GuardEvaluator（链式评估）
  │   ├── resolveProviders() 配置解析
  │   ├── evaluate() 链式调用 Provider
  │   └── normalizeResult() 结果标准化
  │
  └── PolicyCheckHook（BeforeHook 包装器）
```

#### 核心组件

```python
class PolicyProvider(Protocol):
    name: str
    async def evaluate(self, context: PolicyContext, config: ProviderConfig) -> ProviderResult:
        ...

class GuardEvaluator:
    def __init__(self):
        self._providers: dict[str, PolicyProvider] = {}

    def register_provider(self, provider: PolicyProvider):
        self._providers[provider.name] = provider

    async def evaluate(self, context: PolicyContext, config: GuardConfig) -> GuardResult:
        steps = []
        for provider_config in config.providers:
            provider = self._providers.get(provider_config.name)
            result = await provider.evaluate(context, provider_config)
            steps.append(StepResult(provider=provider_config.name, ...))
            if result.decision in ('block', 'require_confirm'):
                return GuardResult(blocked=True, steps=steps)
        return GuardResult(blocked=False, steps=steps)
```

#### 优势

- **强扩展性**: Provider 可插拔，支持外部审计服务（HTTP）
- **决策类型丰富**: 天然支持 allow/block/rewrite/require_confirm 四种
- **配置灵活**: 支持 global + per-agent + per-rule 三级配置解析

#### 劣势

- **过度工程化**: 对 OctoAgent 单用户场景来说，外部审计 Provider 和 HTTP 审计是 overkill
- **纯函数性弱**: Provider 链本质是异步 I/O 调用链，难以保证纯函数性
- **label 追踪较弱**: Provider 返回的审计信息（steps）不如 Pipeline label 直观
- **与 Feature 004 契约不如方案 A 贴合**: 需要额外适配层

### 3.3 方案对比表

| 维度 | 方案 A: 纯函数 Pipeline | 方案 B: Strategy + Provider |
|------|----------------------|---------------------------|
| **可测试性** | 极佳（纯函数，可独立测试每层） | 良好（需 mock Provider） |
| **可追溯性** | 极佳（label 逐层追踪） | 良好（steps 审计） |
| **性能** | 极佳（同步纯函数，无 I/O） | 良好（异步 Provider 链有网络开销） |
| **可维护性** | 优秀（逻辑集中，层清晰） | 良好（Provider 分散，需注册管理） |
| **扩展性** | 良好（新增层 = 新增 evaluator） | 极佳（新增 Provider = 新增类） |
| **学习曲线** | 低（纯函数 + dataclass） | 中（Protocol + Registry + Config） |
| **社区验证** | OpenClaw 生产验证 | AgentStudio 生产验证 |
| **Feature 004 对齐** | 完美对齐 PolicyCheckpoint Protocol | 需适配层 |
| **Blueprint 对齐** | 完美对齐 8.6.4 四层 Pipeline | 部分对齐 |
| **Constitution C4 对齐** | 直接满足 Two-Phase 要求 | 需额外封装审批流 |
| **M1 交付风险** | 低（scope 清晰） | 中（Provider 抽象引入额外复杂度） |

### 3.4 推荐方案

**推荐方案 A: 纯函数 Pipeline 模式**，理由：

1. **Blueprint 明确指定**: 8.6.4 节明确定义了 4 层 Pipeline（Profile -> Global -> Agent -> Group），方案 A 是其直接映射
2. **OpenClaw 深度验证**: 最成熟的竞品选择了同一模式，证明在 personal assistant 场景下可行
3. **与 Feature 004 契约无缝集成**: PolicyCheckpoint Protocol 和 BeforeHook Protocol 天然匹配
4. **M1 scope 最小化**: 仅需实现 Layer 1 + Layer 2 + ApprovalManager，工程量可控
5. **可测试性优势**: 纯函数 evaluator 可覆盖 100% 边界条件

**方案 B 的扩展性优势保留路径**: M2+ 可在 Layer 3/4 中引入 Provider 模式的元素（如 per-agent 白名单可配置为外部服务调用），无需重构 Pipeline 主干。

---

## 4. 依赖库评估

### 4.1 核心依赖（无新增 — 全部复用现有技术栈）

Feature 006 的技术方案**不需要引入新的第三方依赖**，全部基于项目现有技术栈实现：

| 依赖 | 用途 | 版本要求 | 已有/新增 |
|------|------|---------|----------|
| **Python 3.12+** | asyncio.Event, match/case, StrEnum | 3.12+ | 已有 |
| **Pydantic v2** | PolicyDecision, ApprovalRequest 数据模型 | >=2.0 | 已有 |
| **FastAPI** | Approvals REST API, SSE 端点 | >=0.100 | 已有 |
| **sse-starlette** | SSE 事件推送 | >=1.0 | 已有（M0 SSE 基础设施） |
| **SQLite WAL** | Event Store 持久化审批事件 | Python stdlib | 已有 |
| **structlog** | PolicyEngine 决策日志 | >=23.0 | 已有 |
| **Logfire** | OTel trace 审批流程 | >=1.0 | 已有 |

### 4.2 前端依赖评估

| 依赖 | 用途 | 评估 |
|------|------|------|
| **React 18+** | Approvals 面板 + Chat UI | 已有（Blueprint 确定） |
| **Vite** | 前端构建 | 已有（Blueprint 确定） |
| **EventSource API** | SSE 客户端 | 浏览器原生，无需依赖 |
| **TailwindCSS** | UI 样式 | [推断] 基于 Blueprint React+Vite 组合的常见搭配 |

### 4.3 关键评估：asyncio 原生 vs 第三方异步库

**结论**: 直接使用 Python 3.12 标准库的 `asyncio.Event` + `asyncio.get_event_loop().call_later()`，不引入任何第三方异步原语。

理由：
- `asyncio.Event` 完全满足 Two-Phase Approval 的等待需求
- `call_later` 满足超时定时器需求
- OctoAgent 是单进程单 event loop 架构（FastAPI + Uvicorn），不需要跨进程同步
- 引入 Redis/Celery 等分布式原语会违反 Blueprint "先单机打牢"的设计原则

---

## 5. 设计模式推荐

### 5.1 Chain of Responsibility（职责链）— Policy Pipeline

**适用场景**: 多层策略逐级评估，每层可独立判断并传递给下层

**在 Feature 006 中的应用**:

```python
# 每个 PolicyStep 是职责链中的一个处理器
steps = [
    PolicyStep(evaluator=profile_filter, label="tools.profile"),
    PolicyStep(evaluator=global_rule, label="global.side_effect"),
    # M2+ 扩展
    # PolicyStep(evaluator=agent_policy, label="agent.{id}.tools"),
    # PolicyStep(evaluator=group_policy, label="group.{id}.tools"),
]
```

**案例参考**: OpenClaw `applyToolPolicyPipeline()` — 7 层 cascade 策略

### 5.2 Observer/Event-Driven（观察者/事件驱动）— 审批状态变更通知

**适用场景**: 审批决策完成后通知多个消费者（前端 SSE、Task 状态机、Event Store）

**在 Feature 006 中的应用**:

```python
# ApprovalManager 解决审批时：
# 1. 设置 asyncio.Event (通知等待者)
# 2. 写入 Event Store (持久化)
# 3. 推送 SSE 事件 (前端更新)
def resolve(self, approval_id, decision):
    self._pending[approval_id].event.set()           # 通知 awaiter
    self._event_store.append(ApprovalEvent(...))      # 持久化
    self._sse_broadcaster.send(approval_resolved(...)) # SSE 推送
```

### 5.3 Two-Phase Commit（两阶段提交）— Approval 生命周期

**适用场景**: 审批注册和决策的原子性保证

**在 Feature 006 中的应用**: 直接借鉴 OpenClaw `ExecApprovalManager` 的 register + resolve 分离模式。

### 5.4 Strategy（策略模式）— PolicyStep Evaluator

**适用场景**: 不同策略层使用不同的评估策略

**在 Feature 006 中的应用**:

```python
# Layer 1: Profile 过滤策略
def profile_filter(tool_meta, params, context) -> PolicyDecision:
    if not _profile_allows(tool_meta.tool_profile, context.profile):
        return PolicyDecision(action="deny", label="tools.profile", reason="profile mismatch")
    return PolicyDecision(action="allow", label="tools.profile")

# Layer 2: Global 规则策略
def global_rule(tool_meta, params, context) -> PolicyDecision:
    match tool_meta.side_effect_level:
        case SideEffectLevel.IRREVERSIBLE:
            return PolicyDecision(action="ask", label="global.irreversible")
        case SideEffectLevel.REVERSIBLE:
            return PolicyDecision(action="allow", label="global.reversible")
        case SideEffectLevel.NONE:
            return PolicyDecision(action="allow", label="global.readonly")
```

### 5.5 Adapter（适配器）— PolicyCheckHook

**适用场景**: 将 PolicyEngine 的 Pipeline 接口适配为 Feature 004 的 BeforeHook Protocol

**在 Feature 006 中的应用**: 见 Feature 004 契约文档 11.2 节的 `PolicyCheckHook` 示例。

### 5.6 适用性与风险评估

| 模式 | 适用性 | 风险 |
|------|--------|------|
| Chain of Responsibility | 高 — Policy Pipeline 核心 | 低 — 成熟模式 |
| Observer/Event-Driven | 高 — 审批状态通知 | 中 — SSE 断线重连需处理 |
| Two-Phase Commit | 高 — Approval 生命周期 | 中 — asyncio.Event 在进程重启后丢失 |
| Strategy | 高 — 可插拔 evaluator | 低 — 函数即策略 |
| Adapter | 高 — BeforeHook 包装 | 低 — 接口明确 |

---

## 6. 技术风险清单

### 6.1 高风险

#### R1: asyncio.Event 在进程重启后丢失（概率: 高, 影响: 高）

**描述**: ApprovalManager 使用 `asyncio.Event` 作为等待原语。进程崩溃/重启后，所有 pending Event 丢失，等待中的审批请求无法恢复。

**缓解策略**:
1. **双写**: ApprovalManager 每次 register/resolve 操作同时写入 Event Store
2. **启动恢复**: 进程重启时扫描 Event Store 中状态为 APPROVAL_REQUESTED（无 APPROVED/REJECTED/EXPIRED 配对）的事件，重建 pending 状态
3. **超时兜底**: 所有审批请求设置超时（默认 120s），超时后自动 deny + 记录 APPROVAL_EXPIRED 事件

**验收条件**: 审批注册后立即 kill 进程，重启后 GET /api/approvals 能返回该 pending 审批

#### R2: Policy Pipeline 与 ToolBroker Hook Chain 的集成竞态（概率: 中, 影响: 高）

**描述**: PolicyCheckHook 作为 BeforeHook 注册到 ToolBroker。当 PolicyPipeline 返回 `ask` 时，需要异步等待审批决策。但 BeforeHook 的接口是 `async def before_execute() -> BeforeHookResult`，等待审批会长时间阻塞 hook 链。

**缓解策略**:
1. **BeforeHookResult 扩展**: 在 `BeforeHookResult` 中增加 `requires_approval: bool` 和 `approval_id: str | None` 字段
2. **ToolBroker 感知审批**: ToolBroker.execute() 检测到 `requires_approval=True` 时，不执行工具，返回 `ToolResult(is_error=False, output="approval_pending", approval_ref=approval_id)`
3. **调用方处理**: Skill Runner / Worker 收到 approval_pending 结果后，进入 WAITING_APPROVAL 状态，等待 ApprovalManager 通知

**替代方案**: PolicyCheckHook 内部直接 await ApprovalManager.wait_for_decision()，将审批等待封装在 hook 内部。这样 ToolBroker 不需要感知审批概念。OpenClaw 采用此方案。

**推荐**: 采用替代方案（hook 内部等待），原因：
- 减少对 Feature 004 契约的变更
- OpenClaw 已验证此模式
- 审批等待不应是 ToolBroker 的关注点

#### R3: WAITING_APPROVAL 状态与 Task 状态机冲突（概率: 中, 影响: 中）

**描述**: Blueprint 定义 Task 状态为 `RUNNING -> WAITING_APPROVAL -> RUNNING`。但当前 Task 状态机的转换规则是否允许此循环需要验证。

**缓解策略**:
1. **状态转换规则扩展**: 在 Task 状态机中增加 `RUNNING -> WAITING_APPROVAL` 和 `WAITING_APPROVAL -> RUNNING / REJECTED` 转换规则
2. **事件驱动**: 状态转换通过事件触发（APPROVAL_REQUESTED -> WAITING_APPROVAL, APPROVED -> RUNNING, REJECTED -> REJECTED）
3. **可选 PAUSED 复用**: 如果 WAITING_APPROVAL 语义与 PAUSED 接近，可考虑复用 PAUSED 状态 + metadata 区分。但 Blueprint 明确区分了两者（PAUSED 是用户主动暂停，WAITING_APPROVAL 是策略触发），建议保持独立状态。

### 6.2 中风险

#### R4: SSE 实时推送审批状态变更的可靠性（概率: 中, 影响: 中）

**描述**: 前端通过 SSE 接收审批状态变更。SSE 连接可能断开（网络切换、浏览器休眠），导致用户错过审批通知。

**缓解策略**:
1. **SSE 断线重连**: 使用 `Last-Event-ID` 机制，重连后从断点续传
2. **轮询兜底**: 前端定期 GET /api/approvals 刷新待审批列表（间隔 30s）
3. **Event Store 为真**: SSE 仅是通知通道，前端始终以 API 响应为事实来源

#### R5: 审批超时与 Task 超时的协调（概率: 中, 影响: 中）

**描述**: 审批超时（默认 120s）和 Task 级别的超时可能冲突。例如 Task 设置了 60s 超时，但审批等待需要 120s。

**缓解策略**:
1. **审批超时独立**: 审批超时由 ApprovalManager 管理，不受 Task 超时影响
2. **Task 超时暂停**: Task 进入 WAITING_APPROVAL 状态时，Task 超时计时器暂停
3. **审批超时可配**: 支持 global 和 per-tool 两级审批超时配置

#### R6: 前端 Chat UI SSE 流式输出的性能（概率: 低, 影响: 中）

**描述**: Chat UI 需要同时处理 LLM 流式输出和审批状态推送，两个 SSE 流的并发可能导致 UI 卡顿。

**缓解策略**:
1. **单 SSE 连接**: 合并所有事件类型到同一 SSE 流（`/stream/task/{task_id}`），通过 `event` 字段区分类型
2. **RAF 节流**: 前端使用 `requestAnimationFrame` 节流 SSE 消息的 UI 更新（参考 AgentStudio 的 60fps RAF 节流方案）
3. **消息批处理**: 高频事件（如 LLM token 流）本地累积，按帧批量渲染

### 6.3 低风险

#### R7: Policy 配置热更新（概率: 低, 影响: 低）

**描述**: M1 阶段 Policy 配置为代码内静态规则。后续支持配置文件/API 动态更新时，需要确保更新不影响正在执行的策略评估。

**缓解策略**: Pipeline 是纯函数，每次评估传入当前配置快照。配置更新在下次评估时生效，无竞态风险。

#### R8: 前端 React 状态管理复杂度（概率: 低, 影响: 低）

**描述**: Approvals 面板需要实时更新，Chat UI 需要流式渲染，两者的状态管理可能交叉干扰。

**缓解策略**: Approvals 和 Chat 使用独立的 React state（或 Zustand store），通过 SSE 事件分发到各自的 store，互不干扰。

---

## 7. 需求-技术对齐度评估

### 7.1 功能覆盖检查

| 需求交付物 | 技术方案覆盖 | 风险 |
|-----------|-------------|------|
| 多层 Policy Pipeline | 方案 A 完整覆盖（M1: Layer 1+2, M2: Layer 3+4） | 低 |
| Two-Phase Approval | ApprovalManager 完整覆盖（register + wait + resolve + consume） | 中（R1 进程重启） |
| PolicyEngine 核心 | PolicyCheckHook 包装 Pipeline + ApprovalManager | 中（R2 集成竞态） |
| 审批工作流 | Event Store 事件 + Task 状态机扩展 | 中（R3 状态机） |
| Approvals REST API | FastAPI 路由 + ApprovalManager 接口 | 低 |
| 前端 Approvals + Chat UI | React + SSE EventSource | 中（R4 SSE 可靠性） |

### 7.2 Constitution 约束检查

| Constitution 原则 | Feature 006 对齐 | 状态 |
|------------------|-----------------|------|
| C1: Durability First | 审批状态双写 Event Store + 启动恢复 | 满足 |
| C2: Everything is an Event | APPROVAL_REQUESTED / APPROVED / REJECTED / EXPIRED 四类事件 | 满足 |
| C3: Tools are Contracts | 复用 Feature 004 的 ToolMeta + PolicyCheckpoint Protocol | 满足 |
| C4: Side-effect Two-Phase | PolicyPipeline(ask) -> ApprovalManager(register -> wait -> resolve) -> execute | 满足 |
| C5: Least Privilege | Layer 1 ToolProfile 过滤 + Layer 2 side_effect 规则 | 满足 |
| C6: Degrade Gracefully | PolicyCheckHook fail_mode="closed"（不可降级）；SSE 断线有轮询兜底 | 满足 |
| C7: User-in-Control | Approvals 面板三按钮（Allow Once / Always / Deny）+ 超时策略 + Policy Profile 可配 | 满足 |
| C8: Observability | 每个决策附 label + Event Store 完整审计 + Logfire trace | 满足 |

### 7.3 技术方案可能限制需求扩展的地方

1. **M2 Agent 级策略**: M1 的 Pipeline 只有 2 层。扩展 Layer 3/4 时需要定义 Agent/Group 策略的配置 schema 和加载机制。纯函数 Pipeline 架构可平滑扩展，但配置管理是额外工作。

2. **Telegram 渠道审批**: M1 仅实现 Web UI 审批。Telegram inline keyboard 审批需要在 M2 实现 Telegram Channel Adapter 时同步实现 `/approve` 回调处理。技术上无障碍，但需要同步设计 Telegram 的审批 UX。

3. **allow-always 白名单持久化**: OpenClaw 的 `allow-always` 决策会将命令添加到 allowlist 文件持久化。OctoAgent 需要在 M1.5 实现 Safe Bins 白名单持久化（写入 SQLite 或 JSON 配置），否则每次进程重启 allow-always 失效。

---

## 8. 推荐技术架构总结

### 8.1 模块划分

```
packages/policy/                     # Feature 006 核心
  __init__.py
  models.py                          # PolicyDecision, ApprovalRequest, ApprovalRecord, ApprovalDecision
  pipeline.py                        # PolicyPipeline (纯函数)
  evaluators/
    __init__.py
    profile_filter.py                # Layer 1: ToolProfile 过滤
    global_rule.py                   # Layer 2: side_effect 驱动规则
  approval_manager.py                # ApprovalManager (有状态)
  policy_check_hook.py               # PolicyCheckHook (BeforeHook 适配器)
  policy_engine.py                   # PolicyEngine 门面 (组合 Pipeline + ApprovalManager)

apps/gateway/
  routes/
    approvals.py                     # POST /api/approve/{id}, GET /api/approvals
  sse/
    approval_events.py               # SSE 审批事件推送

frontend/src/
  components/
    ApprovalPanel/                   # Approvals 面板组件
    ChatUI/                          # Chat UI 组件 (SSE 流式)
  hooks/
    useApprovals.ts                  # SSE + 轮询审批状态
    useChatStream.ts                 # SSE 流式 Chat
```

### 8.2 EventType 扩展

```python
class EventType(StrEnum):
    # ... 现有值 ...
    # Feature 004
    TOOL_CALL_STARTED = "TOOL_CALL_STARTED"
    TOOL_CALL_COMPLETED = "TOOL_CALL_COMPLETED"
    TOOL_CALL_FAILED = "TOOL_CALL_FAILED"

    # Feature 006: 审批事件
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_APPROVED = "APPROVAL_APPROVED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"

    # Feature 006: 策略事件
    POLICY_DECISION = "POLICY_DECISION"
```

### 8.3 关键数据模型

```python
class PolicyDecision(BaseModel):
    """策略决策结果"""
    action: Literal["allow", "ask", "deny"]
    label: str          # 决策来源追踪（如 "global.irreversible"）
    reason: str = ""
    tool_name: str = ""
    side_effect_level: SideEffectLevel | None = None

class ApprovalRequest(BaseModel):
    """审批请求"""
    approval_id: str
    task_id: str
    tool_name: str
    tool_args_summary: str    # 参数摘要（脱敏后）
    risk_explanation: str
    policy_label: str         # 触发审批的策略层 label
    expires_at: datetime

class ApprovalDecision(StrEnum):
    ALLOW_ONCE = "allow-once"
    ALLOW_ALWAYS = "allow-always"
    DENY = "deny"
```

### 8.4 关键集成接口

```python
# PolicyCheckHook 内部等待审批的核心流程（推荐方案）
class PolicyCheckHook:
    async def before_execute(self, tool_meta, args, context) -> BeforeHookResult:
        # Step 1: Pipeline 评估
        decision = evaluate_pipeline(self._steps, tool_meta, args, context)

        # Step 2: 处理决策
        match decision.action:
            case "allow":
                return BeforeHookResult(proceed=True)
            case "deny":
                return BeforeHookResult(proceed=False, rejection_reason=decision.reason)
            case "ask":
                # Step 3: 注册审批
                record = await self._approval_mgr.register(ApprovalRequest(
                    approval_id=str(uuid4()),
                    task_id=context.task_id,
                    tool_name=tool_meta.name,
                    ...
                ))
                # Step 4: 等待决策（阻塞在 hook 内部）
                approval = await self._approval_mgr.wait_for_decision(
                    record.approval_id, timeout_s=120.0
                )
                if approval and approval in (ApprovalDecision.ALLOW_ONCE, ApprovalDecision.ALLOW_ALWAYS):
                    self._approval_mgr.consume_allow_once(record.approval_id)
                    return BeforeHookResult(proceed=True)
                return BeforeHookResult(proceed=False, rejection_reason="approval denied or expired")
```

---

## 9. 后续建议（供产研汇总参考）

1. **Feature 004 契约确认**: 确认 `BeforeHookResult` 是否需要扩展 `approval_ref` 字段，或 hook 内部自行等待审批（推荐后者）
2. **Task 状态机扩展评审**: WAITING_APPROVAL 状态转换规则需与 core 模块的 Task 状态机设计评审对齐
3. **前端 Chat UI 范围确认**: M1 Chat UI 是最小 MVP（纯文本 + 流式输出），还是需要支持 Markdown 渲染 + 代码高亮？
4. **Safe Bins 白名单 scope**: allow-always 审批结果持久化范围是 global 还是 per-project？
5. **Telegram 审批 UX**: 虽然 M2 才实现 Telegram 渠道，但审批数据模型需提前考虑多渠道兼容（审批请求中包含 `channel_hint` 字段）

---

## 附录 A: 竞品源码索引

| 文件 | 项目 | 核心概念 | 借鉴程度 |
|------|------|---------|---------|
| `src/agents/tool-policy-pipeline.ts` | OpenClaw | 7 层 Cascade 策略 + label 追踪 | 高 |
| `src/gateway/exec-approval-manager.ts` | OpenClaw | Two-Phase Approval + 幂等注册 + 15s 宽限期 | 高 |
| `src/infra/exec-approvals.ts` | OpenClaw | ExecSecurity/ExecAsk 类型 + requiresExecApproval() | 高 |
| `src/node-host/exec-policy.ts` | OpenClaw | evaluateSystemRunPolicy() 决策矩阵 | 高 |
| `src/agents/bash-tools.exec-approval-request.ts` | OpenClaw | registerExecApprovalRequest() 双阶段 | 高 |
| `src/channels/command-gating.ts` | OpenClaw | 三模式门禁 (allow/deny/configured) | 中 |
| `backend/src/services/preSendGuard/index.ts` | AgentStudio | Provider Pipeline + 四种决策 | 中 |
| `backend/src/services/preSendGuard/configResolver.ts` | AgentStudio | default + rules 配置解析 | 低 |
| `backend/src/services/preSendGuard/providers/httpAuditProvider.ts` | AgentStudio | HTTP 外部审计 Provider | 低（M2+） |
| `agent.py` (L344-849) | Agent Zero | InterventionException + 轮询暂停 | 低（反模式参考） |
| `pydantic_ai/toolsets/approval_required.py` | Pydantic AI | ApprovalRequiredToolset + raise ApprovalRequired | 中 |
| `docs/deferred-tools.md` | Pydantic AI | DeferredToolRequests 审批续传模式 | 中 |

## 附录 B: 必须避免的反模式

| # | 反模式 | 来源 | OctoAgent 对策 |
|---|--------|------|---------------|
| 1 | 审批状态纯内存存储 | Agent Zero | 双写 Event Store + 启动恢复 |
| 2 | `asyncio.sleep(0.1)` 轮询等待 | Agent Zero | `asyncio.Event` 事件驱动 |
| 3 | 枚举值部分实现 | AgentStudio `require_confirm` | `match/case` + `assert_never()` 全覆盖 |
| 4 | Provider 链无短路 | AgentStudio (block 后继续评估) | Pipeline 遇到 deny 立即返回 |
| 5 | 审批无超时 | 自行设计 | 默认 120s 超时 + APPROVAL_EXPIRED 事件 |
