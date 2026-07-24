# F149 Endpoint / Stream Contract Manifest

> 基线：`origin/master@9d5e1e48691c5ae5a12b33f224d64ac03d5442fc`，2026-07-20 codebase scan。本文只冻结 F149 实际消费的 contract slice，不授权生产实现，也不把开放 JSON 伪装成闭合模型。

## 1. JSON 与类型边界

| 边界 | 允许 | 禁止 |
|---|---|---|
| F149 新增/触达的 wire、projection、action、SSE 代码 | 明确字段的 generated/Pydantic DTO；UI view model/command | `any`；用裸 `object` 或 `Record<string, unknown>` 冒充已验证业务 DTO |
| 命名 transport boundary | 递归 `JsonValue`、`RawSnapshotJson`、`RawEventPayload`、`MetadataJson`、`JsonSchemaDocument` | 未命名的 `unknown` 漂入 application/domain/page |
| schema-as-data | JSON Schema 中合法的 `{ "type": "object" }` | 把 schema-as-data 当作 action params/result 已强类型的证明 |
| domain/UI | 只接收 runtime decoder/type guard 产出的 F149 consumed projection | 直接读取 raw snapshot/event/metadata；把全 control-plane 强行闭合 |
| Advanced diagnostic | 经结构校验、secret scrub、大小/深度限制后的 `SafeDiagnosticJson` | raw payload 直接渲染、复制或参与业务状态机 |

F149 实际 contract slice 的新增/触达代码中，`any` 一律失败；这不是清扫全仓历史 `Any` 的 big-bang 授权。`unknown`/`JsonValue` 只能停在上述明确命名的原始 transport、metadata 或 schema-as-data 边界；每个进入 F149 domain/UI 的实际字段都必须经过 decoder/type guard。开放 JSON 是有意义的扩展边界，不要求 F149 闭合 config schema/current value、所有 event payload、registry schema、metadata/details 或完整 control-plane。

## 2. 现有目录与唯一数据路径

| 职责 | 现有目录/对象 | F149 规则 |
|---|---|---|
| 唯一 transport | `frontend/src/api/client.ts` | 唯一认证 request/SSE URL 底层；generated client 仅在可注入此 transport 时允许，默认只生成 types |
| application orchestration | `frontend/src/platform/queries/*`、`platform/actions/*`、`useWorkbenchData` | load/action/retry/资源级 403 编排；不复制后端规则 |
| pure projection/state | 各页面可抽取的 mapper/reducer/view model | 不 import React、Gateway、transport 或 generated wire DTO |
| UI/composition | page、`WorkbenchContext`/`WorkbenchLayout`、route composition | page 可消费 WorkbenchContext 与 pure projection；不得 direct fetch、拼 token、另建 store |

Application Port 仅在真实多实现或隔离测试价值存在时引入 structural seam；不按页面/endpoint 配发 interface/service/registry。`domains/` 当前也包含 React UI，不能仅凭目录名断言它是纯 domain；architecture checker 必须按文件职责/import graph 判定。

## 3. Snapshot：选择 raw envelope → F149 projection

`GET /api/control/snapshot` 当前由 `_coordinator.py:646-761` 返回未建模的 `dict[str, Any]`。完整 envelope 包含：

- `status`、`contract_version`、`resources`、`registry`、`degraded_sections`、`resource_errors`、`generated_at`；
- resources：`config`、`project_selector`、`sessions`、`agent_profiles`、`worker_profiles`、`owner_profile`、`bootstrap_session`、`context_continuity`、`capability_pack`、`skill_governance`、`mcp_provider_catalog`、`setup_governance`、`delegation`、`diagnostics`、`retrieval_platform`、`memory`。

当前 frontend `ControlPlaneSnapshot`（`types/index.ts:1855-1877`）列出大部分 resources，却漏掉 `status`、`degraded_sections`、`resource_errors`，因此不是完整 wire envelope。

F149 冻结的方向是：

