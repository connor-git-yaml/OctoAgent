# F149 Web 其余页面 v2 — Tasks

> 状态：`GATE_TASKS=true`（2026-07-21 main PASS）；T000 已在 F150 stable 后完成
> rebase/recon，Implement 已放行，当前从 T001 checker RED 开始推进。
>
> F149 分支以 `112a54aed0aca2971d8dabb34b6390efe9d779aa` 为上游基线；
> 不得修改 F150/F151 authority，不得 stage/commit/push 未通过当前 task review 的字节。

## 0. 执行纪律

- 每个行为 task 严格按 `RED → GREEN → REFACTOR`；三个 command 都从仓库根独立可复制，且验证同一行为 oracle。
- RED setup 必须先让目标 test/fixture 可收集；失败只能是目标断言失败，不能是缺文件、import error、依赖缺失、0 tests 或基础设施错误。
- Implement 时每个行为 task 保存 `evidence/tdd/<task-id>/{red,green,refactor}.{yaml,log}`，包含 UTC、HEAD SHA、`git status --short`、command、exit、expected/observed oracle 与 secret scan。
- Python command 固定消费 post-F151 canonical profile：`core/provider/protocol/tooling/skills/policy/memory` 七个保留 workspace packages + Gateway；`packages/sdk/src` 必须 absent。继续使用 `PYTHONNOUSERSITE=1`、`uv run --project . --no-sync python -m pytest`；禁止 `uv sync`、裸 pytest、固定 sleep、blanket rerun、ambient/host path 与宿主 `~/.octoagent`。
- L4 为主；L3 只用 ASGI/DI fake/Echo/ScriptedModelClient；L1 只验证浏览器语义；L2 不新增。
- 当前不预声明任何 atomic relocation。Recovery 归位、DTO 生成化、transport 收敛和视觉 CSS 都改变行为/契约，不得伪装。若实现中出现纯搬迁，必须另拆 conditional task 并保存 before/after contract、旧路径 absence、import gate 与纯 location/import diff。
- 产品 surface：桌面端保留 Web 入口；手机产品只走原生 iOS App，不把 390px Web/手机浏览器作为移动产品入口。F149 只实施 Web；所有 390px task 仅验证 Web 窄浏览器窗口的响应式健壮性，不得称为手机产品、移动认证或 iOS 验收。iOS 实现不在本任务清单内，不得新增 iOS production task。
- 视觉实现必须对照 `design-output/2026-07-21/OctoAgent Web.dc.html`；Claude 最开始方案是 Web/iOS 的共同视觉/交互基线，现有 Web UI 仅作 current-state evidence。只允许因明确功能合同、可用性、无障碍或 iOS Feature 的 Apple 原生平台规范调整原稿的层级、留白、卡片节奏、信息密度、视觉张力和排版；每项偏离必须在逐页 Review 记录原因与影响，禁止以“实现方便”为理由。F148 只约束 `--cp-*`、主题/组件边界与功能合同；iOS 保留同一视觉语言但用 SwiftUI/Apple 原生导航、手势、控件和无障碍语义，不复制 Web 组件结构。

## P0 — Implement 前置与 checker TDD

### T000 — Implement readiness 硬阻断

- [x] **状态：完成**
- **类型**：process blocker；非行为 task，不产生伪 RED。
- **FR/SC**：FR-020；SC-008。
- **关闭条件**：记录 F150 global Access/SSE URL 契约稳定 commit、F151 runtime/config/secret-reference seam 稳定 commit、`origin/master` 新基线与 main 明确 Implement 放行；随后完成 rebase/recon，重新完整读取届时的 `octoagent/tests/AGENTS.md`、`octoagent/frontend/playwright.config.ts` 与 F151 stage profile。
- **ORACLE**：任一证据缺失时 T001–T066 均保持 blocked；Python/Playwright 必须采用 post-F151 canonical `core/provider/protocol/tooling/skills/policy/memory` 七包 + Gateway profile 且 retired SDK absent。若届时 canonical package set 与本计划不一致，回 Tasks Gate；不得创建 fallback auth/DTO 或兼容旧 SDK。
- **RESULT**：F151 stable=`687f20fc6246e7157957ab51ac474d46e91578b6`；
  F150产品实现=`bf29d6be7d7a86c298cd45699488a8640065a566`，F150 stable
  tip=`5e6f4846703b7126cd104c8b9678e0c2f5300cc8`，`origin/master`真值同步
  commit=`112a54aed0aca2971d8dabb34b6390efe9d779aa`。F149已无冲突rebase，
  merge-base精确等于该`origin/master`。重新完整读取tests契约、Playwright config与
  F151 stage profile后，post-SDK profile仍为七包+Gateway且SDK计数为0；Design export
  SHA仍为`a2db08ea0eb39278558e61e87a355b042c98ad24273b201940925f310271d98a`。
  Playwright现存`CI retries=1`漂移已登记为T050的真实目标，不扩成兼容路径，也不阻断
  T001–T049的确定性L4/L3任务。

### T001 — F149 boundary checker 行为

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T001/`；main复审发现并以
  `evidence/tdd/T001-corrective/`闭环property fetch、`Headers.set`、token helper和
  query token四类绕过。仓库实扫仅保留4个后续owner文件中的真实违规，
  F150 `FrontDoorGate`合法token读取以及分层/fallback/Restore命名均无误报。
- **层/FR**：L4 checker；FR-011/012/013/024/029。
- **文件**：`octoagent/frontend/scripts/check-f149-boundaries.mjs`、`check-f149-boundaries.test.ts`、fixtures。
- **依赖**：T000。
- **RED_SETUP**：创建可调用但无规则的 inert checker 壳与 seeded fixtures（direct fetch、token/header、page 401、generated→page、reverse import、duplicate store/transport、fallback、cycle）；壳存在且 test 可收集。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/check-f149-boundaries.test.ts`
- **RED_ORACLE**：checker 错误接受 seeded violation；不是 import/file failure。
- **GREEN_CHANGE**：用 TypeScript AST/import graph 实现规则；合法 `api/client` 与 F150 SSE builder allowlist 明确且最窄。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/check-f149-boundaries.test.ts`
- **GREEN_ORACLE**：坏 fixtures 全拒绝、合法 fixture 通过。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/check-f149-boundaries.test.ts`
- **EXIT_GATE_COMMAND**：`cd octoagent/frontend && node scripts/check-f149-boundaries.mjs`

### T002 — style/token/index.css ratchet checker

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T002/`。当前全局遗留值只作为不恶化ratchet，不宣称已清零。
- **层/FR**：L4 checker；FR-009/019/029。
- **文件**：`octoagent/frontend/scripts/check-f149-style-ratchet.mjs`、test、fixtures。
- **依赖**：T000。
- **RED_SETUP**：inert checker 壳 + index growth、硬编码浅色、Spotify/第二主题 token、非法非 `--cp-*` 与合法 `--cp-*` fixtures。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/check-f149-style-ratchet.test.ts`
- **RED_ORACLE**：seeded palette/theme/index growth 被漏报或合法 `--cp-*` 被误报。
- **GREEN_CHANGE**：实现真实 style/import/source 扫描；`index.css` wc baseline=4476 且不得增长。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/check-f149-style-ratchet.test.ts`
- **GREEN_ORACLE**：seeded bad/good fixtures 精确分类。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/check-f149-style-ratchet.test.ts`
- **EXIT_GATE_COMMAND**：`cd octoagent/frontend && node scripts/check-f149-style-ratchet.mjs`

### T003 — OpenAPI/generated clean-diff checker

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T003/`；npm alias与真实generated artifact仍由T005及后续contract任务拥有。
- **层/FR**：L4 checker；FR-010/018。
- **文件**：`octoagent/frontend/scripts/check-openapi-generated.mjs`、test、fixtures。
- **依赖**：T000。
- **RED_SETUP**：inert checker 壳 + generated drift、`any`、命名 raw boundary 外 `unknown/JsonValue`、合法 schema-as-data/open boundary fixtures。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/check-openapi-generated.test.ts`
- **RED_ORACLE**：坏 artifact 被接受或合法开放 JSON 被伪报。
- **GREEN_CHANGE**：实现 deterministic diff 与 F149 slice type policy；不把非 F149 历史类型纳入 big-bang。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/check-openapi-generated.test.ts`
- **GREEN_ORACLE**：drift/`any`/非法 raw leakage 被拒绝，合法 boundary 通过。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/check-openapi-generated.test.ts`
- **EXIT_GATE_COMMAND**：`cd octoagent/frontend && npm run openapi:check`（仅 T005 后可运行）。

