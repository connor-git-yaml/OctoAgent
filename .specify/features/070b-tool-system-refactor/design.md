# Feature 070: 工具系统简化重构

> **状态**: 设计草案
> **上游**: Blueprint §8.5 / §8.6 / §9.8, Feature 061 (统一权限), Feature 064/068 (并行执行)
> **参考**: Claude Code · Agent Zero · OpenClaw 架构分析

---

## 1. 现状诊断

### 1.1 当前权限决策链（过于复杂）

```
ToolBroker.execute()
  │
  ├── [priority=0] PolicyCheckHook ← PolicyEngine (profile_filter + global_rule)
  │     └── ASK → 内部调用 ApprovalManager.register + wait_for_decision
  │
  ├── [priority=10] ApprovalOverrideHook ← 查 always 缓存（仅打标记，不拦截）
  │
  ├── [priority=20] PresetBeforeHook ← PRESET_POLICY 矩阵
  │     ├── 内部再查一次 override 缓存（与 ApprovalOverrideHook 冗余）
  │     ├── 路径感知升级（workspace 外 → IRREVERSIBLE）
  │     └── ASK → 返回 "ask:preset_denied:..." 前缀错误
  │
  └── SkillRunner._handle_ask_bridge()
        └── 识别 "ask:" 前缀 → 桥接到 ApprovalBridge → 重新执行
```

**问题清单**：

| # | 问题 | 根因 |
|---|------|------|
| P1 | **三套 Hook 做同一件事** | PolicyCheckHook 和 PresetBeforeHook 都在判断 allow/ask，只是判断依据不同 |
| P2 | **`ask:` 前缀桥接** | PresetBeforeHook 无法直接做审批，只能通过字符串前缀让上层 SkillRunner 二次桥接 |
| P3 | **override 缓存被查两次** | ApprovalOverrideHook 查一次（打标记），PresetBeforeHook 又查一次（实际跳过） |
| P4 | **PolicyPipeline 形同虚设** | 两层评估器（profile_filter + global_rule）效果等价于一次矩阵查表 |
| P5 | **ToolProfile 残留 30+ 文件** | deprecated 但 ToolMeta 仍是必填字段，所有工具注册都要传 |
| P6 | **两套概念并存** | ToolProfile(3 值) + PermissionPreset(3 值) + SideEffectLevel(3 值) + PolicyAction(3 值) + PresetDecision(2 值) = 5 个枚举做 1 件事 |
| P7 | **REVERSIBLE/IRREVERSIBLE 全串行** | 无冲突的独立写操作（写不同文件）也被迫串行 |
| P8 | **IRREVERSIBLE 多工具逐个审批** | 5 个待审批工具 = 用户点 5 次 |
| P9 | **无并发度上限** | NONE 桶 asyncio.gather 无 Semaphore |

### 1.2 对标 Claude Code 的启示

Claude Code 的权限模型本质上只有一个函数：

```typescript
function shouldAllow(tool, mode, alwaysAllowList): "allow" | "ask" {
  if (tool.name in alwaysAllowList) return "allow"
  if (tool.isReadOnly) return "allow"
  if (mode == "bypassPermissions") return "allow"
  return "ask"   // 弹 TUI 问用户
}
```

它够用的原因：**用户始终在场**，出问题 Ctrl+C 立即中断。

OctoAgent 的唯一额外需求：**Agent 可能在用户不看的时候自主运行**（Telegram 触发、Cron 定时、后台 Worker）。这需要：
1. 危险操作不能静默执行 → 需要审批
2. 不同 Agent 权限不同 → 需要权限分级
3. 审批持久化（进程可能重启）→ 需要 always 覆盖存储

**但只需要这些。不需要 Policy Pipeline、不需要三套 Hook、不需要 ToolProfile。**

---

## 2. 目标：一个函数搞定权限

### 2.1 核心模型精简

**删除**：
- `ToolProfile` 枚举及全部引用（30+ 文件）
- `profile_allows()` 函数
- `PolicyCheckHook` + `ApprovalOverrideHook` + `PresetBeforeHook` 三个 Hook 类
- `PolicyPipeline`（profile_filter + global_rule 两个 evaluator）
- `PolicyProfile`（DEFAULT/STRICT/PERMISSIVE 三个预设）
- `PolicyEngine` 门面类（Pipeline + Hook + ApprovalManager 的组合）
- `_handle_ask_bridge()` 桥接逻辑
- `PROFILE_LEVELS` 映射、`TOOL_PROFILE_TO_PRESET` 映射