1. adapter 以 `RawSnapshotJson` 接收并先验证 envelope 基础字段；
2. 只对 F149 实际消费的 config/project/agent/worker/capability/skill/MCP/setup/retrieval/memory resources 建 generated-or-decoded projection；
3. owner/bootstrap/context/delegation/diagnostics 等非 F149-owned 聚合字段保留在命名 raw/兼容边界，F149 不重写其全模型；
4. raw resource 不能进入页面。section 缺失/错误映射为 degraded/error/permission view state；
5. RED：当前手写 snapshot 漏 envelope 字段、raw resource 可直接穿透时稳定失败。

## 4. REST endpoint manifest

| Surface | Endpoint | 当前事实 | F149 目标/允许边界 | RED |
|---|---|---|---|---|
| Shared | `GET /api/control/snapshot` | response model 缺失、聚合开放 | §3 raw envelope + consumed projection | `CP-SNAPSHOT` |
| Control resources | `GET /api/control/resources/{config,project-selector,agent-profiles,worker-profiles,worker-profile-revisions,skill-governance,mcp-provider-catalog,setup-governance,automation,retrieval-platform,memory}` | route response model 缺失/不齐 | 对实际消费 document 增 response schema；开放 schema/details 仍为命名 JsonValue | `CP-RESOURCES` |
| Actions | `GET/POST /api/control/actions` | envelope dynamic；registry params/result 多为 schema-as-data object | §5 同源 action contract；UI 只留 intent command/view model | `CP-ACTIONS` |
| Tasks | `GET /api/tasks` | list response 已有 | OpenAPI types-only → task row projection | `TASK-REST` |
| Task detail | `GET /api/tasks/{task_id}` | response schema 缺失 | Pydantic response → OpenAPI types-only | `TASK-REST` |
| Approvals | memory/consolidation/behavior compact candidate list/mutations + `/api/approval-center/summary` | list 部分有 schema，mutation 多数缺 | F145 三类有限 slice；404/409 由 adapter mapper | `APPROVAL-REST` |
| Agent auxiliary | approval overrides、behavior versions/diff | response model 已有 | OpenAPI types-only；统一 transport | `AGENT-AUX` |
| Skills | list/detail/install/delete | response model 已有 | OpenAPI types-only；统一 transport | `SKILL-REST` |
| Files | task files/diff/versions + workspace-git read/two-phase rollback | response model 多数已有 | OpenAPI types-only | `FILE-REST` |
| Tasks 管理入口 | operator inbox/actions | 当前 `OperatorInboxPanel` 真实渲染于 TaskList | 保留在 Tasks，但 UI projection 只叫“待处理事项”：非零显著可进入、0 项折叠/隐藏；不得与 F145 三类候选合并 | `TASKS-PENDING` |
| Settings 维护与恢复 | recovery summary/update status、backup/export、update dry-run/apply、restart/verify | 当前 `RecoveryPanel` 错置于 TaskList；API 已通过 `api/client`，部分 response schema 缺 | endpoint/handler 保留，UI 归位 Settings → Advanced → 维护与恢复；backup/apply/restart确认、export范围、dry-run-before-apply与restart不可用说明进入 view state | `SETTINGS-MAINTENANCE` |

优先生成 types-only artifact。generated wire DTO 只在 adapter；页面可手写互斥 page state、展示 view model 与用户意图 command，不得手写 request/response envelope。

上述归位只改变 F149 UI/composition，不迁移 endpoint service ownership。生产阶段应把维护 projection/交互拆为窄的 `MaintenanceRecoverySection` 一类组件，由 Settings 组合并继续消费现有 application/`api/client` 状态；不得新增页面、management service、registry、transport 或第二状态源。

## 5. Action import/call graph 与 registry drift

### 5.1 目标页面真实可达 action

