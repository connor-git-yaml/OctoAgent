# F149 Web 其余页面 v2 — Implementation Plan

> 状态：`GATE_DESIGN=true`；`GATE_TASKS=true`（2026-07-21 main PASS）。Implement 仍由 T000 硬阻断；未获再次明确放行，不得修改 production/tests、执行行为测试、stage/commit/push。
>
> Implement 硬前置：F150/F151 契约稳定且 main 明确放行。前置未满足时，Phase 0 只能复核事实，不能以 optional fallback 或临时手写 DTO 绕过。

## 1. 结果与不可回退约束

F149 交付 A 波 `/approvals`、`/work`、`/tasks/:taskId`、`/automation`、`/settings` 与 B 波 `/agents`、`/memory`、`/files`、`/skills`、`/mcp` 的 v2 页面，并先关闭它们实际消费的有限 REST/action/SSE/secret contract slice。

产品 surface 边界：桌面端保留 Web 入口；手机产品只走原生 iOS App，不把 390px Web/手机浏览器作为移动产品入口。F149 只实施 Web，原生 iOS 实现不属于本 Feature；以下 Desktop/390 双视口中，390 只用于验证 Web 窄浏览器窗口的响应式健壮性，不是手机产品、移动认证或 iOS 验收。Web/iOS 共用 Claude Design 最开始方案的视觉语言；iOS 由其 Feature 用 SwiftUI 与 Apple 原生导航、手势、控件、无障碍语义适配，不复制 Web 组件结构。

视觉单一事实源是已通过 Design Gate 的 `design-output/2026-07-21/OctoAgent Web.dc.html`：

- Claude Design 最开始方案的视觉层级、留白、卡片节奏、信息密度、视觉张力与排版是 Web/iOS 的共同视觉/交互基线；F149 Web 实现适配设计稿，现有 Web UI 仅作 current-state evidence；
- F148 只约束现有 `--cp-*` token、主题/组件边界与功能合同；不得以“对齐 F148”为由回退为旧 Web 的密集、平铺样式。只允许因明确功能合同、可用性、无障碍或 iOS Feature 的 Apple 原生平台规范调整原稿；每项偏离必须记录原因、影响和逐页审查证据，禁止以“实现方便”为理由；
- 不引入 Spotify Design System token/component/theme，不建立第二主题，不新增浅色 palette；
- 不增加 `octoagent/frontend/src/index.css` 行数；页面样式按 surface/domain co-locate 并只消费 `--cp-*`；
- 旧 Claude page 1–3 与 F148 shell 不在 F149 重设计范围，F149 不移除 F148 已有字体/图标依赖。

## 2. 架构决策

### 2.1 依赖方向

```text
Gateway/Pydantic contract source
  ├─ REST OpenAPI artifact
  ├─ F149 action-contract artifact
  └─ Task SSE frame schema
             ↓
generated types-only artifacts（adapter only）
             ↓
api/client transport → api/f149 runtime decoder/error mapper
             ↓
platform queries/actions（application orchestration）
             ↓
pure domain projection/page-state（无 React/Gateway/transport/generated import）
             ↓
page + WorkbenchContext（UI/composition）
```

禁止方向：pure projection → React/Gateway/transport/generated wire；page → raw/generated DTO；Gateway/core → UI；任何 F149 path → 第二 fetch/token/header/401 状态机。

### 2.2 REST、action、SSE 三个契约源

1. **REST**：FastAPI/Pydantic 是唯一源。只补 F149 实际消费端点的 response/request schema；`GET /api/control/snapshot` 保持命名 `RawSnapshotJson` envelope，经 runtime decoder 产生 consumed projection，不闭合全 control-plane。
2. **Action**：以 Gateway control-plane 的 `ActionContractDefinition`（工作名）作为唯一 seam。每条 route record 同时持有 handler、runtime params/result adapter 与 registry metadata；coordinator 由同一 records 派生 dispatch、validation、registry discovery 与 F149 schema artifact。F149 可达 action 使用强类型 Pydantic params/result；非 F149 action 显式保留命名 open schema-as-data，不进行全 registry 强类型 big-bang。
3. **SSE**：Task SSE frame Pydantic/JSON Schema 归 Gateway/web adapter；core 不新增对应模型。只强类型化 envelope、`STATE_TRANSITION.to_status` 与 `ARTIFACT_CREATED` 刷新信号；其他已知/历史事件走 `RawEventPayload → SafeDiagnosticJson`，仅 Advanced 可见。

