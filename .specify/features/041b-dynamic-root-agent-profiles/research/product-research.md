# Product Research: Feature 041 — Dynamic Root Agent Profiles

## 结论

041 要解决的不是“再加几个内建 worker type”，而是把 `WorkerProfile` 升成正式产品对象，并让 Butler 能帮助用户创建、审查和调用 Root Agent。

对用户来说，这个 Feature 的价值不在抽象架构，而在三个产品结果：

1. 用户终于能看懂“这个 Agent 是谁、能做什么、边界在哪”。
2. 用户可以围绕 NAS、软路由、打印机、财经等真实场景，积累自己的 Agent 资产，而不是不断复用模糊的 `research/dev/ops`。
3. Butler 不再只是“派固定 worker 类型”，而是真正像管家一样帮用户管理一组长期可复用的 Root Agent。

## 对标观察

## 1. Agent Zero：profile 是对象，不是枚举

参考：

- `_references/opensource/agent-zero/docs/developer/architecture.md`
- `_references/opensource/agent-zero/python/helpers/subagents.py`
- `_references/opensource/agent-zero/python/tools/call_subordinate.py`
- `_references/opensource/agent-zero/python/websocket_handlers/state_sync_handler.py`

产品信号：

- Agent Zero 的 subordinate 不是固定类型表，而是一个可加载、可覆盖、可保存的 profile 对象。
- profile 支持 default / user / project 三层来源合并，这让“通用模板 + 用户定制 + 项目覆盖”成为一等能力。
- 创建 subordinate 时可以直接按 profile 调起，而不是先修改系统内建类型。
- 前端状态同步采用“全量 snapshot + 增量游标”的方式，适合展示复杂 runtime truth。

对 041 的启发：

- `Root Agent` 应该成为用户可管理的 profile 对象，而不是继续依赖固定 worker type。
- 作用域应该支持至少 `system / project` 两层；运行态再通过 effective snapshot 承接 session/work override。
- 运行时必须能回答“这个 work 用的是哪个 profile / 哪个版本 / 哪个实际工具集”。

## 2. OpenClaw：控制面必须像产品，而不是后端投影

参考：

- `_references/opensource/openclaw/README.md`
- `_references/opensource/openclaw/docs/web/dashboard.md`
- `_references/opensource/openclaw/docs/gateway/index.md`

产品信号：

- OpenClaw 把 Gateway 明确定义为 control plane，而不是把原始 runtime 结构直接塞给用户。
- Dashboard 的定位是管理面，强调 auth、状态、入口、下一步动作，而不是暴露实现细节。
- 对普通用户来说，入口是“我的助手能不能用、现在有什么状态、下一步去哪”，不是“去理解 capability pack 和 delegation plane”。

对 041 的启发：

- Agent 管理不能继续停留在“系统模板卡片 + work 聚合列表”的 operator 表达。
- 前端必须明确拆出 `Profile Library / Profile Studio / Runtime Workers` 三层，而不是把模板、草稿、实例混在一起。
- “创建 Agent” 应该像一个产品流程，而不是一个本地 state 小表单。

## 3. 当前 OctoAgent 的产品缺口

### 3.1 Agent 仍然是“一个 Butler + 一组内建 worker 模板”

当前 `Agents` 页虽然已经把 Butler 和 Worker 分开，但 Worker 模板直接来自静态 `capability_pack.pack.worker_profiles`，运行实例也只是把 `delegation.works` 按 `selected_worker_type` 聚合出来。

这意味着：

- 用户无法长期积累自己的 Root Agent；
- 模板和实例都缺少 `profile_id / version / origin / snapshot`；
- “自定义模板”更多是前端草稿，而不是系统正式对象。

### 3.2 产品语言仍然偏系统内建，而不是用户任务域

对用户真正重要的是：

- “NAS 管理 Agent”
- “打印机 Agent”
- “财经追踪 Agent”
- “软路由运维 Agent”

但当前 UI 暴露给用户的是：

- Butler
- Research Worker
- Dev Worker
- Ops Worker

这会直接限制用户对系统能力的心智模型。

### 3.3 Butler 还没成为“Agent 管家”

当前 Butler 的产品叙事已经成立，但实际能力仍主要围绕：

- split / merge / repartition worker
- 选择固定 worker_type
- 赋予有限 tool_profile

离用户期待的“帮我新建一个专门管 NAS 的 Root Agent，并说明它需要哪些工具和权限”还差一层真正的 profile 治理。

## 产品目标收敛

### 目标一：把 Root Agent 变成用户资产

用户应该能：

- 从内建模板 fork 一个 Agent
- 从空白创建一个 Agent
- 从运行实例提炼成 Agent
- 为 project 选择默认 Agent
- 在后续会话和 work 中持续复用它

### 目标二：Butler 负责提案和治理，不直接放权

Butler 可以帮助用户：

- 识别是否需要一个新 Root Agent
- 生成 profile 草案
- 解释需要的工具、风险和适用范围
- 发起 review/apply

但不能绕过治理链路，直接凭空创建越权 profile。

### 目标三：前端必须把“模板 / 正式 Profile / 运行实例”拆开

用户要清楚知道：

- 系统给了我哪些 starter templates
- 我正式保存了哪些 Root Agent
- 现在正在运行的是哪些实例

否则自定义 Agent 一多，系统很快会失控。

## 产品决策

- 041 应定义为 `Dynamic Root Agent Profiles + Profile Studio`，而不是“扩充 worker type”。
- Butler 继续保留为主 Agent / supervisor，不变成任意可删除对象。
- `WorkerType` 在产品层应退化为 starter template 或 base archetype，不再作为用户可见的唯一分类轴。
- 前端应新增 `Profile Library` 和 `Profile Studio`，并把现有 `AgentCenter` 重构成真正的 Agent Console。