### T004 — frontend changed-lines coverage checker

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T004/`；真实LCOV provider/npm alias仍由T005创建。
- **层/FR**：L4 checker；FR-018。
- **文件**：`repo-scripts/check-frontend-changed-lines-coverage.mjs`、`octoagent/frontend/scripts/check-frontend-changed-lines-coverage.test.ts`、fixtures。
- **依赖**：T000。
- **RED_SETUP**：inert API + TypeScript/LCOV fixtures；authored executable TS/TSX/JSX 计入，tests/generated/`.d.ts`/import type/interface/type-only 排除；89.99%/90%/新文件无记录三组边界。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/check-frontend-changed-lines-coverage.test.ts`
- **RED_ORACLE**：分类错误、89.99 被放行、90 被拒绝或无 coverage 新源文件未按 0 计。
- **GREEN_CHANGE**：AST 分类 authored executable 行并与 lcov/git diff 求交。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/check-frontend-changed-lines-coverage.test.ts`
- **GREEN_ORACLE**：所有分类/阈值 fixtures 精确通过。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/check-frontend-changed-lines-coverage.test.ts`
- **EXIT_GATE_COMMAND**：`node repo-scripts/check-frontend-changed-lines-coverage.mjs --lcov octoagent/frontend/coverage/lcov.info --base origin/master --min-percent 90`

### T005 — package scripts 与 dev-only generator/coverage provider

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T005/`。aliases与provider已可用；真实OpenAPI exporter/artifact仍由
  T010–T012交付后执行。
- **层/FR**：L4 config contract；FR-010/018。
- **文件**：`octoagent/frontend/scripts/package-scripts.test.ts`、`package.json`、`package-lock.json`、`vite.config.ts`。
- **依赖**：T003/T004。
- **RED_SETUP**：test 解析 package/config，断言存在 types-only generator、coverage provider 与 `openapi:generate/openapi:check/test:coverage`，OpenAPI alias 先以完整 worktree PYTHONPATH + `uv --no-sync` 调 Gateway exporter，再运行 types-only generator，且不存在 generated fetch client。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/package-scripts.test.ts`
- **RED_ORACLE**：当前缺 aliases、`openapi-typescript@7.13.0` 与 `@vitest/coverage-v8@2.1.9`。
- **GREEN_CHANGE**：锁定上述 devDependencies/lock；alias 以 frontend cwd 可独立执行的 `cd ..` + post-F151 canonical 七个保留 packages/Gateway PYTHONPATH + `uv run --project . --no-sync` 调 exporter，再生成/检查 types；retired SDK absent，不安装 client runtime。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/package-scripts.test.ts`
- **GREEN_ORACLE**：alias/dependency/config 合同满足且 lock 一致。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run scripts/package-scripts.test.ts`

## P1 — Gateway 有限 contract slice

### T010 — REST/snapshot/task-detail contract

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T010/`。67个实际REST operation均由FastAPI/Pydantic有限模型导出；
  snapshot与TaskDetail的开放JSON分别只停留在命名raw boundary。
- **层/FR**：Gateway L4；FR-010/025。
- **文件**：`octoagent/apps/gateway/tests/test_f149_web_contract.py`；Gateway route-adapter Pydantic contract/exporter。
- **依赖**：T000。
- **RED_SETUP**：先创建可收集 test，验证 snapshot 基础 envelope、全部 resource names、degraded/resource_errors，以及 endpoint manifest 全 slice：control resources、tasks list/detail、F145 approvals/candidates、agent auxiliary、skills、files/workspace-git、operator inbox、recovery/update/backup/export/restart/verify 的实际 request/response schema。
- **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_web_contract.py -k "snapshot or resource or task_detail"`
- **RED_ORACLE**：当前 snapshot schema 漏 envelope、raw resource 可穿透，或 endpoint manifest 任一实际消费 response/request model 缺失/过宽。
- **GREEN_CHANGE**：补有限 response models、命名 raw envelope decoder 与 deterministic artifact exporter；不闭合全部聚合字段。
- **GREEN_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_web_contract.py -k "snapshot or resource or task_detail"`
- **GREEN_ORACLE**：基础 envelope/实际消费字段/开放 boundary 均符合 manifest。
- **REFACTOR_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_web_contract.py apps/gateway/tests/test_control_plane_api.py -k "snapshot or resource or task_detail"`

### T011 — action dispatch/validation/registry/artifact 同源

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T011/`。七个F149实际action由同一`ActionContractDefinition`
  派生dispatch、runtime validation、registry与types artifact；非法参数在owner前
  fail closed并保留既有稳定错误码，其他action继续显式open schema-as-data。
- **层/FR**：Gateway L4；FR-026/027。
- **文件**：同一 `test_f149_web_contract.py`；control-plane base/coordinator/registry 与实际 owner services。
- **依赖**：T010。
- **RED_SETUP**：test 枚举 F149 真实可达 actions，包含 resource limits、behavior read/write/restore、memory consolidate、MCP install/status；验证 handler/params/result/registry/artifact 同一 record，且不纳入 dead Memory 管理动作。
- **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_web_contract.py -k "action_registry or action_contract"`
- **RED_ORACLE**：已有 handler 缺 registry/schema，或 dispatch/validation/artifact仍来自重复来源。
- **GREEN_CHANGE**：以 `ActionContractDefinition` records 派生四者；F149 action 强类型，其他 action 显式 open schema-as-data。
- **GREEN_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_web_contract.py -k "action_registry or action_contract"`
- **GREEN_ORACLE**：F149 action 无缺项/重复 source/legacy open fallback。
- **REFACTOR_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_web_contract.py apps/gateway/tests/services/test_control_plane_registry.py apps/gateway/tests/services/test_behavior_restore.py -k "action or registry or restore"`

### T012 — Task SSE Gateway/web adapter contract

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T012/`。现有 Task SSE 路由输出必填 `final`，仅闭合
  `STATE_TRANSITION.to_status` 与 artifact refresh；其他事件只能经有界脱敏
  diagnostic payload 进入 Advanced，core 未新增对应模型。
- **层/FR**：Gateway L4；FR-028。
- **文件**：`octoagent/apps/gateway/tests/test_f149_task_sse_contract.py`；Gateway route-adapter SSE schema/decoder。
- **依赖**：T010。
- **RED_SETUP**：test 覆盖 final 必填、state transition to_status、artifact refresh signal、unknown/history diagnostic size/depth/secret scrub 与 core absence。
- **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_task_sse_contract.py`
- **RED_ORACLE**：final optional、raw unknown 进入业务 mapper、payload 未验证/净化或 SSE model 落 core。
- **GREEN_CHANGE**：Gateway adapter schema/encoder/decoder；普通 OpenAPI 仍只描述 text/event-stream endpoint。
- **GREEN_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_task_sse_contract.py`
- **GREEN_ORACLE**：业务 payload 有限闭合，diagnostic boundary 安全且 core absence。
- **REFACTOR_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_task_sse_contract.py apps/gateway/tests/test_us3_sse.py`

