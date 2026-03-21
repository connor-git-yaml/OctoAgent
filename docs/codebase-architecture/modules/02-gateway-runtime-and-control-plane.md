# Gateway Runtime / Control Plane 模块

当前 OctoAgent 的“运行时大脑”主要不在一个叫 `kernel` 的独立目录里，而是落在 [`octoagent/apps/gateway`](../../../octoagent/apps/gateway) 下面。

这是理解当前代码最容易出错的地方：**Gateway 现在不只是 HTTP 入口，它同时承载了应用装配、durable task runtime、Butler / delegation / worker runtime、控制面资源和大量运行治理逻辑。**

## 1. 模块职责

当前 `apps/gateway` 主要承担：

1. **FastAPI 应用装配与生命周期**
2. **Task runtime**
3. **Orchestrator / Butler / delegation / worker dispatch**
4. **Control plane snapshot 和 action**
5. **SSE、Execution Console、Operator Inbox、Automation 等外部 surface**

## 2. 关键文件与角色

| 文件 | 当前角色 |
| --- | --- |
| [`main.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/main.py) | 应用装配入口 |
| [`services/task_service.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py) | task durable 主链 |
| [`services/task_runner.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py) | 持久化调度、恢复、监控 |
| [`services/orchestrator.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py) | Butler / A2A / dispatch / worker 回传 |
| [`services/delegation_plane.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py) | work / target selection / pipeline plane |
| [`services/worker_runtime.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py) | 执行后端、预算、取消、超时 |
| [`services/llm_service.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py) | 运行时 LLM 入口 |
| [`services/control_plane.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py) | 当前控制面聚合器 |

## 3. `main.py`: 当前真实应用装配入口

位置：[`main.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/main.py)

`main.py` 的意义不是“建一个 FastAPI app”这么简单，它实际决定了当前运行时对象图。

### 3.1 `_resolve_project_root()`

职责：

- 统一解析实例运行根目录
- 让 gateway、provider 配置、memory、artifact、runtime state 都围绕同一个 project root 运转

### 3.2 `_build_runtime_alias_registry()`

职责：

- 从 `octoagent.yaml` 读取当前 alias 集
- 构造运行时 `AliasRegistry`

这一步把“配置层 alias”接到了“运行时调用 alias”。

### 3.3 `lifespan`

主逻辑可以概括成：

1. 加载 `.env`
2. 创建 `StoreGroup`
3. 初始化 memory db
4. 装配 `LiteLLMClient`、`LLMService`
5. 装配 `TaskRunner`、`ControlPlaneService`、`DelegationPlaneService`
6. 注册路由、SSE、Telegram、Automation 等服务
7. 在关闭时做后台任务的优雅 shutdown

因此 `main.py` 是“实例从文件系统和配置，变成可运行系统”的根装配器。

## 4. `TaskService`: durable task 主链

位置：[`task_service.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py)

`TaskService` 是当前 runtime 里最关键的服务之一。  
它不只是“调用 LLM”，而是把用户消息变成一个 durable、可恢复、可观测的 task。

### 4.1 `create_task()`

实现逻辑：

1. 检查 `idempotency_key` 去重
2. 构造 `Task`
3. 构造 `TASK_CREATED` 和 `USER_MESSAGE` 初始事件
4. 调用 core 事务函数一次性写入 task + event
5. 广播 SSE

这一步确立了“用户输入先成为 durable task，再进入后续运行”的原则。

### 4.2 `append_user_message()`

职责：

- 给已有 task 继续追加 `USER_MESSAGE`
- 保留输入 metadata / control metadata
- 仍然走 append-only event 语义

### 4.3 `process_task_with_llm()`

这是整个 task runtime 的主执行函数。

它内部串了几件大事：

1. 先把 task 切到运行状态
2. 根据 task、behavior、memory、session 构建上下文
3. 在需要时做 memory recall plan
4. 在需要时做上下文压缩与 delayed recall
5. 发起 LLM 调用
6. 存储输出 artifact
7. 写 `MODEL_CALL_*`、`ARTIFACT_CREATED`、`STATE_TRANSITION` 等事件
8. 失败时走 `_handle_llm_failure()`

它的关键价值是：**模型调用不是孤立的 HTTP 请求，而是被纳入了 task/event/artifact/checkpoint 主链。**

### 4.4 `_build_task_context()`

职责：

- 组装给 LLM 的 prompt/messages
- 拼接行为文件、历史对话、召回内容、runtime hints 等上下文材料

这是 task runtime“为什么会给模型看到这些内容”的解释入口。

### 4.5 `_build_memory_recall_plan()`

职责：

- 判断是否需要 recall planning
- 调用 recall planner 或解析预计算计划
- 产出 `RecallPlan`

这一步把记忆召回从“固定搜索”变成了“任务驱动的 recall 计划”。

### 4.6 `_record_context_compaction()` / `_persist_compaction_flush()`

