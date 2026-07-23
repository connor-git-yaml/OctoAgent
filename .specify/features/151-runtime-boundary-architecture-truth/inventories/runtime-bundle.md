# F151 Runtime bundle 构造与传播清单

## 1. 构造环与解法

真实构造环为：`AgentSessionTurnHook → SkillRunner → 最终 LLMService`。Hook 当前只写 AgentSession turn store，不使用 LLM/Provider/background task。

采用现有 `OctoHarness._bootstrap_executors()` 内的 composition-root factory 顺序，不新增 factory service：

1. 复用 `_bootstrap_llm()` 已创建的同一个 `app.state.background_tasks` set 与 ProviderRouter。
2. 以 `AgentContextService(..., storage_only=True)` 构造 AgentSessionTurnHook。
3. 用 Hook 构造 SkillRunner，再构造最终 LLMService。
4. 最终 LLMService ready 后创建单一 `RuntimeServiceBundle` 实例。
5. bundle传给TaskRunner→Orchestrator/WorkerRuntime，以及3个runtime-bearing TaskService；普通模型执行的`process_task_with_llm()`删除独立`llm_service`override，只能使用bundle中的同一final LLM。
6. Orchestrator deterministic non-direct reply与Graph-start失败fallback改用Task application内的storage-only正向operation `complete_task_with_precomputed_result`：只收完整`ModelCallResult`与既有完成上下文；TaskService复用Task/Event/Artifact/checkpoint原语，AgentContext现有`agent_context_session_replay.py`拆出唯一storage persistence primitive保存SessionContext/turn/session，runtime wrapper才额外触发SessionMemoryExtractor。预计算路径复用该primitive而不复制算法，bundle/Router/recall/compaction/extraction调用为0；删除`_InlineReplyLLMService`，不提供通用LLM override。

不采用 mutable late-binding locator；storage-only Hook 解开了构造环。

## 2. 模式不变量

`AgentContextService` 与 `TaskService` 均要求二选一：

- `runtime_services=<bundle>`：runtime-bearing；
- `storage_only=True`：只允许 storage/replay/project/profile 方法。

缺失两者或同时提供必须在构造时 fail fast。禁止 `bundle=None` 回退，禁止 class state。`AgentContextService(..., storage_only=True)`构造本身不得创建`MemoryRuntimeService`、`ModelRerankerService(auto_load=True)`、background task或网络对象；storage-only 调用 runtime/memory recall/extraction 方法时以 typed error fail fast。

## 3. TaskService 48 点基线与45点目标

### Bundle-bearing：3（保留并显式传bundle）

| 文件 | 行 | 原因 |
|---|---:|---|
| `services/worker_runtime.py` | 512 | Inline/Graph 都调用 `process_task_with_llm` |
| `services/orchestrator.py` | 1268 | direct execution 分支 |
| `services/orchestrator.py` | 1343 | owner-self worker 分支 |

### 基线中删除的2个重复storage实例

| 文件 | 行 | 复用规则 |
|---|---:|---|
| `services/orchestrator.py` | 1227 | `_dispatch_direct_execution`中tool-index append复用同方法1268的bundle-bearing TaskService；不得先构造第二实例 |
| `services/orchestrator.py` | 1319 | `_dispatch_owner_self_worker_execution`中tool-index append复用同方法1343的bundle-bearing TaskService |

### Pure-storage：42（目标）