### T013 — Settings/MCP write-only secret 与全出站 scrub

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T013/`。MCP 读取模型已移除 `env`，只公开
  `name/configured/redacted_summary`；首次 MCP 安装只接受一次性新值，后续
  Settings/MCP 持久化编辑使用严格 `keep|replace|remove` mutation，placeholder、
  未声明字段与 schema 漂移 fail closed。原始值从读模型和领域结果边界排除，
  持久化异常映射为固定错误，SSE 复用既有 diagnostic sanitizer；未新增全局
  secret 值扫描器、registry、第二套 scrub 算法或第二 transport。
- **层/FR**：Gateway L4 security；FR-022。
- **文件**：`octoagent/apps/gateway/tests/test_f149_secret_egress.py`；setup/MCP application contract。
- **依赖**：T010/T011/T012。
- **RED_SETUP**：用运行时拼接的合成 sentinel；测试 read summary、keep/replace/remove、placeholder 拒绝、list/get/snapshot/action/SSE/error/captured log。失败只输出固定 oracle，不打印 payload/value。
- **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_secret_egress.py`
- **RED_ORACLE**：MCP env/read response 泄漏、mutation 三态/placeholder 拒绝错误或出站任一路径含 sentinel。
- **GREEN_CHANGE**：F149 窄 representation/validation/error mapping，复用现有 config/secret store 与 SSE sanitizer；不把请求值提升为全局净化状态。
- **GREEN_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_secret_egress.py`
- **GREEN_ORACLE**：所有出站为 name/configured/redacted summary，mutation 三态精确。
- **REFACTOR_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_secret_egress.py apps/gateway/tests/test_control_plane_api.py -k "mcp or setup or secret or snapshot"`

### T014 — deterministic L3 contract/egress 拼接

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T014/`。真实 FastAPI lifespan、SQLite 与 ASGITransport 串联
  OpenAPI、snapshot、action dispatch/structured error、终态 SSE 历史回放和
  secret 零出站；只组合 T010–T013 已有 owner，没有新增 production seam。
- **层/FR**：L3；FR-010/022/027/028。
- **文件**：`octoagent/tests/integration/test_f149_web_contract.py`。
- **依赖**：T010–T013。
- **RED_SETUP**：ASGITransport + tmp root/SQLite + DI fake/Echo，覆盖 OpenAPI→snapshot/action→SSE→secret egress；若共享实时队列则标 `xdist_group("f149_web_contract")` 并用条件同步/受控队列。
- **RED_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q tests/integration/test_f149_web_contract.py`
- **RED_ORACLE**：HTTP body/SSE bytes/structured error/log、registry dispatch 或 schema 任一漂移；无网络/真 LLM/宿主状态。
- **GREEN_CHANGE**：只补 composition/DI 接线，不复制下层算法。
- **GREEN_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q tests/integration/test_f149_web_contract.py`
- **GREEN_ORACLE**：deterministic 全链通过且 sentinel 零出站。
- **REFACTOR_COMMAND**：`cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q tests/integration/test_f149_web_contract.py`

### T015 — schema artifacts 与 types-only generation

- **状态**：`[x]`；真实 generated-drift RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T015/`。唯一 exporter 从 Gateway REST/action/SSE 三个现有
  authority 导出最小依赖闭包，`openapi-typescript` 只生成 `.d.ts`，两次独立
  导出字节一致；runtime architecture gate 仅授权三个 exact generated path，
  并继续拒绝可执行 export 与新增 F150 敏感模式；没有 fetch client、第二
  schema source 或宿主安装 fallback。
- **层/FR**：contract/codegen；FR-010。
- **文件**：`repo-scripts/export-f149-contracts.py`；
  `repo-scripts/check-runtime-architecture.py` 的 exact types-only path authority；
  `frontend/src/generated/f149/*`；package aliases。
- **依赖**：T003/T005/T010–T014。
- **RED_SETUP**：后端 schema 已真实改变、alias/dependency 已由 T005 建立，但 checked-in generated artifact 尚未刷新。
- **RED_COMMAND**：`cd octoagent/frontend && npm run openapi:check`
- **RED_ORACLE**：clean-diff checker 精确报告 deterministic generated drift；不是 alias/依赖缺失。
- **GREEN_CHANGE**：`openapi:generate/check` 先经 worktree-locked exporter 导出/比对 REST/action/SSE 三个唯一源的最小 schema document，再生成/比对 `.d.ts`；禁止 fetch client。
- **GREEN_COMMAND**：`cd octoagent/frontend && npm run openapi:generate && npm run openapi:check`
- **GREEN_ORACLE**：生成可重复、无 drift/`any`，开放 JSON 只在命名 boundary。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npm run openapi:check && npx tsc -b`

## P2 — Frontend adapter/application/domain foundations

### T020 — raw snapshot → consumed projection

- **状态**：`[x]`；真实 inert RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T020/`。generated wire只进入命名raw adapter；16个resource
  exact校验后仅输出稳定消费字段，开放metadata不进入platform；pure projection
  不import generated、transport或React。runtime architecture gate只授权raw
  decoder exact path的resource-name常量与decoder两个runtime exports。
- **层/FR**：frontend L4；FR-010/025。
- **文件**：`src/api/f149/snapshotDecoder.test.ts`、adapter；
  `src/platform/queries/controlPlaneResources.test.ts`；
  `repo-scripts/check-runtime-architecture.py` 的exact raw-decoder authority。
- **依赖**：T015。
- **RED_SETUP**：先创建可导入但只返回 `invalid` 的 inert `snapshotDecoder` export，再用 test 输入完整 raw envelope、缺 section、degraded/resource_errors、开放 metadata；断言 raw 不进入 page/domain。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/api/f149/snapshotDecoder.test.ts src/platform/queries/controlPlaneResources.test.ts`
- **RED_ORACLE**：inert decoder/当前手写 snapshot 无法满足 envelope/projection assertions，或 raw resource/metadata 穿透；不是缺 import/file。
- **GREEN_CHANGE**：adapter runtime decoder 产出 F149 consumed projection；pure projection 不 import generated/transport/React。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/api/f149/snapshotDecoder.test.ts src/platform/queries/controlPlaneResources.test.ts`
- **GREEN_ORACLE**：实际消费字段强类型，开放字段停在命名 raw boundary。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/api/f149/snapshotDecoder.test.ts src/platform/queries/controlPlaneResources.test.ts`

### T021 — Approvals/Memory helper 统一 transport 与 error ownership

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T021/`。Approvals/Memory 已删除各自的 token/header/direct fetch
  helper，统一经 `api/client` 的 `frontDoorRequest`；401 保持 F150 global auth
  owner，403/404/409 由 F149 surface mapper 精确归属。`apiErrorFromResponse` 是
  `client.ts` 唯一新增公开能力，runtime architecture exact authority 的正向控制
  通过，并拒绝额外 export 与新增敏感 session/device 语义。仓库边界扫描剩余的
  Agent/Skills direct fetch 全部由 T022 拥有，不把全局未完成伪报为 T021 失败。
- **层/FR**：frontend L4；FR-002/011/021。
- **文件**：`src/api/approval-center*.ts`、新 `src/api/memory-candidates.test.ts`、
  `src/api/memory-candidates*.ts`、`src/api/f149/errorOwnership.ts`、
  `src/api/client.ts`、`repo-scripts/check-runtime-architecture.py` exact authority。
- **依赖**：T001/T015。
- **RED_SETUP**：扩 test 断言统一 request 注入、404/409/403 mapper 与 401 交回 global owner；不只断言 mock called，还断言 mapper/可观察结果。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/api/approval-center.test.ts src/api/memory-candidates.test.ts src/api/client.test.ts`
- **RED_ORACLE**：当前 helper 自建 fetch/token/header，或 404/409/403/401 被混淆。
- **GREEN_CHANGE**：helper 只经 `api/client`/`frontDoorRequest`；删除两个鉴权 helper，保留 domain error semantics。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/api/approval-center.test.ts src/api/memory-candidates.test.ts src/api/client.test.ts`
- **GREEN_ORACLE**：统一 transport 且四类 error ownership 精确。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/api/approval-center.test.ts src/api/memory-candidates.test.ts src/api/client.test.ts`

### T022 — Agent auxiliary/Skills REST 统一 transport

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T022/`。Agent审批覆盖列表/撤销与Skills列表/详情/安装/删除六条
  请求已迁入同一`api/f149/adapters.ts`，wire type直接消费T015 generated REST
  declaration，transport/auth/error只经`api/client`。F149全仓boundary现已PASS；
  页面DOM、`wb-*` class multiset与inline style均未改变。跨Feature exact authority
  同时拒绝adapter额外export、页面direct fetch与新增视觉class，避免借transport迁移
  偷改F150安全面或Claude Design后续视觉基线。