职责：

- 记录上下文压缩过程
- 在必要时把 compaction flush 写成持久化结果

说明当前 runtime 已把 context budget 管理纳入正式流程，而不是简单截断 prompt。

### 4.7 `_store_llm_artifact()` / `_write_model_call_completed()`

职责：

- 把模型输出落成 artifact
- 写成本次调用的完成事件和摘要

这保证了“回答文本”和“状态迁移”不是分离的。

## 5. `TaskRunner`: durable 调度与恢复层

位置：[`task_runner.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py)

`TaskRunner` 解决的问题不是“起一个后台任务”，而是“任务在进程重启后也不能丢”。

### 5.1 `startup()`

实现逻辑：

1. 恢复 orphan running jobs
2. 拉起 queued jobs
3. 启动 monitor loop

### 5.2 `_recover_orphan_running_jobs()` / `_recover_one_orphan_job()`

职责：

- 处理“数据库里 job 还是 RUNNING，但进程已经重启”的情况
- 根据 task 当前状态决定：
  - 标记成功
  - 标记等待输入/审批
  - 尝试 resume
  - 或清理失败状态

这一步是当前 durable runtime 的恢复关键。

### 5.3 `enqueue()` / `_start_job()` / `_spawn_job()`

职责：

- 把 task 放进 `task_jobs`
- 抢占执行 lease
- 以真实后台任务运行 `_run_job()`

### 5.4 `_run_job()`

这是 TaskRunner 的真正执行入口。它会：

1. 从 job 读取上下文
2. 把执行权交给 `OrchestratorService`
3. 处理执行完成、失败、取消
4. 更新 `task_jobs` 和 execution console

### 5.5 `schedule_dispatch_envelope()`

职责：

- 接住来自 delegation plane 的 `DispatchEnvelope`
- 将其放入当前调度体系

这让多 worker / delegation 结果最终回到同一套 durable 调度框架里。

## 6. `OrchestratorService`: Butler / A2A / dispatch 中枢

位置：[`orchestrator.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py)

`OrchestratorService` 是当前“Kernel 逻辑”的主要落点。

### 6.1 `dispatch()`

主职责：

- 接受 `OrchestratorRequest`
- 做高风险 gate
- 决定 Butler 是否直接回答、委派给 worker，还是走 graph delegation
- 最终转成 `DispatchEnvelope` 并执行

### 6.2 `_resolve_butler_decision()`

职责：

- 结合当前请求、behavior hints、freshness、worker capability 等信息
- 决定 Butler 的处理模式

这是当前 Butler “先自己收口还是继续委派”的核心判断点。

### 6.3 `_dispatch_inline_butler_decision()` / `_dispatch_butler_direct_execution()`

职责：

- 在确定 Butler 可以直接回答时，走 inline reply 或 owner-self execution 路径

这部分解释了为什么当前系统里 Butler 有时不会创建新的 specialist work。

### 6.4 `_dispatch_butler_delegate_graph()`

职责：

- 当需要委派时，构造 delegation 请求并接到 graph/pipeline 路径

### 6.5 `_prepare_a2a_dispatch()` / `_persist_a2a_message_and_event()`

职责：

- 生成 A2A-Lite 消息
- 记录 conversation、message audit、事件

这说明 A2A 在当前实现里不是外部协议装饰，而是当前 worker 派发和结果回传的 durable 表达。

### 6.6 `_write_orch_decision_event()` / `_write_worker_dispatched_event()` / `_write_worker_returned_event()`

职责：

- 将 orchestrator 决策、派发和回传写成正式事件

它们是控制流可观测性的核心。

## 7. `DelegationPlaneService`: Work / target selection / pipeline 平面

位置：[`delegation_plane.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py)

这是当前代码里非常值得注意的新中心，因为它已经把“委派”“work”“pipeline”“tool selection”收进了一个统一平面。

### 7.1 `prepare_dispatch()`

实现逻辑：

1. 解析 project / workspace / session owner / target profile
2. 创建 `Work`
3. 初始化 pipeline run
4. 根据 run 状态推导当前 selection 和 work status
5. 必要时准备 `DispatchEnvelope`

这一步把过去零散的委派逻辑升级成了“先有 work 和 pipeline 状态，再决定如何派发”。

### 7.2 `_build_definition()` 与 `_register_pipeline_handlers()`

职责：

- 定义 delegation pipeline 的节点结构
- 把 route resolve、bootstrap prepare、tool index select、gate review、finalize 等 handler 接起来

### 7.3 `_handle_route_resolve()` / `_handle_tool_index_select()` / `_handle_gate_review()`

职责：

- 逐节点推进 target 解析、工具选择、门禁审查

这让 delegation plane 不是一次性函数，而是一个可 checkpoint 的 deterministic flow。

### 7.4 `mark_dispatched()` / `complete_work()` / `retry_work()` / `cancel_work()`

职责：

- 统一维护 `Work` 生命周期
- 把派发与执行结果同步回 work 平面

## 8. `WorkerRuntime`: 执行后端与预算控制

位置：[`worker_runtime.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py)