**保留**：
- `SideEffectLevel`（NONE / REVERSIBLE / IRREVERSIBLE）— 工具契约的核心，不动
- `PermissionPreset`（MINIMAL / NORMAL / FULL）— Agent 权限分级，不动
- `PRESET_POLICY` 矩阵 + `preset_decision()` — 这就是最终的决策函数
- `ApprovalManager` — 审批状态机，功能合理，保留
- `ApprovalOverrideStore`（Cache + Repository）— always 覆盖持久化，保留
- `ToolTier`（CORE / DEFERRED）— 工具分层加载，不动
- `ToolMeta`（删除 `tool_profile` 字段）
- `ExecutionContext`（删除 `profile` 字段）

### 2.2 简化后的权限决策

```python
# === 整个权限系统的核心：一个函数 ===

async def check_permission(
    tool_meta: ToolMeta,
    args: dict[str, Any],
    ctx: ExecutionContext,
    override_store: ApprovalOverrideStore,
    approval_manager: ApprovalManager,
) -> PermissionResult:
    """工具权限决策。

    四步短路，命中即返回：
    1. always 覆盖 → 放行
    2. 有效副作用等级（含路径升级）+ Preset 矩阵 → ALLOW → 放行
    3. 矩阵返回 ASK → 发起审批等待
    4. 审批结果 → 放行 / 拒绝
    """

    # Step 1: always 覆盖快速路径
    if override_store.has(ctx.agent_runtime_id, tool_meta.name):
        return PermissionResult(allowed=True, reason="always_override")

    # Step 2: Preset 矩阵查表
    effective_sel = _effective_side_effect(tool_meta, args, ctx)
    decision = preset_decision(ctx.permission_preset, effective_sel)

    if decision == PresetDecision.ALLOW:
        return PermissionResult(allowed=True, reason="preset_allow")

    # Step 3: 需要审批
    approval_decision = await approval_manager.request_and_wait(
        tool_name=tool_meta.name,
        tool_args_summary=_summarize_args(args),
        side_effect_level=effective_sel,
        ctx=ctx,
    )

    # Step 4: 审批结果
    if approval_decision == ApprovalDecision.ALLOW_ONCE:
        return PermissionResult(allowed=True, reason="approved_once")
    elif approval_decision == ApprovalDecision.ALLOW_ALWAYS:
        override_store.set(ctx.agent_runtime_id, tool_meta.name)
        return PermissionResult(allowed=True, reason="approved_always")
    else:
        return PermissionResult(allowed=False, reason="denied")


def _effective_side_effect(
    tool_meta: ToolMeta,
    args: dict[str, Any],
    ctx: ExecutionContext,
) -> SideEffectLevel:
    """计算参数感知的有效 SideEffectLevel。

    当前唯一规则：filesystem 工具访问 workspace 外路径 → 升级为 IRREVERSIBLE。
    后续如有新规则，在此函数内追加，不需要新增抽象层。
    """
    sel = tool_meta.side_effect_level

    # 路径感知升级（从 PresetBeforeHook 迁移过来的逻辑）
    if tool_meta.name in _FILESYSTEM_PATH_TOOLS:
        path_arg = args.get("path") or args.get("directory")
        if path_arg and not _is_within_workspace(path_arg, ctx):
            sel = SideEffectLevel.IRREVERSIBLE

    return sel
```

### 2.3 集成到 ToolBroker

**不再使用 Hook Chain 做权限决策**。权限检查直接内联到 `ToolBroker.execute()`。

```python
# broker.py 简化后

class ToolBroker:
    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        ctx: ExecutionContext,
    ) -> ToolResult:
        # 1. 查找工具
        meta, handler = self._registry[tool_name]

        # 2. 事件：开始
        self._emit_started(meta, args, ctx)

        # 3. 权限检查（替代三套 Hook）
        perm = await check_permission(
            meta, args, ctx,
            self._override_store,
            self._approval_manager,
        )
        if not perm.allowed:
            self._emit_failed(meta, args, ctx, reason=perm.reason)
            return ToolResult(is_error=True, error=f"权限拒绝: {perm.reason}")

        # 4. 执行工具（带超时）
        try:
            result = await self._invoke_handler(handler, args, meta.timeout_seconds)
        except Exception as e:
            self._emit_failed(meta, args, ctx, reason=str(e))
            return ToolResult(is_error=True, error=str(e))

        # 5. after hooks（仅保留 LargeOutputHandler 等非权限类 hook）
        result = await self._run_after_hooks(meta, args, ctx, result)

        # 6. 事件：完成
        self._emit_completed(meta, args, ctx, result)
        return result
```