### 2.3 代码生成

- 选择并锁定 dev-only `openapi-typescript@7.13.0`，仅生成 `.d.ts`/types-only artifact，不生成或引入 fetch client；该工具的[官方 CLI 文档](https://openapi-ts.dev/cli)与[npm 发布页](https://www.npmjs.com/package/openapi-typescript)确认支持本地 OpenAPI 3.0/3.1 输入与 runtime-free types。
- 生成三份隔离 artifact：REST、F149 actions、Task SSE；后两者由各自唯一源导出为最小 OpenAPI-compatible schema document，再由同一 types-only generator 转换。
- generated 只允许 adapter import；domain/page import 由 boundary checker 拒绝。
- `openapi:generate`/`openapi:check` 及 checker 当前不存在；必须先用 inert scaffold + seeded bad fixture 得到行为断言 RED，再实现 checker/npm alias。alias 必须先用 worktree-locked `uv --no-sync` exporter 从 Gateway 唯一源生成/校验 schema，再运行 types-only generator；文件不存在、import error 或 0 tests 不算 RED。

### 2.4 唯一 transport 与 application seam

- `frontend/src/api/client.ts` 保持唯一认证 transport；F149 adapter 通过现有 `frontDoorRequest`/统一 error builder，不在 helper/page 读取 token。
- `platform/queries/actions` 负责 load/action/retry/origin-403/not-found/conflict 编排；页面不得解释 401。
- 页面可以继续使用 WorkbenchContext；只有真实多实现或隔离价值才引入 TypeScript structural port，不创建 per-page service/registry/store。
- 404/409/403 在 adapter/error mapper 分别映射 not-found/conflict/resource-permission；401/session/logout 只交给 F150 global Access Gate。

### 2.5 write-only secret

Settings/MCP 共用语义但不新增 global secret registry：

- read=`name/configured/redacted_summary`；value 在服务端出站前移除；
- mutation=`keep|replace|remove`，只有 replace 可携带非 placeholder value；
- masked input 是 ephemeral，成功、失败、关闭均清空；placeholder 永不进入 request/state/clipboard；
- response/list/get/snapshot/action/SSE/error/log/DOM/clipboard/evidence 均不得出现 sentinel/value；
- 复用 Settings `setup.apply` 与 MCP catalog/save/install 现有 application/store seam。

## 3. 实施阶段与 Gate

### Phase 0 — Implement 前置与基线冻结

- 复核 `origin/master`、F150/F151 completion/contract evidence 与 main Implement 放行；任何一项缺失即暂停。
- F151 stable commit 落地后先 rebase/recon：重新完整读取届时的 `octoagent/tests/AGENTS.md`、`octoagent/frontend/playwright.config.ts` 与 F151 stage profile。Python/Playwright 只能消费 post-F151 canonical profile（`core/provider/protocol/tooling/skills/policy/memory` 七个保留 workspace packages + Gateway）；`packages/sdk/src` 必须 absent。若实际 canonical package set 与本计划不一致，立即回 Tasks Gate，禁止兼容已退休 SDK。
- 固定当前基线：direct fetch=10、token helper=2、F149 bypass cluster=4、`index.css` wc=4476、import SCC=0、secret leak=3。
- 冻结 Design artifact hash `a2db08ea0eb39278558e61e87a355b042c98ad24273b201940925f310271d98a` 与 20 frame 名称；禁止实现阶段自行重画。

**Gate**：只有上游契约与 main 放行均满足，才进入行为 RED。

### Phase 1 — Quality checker 与证据基础设施

- 为 OpenAPI clean-diff、F149 boundary、style ratchet、frontend changed-lines coverage 各建 seeded fixture；先以仅有 CLI 壳、尚未实现规则的状态稳定见红，再实现 checker。
- 增加 `openapi-typescript@7.13.0`、与现有 Vitest `2.1.9` 匹配的 `@vitest/coverage-v8@2.1.9`，生成 lock diff；不得引入 generated fetch client。
- 新增 `test:coverage`、`openapi:generate`、`openapi:check` alias；frontend coverage 只统计 authored executable TS/TSX，排除 tests、generated、`.d.ts`、type-only 行。
- checker 必须 AST/结构化解析真实 source；说明文字中的禁令不参与判定。

**Gate**：seeded violation 必须被捕获；真实基线允许以明确 baseline mode 通过，不能把缺 script 当 RED。

### Phase 2 — Gateway 有限 contract slice

1. REST consumed response models、raw snapshot envelope decoder 与 deterministic artifact exporter。
2. action registration 收敛到单一 `ActionContractDefinition` seam；F149 actions 强类型，其他 actions 保持显式 open schema-as-data。
3. Task SSE adapter frame schema、final 必填、业务 payload decoder 与 scrubbed diagnostic boundary。
4. Settings/MCP write-only secret request/response、placeholder rejection 与全出站 scrub。
5. deterministic L3 经 DI fake/Echo 串联 snapshot/action/SSE/secret egress；不使用网络、真 LLM、宿主状态或固定 sleep。

**Gate**：Gateway L4/L3 全绿；schema artifact clean；sentinel 在 body/frame/error/log 为零；core 无 Task SSE contract model。

### Phase 3 — Frontend generated contract、adapter 与单一 transport

- 生成 REST/action/SSE types-only artifact；adapter 用 runtime decoder 将 raw boundary 转为 F149 view projection。
- approval/memory helper、Agent auxiliary、Skill REST 全部改用唯一 transport；删除 10 个 F149 direct fetch 与 2 个 token helpers。
- application actions 为实际 action 提供 typed command/result wrapper；页面不直接构造 raw params/result。
- TaskDetail 只消费 typed state transition/artifact refresh；unknown/history 只能进入 scrubbed Advanced diagnostic。

**Gate**：F149 closure direct fetch/token/header/query-token/page-401=0；generated import 只在 adapter；命名 raw boundary 外 `any`=0、`unknown/JsonValue`=0。

### Phase 4 — Shared page state、视觉 primitive 与 auth ownership

- 建立共享但不集中业务的 page-state/view primitives：loading、empty、recoverable error、origin-403、not-found、disconnected、409；每个 surface 只声明适用矩阵与用户文案。
- 保持 F150 global 401/Access；F149 origin-403 留在 shell 内。
- 实现 reduced-motion、focus return、accessible name、长内容截断/复制 sensitivity policy。
- 样式按 domain/surface co-locate，仅使用 `--cp-*`；不增加 `index.css`。

**Gate**：L4 参数化 shared-pattern tests 通过，未产生 A/B 巨型 test 文件；style/boundary checker 通过。

### Phase 5 — A 波页面

在任何 A/B 页面 GREEN 前，先执行 T051–T054 中与该页面对应的 L1 RED lane；Phase 7 只负责这些已见红 spec 的 GREEN/REFACTOR 与统一收口。任务编号分组不代表可以把 L1 RED 延后到页面实现之后。

1. **Approvals**：保留 F145 三类候选、分来源错误、404/409 mapper；不并入通用 tool approval。
2. **Tasks/TaskDetail**：任务状态人话 projection；“待处理事项”非零显著/0 隐藏；不新增 cancel/resume；Recovery 从 Tasks 移除；SSE diagnostics 分层。
3. **Automation**：只 pause/resume；cron/job/action 只进 Advanced。
4. **Settings**：以窄 `MaintenanceRecoverySection` 组合 summary/backup/export/update/restart/verify；backup/apply/restart 强确认，export 范围，apply 依赖最近有效 dry-run，restart 说明短暂不可用。Settings 普通区使用用户语言，不继续扩大 God component。

**Gate**：A 波各 domain L4/component tests 全绿；Design Desktop/390 Web 窄窗口逐 frame review 无视觉回退。

### Phase 6 — B 波页面

1. **Agents**：保留 `behavior.restore_version`，Agent/worker/resource action typed；raw id/path/model alias 只在 Advanced。
2. **Memory**：不接入 dead `MemoryActionsSection` 管理动作；移除 `/advanced` 死链；保留实际 query/consolidate/SOR/retrieval actions。
3. **Files**：file/git/rollback projection 与 path sensitivity；浏览器 file chooser 只在 L1 验真实语义。
4. **Skills**：list/detail/install/delete 统一 transport；普通区不显示 raw schema/tool name。
5. **MCP**：catalog/save/delete/install/status typed；secret/env write-only；polling 使用可取消/可控状态，不用固定 sleep 断言或静默 catch。

**Gate**：B 波 co-located L4/component tests 全绿；MUST FIX smell 清零，ratchet 不退。

### Phase 7 — L1 GREEN/REFACTOR 与全路由收口

- L1 spec 的 RED 必须在对应页面 GREEN 之前创建并稳定失败；Phase 7 负责统一收口 GREEN/REFACTOR，不允许先改完 UI 再补一个首次即绿的 L1。
- 以 test-only config contract 钉住 post-F151 canonical worktree profile：`core/provider/protocol/tooling/skills/policy/memory` 七个保留 workspace packages + Gateway，retired SDK/ambient/host path 必须 absent；移除 CI blanket retry。若 rebase 后实际 config 已满足，保持 production 零改动；确定性 L1 抖动必须修复或按正式 quarantine 机制处理。
- `f149-responsive-a11y.spec.ts`：10 个 Web surface 在 390px 窄窗口只测 geometry overflow、可感知名称、键盘/focus/reduced-motion；不作为手机产品、移动认证或 iOS 验收。
- `f149-a-wave.spec.ts`：一条 A 波关键用户旅程。
- `f149-b-wave.spec.ts`：一条 B 波含 modal/file chooser/focus return 的旅程。
- `f149-auth-boundary.spec.ts`：一条 F150 401 global Gate / F149 origin-403 page state 交界。
- SSE/EventSource、clipboard、DOM secret absence 只验证浏览器独有语义，不穷举 L4/L3 已覆盖分支。

**Gate**：四个 L1 spec 通过；不使用 blanket rerun、固定 sleep 或真 LLM。

### Phase 8 — Verify、坏味道审计与 living docs

- 逐 task 核对真实 RED/GREEN/REFACTOR evidence、secret scan 与 command policy；atomic relocation 仅在实现实际出现纯搬迁时单独拆分。
- 生成 `review/code-smell-audit.md`，每项包含 source:line、baseline/current/delta、判定、owner、证据；mechanical 与 adversarial review 分开。
- 运行 frontend types/Vitest/complexity/build、Gateway L4、deterministic L3、L1、OpenAPI/boundary/style/coverage gate；changed-lines authored executable TS/TSX ≥90%。
- 对照 Design export 生成 20-frame implementation screenshot/checklist；视觉层级与留白偏离视为失败，不以 token 一致替代视觉一致。
- 同步 `docs/blueprint/milestones.md` 与 `docs/codebase-architecture/modules/06-frontend-workbench.md` 的 auth/contract/sensitivity/scope；completion report 不能替代 Blueprint。

**Gate**：0 HIGH review finding、MUST FIX=0、ratchet 无回退、所有 SC 有证据。不得自动 commit/push。

## 4. 目标文件落点

| 职责 | 目标落点（允许 Plan 后按现有命名微调，但不得改变职责） |
|---|---|
| Gateway REST/action/SSE/secret contract | `octoagent/apps/gateway/src/octoagent/gateway/{routes,services/control_plane,contracts}/` |
| Gateway L4 | `octoagent/apps/gateway/tests/test_f149_{web_contract,task_sse_contract,secret_egress}.py` |
| deterministic L3 | `octoagent/tests/integration/test_f149_web_contract.py` |
| schema exporter/artifacts | `repo-scripts/export-f149-contracts.py`；`octoagent/frontend/src/generated/f149/` |
| unified transport adapters | `octoagent/frontend/src/api/f149/`，底层只调用 `api/client.ts` |
| application orchestration | `octoagent/frontend/src/platform/{queries,actions}/f149*` |
| pure projection/state | 各 `src/domains/<surface>/` co-located mapper/state；不建总 A/B domain |
| shared UI state pattern | `src/ui/` 或 `src/domains/shared/` 的窄 primitives；不承载 surface 业务规则 |
| maintenance composition | `src/domains/settings/MaintenanceRecoverySection.tsx` + co-located test/style |
| styles | 各 surface/domain co-located `*.css`，只用 `--cp-*`；`index.css` 零增长 |
| L1 | `octoagent/frontend/e2e/f149-*.spec.ts` + existing support/launcher narrow fixture |
| checkers | `octoagent/frontend/scripts/`、`repo-scripts/check-frontend-changed-lines-coverage.mjs` |

不创建：per-page service/port/registry、management service、第二 store/transport/theme、全局 secret registry、core SSE model、A/B 巨型 test 文件。

## 5. 测试分层与覆盖归属

| 层 | 只负责 |
|---|---|
| L4 frontend | decoder/projection/page state/error/permission/secret/a11y/component action；十页按 domain co-locate |
| L4 Gateway | response/action/SSE/secret contract 与 runtime validation；tmp SQLite + DI fake |
| L3 deterministic | bootstrap/API/storage/action/SSE/secret egress 拼接；Echo/DI stub，CI-runnable |
| L1 Playwright | 390px Web 窄窗口 geometry、真实 focus/modal/file chooser/EventSource/clipboard/DOM 与 A/B/Web auth 最小旅程；不覆盖移动认证或原生 iOS |
| L2 live | 不新增；F149 没有必须由真 LLM/外部系统判断的事实 |

changed-lines ≥90% 只是一条最低门；不替代测试层级、oracle、架构或设计质量。

## 6. TDD 与 atomic relocation

- 每个行为 task 必须按 `RED_COMMAND → GREEN_COMMAND → REFACTOR_COMMAND`，三个命令从仓库根独立可复制且验证同一 oracle。
- RED test/fixture 必须先存在且被收集；失败只能来自目标行为断言。
- Implement 时 evidence 落 `evidence/tdd/<task-id>/`，记录命令、输出、exit、UTC、HEAD SHA、工作树状态、oracle 与 secret scan。
- 本计划没有预先宣称 atomic relocation。若实现时发现纯文件搬迁，必须从行为 task 拆出，并提供 before/after contract、旧路径 absence、import gate 与纯 location/import diff；行为与视觉变化不得伪装为 relocation。

## 7. 风险与停止条件

| 风险 | 停止/缓解 |
|---|---|
| F150/F151 contract 尚未稳定 | 停在 Phase 0，不写 fallback DTO/auth |
| action seam 需要闭合全 registry | 只统一 source record；强类型仅 F149 可达 action，其他保持显式 open schema-as-data |
| Settings/Agent God component继续增长 | 只拆真实 pure projection/section seam；复杂度 ratchet，不 big-bang |
| Design 被“token 对齐”压扁 | 20-frame review 同时检查层级/留白/节奏/密度/张力；token 一致不等于通过 |
| L1 膨胀 | 业务分支下沉 L4/L3；L1 只能给出浏览器独有理由 |
| secret evidence 自身泄漏 | 只用合成 sentinel，保存 evidence 前扫描；真实 secret 永不进入测试 |
| 产品/合同分叉 | 立即回 Gate；普通实现细节或文件命名不触发微型 Round |

## 8. Plan Gate 退出条件

- Design PASS 与视觉基线已写入 Plan/Tasks；
- REST/action/SSE/secret 的唯一 source 与生成/decoder 边界明确；
- 所有 FR/SC 映射到任务、层级、文件、完整命令和失败 oracle；
- checker 不存在的 prerequisite 有合法 RED 设计；
- TDD、测试分层、架构分层、坏味道审计均进入 Tasks/Review；
- `GATE_TASKS=true` 只表示 Plan/Tasks 已通过；F150/F151 stable commit、rebase/recon 与 main 再次明确 Implement 放行未齐前，T000 保持 CLOSED，不得 Implement。