- **层/FR**：frontend L4；FR-011/012。
- **文件**：`src/api/f149/adapters.test.ts`、Agent/Skills adapter、
  `pages/AgentCenter.tsx`、`pages/SkillCenter.tsx`、
  `repo-scripts/check-runtime-architecture.py` exact transport-only authority。
- **依赖**：T001/T015。
- **RED_SETUP**：先创建可导入但返回固定 invalid result 的 inert adapter exports，再由 test 驱动 approval override/behavior auxiliary 与 skill list/detail/install/delete；断言统一 error/transport，不使用 global fetch stub 自证页面逻辑。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/api/f149/adapters.test.ts src/api/client.test.ts`
- **RED_ORACLE**：inert adapter/当前 Agent 2 处与 Skills 4 处 direct fetch 无法满足统一 auth/error assertions；不是缺 import/file。
- **GREEN_CHANGE**：迁入窄 adapter，底层只走 client；BuildVersionWatcher 不在 F149 closure。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/api/f149/adapters.test.ts src/api/client.test.ts`
- **GREEN_ORACLE**：adapter 行为正确且页面无需 fetch/token。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/api/f149/adapters.test.ts src/api/client.test.ts`
- **EXIT_GATE_COMMAND**：`cd octoagent/frontend && node scripts/check-f149-boundaries.mjs`

### T023 — Task SSE frontend decoder 与 TaskDetail state

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T023/`。SSE wire 只在命名 `raw/` adapter 由 generated
  `TaskStatus` 与 runtime decoder 收窄；TaskDetail 只接收 state、artifact
  refresh、scrubbed diagnostic 三类互斥投影。非当前任务与非单调状态不会改写
  badge，子任务 final 不关闭当前流；REST 历史只重建最小 state/artifact 投影，
  原始 payload 与额外顶层字段均不进入 DOM。Advanced 默认收起，403/404/
  recoverable/disconnected 状态互斥。全前端 490/490 与生产 build 通过，
  `openapi:check`、F149 boundary、complexity 均通过；未修改 CSS、视觉 class
  或 Claude Design 页面排版。repository-scope runtime architecture 同时通过，
  并实际拒绝额外 runtime export、第二 re-export 与新视觉 class 三类对抗样本。
- **层/FR**：frontend L4；FR-003/010/028。
- **文件**：`src/api/f149/taskSseDecoder.test.ts`、decoder；`src/pages/TaskDetail.test.tsx`、TaskDetail pure state/projection。
- **依赖**：T012/T015。
- **RED_SETUP**：先创建可导入但只返回 `invalid` 的 inert decoder export，再让 test 覆盖 final required、current-task/seq monotonic、to_status、artifact refresh、unknown/history scrubbed Advanced、raw secret/payload absence；页面逐项钉 loading/recoverable-error/origin-403/not-found/disconnected，明确 empty/409 不适用于详情只读合同。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/api/f149/taskSseDecoder.test.ts src/pages/TaskDetail.test.tsx`
- **RED_ORACLE**：inert decoder/当前页面无法满足 typed event 与 state assertions，或 raw unknown/default timeline/DOM 穿透；不是缺 import/file。
- **GREEN_CHANGE**：generated wire only in adapter；业务 mapper只收 typed projection，diagnostic 是 scrubbed/size-limited。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/api/f149/taskSseDecoder.test.ts src/pages/TaskDetail.test.tsx`
- **GREEN_ORACLE**：状态/刷新/diagnostic 三路互斥且无 raw leakage。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/api/f149/taskSseDecoder.test.ts src/pages/TaskDetail.test.tsx`

### T024 — shared resource page-state、permission 与 sensitivity

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T024/`。八类surface状态由单一pure helper互斥决策，401继续交回
  F150 global auth，403/404/409与recoverable复用既有F149 error owner。Advanced
  path/command只有已净化、非secret、workspace-relative且获权时才可截断/复制；
  不可达结果不保留输入字节。可访问primitive复用既有`InlineCallout`，没有新CSS、
  视觉class、全局业务store或Claude Design排版改动。精确18/18、全前端508/508、
  build、OpenAPI、boundary/style/complexity与repository runtime architecture均通过。
- **层/FR**：frontend L4 pure/component；FR-006/007/012/021/022/024。
- **文件**：`src/domains/shared/resourcePageState.test.ts`、pure state/error/sensitivity helpers；`src/ui/primitives/resourceState.test.tsx` 与窄 UI primitives。
- **依赖**：T020/T021。
- **RED_SETUP**：先创建可导入但只返回 generic error/inert markup 的 pure helper与 primitive exports，再以参数化 state matrix 测共享互斥规则；surface-specific 渲染仍留各域。覆盖 loading/empty/error/403/not-found/disconnected/409、401 absence、path/command truncation/clipboard permission、secret impossible state。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/shared/resourcePageState.test.ts src/ui/primitives/resourceState.test.tsx`
- **RED_ORACLE**：inert helper/primitive 无法满足 403/401/sensitivity/loading assertions；不是缺 import/file。
- **GREEN_CHANGE**：窄 pure state/sensitivity + accessible primitives，不建全局业务 store。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/shared/resourcePageState.test.ts src/ui/primitives/resourceState.test.tsx`
- **GREEN_ORACLE**：共享规则稳定且没有 surface 业务分支集中化。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/shared/resourcePageState.test.ts src/ui/primitives/resourceState.test.tsx`

### T025 — F149 typed action commands/results

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T025/`。七条 F149 action 由 generated schema 约束并在
  application boundary 做 runtime fail-closed；`behavior.restore_version`
  保留，dead Memory 与 Automation action 不进入本任务。typed wrapper 直接复用
  既有 Workbench executor 与 `executeWorkbenchActionWithRefresh`，非法 command、
  envelope/action/result 漂移和 executor exception 均收敛为稳定 typed error，
  异常信息不回显 params/secret。最终精确15/15、全前端520/520、生产build、
  OpenAPI、F149 boundary/style/complexity与repository runtime architecture均通过；
  未修改页面、CSS、视觉class或Claude Design排版。
- **层/FR**：frontend L4 application；FR-010/012/027。
- **文件**：`src/platform/actions/f149Actions.test.ts`、`f149Actions.ts`。
- **依赖**：T011/T015/T020。
- **RED_SETUP**：先创建可导入但拒绝所有 command 的 inert typed wrapper export，再由 test 枚举 Automation/Settings/Agents/Memory/MCP 真实 actions，断言 typed command→existing executor→typed result/error，包含 `behavior.restore_version`，排除 dead Memory actions。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/platform/actions/f149Actions.test.ts`
- **RED_ORACLE**：inert wrapper/当前 raw submitAction 无法满足 typed command/result assertions；不是缺 import/file。
- **GREEN_CHANGE**：结构化 wrapper 复用现有 `executeWorkbenchActionWithRefresh`/Workbench executor，不建第二 action pipeline/service。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/platform/actions/f149Actions.test.ts`
- **GREEN_ORACLE**：F149 action union 完整、非法 command/result 被 decoder 拒绝、refresh 语义复用。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/platform/actions/f149Actions.test.ts src/platform/actions/controlPlaneActions.test.ts`

## P3 — A 波页面

### T030 — Approvals v2

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T030/`。实现前逐页复核 Claude Design
  `F149/Approvals/Desktop` 与 `F149/Approvals/390`，以原稿 hero、三类数量胶囊、
  从容留白、卡片节奏和单列操作层级为视觉基线；没有按旧 Web 回退成平均分栏或
  管理后台表格。三类候选与既有动作未扩域，loading/empty及分来源403/404/409
  复用共享state/error owner，401不下沉，技术错误不进普通界面。旧`wb-*`视觉类
  从本页新增/触达文件清零，新增样式只消费`--cp-*`；真实Chromium桌面与390 Web
  窄窗口均无横向溢出、主操作≥44px。最终精确35/35、全前端523/523、生产build、
  OpenAPI、F149 boundary/style/complexity与repository runtime architecture均通过。
  390只作Web响应式检查，不代表手机产品；手机仍只走原生iOS。