| Surface | 真实页面调用 |
|---|---|
| Automation | `automation.pause`、`automation.resume` |
| Settings | `setup.review`、`setup.apply`、`setup.oauth_and_apply`、`setup.quick_connect`、`agent_profile.update_resource_limits` |
| Agents | `behavior.read_file`、`behavior.write_file`、`behavior.restore_version`、`agent.create_worker_with_project`、`worker_profile.review`、`worker_profile.apply`、`worker_profile.archive` |
| Memory | `memory.query`、`memory.consolidate`、`memory.sor.edit/archive/restore`、`retrieval.index.start/cancel/cutover/rollback` |
| MCP | `mcp_provider.save/delete/install/install_status` |

`BehaviorVersionHistory.tsx:134` 真实调用 `behavior.restore_version`，因此必须进入 Agent contract 与 Prompt。

`MemoryActionsSection.tsx` 没有任何生产 importer，只有自身声明；其 `diagnostics.refresh`、`backup.create`、`session.export` 及 shared map 中的 operator/channel 管理动作不属于当前 Memory page 可达 contract，已从 F149 Memory manifest 移除。它作为 dead/unreachable smell 进入审计，不因文件存在就扩域。

### 5.2 handler/registry 不同源事实

`build_action_registry()`（`action_registry.py:20`）默认 `params_schema`/`result_schema` 为开放 object（53-54），并与 service `action_routes` 分开声明。以下 F149 调用已有 handler，但 registry 当前未声明：

| Action | handler 证据 | registry |
|---|---|---|
| `agent_profile.update_resource_limits` | `agent_service.py:74` | 缺失 |
| `behavior.read_file/write_file/restore_version` | `worker_service.py:91-93` | 缺失 |
| `memory.consolidate` | `memory_service.py:105` | 缺失 |
| `mcp_provider.install/install_status` | `mcp_service.py:72-73` | 缺失 |

Plan Gate 必须选择一个唯一 seam，使 handler dispatch、runtime validation、registry discovery 与生成 artifact 来自同一 action contract；不能只补 frontend union，也不能一次闭合全 registry。F149 仅为上述真实可达 actions 建强类型 params/result projection；registry 的其他开放 schema-as-data 保持命名 boundary。

## 6. Write-only secret mutation contract

源码事实：

- MCP catalog 在 `mcp_service.py:123-151` 把 `config.env` 实值直接放入 response；MCP edit 又在 `McpProviderCenter.tsx:56-63` 回填编辑框。这是 F149 必修的出站漏洞。
- MCP save/install 接收 raw env map（`mcp_service.py:219-226,321-351`）。
- Settings `setup.apply` 接收 `draft.secret_values`（`setup_service.py:881-990`）；普通 apply 成功后 `SettingsPage.tsx:372-405` 没有统一清空 secret state。
- config 模型保存的是 env 名/OAuth profile 引用而不是 credential value；`get_config_schema` 仍把完整 `current_value` 放入开放 config document。

F149 有限 slice 冻结：

```text
read: SecretFieldSummary { name, configured, redacted_summary }
edit input: ephemeral masked value，初始永远为空
mutation: { mode: keep | replace | remove, value?: string }
```

- `keep`/`remove` 禁止携带 value；`replace` 必须携带非占位值。
- UI redacted placeholder 只是展示，不得进入 draft、clipboard 或 request；adapter/runtime validation 必须拒绝占位符回写。
- 成功、失败、关闭 modal/向导后均清空 ephemeral value；不得进入持久化 page state、URL/session/local storage。
- list/get/snapshot/action response/SSE/error/log 只能出现 name/configured/redacted summary；不得回显 value。
- MCP catalog/read/save/install 属 F149 直接有限 slice，不另建系统。
- Settings 与 MCP 的 write-only HTTP/application representation 都由 F149 的同一窄 contract slice承担，但不建立全局 secret registry 或第二 transport。Settings 复用 exact seam：`SetupDomainService._handle_setup_apply` + `SetupConfigIOMixin._save_runtime_secret_values` + frontend `buildSetupDraft/handleApply`；MCP 复用现有 catalog/save/install application boundary。
- F151 只负责既定底层 runtime/package/secret reference 边界，不拥有本 HTTP representation，也不是 F149 的 security Fix 前置。F149 必须直接把 read summary、keep/replace/remove validation、placeholder 拒绝与全出站 scrub 固定在上述现有 seam。

