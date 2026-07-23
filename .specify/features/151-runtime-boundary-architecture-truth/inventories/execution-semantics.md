# F151 Execution 语义与消费者冻结清单

本清单把四个域严格分开；禁止用一个 `ExecutionBackend` 或新公共 enum 承载它们。

## 1. 四个值域

| 域 | 输入/存储 | 精确值域 | 责任 |
|---|---|---|---|
| delegation `target_kind` | 四个现役用户输入面 | case-sensitive `worker|subagent|acp_runtime|graph_agent|fallback` | 选择委派目标；不得trim/lower/coerce或按worker_type重选 |
| profile capability | profile/revision capability | `worker|subagent|acp_runtime|graph_agent` | 声明profile可提供的runtime；不含`fallback` |
| Worker transient backend | `WorkerDispatchState/Result.backend` | `graph|inline` | 单次执行实现选择；`graph_agent→graph`，其他合法target→inline |
| persisted Event / Console projection | raw event backend / public response | 新写`inline`；历史读`inline|docker` | 冻结Console输出；不反推runtime selector |

四域各用所属既有类型/validator。`target_kind`字段缺省时才采用入口既有默认；显式空字符串、空白包裹、大小写变化、非string、`docker`及枚举外值均拒绝。`delegate_task`是硬编码producer，不是用户输入，不扩展矩阵。

## 2. 四个 target_kind 输入面

| 入口 | 字段 | 缺省 | selector/runtime preflight位置 | 原子性边界 |
|---|---|---|---|---|
| `POST /api/control/actions` `work.split` | `params.target_kind` | `subagent` | Control Plane application handler；不得交给Pydantic/FastAPI提前拒绝 | 第一个child Task/Work/Event前验证selector、Graph/TaskRunner |
| `POST /api/control/actions` `worker.spawn_from_profile` | `params.target_kind` | `worker` | 同上 | 任何业务Task/Event前；缺TaskRunner禁止`TaskService.create_task` fallback |
| `POST /api/control/actions` `worker.apply` | `params.plan.assignments[*].target_kind` | assignment既有`subagent` | 整个assignments batch一次性解析与availability预检 | 必须早于descendant cancellation、merge、Task/Work/Event写入；任一项失败全批0副作用 |
| agent runtime tool `subagents.spawn` | tool arg `target_kind` | `subagent` | tool service在batch loop前 | selector/runtime preflight失败时首个child前失败；既有capacity/blacklist运行期partial-accept语义不变 |

严格拒绝测试必须含 `target_kind=docker` + `worker_type=dev`，证明DelegationPlane不得自动改选`graph_agent`。`worker.apply`还必须含 mixed batch（首项合法、后项非法/Graph unavailable）并预置active descendant，断言cancel/create均为0。

## 3. Control Plane audit 与 tool rejection

三个Control Plane action的selector拒绝：

- HTTP 422 / `WORKER_RUNTIME_SELECTOR_UNSUPPORTED`；
- audit按本次`request_id`恰为 `ACTION_REQUESTED` + `ACTION_REJECTED`；
- 新业务Task、child Task、Work、业务Task Event、cancel、Inline/Graph execute均为0。

Graph/TaskRunner请求期不可用：HTTP 503 / `WORKER_RUNTIME_UNAVAILABLE`，同样只有两条Control Plane audit。

测试必须预热懒创建的`ops-control-plane` audit singleton，或按ID/type排除它；禁止断言EventStore/TaskStore总增量为0。`subagents.spawn`不是HTTP/Control Plane action，返回稳定tool rejection/error；其Control Plane audit和`SUBAGENT_SPAWNED`均为0，spawn loop调用为0。

## 4. Graph preflight/execute race

请求期preflight成功后，Graph依赖可能在实际执行前失效。该格不回滚或新建第二Task：

- Control Plane action已经是REQUESTED+COMPLETED；
- 只允许已创建的同一业务Task发生一次terminal `FAILED` transition；
- 新Task=0、Inline execute=0，不产生第二terminal；
- Console backend仍`inline`，metadata `runtime_kind=graph_agent`。

测试需跨Orchestrator/TaskRunner断言terminal transition；只测`WorkerRuntime.run`返回错误不足以证明持久化行为。

## 5. Event decoder 与 Console/API projection

| 项目 | 写侧 | 读/输出侧 |
|---|---|---|
| `ExecutionConsoleService.register_session` | 删除公开`backend`参数；固定写`ExecutionBackend.INLINE`，Graph以metadata表达 | 不接受调用方伪造Docker history |
| raw status event decoder | 不新增模型/registry | 继续读`inline|docker`；未知backend返回稳定projection error，不fallback |
| Console REST | 无backend输入 | `GET /api/tasks/{task_id}/execution`保持`inline|docker`JSON |
| `container_name` | 新runtime不产生容器语义 | 仅历史raw payload/projection字段 |

历史Docker兼容只新增一条L3：向tmp SQLite EventStore注入raw `EXECUTION_STATUS_CHANGED` JSON，再经GET projection验证；禁止调用`register_session(DOCKER)`。现有八个F101 service fixtures不用于历史兼容，删除其backend参数后继续作为L4 normal Inline characterization。unknown backend只由纯L4 projection test覆盖。写侧固定inline消除了omission/default二义性。

## 6. Docker 形态删除/保留

| 形态 | 生产消费者 | 决策 |
|---|---:|---|
| `JobSpec` | 0 | **D-01 已决定删除**；同步core exports/tests/docs |
| `ExecutionRuntimeRecord` | 0 | **D-01 已决定删除**；同步core exports/tests/docs |
| `ExecutionStatusChangedPayload.container_name` | event decode/projection | 仅历史JSON兼容保留 |
| `ExecutionBackend.DOCKER` | decoder/projection | 历史输出兼容保留；不得成为selector |

禁止用被删模型承载历史兼容，也不新增 `LegacyExecutionRecord`、decoder service或backend registry。历史分支内聚在现有decoder/projection。

## 7. 验收矩阵

1. 四入口均有L4严格selector/preflight契约和L3 wiring；三个CP action另有HTTP/audit/side-effect矩阵，tool入口有稳定tool error。
2. 五个delegation target逐值、逐大小写/空白/类型边界测试；profile四值、transient二值、projection二值分别测试，类型不可复用。
3. `worker.apply`全批预检早于cancel；`subagents.spawn`预检早于loop；profile缺TaskRunner不创建孤儿Task。
4. Graph race只使同一Task FAILED一次，Inline0。
5. 新写session全为inline；8个F101 L4 Inline保持；raw Docker历史GET L3恰一条；unknown history L4稳定失败。
6. `JobSpec`/`ExecutionRuntimeRecord`定义、exports与仅存tests/docs引用均为0。