**Hook Chain 不再承载权限逻辑**。BeforeHook/AfterHook 机制保留，但仅用于可观测性（LargeOutputHandler、自定义 metrics 等），不用于权限决策。权限是系统内核逻辑，不应该可被外部 Hook 影响或绕过。

### 2.4 SkillRunner 简化

删除 `_handle_ask_bridge()`。审批在 `check_permission` 内完成（阻塞等待），ToolBroker 返回的 ToolResult 已经是最终结果，SkillRunner 不需要二次桥接。

```python
# runner.py 简化后的 _execute_single_tool

async def _execute_single_tool(
    self, call: ToolCall, ctx: SkillExecutionContext,
) -> ToolCallFeedback:
    exec_ctx = self._build_exec_context(ctx)
    result = await self._broker.execute(call.tool_name, call.tool_args, exec_ctx)
    return self._build_feedback(call, result)
    # 没有了。不需要 ask bridge、不需要前缀检测、不需要重试。
```

---

## 3. 并发执行简化

### 3.1 当前问题

当前三桶策略（runner.py L395-516）实现合理，但有两个改进空间：

1. **REVERSIBLE 全串行过于保守** — `write("a.txt")` 和 `write("b.txt")` 没有数据依赖却串行
2. **无并发度上限** — NONE 桶 `asyncio.gather` 无 Semaphore，极端场景可能爆炸
3. **IRREVERSIBLE 逐个审批** — 用户体验差

### 3.2 简化方案：两桶 + 信号量

不需要 ToolCallPlanner、ExecutionPlan、ExecutionGroup、ConflictDetector 这些抽象。直接改 `_execute_tool_calls`。

```python
# runner.py 简化后的 _execute_tool_calls

_MAX_CONCURRENCY = 10

async def _execute_tool_calls(
    self,
    tool_calls: list[ToolCall],
    ctx: SkillExecutionContext,
) -> list[ToolCallFeedback]:
    if not tool_calls:
        return []

    # 分两桶：auto（会被自动放行的）和 gated（可能需要审批的）
    auto_calls: list[ToolCall] = []
    gated_calls: list[ToolCall] = []

    for call in tool_calls:
        meta = self._broker.get_tool_meta(call.tool_name)
        if meta and meta.side_effect_level == SideEffectLevel.NONE:
            auto_calls.append(call)
        else:
            gated_calls.append(call)

    results: dict[str, ToolCallFeedback] = {}

    # Auto 桶：带信号量并行
    if auto_calls:
        sem = asyncio.Semaphore(_MAX_CONCURRENCY)
        async def _guarded(c: ToolCall) -> ToolCallFeedback:
            async with sem:
                return await self._execute_single_tool(c, ctx)

        auto_results = await asyncio.gather(
            *[_guarded(c) for c in auto_calls],
            return_exceptions=True,
        )
        for call, result in zip(auto_calls, auto_results):
            if isinstance(result, BaseException):
                results[call.call_id] = self._error_feedback(call, result)
            else:
                results[call.call_id] = result

    # Gated 桶：串行（审批在 broker.execute 内完成，不需要桥接）
    for call in gated_calls:
        fb = await self._execute_single_tool(call, ctx)
        results[call.call_id] = fb
        if fb.skip_remaining:
            break

    # 按原始顺序返回
    return [results[c.call_id] for c in tool_calls if c.call_id in results]
```

### 3.3 后续可选优化（不在本次范围）

以下改进等真实遇到痛点再做，不预设：

| 优化 | 触发条件 | 做法 |
|------|---------|------|
| REVERSIBLE 并行 | 实际出现多个无冲突写操作串行延迟的 case | 在 `@tool_contract` 增加 `resource_keys` 声明，分桶时做冲突检测 |
| 批量审批 | 用户反馈 IRREVERSIBLE 多工具逐个审批太烦 | ApprovalManager 增加 `batch_request` 接口 |
| 流式派发 | LiteLLM 流式 tool_use 解析稳定后 | NONE 桶在流式响应中逐个完成后立即执行 |
| 参数级风险评估 | 需要区分 `rm temp.txt` 和 `rm -rf /` 的 case | 在 `_effective_side_effect` 中追加规则 |
| 频率限制 | Agent 实际出现短时间大量调用 | 在 `check_permission` 前加 rate limit 检查 |
| 细粒度 always | 用户希望 always allow `ls` 但不 always allow `rm` | override_store 增加 pattern 匹配 |

