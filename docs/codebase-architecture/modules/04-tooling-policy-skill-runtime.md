# Tooling / Policy / Skill Runtime 模块

这一层对应当前代码中的：

- [`octoagent/packages/tooling`](../../../octoagent/packages/tooling)
- [`octoagent/packages/policy`](../../../octoagent/packages/policy)
- [`octoagent/packages/skills`](../../../octoagent/packages/skills)

它们共同解决的是一个非常具体的问题：  
**LLM 不是直接随便调工具，而是先通过 tool contract 被注册、发现、执行，再被 policy gate 审查，再由 skill runtime 驱动成可恢复的 loop 或 pipeline。**

## 1. 模块职责

### 1.1 Tooling

- 定义工具契约
- 反射 schema
- 注册与执行工具
- 提供 hook 扩展点

### 1.2 Policy

- 对工具调用做 allow / ask / deny 评估
- 管理审批请求
- 恢复未完成审批

### 1.3 Skills

- 跑结构化 LLM + tool loop
- 处理重试、资源限制、循环检测
- 提供 deterministic pipeline engine

## 2. `reflect_tool_schema()`: 代码签名到 ToolMeta 的单一事实源

位置：[`schema.py`](../../../octoagent/packages/tooling/src/octoagent/tooling/schema.py)

这是 Tooling 层最基础的约束点。

### 2.1 `reflect_tool_schema()`

实现逻辑：

1. 检查函数是否带了 `@tool_contract` 元数据
2. 检查所有参数是否有类型注解
3. 调用 `pydantic_ai._function_schema.function_schema()` 生成 JSON Schema
4. 合并装饰器元数据和自动生成的 schema
5. 返回 `ToolMeta`

这个函数存在的意义是：**工具 schema 不是写两遍，而是从代码签名反射出来。**

### 2.2 `_validate_type_annotations()`

职责：

- 强制要求工具参数必须有完整类型注解

这一步把“Tools are Contracts”落到代码层面。

## 3. `ToolBroker`: 当前工具系统中枢

位置：[`broker.py`](../../../octoagent/packages/tooling/src/octoagent/tooling/broker.py)

`ToolBroker` 是工具系统的运行时中心。

### 3.1 `register()` / `try_register()`

职责：

- 注册工具元数据和处理函数
- 处理冲突与 fail-open diagnostics

这里说明当前工具注册表仍然是进程内存态，但历史调用事件会通过 event store 落盘。

### 3.2 `discover()`

职责：

- 根据 `ToolProfile` 和 `tool_group` 过滤可用工具

这是 tool selection 的基础。

### 3.3 `add_hook()`

职责：

- 注册 before/after hook
- 按优先级排序

这意味着 policy、审计、扩展行为都是通过 hook 链接入工具执行链的。

### 3.4 `execute()`

这是 Broker 最核心的函数。

实现逻辑：

1. 查找工具
2. 写 `TOOL_CALL_STARTED`
3. 执行 before hooks
4. 执行真实工具
5. 执行 after hooks
6. 写完成或失败事件
7. 返回统一 `ToolResult`

也就是说，工具调用在当前系统里已经是正式运行时事件，而不是普通函数调用。

## 4. Policy 层：工具调用的门禁与审批

### 4.1 `evaluate_pipeline()`

位置：[`pipeline.py`](../../../octoagent/packages/policy/src/octoagent/policy/pipeline.py)

这是 policy 的纯函数核心。

实现逻辑：

1. 依次执行 `PolicyStep`
2. 每层都产出带 label 的 `PolicyDecision`
3. 一旦遇到 `DENY`，立刻短路
4. 后续层只能收紧、不能放松前面的决定

这让策略评估具备：

- trace
- 只收紧不放松
- deny fast-fail

### 4.2 `ApprovalManager`

位置：[`approval_manager.py`](../../../octoagent/packages/policy/src/octoagent/policy/approval_manager.py)

`ApprovalManager` 当前实现的是 two-phase approval 状态机。

#### `register()`

实现逻辑：

1. 做幂等注册
2. 检查 allow-always 覆盖
3. 写 `APPROVAL_REQUESTED` 事件
4. 推送 SSE
5. 启动超时定时器

#### `wait_for_decision()`

职责：

- 异步等待审批结果
- 与 timeout/grace 逻辑协同