- **层/FR**：frontend L4 component/projection；FR-002/007/009。
- **文件**：`domains/approval-center/{ApprovalCenterPage,ProposalCard,approvalModels}*` 与 co-located CSS。
- **依赖**：T021/T024。
- **RED_SETUP**：先扩现有 tests 覆盖三类候选、loading/empty/分来源 error/origin-403/404/409、用户术语、keyboard/accessible name；结构断言对齐 Desktop/390 Web 窄窗口信息层级，不做像素测试。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/approval-center/ApprovalCenterPage.test.tsx src/domains/approval-center/ProposalCard.test.tsx src/domains/approval-center/approvalModels.test.ts`
- **RED_ORACLE**：permission/state/a11y/术语或三类候选边界任一不符。
- **GREEN_CHANGE**：实现 Claude frame 的层级/留白/card rhythm；不并入通用 tool approval，不新增动作。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/approval-center/ApprovalCenterPage.test.tsx src/domains/approval-center/ProposalCard.test.tsx src/domains/approval-center/approvalModels.test.ts`
- **GREEN_ORACLE**：所有状态/动作/普通语言通过。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/approval-center/ApprovalCenterPage.test.tsx src/domains/approval-center/ProposalCard.test.tsx src/domains/approval-center/approvalModels.test.ts`

### T031 — Tasks list 与“待处理事项”归位

- **层/FR**：frontend L4 component/projection；FR-003/007/030。
- **文件**：新 `src/pages/TaskList.test.tsx`、tasks projection/section、`TaskList.tsx` 与 CSS。
- **依赖**：T024/T025/T032（先让维护能力在 Settings 可达，再从 Tasks 移除，避免中间态能力消失）。
- **RED_SETUP**：test raw status→人话、loading/empty/recoverable-error/origin-403、retry、nonzero pending显著可进入、0隐藏、ordinary-language absence、与 F145 审批不合并、Recovery absence、不出现 cancel/resume；明确 not-found/disconnected/409 对只读列表 N/A。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/TaskList.test.tsx`
- **RED_ORACLE**：当前 raw status、适用 state/retry 缺失、operator/ops 文案、Recovery 占首屏或 0 pending 仍占位。
- **GREEN_CHANGE**：Tasks 只组合任务列表与窄“待处理事项”入口；管理详情沿用现有 endpoint/action，不造第二 store。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/TaskList.test.tsx`
- **GREEN_ORACLE**：显隐/文案/状态/范围全部符合 main 决策。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/TaskList.test.tsx`

### T032 — Settings Advanced“维护与恢复”

- **状态**：`[x]`；真实 RED→GREEN→REFACTOR 已完成，证据见
  `evidence/tdd/T032/`。维护能力从 Tasks 移入 Settings 唯一 Advanced 路径，
  由新 `SettingsCenter` 组合窄 `MaintenanceRecoverySection`，没有继续扩大
  既有 Settings God component，也没有新增 store/service/registry。loading、
  empty、recoverable-error、origin-403 与 ready 由单一 reducer/hook 互斥拥有；
  backup/apply/restart 强确认、有效 dry-run-before-apply、导出范围与 restart
  短暂不可用合同全部闭合。逐页对照 Claude Design Settings Desktop/390 后保留
  宽松 hero、窄 Advanced 卡片、留白与卡片节奏；真实 Chromium 1440/390 Web
  窄窗口无横向溢出，操作控件≥44px。最终精确22/22、全前端532/532、生产build、
  OpenAPI、F149 boundary/style/complexity及changed-lines coverage 95.03%通过。
  repository runtime architecture因F151尚无本次新路径证据slice而如实阻断，本
  task未伪造或越界修改F151证据。390只作Web响应式检查，手机产品仍只走原生iOS。
- **层/FR**：frontend L4 component/application；FR-005/007/013/030。
- **文件**：新 `domains/settings/MaintenanceRecoverySection.test.tsx`/component/state/CSS；Settings composition；旧 Recovery panel 移除。
- **依赖**：T024/T025。
- **RED_SETUP**：先创建可导入但仅渲染 inert marker 的 `MaintenanceRecoverySection`，再 test loading、empty（未配置/未连接引导）、recoverable error、origin-403、Advanced 落点、summary/backup/export/dry-run/apply/restart/verify、backup/apply/restart 强确认、export 范围、有效 dry-run-before-apply、restart 短暂不可用、single state source；明确 not-found/disconnected/409 N/A。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/settings/MaintenanceRecoverySection.test.tsx src/domains/settings/SettingsPage.test.tsx`
- **RED_ORACLE**：inert section/当前 Recovery 无法满足适用 page state、归位、确认/范围/dry-run ordering/不可用说明；不是缺 import/file。
- **GREEN_CHANGE**：以窄 section 复用现有 recovery/update API/application state，由 Settings 组合；不新增页面/service/registry/store。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/settings/MaintenanceRecoverySection.test.tsx src/domains/settings/SettingsPage.test.tsx`
- **GREEN_ORACLE**：七项能力与 main 规则完整，Settings God component 不增长职责。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/settings/MaintenanceRecoverySection.test.tsx src/domains/settings/SettingsPage.test.tsx`

### T033 — Settings write-only secret 与普通用户语言

- **层/FR**：frontend L4 pure/component/security；FR-005/006/022。
- **文件**：`domains/settings/secretMutation.test.ts`、pure command/state；Settings tests/components。
- **依赖**：T013/T024/T025/T032。
- **RED_SETUP**：先创建可导入但拒绝所有 mutation 的 inert `secretMutation` export，再 test keep/replace/remove、placeholder拒绝、success/failure/close清空、DOM/clipboard/error absence；普通区 LiteLLM/JWT/AUD/JWKS/API Key/write-only/review/apply/dry-run/vault 等实现词 absence。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/settings/secretMutation.test.ts src/domains/settings/SettingsPage.test.tsx`
- **RED_ORACLE**：inert mutation/当前 page 无法满足三态/清空 assertions，或普通区仍出现内部词/退役 fallback；不是缺 import/file。
- **GREEN_CHANGE**：ephemeral secret VM + typed action command；技术事实只在 Advanced，且 value 永不可见/复制。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/settings/secretMutation.test.ts src/domains/settings/SettingsPage.test.tsx`
- **GREEN_ORACLE**：三态/清空/absence 与 backend contract 一致。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/settings/secretMutation.test.ts src/domains/settings/SettingsPage.test.tsx`

### T034 — Automation v2

- **层/FR**：frontend L4 component/projection；FR-004/007/009。
- **文件**：`pages/AutomationCenter.test.tsx`、automation pure projection/page/CSS。
- **依赖**：T024/T025。
- **RED_SETUP**：test 只允许 pause/resume、raw cron/job/action 仅 Advanced、loading/empty/error/403/409、visible/focusable Advanced trigger。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/AutomationCenter.test.tsx`
- **RED_ORACLE**：状态/权限/术语/a11y 或产品动作范围漂移。
- **GREEN_CHANGE**：实现 Claude Desktop/390 Web 窄窗口 frame，不新增 create/delete/cancel 等后端未授权动作。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/AutomationCenter.test.tsx`
- **GREEN_ORACLE**：页面状态与动作范围准确。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/AutomationCenter.test.tsx`

## P4 — B 波页面

### T040 — Agents v2 与 behavior restore

- **层/FR**：frontend L4 component/projection；FR-006/007/026。
- **文件**：Agent page、existing agent domain files/tests、co-located CSS。
- **依赖**：T022/T024/T025。
- **RED_SETUP**：test loading/empty/recoverable-error/origin-403/behavior conflict-409、restore_version 可达、override load failure可见、raw id/path/model alias Advanced、390px Web 窄窗口 visible/focusable trigger；明确 not-found/disconnected N/A；call assertion 必须同时断言 DOM/state/result。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/AgentCenter.test.tsx src/domains/agents/BehaviorVersionHistory.test.tsx src/domains/agents/agentManagementData.test.ts`
- **RED_ORACLE**：restore/permission/失败状态/敏感字段/a11y 任一不符。
- **GREEN_CHANGE**：页面消费 typed application commands/projection；不继续扩大 AgentCenter God component。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/AgentCenter.test.tsx src/domains/agents/BehaviorVersionHistory.test.tsx src/domains/agents/agentManagementData.test.ts`
- **GREEN_ORACLE**：真实 action 可达且普通摘要/Advanced 边界正确。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/AgentCenter.test.tsx src/domains/agents/BehaviorVersionHistory.test.tsx src/domains/agents/agentManagementData.test.ts`