| 文件 | 行 | 数量 | 用途 |
|---|---|---:|---|
| `services/builtin_tools/_deps.py` | 111, 138 | 2 | task lookup/project context；后者改为显式 storage-only AgentContext，不再访问 TaskService 私有字段 |
| `services/builtin_tools/misc_tools.py` | 58 | 1 | artifact 写入 |
| `services/delegation_plane.py` | 122 | 1 | structured event |
| `services/execution_console.py` | 221,250,301,370,420,493,595,667 | 8 | event/state/artifact |
| `routes/cancel.py` / `routes/tasks.py` | 40 / 128,158 | 3 | cancel/read/list |
| `services/telegram.py` / `discord.py` / `slack.py` | 875 / 272 / 285 | 3 | create task only |
| `services/task_runner.py` | 183,236,356,420,472,704,807,895,900,972,1151,1250,1299,1373 | 14 | state/metadata/recovery；实际模型执行在 WorkerRuntime 的 bundle-bearing 实例 |
| `services/dispatch_service.py` | 990 | 1 | audit event |
| `services/orchestrator.py` | 819,1141,2619,2661,2747 | 5 | event/state transition；1141仅调用预计算结果storage operation，不接收/读取bundle |
| `services/operator_actions.py` | 149 | 1 | create task/event |
| `services/control_plane/session_service.py` | 1484 | 1 | cancel task；T062已删除worker-profile fallback构造并改用既有TaskRunner |
| `routes/chat.py` / `routes/message.py` | 451 / 65 | 2 | TaskRunner必须在Task创建前preflight；删除缺失时直跑LLM fallback，仅保留create/read storage用途 |

Chat的module-level`_background_tasks`随直跑fallback删除。所有LLM/AgentContext fire-and-forget任务只能注册到同一个`app.state.background_tasks`；AutomationScheduler等拥有明确独立lifecycle的内部task set不在此禁令范围。

## 4. AgentContext 直接构造点

| 构造根 | 模式 |
|---|---|
| `AgentSessionTurnHook:22` | storage-only |
| `TaskService:149` | 仅3个bundle-bearing TaskService构造；42个pure-storage TaskService不创建AgentContext |
| `Orchestrator:1010` | storage-only project/profile lookup |
| `Orchestrator:1834` | storage-only replay projection |

storage-only采用[`runtime-operation-modes.v1.json`](runtime-operation-modes.v1.json)正向machine allowlist。AgentContext允许纯store/session/profile/project/replay操作及新的内部session persistence primitive；memory namespace/recall/extraction仍为runtime能力。TaskService另明确允许`complete_task_with_precomputed_result`及既有create/read/event/state/artifact操作。该预计算operation逐字段保留`ModelCallResult`与标准副作用，同时拒绝LLM对象并保证bundle/Router/recall/compaction/extraction=0。gate以AST重算每个public/production-called-private operation与45个TaskService/3个direct AgentContext target callsite；missing/extra/unknown默认拒绝，并从storage entrypoints做capability reachability，不能靠runtime黑名单猜测。

## 5. Background 与 teardown

- bundle 必须引用现有 `app.state.background_tasks`，不得创建第二个 set。
- 保留 Harness 已有两阶段 drain：首轮 snapshot drain；停止 producer 后 final loop-drain。
- final drain 后执行 bundle `aclose()`，再关闭 stores。
- exact API：`ProviderModelClient.aclose()`只幂等清理local histories/metadata，不关Router；`SkillRunner.aclose()`幂等调用model-client local close；`LLMService.aclose()`幂等调用SkillRunner close；bundle最后且唯一调用`ProviderRouter.aclose()`。
- `OctoHarness.shutdown()`增加instance guard/lock；重复shutdown无副作用。测试分别断言LLM、SkillRunner、ProviderModelClient、Router、Store close count=1，顺序为`final-drain < local-close-chain < router-close < stores-close`。

## 6. 迁移测试

1. 构造参数缺失/同时提供失败。
   S083唯一exact node为`test_runtime_bundle_is_minimal_instance_holder_and_task_service_and_agent_context_require_exactly_one_mode`，必须在同一个selector中分别构造TaskService与AgentContext的missing/both/valid两种mode矩阵；不得只验证TaskService后用测试名声称AgentContext已覆盖。