## 7. Task SSE 属 Gateway/web adapter contract

`TaskSSEEventData` 不放 core domain。唯一 schema/decoder 落在 Gateway SSE/web adapter；Gateway L4 验证 `_event_to_sse_data`、历史回放、实时帧与 secret scrub，前端 adapter 从独立 JSON Schema/types-only artifact消费。

TaskDetail 当前实际业务消费：

| event | typed payload projection | 业务用途 |
|---|---|---|
| all accepted events | envelope `event_id/task_id/task_seq/ts/type/actor/final` | 去重、排序、连接终止 |
| `STATE_TRANSITION` | `{ to_status: TaskStatus }` | 仅当前 task 且 seq 单调时更新 badge |
| `ARTIFACT_CREATED` | 无需读取 payload | 触发 detail/artifact refresh |
| 其他已知/历史扩展 | 经 scrub/size-limit 的 `SafeDiagnosticJson` | 只进 Advanced timeline，不参与业务 mapper |

普通 OpenAPI 只声明 `text/event-stream`/错误 response，不是帧 data schema。未知历史/扩展事件必须停在 `RawEventPayload` → diagnostic decoder 边界；不要求一次闭合全部 `EventType`，也不得把 raw unknown 传给 `classifyEvents`、状态 reducer 或 DOM。`final` 是 frame 必填布尔值；当前 frontend optional 是 RED oracle。

## 8. Secret 出站与测试 oracle

所有 secret 测试只使用合成 sentinel `F149_SECRET_SENTINEL_DO_NOT_STORE`，不得把真实 secret 写入 evidence。

| 层 | 目标 | oracle |
|---|---|---|
| Gateway L4 | MCP/config read、secret mutation decoder、SSE frame、error mapper、log scrub | response/frame/error/captured log 均不含 sentinel；keep/replace/remove 与 placeholder 拒绝精确 |
| L3 | snapshot/action/task SSE 经 DI fake/Echo 的完整出站 | HTTP body、SSE bytes、structured error/log 无 sentinel；不访问网络/宿主状态 |
| Frontend L4 | adapter → view model、modal state、submit payload | DOM/props/clipboard mock 无 sentinel；submit 后 state 清空 |
| L1 | 仅真实 clipboard/DOM 浏览器语义 | 页面与 clipboard 无 sentinel，不穷举后端分支 |
| TDD evidence | stdout/stderr/YAML/raw output | 保存前 secret scanner 对合成 sentinel 零命中；真实 secret 永不得作为测试输入 |

## 9. Python RED command（未来测试文件先创建）

以下命令均可独立从仓库根复制。测试文件必须先由行为 RED task 创建；RED 只能是目标断言失败，不能是文件不存在/import error/0 tests。

```bash
cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/packages/sdk/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_web_contract.py -k "snapshot or resource or action_registry"
cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/packages/sdk/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_task_sse_contract.py
cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/packages/sdk/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_secret_egress.py
cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/packages/sdk/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q tests/integration/test_f149_web_contract.py
```

`apps/gateway/tests/*` 是 L4；`tests/integration/*` 是 deterministic L3。不得把 SSE contract test放入 core package。

## 10. 单一 transport completion oracle

F149 触达闭包（page/component/domain/platform/api helper）最终必须满足：除 `api/client.ts` 与 F150 指定的 SSE builder 外 direct fetch、token/header/query-token 拼接、页面 401 解释均为 0。当前 baseline 为 10 个 F149 direct fetch call：SkillCenter 4、AgentCenter 2、approval helper 3、memory candidate helper 1；`BuildVersionWatcher` 的 1 个 shell fetch 不属于 F149 closure，单独由 shell owner 管理。

404/409/403 分别映射 not-found/conflict/resource-permission，不复制鉴权。architecture checker 尚不存在；在未来 Tasks 先以 checker fixture 见 RED、再创建脚本前，不能把其命令宣称为现成 Gate。