### T041 — Memory v2 与 dead action/route 清理

- **层/FR**：frontend L4 component/projection；FR-006/007/026。
- **文件**：`domains/memory/MemoryPage*`、actual action sections/CSS；dead `MemoryActionsSection` 仅在证据确认后删除。
- **依赖**：T024/T025。
- **RED_SETUP**：test `/advanced` 不可达链接 absence、dead management actions不进入页面、query/consolidate/SOR/retrieval实际动作、loading/empty/recoverable-error/origin-403/user terms；明确 not-found/disconnected/409 N/A，index transition 由现有互斥状态表达。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/memory/MemoryPage.test.tsx`
- **RED_ORACLE**：dead link/action扩域、状态/权限/用户术语不符。
- **GREEN_CHANGE**：只呈现真实可达 Memory 能力；若删除 dead file，以 import graph/absence另存机械证据但页面行为仍由本 TDD 证明。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/memory/MemoryPage.test.tsx`
- **GREEN_ORACLE**：无死链/扩域，现有 action state 准确。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/domains/memory/MemoryPage.test.tsx`

### T042 — Files v2 与 path sensitivity

- **层/FR**：frontend L4 component/projection；FR-006/007/022。
- **文件**：`pages/FilesCenter*`、`WorkspaceGitView*`、files projection/CSS。
- **依赖**：T024。
- **RED_SETUP**：test loading/empty/error/403、file/git/rollback conflict、危险确认、workspace-relative path截断/复制权限、hash/storage普通区 absence、a11y。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/FilesCenter.test.tsx src/pages/WorkspaceGitView.test.tsx`
- **RED_ORACLE**：retry/permission/rollback/path sensitivity 或用户语言任一不符。
- **GREEN_CHANGE**：复用现有 client/竞态保护/rollback contract，页面只消费 pure projection。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/FilesCenter.test.tsx src/pages/WorkspaceGitView.test.tsx`
- **GREEN_ORACLE**：状态/确认/sensitivity准确。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/FilesCenter.test.tsx src/pages/WorkspaceGitView.test.tsx`

### T043 — Skills v2 与统一 transport

- **层/FR**：frontend L4 component/projection；FR-006/007/009/011。
- **文件**：新 `pages/SkillCenter.test.tsx`、Skill page/projection/CSS。
- **依赖**：T022/T024。
- **RED_SETUP**：test list/detail/install/delete、loading/empty/error/403、file modal/focus、raw SKILL.md/schema/tools Advanced、hard-coded light/old token absence。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/SkillCenter.test.tsx`
- **RED_ORACLE**：当前 direct fetch、浅色 modal、状态/权限/Advanced/a11y 不符。
- **GREEN_CHANGE**：统一 adapter + generated type + Claude visual hierarchy；安装校验不复制后端算法。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/SkillCenter.test.tsx`
- **GREEN_ORACLE**：动作/状态/transport/style/a11y正确。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/SkillCenter.test.tsx scripts/check-f149-style-ratchet.test.ts`

### T044 — MCP v2 与 write-only env/secret

- **层/FR**：frontend L4 component/projection/security；FR-006/007/013/022。
- **文件**：新 `pages/McpProviderCenter.test.tsx`、`components/McpInstallWizard.test.tsx`、MCP pure state/command/CSS。
- **依赖**：T013/T015/T024/T025。
- **RED_SETUP**：test loading/empty/recoverable-error/origin-403、install disconnected/timeout、read summary、keep/replace/remove/清空、poll error可见、不得伪造“每5s”产品契约、env/command ordinary absence、390px Web 窄窗口 visible/focusable trigger而非 long-press-only；明确 not-found/409 N/A。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/McpProviderCenter.test.tsx src/components/McpInstallWizard.test.tsx`
- **RED_ORACLE**：catalog回填 value、secret未清空、silent catch、手写 InstallResult、普通词/a11y/permission 任一不符。
- **GREEN_CHANGE**：generated action/SSE/REST type + typed VM；轮询状态可取消且不新增 registry/store/transport。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/McpProviderCenter.test.tsx src/components/McpInstallWizard.test.tsx`
- **GREEN_ORACLE**：安全语义、动作与视觉状态准确。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run src/pages/McpProviderCenter.test.tsx src/components/McpInstallWizard.test.tsx`

## P5 — L1 最小浏览器语义

### T050 — L1 harness worktree/PYTHONPATH contract

- **层/FR**：L4 config contract；FR-016/017。
- **文件**：`frontend/testing/l1SelectorsContract.test.ts`、`playwright.config.ts`、narrow L1 support/launcher。
- **依赖**：T000。
- **RED_SETUP**：先在 test 内建立独立 config-contract validator 与 seeded fixtures：retired SDK path、逐一遗漏七个保留 package、CI `retries>0`、ambient/host path 都是 negative；精确七包 + Gateway、`PYTHONNOUSERSITE=1`、`--no-sync`、`retries=0` 与 deterministic fixture route contract 是 accept control。先让 inert validator 错误接受至少一个 seeded negative；actual post-F151 config 只作为额外 accept control。
- **RED_COMMAND**：`cd octoagent/frontend && npx vitest run testing/l1SelectorsContract.test.ts`
- **RED_ORACLE**：test-only inert validator 错误接受 retired SDK、缺任一保留 package、CI retry 或 ambient/host path fixture；失败是具体 seeded assertion，不依赖当前 pre-F151 config。
- **GREEN_CHANGE**：实现 test-only contract validator并校验 actual post-F151 config；只有 actual 不满足 canonical profile 时才窄改 config/注释/fixture contract。若 rebase 后已满足则 production 零改动；绝不加入 retired SDK，不触宿主状态。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx vitest run testing/l1SelectorsContract.test.ts`
- **GREEN_ORACLE**：所有 negative fixtures 被拒绝、accept control 与 actual post-F151 config 通过；七个保留 packages + Gateway 精确、SDK/ambient/host path absent、`retries=0`，selector/fixture anchors 同源。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx vitest run testing/l1SelectorsContract.test.ts`

### T051 — 10 个 Web surface × 390 窄窗口 geometry/a11y sweep

- **层/FR**：L1；FR-001/007/008/016。
- **文件**：新 `frontend/e2e/f149-responsive-a11y.spec.ts`、selectors/support。
- **RED_DEPENDS**：T050；RED 必须在 T030–T044 页面 GREEN 前执行。
- **GREEN_DEPENDS**：T030–T044。
- **RED_SETUP**：先创建并确认被 Playwright 收集的 10 route 参数化 spec；390px 只作为 Web 窄浏览器窗口。每页只断言 `scrollWidth<=clientWidth`、main heading/landmark、可见操作 accessible name、键盘可达/focus、reduced-motion；不穷举业务状态，不验证手机产品、移动认证或原生 iOS。
- **RED_COMMAND**：`cd octoagent/frontend && npx playwright test e2e/f149-responsive-a11y.spec.ts`
- **RED_ORACLE**：当前任一目标 Web 页在 390px 窄窗口的 overflow/focus/a11y/结构不符合 Design matrix；不是 server/import/selector 0-test，也不是 iOS oracle。
- **GREEN_CHANGE**：只修浏览器/布局语义；业务分支仍由 L4。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx playwright test e2e/f149-responsive-a11y.spec.ts`
- **GREEN_ORACLE**：十页 geometry/a11y 全通过。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx playwright test e2e/f149-responsive-a11y.spec.ts`

### T052 — A 波 Task→Detail/EventSource/Advanced clipboard 旅程