2. storage-only构造不创建MemoryRuntime/reranker、不auto-load模型、不spawn background task且网络调用0；调用runtime method失败，turn/session replay/project/profile store操作正常。
3. 两个Harness的bundle/service/background identity隔离。本Feature只收敛AgentContext三项class-level service injection；`TaskService._task_locks`与terminal callback coordination不冒充bundle范围，保留现有register/unregister lifecycle tests且不得恶化。
4. final LLM identity 是 SkillRunner-aware 实例，不是 bootstrap LLM。
5. background set identity 与 `app.state.background_tasks` 相同。
6. 两阶段 drain 与 exactly-once close 顺序。
7. 现有 e2e global-state fixture 不再清 class attrs，且无跨测试残留。
8. TaskRunner缺失的chat/message请求在Task创建前503，不创建Task/Event；不启动route-local background task。
9. `process_task_with_llm`没有独立LLM override，无法把bundle LLM A与调用LLM B混用。
10. characterization锁定non-direct与Graph-start fallback的exact content/provider、标准Task/Event/Artifact/checkpoint/SessionContext/turn/session副作用、bundle/Router/recall/compaction/extraction call=0；precomputed seam拒绝LLM对象，storage primitive只有一个production实现。
11. AST构造点gate先证明baseline 48=4 runtime+44 storage，再证明目标45=3+42：T062先删除worker-profile fallback点；runtime qualname固定为`WorkerRuntime.run`与Orchestrator两个direct/owner-self执行分支，`_dispatch_inline_decision`固定为storage-only，并精确定位T084删除的1227/1319两点；任一未知点或`bundle=None`失败。

## 7. 测试侧构造点迁移

machine source为[`runtime-test-constructors.v1.json`](runtime-test-constructors.v1.json)与[`agent-context-test-constructors.v1.json`](agent-context-test-constructors.v1.json)。baseline区分：

- 文本`TaskService(`为147处：144个direct `Call(TaskService)`、2个subclass definition、1个runtime subclass instantiation；
- 144个direct calls不是144个有行为证据：当前123个live test/nested identities（其中F033两条skip含3个构造点）、20个helper identities与1个dead-shadowed。两个同名顶层`test_task_service_prompt_context_only_exposes_sanitized_control_metadata`定义导致line1816的runtime constructor被line2078定义覆盖；constructor mode基线仍为26 runtime-bundle、116 storage-only、1 characterization-remove、1 dead-shadowed runtime；
- 40个测试文件中有44个AST `process_task_with_llm(..., llm_service=...)` override，即43 collected-live +1 dead-shadowed。完成态先把前一测试改为准确唯一名`test_worker_tool_writeback_and_private_memory_are_isolated_across_sessions`并证明两个node都collect，再逐项迁移为构造时同一bundle LLM。
- AgentContext直接构造31点/7文件使用同级稳定identity，目标23个storage-only与8个runtime-bundle，unknown=0；storage-only正向合同不得因测试迁移锁死不需要的bundle capability。

identity固定为`path+qualname+definition_ordinal+constructor ordinal`，line只报告；baseline entry count=unique identity count=144。完成态不锁死未来测试总数，但duplicate test qualname=0，所有实际TaskService/AgentContext构造必须显式XOR：`runtime_services=<non-None>`或`storage_only=True`；拒绝`None`、`False`、`**kwargs`隐藏mode、positional mode或fallback。runtime tests使用test-only typed factory并公开`service/bundle/llm/router/background_tasks` identity；pure storage tests显式storage-only。hardening混合fixture拆为runtime/storage；dead tuple constructor删除。测试helper不得进入production或成为第二composition root。

T084在production 45点迁移的同一Phase同步完成测试迁移，并以三组gate阻断：

1. production stable key=`path+enclosing qualname+ordinal`与manifest双向集合差，exactly 45=3/42；
2. tests先用collect-only artifact证明baseline只有后定义可收集，再重命名前定义并证明两个exact nodes都collect；随后动态集合要求duplicate qualname=0、unknown mode=0、44 AST overrides（43 live+1 shadowed baseline）→0、AgentContext shared attrs/setters→0、runtime fixture identity一致。
3. C084的machine behavior-owner map必须与两个constructor inventories的44个owner paths双向相等；展开42 files+3 exact nodes=45 selectors。F033两个nodes先移除skip并用restart持久状态/跨project隔离oracle覆盖3个构造点；20 helper identities须有reverse-call evidence。selected>0、fail/error/skip/rerun=0；Provider test路径先投影，退休路径不得进入Phase4命令。

C05行为覆盖必须包含`test_context_compaction.py`、`test_task_service_hardening.py`、`test_task_service_context_integration.py`、F010/F033 integration与`test_session_memory_spawn.py`，不能只靠构造点AST；C19执行其余确定性testpaths，最终baseline覆盖既有unmarked e2e fixture。coverage不覆盖测试源，不能替代test constructor gate。
