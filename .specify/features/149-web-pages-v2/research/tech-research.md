# F149 技术研究：页面 v2 与 OpenAPI 契约

> 研究类型：codebase-scan。未联网、未引入新依赖；版本结论均来自 2026-07-20 的 `origin/master`。

## 1. 问题定义

F149 需要让九个一级页面和任务详情达到 F148 的视觉、语言、响应式和状态质量，同时把网络 DTO 收敛到 OpenAPI 生成方向。难点不在“换皮”，而在当前页面混合了直接 fetch、手写 DTO、snapshot/action、局部轮询和业务展示规则；若逐页直接重画，会固化第二条数据主路径。

## 2. 候选方案

### 方案 A：逐页视觉改造，保留当前数据访问

- 做法：页面原地改 class/style/text，保留 direct fetch、根 types 与当前局部 state。
- 优点：短期改动面小。
- 缺点：继续扩大手写 DTO、认证分叉和页面业务规则；permission/error 会按页漂移；无法满足 OpenAPI 与分层硬门。
- 结论：拒绝。

### 方案 B：稳定有限 contract slice 后，以 generated types + runtime decoder + 现有 platform seam + UI view model 分波改造

- 做法：F150/F151 稳定后固定 REST/OpenAPI；在 Gateway/web adapter 固定 TaskDetail 实际消费的 SSE frame；从同一 action contract 派生 dispatch/validation/registry/artifact；优先生成 wire types-only artifact。开放 snapshot/schema/metadata 保持命名 raw boundary，以 runtime decoder 产出 F149 projection。复用现有 `api/client → platform queries/actions → pure projection → page/WorkbenchContext`。
- 优点：符合单一数据主路径和认证契约；可用 L4 精确验证 mapper/state/a11y，用少量 L1 验证浏览器语义；支持增量 ratchet。
- 缺点：依赖上游 schema 完整；需要先做有限 contract slice，不能立刻开始页面实现。
- 结论：推荐。

### 方案 C：重写整个 frontend platform 与所有旧页面

- 做法：一次替换根 types、API client、全部 CSS 和页面架构。
- 优点：理论上最整齐。
- 缺点：范围不可控，跨越 F149/F150/F151，难以建立行为级 RED oracle，极易形成 big-bang 回归。
- 结论：拒绝。

## 3. 推荐架构

- `api/client` 保持唯一底层认证 transport。
- `platform/queries/actions` 与 `useWorkbenchData` 承担 application orchestration、retry 与资源级 permission。
- pure projection/state 只做确定性转换；禁止 React、Gateway、transport/generated wire import。
- page 与 WorkbenchContext 是 UI/composition；页面可消费现有 context 与 pure projection，不要求人为绕一层 per-page service。
- generated artifact 只表达 wire schema；禁止页面文案、颜色或派生状态。

`WorkbenchContext` 的 snapshot/action 仍是 control-plane 主路径。独立 routes 也通过同一个认证 transport 进入 adapter。优先生成 types-only；generated client 只有能注入现有 transport 时才允许，不能建立兼容 fallback 或第二 fetch client。`domains/` 目录当前也放 React UI，因此依赖门按真实文件职责/import graph，而非目录名称机械推断。

Application Port 是 seam 语言，不是实体配额：不得为每页、每 endpoint 建 interface/service/registry，也不得建立第二状态容器。只有真实多实现或隔离测试价值时，才新增 TypeScript structural port。

## 4. OpenAPI 可行性

FastAPI 已提供 OpenAPI，但当前并非所有核心端点都能生成有用 schema：独立 approvals/files/skills/workspace-git routes 多数有 `response_model`，而 canonical control-plane snapshot/resource/action 仍缺明确 response schema。`/api/stream/task/{id}` 的 SSE data 不能由普通 JSON OpenAPI自然表达；action envelope 的 params/result data 仍为动态字典。任何 generator 都无法弥补这些源 schema 缺失。