- **层/FR**：L1；FR-003/008/016/028。
- **文件**：新 `frontend/e2e/f149-a-wave.spec.ts`、deterministic fixture/support。
- **RED_DEPENDS**：T050 与 deterministic fixture；RED 必须在 T023/T031 页面 GREEN 前执行。
- **GREEN_DEPENDS**：T023/T031。
- **RED_SETUP**：fixture 提供任务、真实 EventSource state/disconnect 与合成 diagnostic；spec 从 Tasks 进入 Detail，断言状态/断连恢复、Advanced focus return 与 clipboard scrub，并用 REST/事件链做 UI 外 oracle。使用条件 poll，不固定 sleep。
- **RED_COMMAND**：`cd octoagent/frontend && npx playwright test e2e/f149-a-wave.spec.ts`
- **RED_ORACLE**：当前 raw status/payload、断连/clipboard/focus 或外部事件链不符合合同。
- **GREEN_CHANGE**：接通已由 L4/L3证明的 adapter/state；不新增 task cancel/resume。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx playwright test e2e/f149-a-wave.spec.ts`
- **GREEN_ORACLE**：浏览器独有 EventSource/clipboard/focus 与外部事件链一致。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx playwright test e2e/f149-a-wave.spec.ts`

### T053 — B 波 Skill file chooser/modal/focus 旅程

- **层/FR**：L1；FR-006/008/016。
- **文件**：新 `frontend/e2e/f149-b-wave.spec.ts`、deterministic fixture/support。
- **RED_DEPENDS**：T050 与 deterministic fixture；RED 必须在 T043 页面 GREEN 前执行。
- **GREEN_DEPENDS**：T043。
- **RED_SETUP**：用真实 `<input type=file>` 上传合成 SKILL.md，验证 modal accessible name、安装结果的 API/文件外部 oracle、关闭后 focus return；不重复 L4 的 error/permission 分支。
- **RED_COMMAND**：`cd octoagent/frontend && npx playwright test e2e/f149-b-wave.spec.ts`
- **RED_ORACLE**：当前 file chooser/modal/focus/外部结果不满足浏览器旅程。
- **GREEN_CHANGE**：只补 browser wiring/accessibility；安装业务规则继续由 backend/adapter。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx playwright test e2e/f149-b-wave.spec.ts`
- **GREEN_ORACLE**：真实 chooser/modal/focus 与外部结果一致。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx playwright test e2e/f149-b-wave.spec.ts`

### T054 — F150 global 401 / F149 origin-403 交界

- **层/FR**：L1；FR-007/008/021。
- **文件**：新 `frontend/e2e/f149-auth-boundary.spec.ts`、F150 deterministic fixture extension。
- **RED_DEPENDS**：T000/T050；RED 必须在 T024/page permission GREEN 前执行。
- **GREEN_DEPENDS**：T024 与各 surface permission composition。
- **RED_SETUP**：一条旅程分别触发 401 与 origin 403；401 必须进入 F150 global Access，403 必须保留 shell并显示资源权限不足，页面不出现 owner/重新登录动作。
- **RED_COMMAND**：`cd octoagent/frontend && npx playwright test e2e/f149-auth-boundary.spec.ts`
- **RED_ORACLE**：ownership 任一反转、重复 Access state machine 或 403 丢 shell。
- **GREEN_CHANGE**：只接 F150 已稳定 global contract；不在页面解释认证实现。
- **GREEN_COMMAND**：`cd octoagent/frontend && npx playwright test e2e/f149-auth-boundary.spec.ts`
- **GREEN_ORACLE**：401/403 唯一 owner 清晰且恢复动作正确。
- **REFACTOR_COMMAND**：`cd octoagent/frontend && npx playwright test e2e/f149-auth-boundary.spec.ts`

## P6 — Coverage、Review 与最终 Gate

### T060 — authored executable frontend changed-lines ≥90%

- **类型**：quality gate；不新增行为。
- **依赖**：T004/T005/T020–T054。
- **COMMAND_1**：`cd octoagent/frontend && npm run test:coverage`
- **COMMAND_2**：`node repo-scripts/check-frontend-changed-lines-coverage.mjs --lcov octoagent/frontend/coverage/lcov.info --base origin/master --min-percent 90`
- **ORACLE**：只统计 authored executable TS/TSX；tests/generated/`.d.ts`/type-only 排除；低于 90% 或新源码无 lcov 失败。

### T061 — frontend contract/architecture/style/type/test gates

- **类型**：quality gate；不新增行为。
- **依赖**：T001–T005/T015/T020–T054。
- **COMMANDS**：
  - `cd octoagent/frontend && npm run openapi:check`
  - `cd octoagent/frontend && node scripts/check-f149-boundaries.mjs`
  - `cd octoagent/frontend && node scripts/check-f149-style-ratchet.mjs`
  - `cd octoagent/frontend && npx tsc -b`
  - `cd octoagent/frontend && npm test`
  - `cd octoagent/frontend && npm run check:complexity`
  - `cd octoagent/frontend && npm run build`
- **ORACLE**：generated clean、closure bypass=0、style/index ratchet、type/Vitest/complexity/build 全通过。

### T062 — Gateway L4/L3 与 deterministic regression

- **类型**：quality gate；不新增行为。
- **依赖**：T010–T014。
- **COMMANDS**：
  - `cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q apps/gateway/tests/test_f149_web_contract.py apps/gateway/tests/test_f149_task_sse_contract.py apps/gateway/tests/test_f149_secret_egress.py tests/integration/test_f149_web_contract.py`
  - `cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest -q -m "e2e_smoke or e2e_scripted"`
- **ORACLE**：L4/L3 deterministic 全绿；无网络/真 LLM/宿主状态/固定 sleep；L2 不新增。

### T063 — TDD evidence 与 command-policy Review

- **类型**：process review。
- **依赖**：全部行为 tasks。
- **REVIEW**：逐 task 解析真实 command/evidence，核对 RED assertion、GREEN/REFACTOR 同 oracle、UTC/HEAD/status/exit/log/secret scan；不得对 tasks 文本做会命中禁令说明的负向 rg。
- **ATOMIC_RELOCATION_CONDITIONAL**：若实现出现纯搬迁，必须有独立 task 及 before/after/absence/import/diff 五证；否则报告 planned atomic relocation=0。
- **ORACLE**：任一行为缺稳定 RED 或任一 relocation 缺证据即 Gate fail。

### T064 — 可执行坏味道审计

- **类型**：mechanical + adversarial review；FR-019/029。
- **文件**：`review/code-smell-audit.md`。
- **MECHANICAL_COMMANDS**：T061 boundary/style/complexity checkers + AST report。
- **ADVERSARIAL_REVIEW**：God class/function、职责漂移、mock-self、命名失真、概念泄漏、hidden global、unreachable/silent branches；逐 source/test 人工挑战，不以关键词命中代替。
- **ORACLE**：每项有 repo-relative path:line、baseline/current/delta、mechanical|adversarial、must-fix|ratchet|future、owner、证据；MUST FIX=0，cycle=0，direct fetch=0，token helper=0，secret leak=0，index.css wc≤4476。

### T065 — 20-frame Design fidelity Review

- **类型**：visual review；FR-009/023。
- **文件**：实现截图/逐 frame checklist、`review/design-fidelity.md`。
- **REFERENCE**：`design-output/2026-07-21/OctoAgent Web.dc.html`，SHA-256=`a2db08ea0eb39278558e61e87a355b042c98ad24273b201940925f310271d98a`。
- **REVIEW**：20 unique frames逐页检查视觉层级、留白、卡片节奏、信息密度、视觉张力、排版、390 Web 窄窗口 overflow/focus、state matrix、ordinary-language absence、Spotify=0；旧 page 1–3/F148 shell 不改。任何因明确功能合同、可用性或无障碍产生的 F149 Web 视觉偏离必须逐页记录原因、影响和证据；iOS 原生平台规范只由 iOS Feature 记录，不在 F149 中据此改变 Web。
- **ORACLE**：不得只以 `--cp-*` 一致宣称通过；现有 Web 不得被当作视觉基线，任一页以“实现方便”为理由、无必要理由偏离 Claude 原稿或回退为旧 Web 密集平铺即 fail。

### T066 — Blueprint/living docs/scope Review