---

## 4. 完整架构：简化后

### 4.1 工具执行链路

```
LLM Output (tool_calls[])
    │
    ▼
SkillRunner._execute_tool_calls()
    │
    ├── 分两桶: auto (SEL=NONE) / gated (SEL≠NONE)
    │
    ├── Auto 桶: asyncio.gather + Semaphore(10)
    │       │
    │       └── ToolBroker.execute()
    │             ├── check_permission() → always/preset → ALLOW
    │             ├── handler(args) + timeout
    │             └── emit events
    │
    └── Gated 桶: 串行
            │
            └── ToolBroker.execute()
                  ├── check_permission()
                  │     ├── always 覆盖 → ALLOW
                  │     ├── preset 矩阵 → ALLOW
                  │     └── preset 矩阵 → ASK
                  │           └── ApprovalManager.request_and_wait()
                  │                 ├── 用户 approve → 执行
                  │                 ├── 用户 always → 写覆盖 + 执行
                  │                 └── 用户 deny → 返回拒绝
                  │
                  ├── handler(args) + timeout
                  └── emit events
```

### 4.2 概念对照表

| 简化前 | 简化后 | 说明 |
|--------|--------|------|
| `ToolProfile` (MINIMAL/STANDARD/PRIVILEGED) | **删除** | 被 PermissionPreset 完全取代 |
| `profile_allows()` | **删除** | 被 `preset_decision()` 取代 |
| `PolicyCheckHook` (priority=0) | **删除** | 逻辑合并到 `check_permission()` |
| `ApprovalOverrideHook` (priority=10) | **删除** | 逻辑合并到 `check_permission()` Step 1 |
| `PresetBeforeHook` (priority=20) | **删除** | 逻辑合并到 `check_permission()` Step 2 |
| `PolicyPipeline` (profile_filter + global_rule) | **删除** | `preset_decision()` 一次矩阵查表等价 |
| `PolicyProfile` (DEFAULT/STRICT/PERMISSIVE) | **删除** | 不再需要，PermissionPreset 直接表达 |
| `PolicyEngine` | **删除** | 门面类，组合的子系统都删了 |
| `_handle_ask_bridge()` | **删除** | 审批在 `check_permission` 内完成 |
| `"ask:preset_denied:"` 前缀协议 | **删除** | 不存在了 |
| `SideEffectLevel` | **保留** | 工具契约核心 |
| `PermissionPreset` | **保留** | Agent 权限分级 |
| `PRESET_POLICY` 矩阵 | **保留** | 权限决策核心 |
| `ApprovalManager` | **保留** | 审批状态机 |
| `ApprovalOverrideStore` | **保留** | always 覆盖 |
| `check_permission()` | **新增** | 替代三套 Hook 的单一入口 |
| `_effective_side_effect()` | **新增** | 路径升级等参数感知逻辑的唯一落点 |

### 4.3 包依赖变化

```
简化前:
  skills → tooling → (models, broker, hooks/*)
  skills → policy  → (policy_engine, policy_check_hook, pipeline, evaluators/*)
  gateway → policy (注册 PolicyCheckHook)
  gateway → tooling (注册 PresetBeforeHook + ApprovalOverrideHook)

简化后:
  skills → tooling → (models, broker, permission)
  tooling → policy → (approval_manager, approval_override_store)
  gateway → tooling (注册 ToolBroker，传入 approval 依赖)
```

`packages/policy/` 包大幅瘦身：
- **保留**: `approval_manager.py`, `approval_override_store.py`, `models.py`（仅审批相关模型）
- **删除**: `policy_engine.py`, `policy_check_hook.py`, `pipeline.py`, `evaluators/`

---

## 5. ToolMeta 字段清理

### 5.1 删除 `tool_profile` 字段

```python
# 简化前 ToolMeta
class ToolMeta(BaseModel):
    name: str
    description: str
    parameters_json_schema: dict
    side_effect_level: SideEffectLevel
    tool_profile: ToolProfile              # ← 删除
    tool_group: str
    timeout_seconds: float | None = None
    tier: ToolTier = ToolTier.DEFERRED
    ...

# 简化后 ToolMeta
class ToolMeta(BaseModel):
    name: str
    description: str
    parameters_json_schema: dict
    side_effect_level: SideEffectLevel     # 这就是权限的唯一依据
    tool_group: str
    timeout_seconds: float | None = None
    tier: ToolTier = ToolTier.DEFERRED
    ...
```