因此技术顺序必须是：

1. 等待 F150/F151 稳定公开/auth/config 契约；
2. 按 `contract-manifest.md` 补全 F149 REST consumed response schema；snapshot 用 raw envelope decoder；Task SSE schema 放 Gateway/web adapter；action dispatch/validation/registry/artifact 选同源 seam；
3. 选择一个 dev-only generator，优先生成 types-only artifact 到独立目录；
4. 通过 import gate 禁止页面导入 generated wire type；
5. 删除 F149 路径的手写 DTO，不保留 optional fallback。

本 Gate 不指定 generator 包与版本：仓库当前没有既定工具，而源 schema 尚未稳定。Plan Gate 应以稳定 artifact、可重复 diff、枚举/nullable/discriminated union 支持、types-only 和无 runtime 依赖为首选。若生成 client，必须证明它注入现有 transport，不能自带 fetch/auth。

F149 新增/触达的 contract/projection 禁止 `any`，但不对非 F149 历史代码做 big-bang 清扫。递归 `JsonValue`/`unknown` 只允许停在明确命名的 raw snapshot/event、metadata 或 schema-as-data transport boundary，经 decoder/type guard 后才能进入 F149 domain/UI；JSON Schema 的 `{type: object}` 是合法数据，但不能证明 action params/result 已强类型。只为 F149 实际消费字段/action 建 projection，不闭合全 control-plane。

## 4.1 源码契约事实增量

- `GET /api/control/snapshot` 是完整开放聚合 envelope，而当前手写前端类型漏 `status/degraded_sections/resource_errors`；选择 raw envelope → F149 consumed projection，不做全量 Pydantic big-bang。
- Agent 页面真实可达 `behavior.restore_version`；Memory 的 `MemoryActionsSection` 无生产 importer，其管理动作不属于 F149 Memory contract。
- action handler 与 `build_action_registry` 分开声明；资源限制、behavior read/write/restore、memory consolidate、MCP install/status 存在 handler 但 registry 缺项。Plan 必须选择同源 seam。
- TaskDetail 只把 `STATE_TRANSITION.to_status` 与 `ARTIFACT_CREATED` 当业务分支；未知/历史 payload 只需 scrubbed diagnostic boundary，不能进入业务 mapper。
- MCP catalog 当前直接返回 env value；Settings/MCP mutation 都缺统一 keep/replace/remove write-only contract。该有限 slice 由 F149 自己端到端承担，复用现有 config/secret store、application boundary 与唯一 transport；F151 只保留底层 runtime/package/secret reference ownership，不需要独立 security Fix。
- main 已冻结管理能力归位：Tasks 只保留用户语言“待处理事项”的管理入口（非零显著、0 项折叠/隐藏，且不与 F145 候选合并）；Recovery summary、backup/export/update/restart/verify 移入 Settings → Advanced → 维护与恢复。

## 5. 测试策略

- L4/Vitest：每个 mapper、state reducer、permission/error/empty 分支、组件 a11y、技术字段隐藏；这是 F149 的主要测试层。
- L4/Python：Gateway schema/adapter/action/SSE/secret egress contract，使用 tmp SQLite 与 DI fake；SSE model 不放 core。
- L3：以 Echo/DI stub/ScriptedModelClient 验证 OpenAPI route、auth error、snapshot/action 与事件流的系统拼接；CI 可运行。
- L1：一条轻量全路由 390px geometry/a11y sweep、A/B 各一条关键旅程、一条 F150/F149 auth 交界，以及确属浏览器语义的 drawer/focus/modal/file chooser/SSE。
- L2 live：本 Feature 不需要新增；除非未来出现真实 provider/LLM 才能回答的事实。