- **类型**：docs + scope gate；FR-020。
- **文件**：`docs/blueprint/milestones.md`、`docs/codebase-architecture/modules/06-frontend-workbench.md`、Feature trace/completion report。
- **REVIEW**：实施完成后同步 `blueprint-sync.md` 的 auth/contract/sensitivity/scope；确认未改 F150/F151 owner、未建第二 transport/store/theme/secret registry/per-page service。
- **SCOPE_COMMAND**：`git status --short && git diff --name-only origin/master`
- **ORACLE**：权威 docs 与实现一致；completion report 不替代 Blueprint；无越权 stage/commit/push。

## 7. FR → Task / 层 / 文件 / command / failure oracle 矩阵

> “command 字段”均指上文对应 task 中完整、可复制的 `RED_COMMAND/GREEN_COMMAND/REFACTOR_COMMAND`；Review 不从说明文字推导命令。

| FR | Task | 层 | 主要文件 | 精确 command 字段 | 失败 oracle |
|---|---|---|---|---|---|
| 001 | T031/T034/T040–T044/T051 | L4+L1 | 十页 co-located tests；responsive spec | 各 task 三命令；T051 三命令 | 路由/动作扩域或 20 个 viewport surface 缺失 |
| 002 | T021/T030 | L4 | approval adapter/models/page | T021/T030 三命令 | 三类候选被合并、404/409/403漂移 |
| 003 | T023/T031/T052 | L4+L1 | task decoder/list/detail/A旅程 | T023/T031/T052 三命令 | raw status/event 或第二状态机 |
| 004 | T025/T034 | L4 | typed actions/Automation | T025/T034 三命令 | 超出 pause/resume 或 raw cron/job普通可见 |
| 005 | T032/T033 | L4 | Settings/Maintenance/secret | T032/T033 三命令 | 维护未归位、God职责增长或内部词 |
| 006 | T024/T040–T044 | L4 | B波 projection/page tests | 对应 task 三命令 | raw ID/path/command/env/schema普通可见 |
| 007 | T024/T030–T044/T054 | L4+L1交界 | shared state + 每页 tests | T024及每页三命令；T054 | loading/empty/error/403不互斥或401下沉 |
| 008 | T051–T054 | L1 | 四个 F149 Web specs | 各 L1 task 三命令 | 390 Web 窄窗口/focus/modal/file/EventSource/Web auth 浏览器语义失败；不含移动认证/iOS |
| 009 | T002/T030–T044/T061/T065 | L4+architecture+visual | style checker/domain CSS/review | T002三命令；T061 commands；T065 review | 第二主题/Spotify/浅色/index增长/视觉回退 |
| 010 | T003/T010/T012/T015/T020/T023/T025 | contract+L4 | schema/generated/decoders | 对应 task 三命令 | any、非法 unknown/raw、生成漂移或未强类型消费字段 |
| 011 | T001/T021/T022/T061 | L4+architecture | boundary/API adapters | T001/T021/T022三命令；T061 | direct fetch/token/header/page401非零 |
| 012 | T001/T020/T024/T025/T061 | L4+architecture | layer checker/projection/application | 对应 task commands | 反向 import、页面读 raw、规则复制 |
| 013 | T001/T032/T044/T061 | L4+architecture | checker/Settings/MCP | 对应 task commands | fallback/global injection/第二状态或主路径 |
| 014 | T001–T054/T063 | process | tests/evidence | 每行为 task 三命令；T063 review | 无稳定 RED 或同 oracle闭环 |
| 015 | T063 conditional | process/architecture | conditional relocation evidence | T063 review | 行为变化伪装搬迁或五证不全 |
| 016 | T020–T054 | L4/L3/L1 | co-located tests + thin L1 | 对应 task commands | view-model/state被挪到大量 L1 |
| 017 | T010–T014/T050/T062/T063 | L4/L3/process | Python tests/Playwright config | 完整 Python fields；T050；T062 | 缺 PYTHONPATH/--no-sync、sleep/rerun/宿主状态 |
| 018 | T004/T005/T060 | L4+quality | coverage checker/config | T004/T005三命令；T060 commands | executable changed-lines<90或错误计入排除项 |
| 019 | T001/T002/T064 | architecture+review | checkers/smell audit | T001/T002；T064 review | smell缺路径/metric/owner或MUST FIX未清零 |
| 020 | T000/T066 | process/scope | readiness/docs/git | T000 oracle；T066 scope | 上游/main未放行即实现或越权改动 |
| 021 | T021/T024/T054 | L4+L1 | error mapper/page state/auth spec | 对应三命令 | 401/403 ownership反转 |
| 022 | T013/T014/T024/T033/T042/T044/T052 | L4+L3+L1 | secret/path/clipboard chain | 对应三命令 | sentinel/value任一出站/DOM/clipboard/evidence泄漏 |
| 023 | Design PASS；T065防漂移 | Design/visual | manifest/export/review | T065 review | 20/20或辅助frame/visual baseline漂移 |
| 024 | T001/T020/T024/T025/T061 | architecture+L4 | seam/checker/application | 对应 task commands | per-page service/第二 transport/store |
| 025 | T010/T020 | Gateway+frontend L4 | snapshot contract/decoder | T010/T020三命令 | envelope/resource错误漏验或raw穿透 |
| 026 | T011/T040/T041 | Gateway+frontend L4 | action contract/Agents/Memory | 对应三命令 | restore缺失或dead Memory action扩域 |
| 027 | T011/T014/T015/T025 | Gateway L4+L3+frontend L4 | action source/artifact/wrapper | 对应三命令 | dispatch/validation/registry/artifact不同源 |
| 028 | T012/T014/T023/T052 | Gateway L4+L3+frontend L4+L1 | SSE schema/decoder/EventSource | 对应三命令 | raw unknown进业务/DOM、final/payload漂移 |
| 029 | T001/T064 | architecture+review | AST report/smell audit | T001三命令；T064 | 只rg关键词或基线/current/owner缺失 |
| 030 | T031/T032 | frontend L4 | TaskList/Maintenance section | T031/T032三命令 | “待处理事项”/维护能力位置或确认顺序漂移 |

## 8. SC → Completion Gate

| SC | 主要证据 |
|---|---|
| 001/002 | Design PASS + T065 20-frame/ordinary-language review |
| 003 | T001/T021/T022/T061：F149 closure bypass=0 |
| 004 | T002/T061：`index.css` wc≤4476、`--cp-*` only |
| 005 | T001–T054 evidence + T063；planned atomic relocation=0 |
| 006 | L4/L3/L1 矩阵 + T060 authored executable ≥90% |
| 007 | T064 MUST FIX=0、ratchet无回退 |
| 008 | T000/T066 scope gate |
| 009 | T013/T014/T033/T044/T052 secret chain |
| 010 | T024/T054 401/403 ownership |
| 011 | T003/T015/T020/T023 boundary/codegen |
| 012 | T011/T012/T014/T025 action/SSE唯一seam |
| 013 | T031/T032 Tasks/Settings产品归位 |

## 9. Tasks Gate 判定

- TDD：每个行为 task 都有稳定 RED、同 oracle GREEN/REFACTOR 与证据策略；checker prerequisite 不以缺文件见红。
- 测试分层：纯逻辑/view-model/state/DTO mapping/a11y 归 L4；全链归 deterministic L3；L1 只有 390px Web 窄窗口、A/B/Web auth 与浏览器独有语义，不覆盖移动认证或原生 iOS；L2 不新增。
- 架构分层：唯一 transport、application orchestration、pure projection、UI composition 与禁止 import 已映射到任务/checker。
- 坏味道：baseline、mechanical AST、adversarial review、MUST FIX/ratchet/future owner 均进入 T064。
- 当前风险：T000–T015、T020–T025、T030、T032 中已执行的任务均有真实证据；下一项是
  T031 Tasks list 与“待处理事项”归位。每个后续页面仍必须先逐页对照 Claude
  Design 最初版；现有 Web 只能作为实现现状，不得成为视觉基线。F149 仍须逐 task 通过
  RED→GREEN→REFACTOR，Tasks Gate 与
  前序完成均不豁免后续证据门。