### 5.2 删除 `ExecutionContext.profile` 字段

```python
# 简化前 ExecutionContext
class ExecutionContext(BaseModel):
    task_id: str | None = None
    agent_runtime_id: str | None = None
    permission_preset: PermissionPreset = PermissionPreset.NORMAL
    profile: ToolProfile = ToolProfile.STANDARD   # ← 删除
    ...

# 简化后 ExecutionContext
class ExecutionContext(BaseModel):
    task_id: str | None = None
    agent_runtime_id: str | None = None
    permission_preset: PermissionPreset = PermissionPreset.NORMAL
    ...
```

### 5.3 简化 `@tool_contract` 装饰器

```python
# 简化前
@tool_contract(
    side_effect_level=SideEffectLevel.REVERSIBLE,
    tool_profile=ToolProfile.STANDARD,       # ← 删除此参数
    tool_group="filesystem",
    tier=ToolTier.DEFERRED,
)
def write_text(path: str, content: str) -> str: ...

# 简化后
@tool_contract(
    side_effect_level=SideEffectLevel.REVERSIBLE,
    tool_group="filesystem",
)
def write_text(path: str, content: str) -> str: ...
```

---

## 6. MCP 工具适配

MCP 工具的权限推断逻辑同步简化：

```python
# mcp_registry.py 简化后的 SideEffectLevel 推断

def _infer_side_effect(tool_annotations: dict) -> SideEffectLevel:
    """从 MCP tool annotations 推断 SideEffectLevel。"""
    if tool_annotations.get("readOnlyHint"):
        return SideEffectLevel.NONE
    if tool_annotations.get("destructiveHint"):
        return SideEffectLevel.IRREVERSIBLE
    return SideEffectLevel.REVERSIBLE  # 默认可逆

# 不再需要推断 ToolProfile
```

---

## 7. 迁移计划

### Phase 1: 权限统一（核心简化）

**目标**: 删除三套 Hook，用 `check_permission()` 统一替代。

1. 新增 `packages/tooling/src/octoagent/tooling/permission.py`
   - `check_permission()` 函数
   - `_effective_side_effect()` 函数（从 PresetBeforeHook 迁移路径升级逻辑）
   - `PermissionResult` 模型

2. 修改 `ToolBroker.execute()` — 在 Step 3 替换 Hook Chain 为 `check_permission()` 调用

3. 删除 Hook 文件:
   - `packages/tooling/src/octoagent/tooling/hooks/preset_hook.py`
   - `packages/tooling/src/octoagent/tooling/hooks/approval_override_hook.py`
   - `packages/policy/src/octoagent/policy/policy_check_hook.py`

4. 删除 Policy Pipeline:
   - `packages/policy/src/octoagent/policy/pipeline.py`
   - `packages/policy/src/octoagent/policy/evaluators/profile_filter.py`
   - `packages/policy/src/octoagent/policy/evaluators/global_rule.py`
   - `packages/policy/src/octoagent/policy/policy_engine.py`

5. 修改 `SkillRunner` — 删除 `_handle_ask_bridge()`, 简化 `_execute_single_tool()`

6. 修改 Gateway `main.py` — 不再注册 PolicyCheckHook

7. 修改 CapabilityPack — 不再注册 PresetBeforeHook + ApprovalOverrideHook

### Phase 2: ToolProfile 清除

**目标**: 从全代码库删除 ToolProfile 的所有引用。

1. `ToolMeta` 删除 `tool_profile` 字段
2. `ExecutionContext` 删除 `profile` 字段
3. `@tool_contract` 删除 `tool_profile` 参数
4. 更新所有工具注册代码（capability_pack.py + 各 plugin）
5. 更新 MCP 工具注册（mcp_registry.py）
6. 删除枚举、映射、兼容函数:
   - `ToolProfile` 枚举
   - `PROFILE_LEVELS` 映射
   - `profile_allows()` 函数
   - `TOOL_PROFILE_TO_PRESET` 映射
   - `migrate_tool_profile_to_preset()` 函数
7. 删除 Policy 模型中的 ToolProfile 引用:
   - `PolicyProfile.allowed_tool_profile` 字段
8. 更新所有测试

### Phase 3: 并发优化

**目标**: 简化 `_execute_tool_calls`，加信号量。

1. 修改 `_execute_tool_calls` 为两桶模型（auto + gated）
2. 加入 `Semaphore(10)` 控制 auto 桶并发度
3. 简化批次事件（保留 TOOL_BATCH_STARTED/COMPLETED）