仓库现有 changed-lines 工具只覆盖 backend Python，并在源码注释中明确排除 frontend。F149 需要在 Plan/Tasks 中增加前端等价门：Vitest 产出 lcov，脚本以 `origin/master` 基线解析 authored executable TS/TSX 新增行，排除 tests、generated DTO、`.d.ts` 和纯 type-only 行，低于 90% 失败。该工具验证覆盖下限，不替代测试层级与失败 oracle 审查。

每个行为 task 必须先以目标测试命令稳定见红，再实施到绿，最后在同一命令与相关 gate 下重构。Tasks 中写了 RED 或用 grep 数文字不构成证据；Implement/Review 必须保存实际输出、exit code、UTC 时间、HEAD SHA、工作树状态和观察到的稳定 oracle。纯文件搬迁使用 atomic relocation 证据：前后契约相同、旧路径不存在、import gate 通过。

## 6. 非功能与兼容性

- 复用 F148 `--cp-*`，不得新增第二主题或硬编码浅色 palette。
- `src/index.css` 零增长；触达的页面样式进入 domain stylesheet。
- 普通界面只显示用户概念；原始 ID/path/command/env/schema/status 在 Advanced。
- secret value 在服务端边界移除，永不进入 response/SSE/error/log/DOM/clipboard/evidence；read 只接收 env 名、configured 和服务端脱敏摘要，mutation 使用 ephemeral keep/replace/remove，placeholder 拒绝回写，成功/失败/关闭均清空。该窄 HTTP/application slice 属 F149；Settings 与 MCP 共享语义但不新增全局 registry、security service 或第二 transport。path/command 只有净化、非 secret、权限允许时才能在 Advanced 截断/复制。
- Recovery/backup/export/update/restart/verify 直接设计到现有 Settings Advanced，并以窄的 `MaintenanceRecoverySection` 一类 UI 组件组合；不得新建页面、management service、registry/状态源，也不得继续扩大 Settings God component。
- F150 唯一处理未登录、401、session 过期与登出；F149 只处理 origin 403 资源权限，不能逐页重建 Access 状态机。
- 390px 是明确验收宽度；键盘焦点、可感知名称、reduced motion 与 modal focus return 需有自动化 oracle。
- changed-lines coverage ≥90% 是最低门，不替代分层、职责与测试 oracle 审查。

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Claude Design 输出尚未回存 | 页面布局与状态仍不能进入实现 | 已提供 main 复核的 22 张 Reference 与 READY Prompt；等待 20/20 + Shared-States + Advanced-Pattern + References |
| F150/F151 契约变化 | auth 或底层 runtime/package/secret reference adapter 返工 | 生产实现硬等待上游稳定；F149 自有 HTTP write-only representation 不转嫁给 F151 |
| OpenAPI schema 不完整 | codegen 产物把开放 JSON误当业务 DTO | raw boundary + runtime decoder，只固定 F149 consumed projection |
| SSE/action 不受普通 OpenAPI 强类型覆盖 | final/payload/action result 漂移 | Gateway SSE schema + action dispatch/validation/registry 同源 seam |
| secret 当前可回显/回填 | catalog、错误、日志或 DOM 泄漏 | write-only keep/replace/remove；Gateway L4/L3 全出站 sentinel oracle |
| 页面 God component | E2E 只能遮住职责混乱，维护能力归位可能继续膨胀 Settings | L4 驱动 mapper/application seam；以窄 MaintenanceRecoverySection 组合现有状态，不新增 service/store |
| 双数据主路径 | auth、错误、状态漂移 | direct fetch ratchet 为零，统一 transport/ports |
| 过量 Playwright | 慢且不定位失败 | 业务分支下沉 Vitest/L3，L1 只保留浏览器语义 |
| 现有 coverage gate 不统计 frontend | UI 变更可在“总门通过”时仍无覆盖 | Plan/Tasks 增加 TS/TSX changed-lines ≥90% gate |
| 过度抽象 Application Port | interface/service/registry 数量膨胀 | 默认复用现有 platform seam，按真实多实现/隔离价值引入 |