#### `resolve()`

职责：

- 在竞态保护下落定审批结果
- 写 approved/rejected 事件
- 更新内存与可恢复状态

审批在当前系统里不是 UI 小功能，而是正式运行时子系统。

### 4.3 `PolicyEngine`

位置：[`policy_engine.py`](../../../octoagent/packages/policy/src/octoagent/policy/policy_engine.py)

`PolicyEngine` 是门面层。

#### `_build_steps()`

当前默认会把：

- `profile_filter`
- `global_rule`

组装成 policy pipeline。

#### `startup()`

职责：

- 启动时恢复未完成审批

#### `hook`

它暴露 `PolicyCheckHook` 给 `ToolBroker` 挂入 before hook 链。

#### `update_profile()`

职责：

- 替换当前策略 profile
- 重建 steps 和 hook
- 写 `POLICY_CONFIG_CHANGED` 事件

因此 `PolicyEngine` 当前是“配置 policy + 恢复审批 + 输出 hook”的统一门面。

## 5. Skill 层：结构化 LLM + tool loop

### 5.1 `SkillRunner`

位置：[`runner.py`](../../../octoagent/packages/skills/src/octoagent/skills/runner.py)

`SkillRunner` 当前负责把一个 skill manifest 变成可执行 loop。

#### `run()`

这是 SkillRunner 的核心函数。

实现逻辑可以概括成：

1. 校验输入
2. 发出 skill started 事件
3. 进入 loop
4. 调用结构化 LLM client 生成输出
5. 校验输出模型
6. 如果有 tool calls，就执行工具
7. 收集 feedback，再次喂给模型
8. 执行循环检测、重试限制、token/cost 限制
9. 产出成功或失败结果

这说明当前 skill runtime 已经具备：

- 输入输出模型校验
- tool feedback 回环
- 循环检测
- retry/backoff
- usage tracking

#### 循环检测相关逻辑

`SkillRunner` 同时做两类循环检测：

- tool_calls 签名重复
- 同一工具反复操作同一目标的语义级循环

这不是装饰性优化，而是为了避免 free loop 在工具层无限转圈。

## 6. `SkillPipelineEngine`: deterministic pipeline 层

位置：[`pipeline.py`](../../../octoagent/packages/skills/src/octoagent/skills/pipeline.py)

与 `SkillRunner` 不同，`SkillPipelineEngine` 处理的是可 checkpoint、可 replay 的确定性 pipeline。

### 6.1 `start_run()`

职责：

- 创建 pipeline run
- 记录初始状态
- 进入 `_drive()`

### 6.2 `resume_run()`

职责：

- 从 checkpoint 或当前节点继续执行

### 6.3 `retry_current_node()`

职责：

- 重试当前失败节点

### 6.4 `cancel_run()`

职责：

- 把 run 切到取消状态

### 6.5 `_drive()`

这是 pipeline engine 的核心执行器。

实现逻辑：

1. 根据当前 run 和 definition 找到待执行节点
2. 调 handler
3. 记录 checkpoint 和 replay frame
4. 更新状态快照
5. 决定进入下一节点、结束或失败

当前 delegation plane 就是通过它获得 deterministic 路径的。

## 7. 这三个子模块之间是怎么接起来的

当前主连接方式是：

1. Tooling 定义“工具如何被描述与执行”
2. Policy 通过 hook 接进 ToolBroker，决定工具能不能执行
3. Skills 决定什么时候调用模型、什么时候执行工具、什么时候停止
4. Pipeline engine 则把“确定性多步骤编排”从 loop 中抽出来

因此可以把这层理解成：

**Tooling 提供能力，Policy 决定边界，Skills 驱动调用，Pipeline 提供确定性编排。**

## 8. 维护时最重要的原则

### 8.1 不要绕开 ToolBroker

否则：

- 事件丢失
- hook 不执行
- policy 无法介入

### 8.2 不要把 policy 写死到业务调用点

当前正确做法是：

- evaluator -> pipeline
- hook -> broker
- approval manager -> two-phase state machine

### 8.3 不要把 deterministic pipeline 和 free loop 混成一层

当前系统已经有明确区分：

- `SkillRunner` 负责 free loop
- `SkillPipelineEngine` 负责 deterministic graph / pipeline

这是后续继续演进时必须保持的结构边界。