### Phase 4: 清理收尾

**目标**: 删除无用代码和空目录。

1. 清理 `packages/policy/` 包（删除空 evaluators/ 目录等）
2. 更新 `__init__.py` 公共导出
3. 更新文档（Blueprint、spec、CLAUDE.md）
4. 清理测试中的 ToolProfile 相关 fixtures

---

## 8. 文件变更总览

### 新增

| 文件 | Phase | 说明 |
|------|-------|------|
| `packages/tooling/src/octoagent/tooling/permission.py` | 1 | `check_permission()` + `_effective_side_effect()` |

### 删除

| 文件 | Phase | 说明 |
|------|-------|------|
| `packages/tooling/.../hooks/preset_hook.py` | 1 | 合并到 permission.py |
| `packages/tooling/.../hooks/approval_override_hook.py` | 1 | 合并到 permission.py |
| `packages/policy/.../policy_check_hook.py` | 1 | 合并到 permission.py |
| `packages/policy/.../pipeline.py` | 1 | 不再需要 |
| `packages/policy/.../evaluators/profile_filter.py` | 1 | 不再需要 |
| `packages/policy/.../evaluators/global_rule.py` | 1 | 不再需要 |
| `packages/policy/.../policy_engine.py` | 1 | 不再需要 |

### 修改

| 文件 | Phase | 变更 |
|------|-------|------|
| `packages/tooling/.../broker.py` | 1 | execute() 内联 check_permission，不走 Hook |
| `packages/skills/.../runner.py` | 1+3 | 删除 _handle_ask_bridge，简化并发模型 |
| `packages/tooling/.../models.py` | 2 | 删除 ToolProfile 及相关 |
| `packages/tooling/.../decorators.py` | 2 | 删除 tool_profile 参数 |
| `apps/gateway/.../main.py` | 1 | 删除 PolicyCheckHook 注册 |
| `apps/gateway/.../capability_pack.py` | 1+2 | 删除 Hook 注册 + tool_profile 声明 |
| `apps/gateway/.../mcp_registry.py` | 2 | 删除 ToolProfile 推断 |
| `packages/policy/.../models.py` | 2 | 删除 PolicyProfile 等 |

---

## 9. Constitution 合规检查

| 原则 | 对齐 |
|------|------|
| #1 Durability First | ApprovalManager + EventStore 审批持久化不变 |
| #2 Everything is an Event | TOOL_CALL_STARTED/COMPLETED/FAILED 事件链不变 |
| #3 Tools are Contracts | SideEffectLevel 声明不变，删除的是冗余层 |
| #4 Side-effect Two-Phase | IRREVERSIBLE → ASK → 审批 → 执行，不变 |
| #5 Least Privilege | PermissionPreset 分级不变，Worker 默认 NORMAL |
| #7 User-in-Control | 无硬 DENY、always 覆盖、审批不变 |
| #8 Observability | 事件链完整，permission reason 记录在事件中 |
| #9 Agent Autonomy | 不替 LLM 做决策，只在执行层做安全门控 |
| #10 Policy-Driven | `check_permission()` 是统一策略入口，不在工具层硬编码 |

---

## 10. 简化效果量化

| 指标 | 简化前 | 简化后 |
|------|--------|--------|
| 权限相关枚举 | 5 个（SideEffectLevel, ToolProfile, PermissionPreset, PolicyAction, PresetDecision） | 2 个（SideEffectLevel, PermissionPreset） |
| 权限相关 Hook | 3 个 BeforeHook（独立文件） | 0 个 |
| Pipeline 评估层 | 2 层（profile_filter + global_rule） | 0 层（矩阵直查） |
| 权限决策入口 | 3 个（PolicyCheckHook + PresetBeforeHook + _handle_ask_bridge） | 1 个（check_permission） |
| 权限决策链路长度 | Hook 注册 → priority 排序 → 逐个执行 → ask 前缀 → 桥接 → 重试 | 函数调用 → 矩阵查表 → 审批等待 |
| override 缓存查询次数/每次调用 | 2 次（ApprovalOverrideHook + PresetBeforeHook 各查一次） | 1 次 |
| 需要理解的概念数 | ToolProfile, PermissionPreset, PolicyAction, PresetDecision, PolicyProfile, PolicyStep, Hook priority, fail_mode, ask前缀协议 | PermissionPreset, SideEffectLevel, preset_decision矩阵 |
| 删除文件数 | - | 7 个 |
| 删除 ToolProfile 涉及文件 | - | ~30 个（改） |