### 8.1 `WorkerRuntimeConfig.from_env()`

职责：

- 从环境变量读取最大步数、超时、docker mode、默认 tool profile 等参数

### 8.2 `WorkerCancellationRegistry`

职责：

- 提供 task 级 cancel signal
- 被 `TaskRunner` 和 runtime backend 共用

### 8.3 `InlineRuntimeBackend` / `DockerRuntimeBackend`

当前实现里：

- `InlineRuntimeBackend` 直接把执行委托回 `TaskService.process_task_with_llm()`
- `DockerRuntimeBackend` 目前仍复用 inline 执行路径，先把路由和探测接入

也就是说，当前 docker runtime 的“执行语义”还没有完全独立出来，但 runtime 选择边界已经存在。

### 8.4 `WorkerRuntime.run()`

职责：

1. 解析 envelope
2. 校验 tool profile / privileged gate
3. 选择 backend
4. 施加超时、预算和取消控制
5. 统一产出 `WorkerResult`

这是当前 worker 执行语义的最终落点。

## 9. `LLMService`: Gateway 侧运行时 LLM 入口

位置：[`llm_service.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py)

`LLMService` 不是简单的 provider wrapper。当前它实际上承担了：

- alias 路由
- LLM 调用
- 工具搜索 / skill / pipeline 上下文组装
- tool promotion 相关逻辑

### 9.1 `call()`

职责：

- 统一接收 prompt/messages
- 通过 alias registry 解析运行时 alias
- 调用底层 provider

### 9.2 `_try_call_with_tools()`

职责：

- 将工具搜索、选中工具、skill/tool promotion 信息注入到模型调用上下文

### 9.3 `_build_loaded_skills_context()` / `_build_skill_catalog_context()` / `_build_pipeline_catalog_context()`

职责：

- 把当前已加载 skill、可用 catalog、pipeline 能力转成模型可消费的上下文块

因此，`LLMService` 当前同时是“模型调用入口”和“运行时能力目录桥接层”。

## 10. `ControlPlaneService`: 当前控制面的聚合器

位置：[`control_plane.py`](../../../octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py)

这是当前仓库里体量最大的服务，也是最需要正确理解的文件。

不要把它理解成“设置页后端”。  
它当前实际上统一承担了：

- snapshot 资源生产
- action registry 暴露
- action dispatch
- setup governance
- project / session / agent / worker profile 管理
- memory console
- import workbench
- automation
- diagnostics

### 10.1 `get_snapshot()`

职责：

- 汇总当前工作台需要的控制面资源
- 产出前端可直接消费的 snapshot 文档

这是 Web Workbench 当前最关键的读接口。

### 10.2 `get_*_document()` 系列

例如：

- `get_config_schema()`
- `get_project_selector()`
- `get_session_projection()`
- `get_agent_profiles_document()`
- `get_setup_governance_document()`
- `get_memory_console()`

这组函数的意义是：  
**Control plane 不是让前端读内部数据库，而是先把内部状态整理成文档型资源。**

### 10.3 `execute_action()` / `_dispatch_action()`

职责：

- 接收控制面动作请求
- 分派到具体 handler
- 统一包装结果和控制面事件

这是当前“写路径”的总入口。

### 10.4 `_handle_setup_review()` / `_handle_setup_apply()` / `_handle_setup_quick_connect()`

这是当前配置治理主链：

- `setup.review` 做草稿检查和下一步建议
- `setup.apply` 保存配置并触发必要的运行态同步
- `setup.quick_connect` 则走更高层的一键接通路径

### 10.5 `_handle_agent_profile_save()` / `_review_worker_profile_draft()` / `_save_worker_profile_draft()`

这组函数承担 Agent / Worker profile 的 review、存储和 revision 发布语义。

说明当前 agent 管理并不是前端本地表单，而是正式控制面动作。

### 10.6 `_build_registry()`

职责：

- 定义控制面可暴露的 actions
- 把 action id、参数、surface、资源失效规则等组织成统一 registry

这一步让 Web/CLI/Agent 可以共享一套控制面动作语言。

## 11. 当前模块最值得注意的架构现实

1. `apps/gateway` 目前已经是运行时、控制面和部分“kernel/worker”逻辑的聚合中心
2. `ControlPlaneService` 的大文件形态反映了当前控制面子域尚未进一步拆分
3. `TaskService` + `TaskRunner` + `OrchestratorService` 构成 durable runtime 主链
4. `DelegationPlaneService` 是当前 work / pipeline / route selection 收敛的关键新中心

如果你要继续推进架构演进，首先要承认这是当前真实结构，而不是假设 `kernel` 已经拆出来了。
