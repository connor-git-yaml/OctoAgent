# Tech Research: Feature 041 — Dynamic Root Agent Profiles

## 1. 当前代码基线

## 1.1 Worker 身份仍然被 `WorkerType` 枚举锁死

证据：

- `octoagent/packages/core/src/octoagent/core/models/capability.py`

现状：

- `WorkerType` 只有 `general / ops / research / dev`
- `WorkerCapabilityProfile.worker_type` 直接使用该枚举
- `ToolIndexQuery.worker_type` 也把工具召回绑定到固定 worker 类型

结论：

- 当前系统里“worker 的身份”和“worker 的默认能力 archetype”是同一个字段。
- 如果继续在这个模型上追加 NAS/财经/打印机/路由器，只会让枚举失控，并把所有前后端标签、统计、路由、工具召回都绑死。

## 1.2 capability pack 已有治理链，但 worker profile 仍是静态构造

证据：

- `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`

现状：

- `_build_worker_profiles()` 直接构建四个内建 `WorkerCapabilityProfile`
- `_classify_worker_type()` 与 `_coerce_worker_type_name()` 最终仍回到固定枚举
- `review_worker_plan()` / `apply_worker_plan()` 已经形成 review/apply 治理闭环，但 assignment 仍以 `worker_type` 为核心字段
- child launch 的 metadata 也还是 `requested_worker_type`

结论：

- 039 已经把“先审再派”主链搭起来了，但它治理的是固定 worker 类型，不是动态 profile。
- 041 最合理的接缝不是推翻 039，而是在它之上把“worker identity”从 `worker_type` 升级到 `profile_id + effective snapshot`。

## 1.3 现有 Agent/Profile 基础设施可以复用

证据：

- `octoagent/packages/core/src/octoagent/core/models/agent_context.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
- `docs/blueprint.md`

现状：

- 主 Agent 已有 `AgentProfile`、owner basics、bootstrap、context frame 等正式对象链路
- blueprint 已明确要求 `AgentProfile / WorkerProfile` 成为正式产品对象，并让 session/automation/work 引用 profile id 与 effective config snapshot

结论：

- 041 不应该发明另一套“动态 Agent 配置模型”。
- 正确方向是镜像现有主 Agent profile 机制，补齐 `WorkerProfile` 的领域模型、存储、projection 和 runtime binding。

## 1.4 前端仍然只认静态模板和运行时投影

证据：

- `octoagent/frontend/src/types/index.ts`
- `octoagent/frontend/src/pages/AgentCenter.tsx`
- `octoagent/frontend/src/pages/ControlPlane.tsx`

现状：

- `WorkerType` 还是前端 union type
- `WorkerCapabilityProfile` 仍是 bundled capability 概念，不是用户对象
- `buildTemplateSeeds()` 直接把 capability pack 里的静态 worker profiles 生成为模板
- `buildWorkAgentSeeds()` 把 delegation works 按 `selected_worker_type` 聚合
- `ControlPlane` 的 delegation 展示也主要围绕 `selected_worker_type`

结论：

- 前端当前没有正式的 `WorkerProfileItem / WorkerProfileRevision / EffectiveWorkerSnapshot / WorkerInstanceProjection` 类型层。
- 如果后端先改成动态 profile，而前端仍按 `worker_type` 看世界，runtime truth 会立刻失真。

## 2. 参考实现的可迁移点

## 2.1 Agent Zero 值得借的是“profile 对象 + 层叠来源”

证据：

- `_references/opensource/agent-zero/python/helpers/subagents.py`
- `_references/opensource/agent-zero/python/tools/call_subordinate.py`

可迁移点：

- subordinate profile 不是 enum，而是对象
- profile 支持 default/user/project 多层来源合并
- spawn 时可以动态指定 profile

不应照搬的点：

- 以目录和 prompt 文件作为唯一事实源
- 缺少 immutable effective snapshot
- 缺少像 OctoAgent 当前这样完整的 policy / event / audit / work lineage 主链

## 2.2 OpenClaw 值得借的是“control plane 表达”

证据：

- `_references/opensource/openclaw/docs/web/dashboard.md`
- `_references/opensource/openclaw/README.md`

可迁移点：

- 把管理面当产品，而不是纯资源页
- 用 readiness / auth / next actions 来组织入口
- 区分 control plane 和 assistant 本身

对 041 的技术要求：

- 不能只补 API；还要同步定义控制面的 profile lifecycle、version diff、runtime lens

## 3. 推荐技术方向

### D1. 领域模型拆分

把当前 `WorkerType` 的职责拆成两层：

- `WorkerArchetype`
  - 内建 archetype，负责默认 runtime kind、默认 tool baseline、默认 bootstrap
  - 仅保留少量系统内建值，例如 `general / ops / research / dev`
- `WorkerProfile`
  - 正式产品对象
  - 至少包含：`profile_id / name / scope / project_id / base_archetype / model_alias / tool_profile / allowed_tool_groups / selected_tools / runtime_kinds / bootstrap overlays / policy refs / version / metadata`

### D2. runtime binding 升级

`Work` 和 child dispatch 应新增：

- `requested_worker_profile_id`
- `requested_worker_profile_version`
- `effective_worker_snapshot_id`
- `requested_worker_type` 退化为兼容字段或派生字段

这样控制面才能回答“这是哪个 profile 派生出来的运行实例”。

### D3. canonical resources / actions 复用 026

041 不应新造私有 `agent-builder/*` backend。

正确做法：

- 读取继续走 `/api/control/snapshot` 与 `/api/control/resources/*`
- 变更继续走 `/api/control/actions`
- 新增 profile 相关 canonical resources/actions，例如：
  - `worker-profiles`
  - `worker-profile-revisions`
  - `worker-profile-review`
  - `worker_profile.create`
  - `worker_profile.update`
  - `worker_profile.clone`
  - `worker_profile.archive`
  - `worker_profile.publish`
  - `worker_profile.spawn`

### D4. 治理规则不能退化

动态 profile 不等于动态越权。

必须保持：

- 工具只能来自 ToolBroker / MCP registry / capability pack
- secret 绑定仍然 refs-only
- profile 设计和发布继续走 review/apply
- 高风险增权必须显式审批

### D5. 迁移策略必须兼容现有系统

推荐迁移方式：

- 现有四个内建 worker profiles 迁移成只读 starter templates
- 现有 `selected_worker_type` 继续保留一段兼容期
- `AgentCenter` 先双读：既能显示 legacy worker type，也能显示新 profile id
- `ControlPlane` 先展示 `profile + archetype` 双字段，避免旧 work 投影失真

## 4. 技术风险

1. 如果直接删除 `WorkerType`，会连带打断 ToolIndex、frontend labels、delegation projection 和大量测试。
2. 如果 profile create/update 绕开 `workers.review/apply`，会破坏 039 刚建立的治理主链。
3. 如果前端继续把“模板/实例/草稿”混成一个列表，动态 profile 上线后会立刻出现对象边界混乱。
4. 如果 runtime truth 不记录 `effective snapshot`，用户永远无法复盘某次 work 为什么拿到了这组工具。
