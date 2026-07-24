# F149 Plan/Tasks Gate Review

> 结论：PASS；`GATE_TASKS=true`（2026-07-21 main 独立复核）。Design/Tasks 均已通过；本轮只修正既有 Plan/Tasks/Review/Trace/Checklist 制品，未进入 production/tests 实施或测试行为执行，未 stage/commit/push。

## 1. Plan 决策闭环

| 议题 | 决策 | 防扩域边界 |
|---|---|---|
| 视觉 SoT | Claude Design 导出 hash `a2db08...d98a`；层级/留白/卡片节奏/密度/张力为主基线 | F148 只约束 `--cp-*`、主题/组件/合同，不向旧 Web 密集平铺回退 |
| REST DTO | Gateway Pydantic/OpenAPI → types-only generated → adapter decoder | 不生成 fetch client，不闭合全 control-plane |
| action contract | 同一 `ActionContractDefinition` records 派生 dispatch/validation/registry/artifact | 强类型仅 F149 可达 action；其他 explicit open schema-as-data |
| Task SSE | Gateway/web adapter schema；业务只收 state transition/artifact refresh | core 不放 SSE model；unknown/history 只进 scrubbed Advanced |
| transport | `api/client` 唯一认证 transport，platform queries/actions application orchestration | page/helper 不 fetch/拼 token/解释 401，不建第二 store/service |
| secret | Settings/MCP F149-owned write-only keep/replace/remove + full egress scrub | 复用既有 store/application；不建 security service/global registry |
| style | domain/surface co-located CSS，`--cp-*` only | `index.css` wc=4476 零增长；无第二主题/Spotify inheritance |
| Implement 前置 | F150/F151 稳定 commit + main 明确放行 | T000 fail-closed；无 fallback DTO/auth |

## 2. Tasks 结构检查

- 总任务：40；行为 TDD tasks：32；process/quality/review tasks：8。
- 行为 command fields：96，恰好每 task 3 条 `RED_COMMAND/GREEN_COMMAND/REFACTOR_COMMAND`。
- 所有 command 从仓库根独立起步：`cd octoagent...` 或 root `node repo-scripts...`。
- 所有 Python pytest command 都消费 post-F151 canonical `core/provider/protocol/tooling/skills/policy/memory` 七个保留 workspace packages + Gateway，且含 `PYTHONNOUSERSITE=1`、`uv run --project . --no-sync python -m pytest`；retired SDK path absent。
- command fields 中无 `uv sync`、裸 pytest、`--reruns`、宿主 `~/.octoagent`、固定 sleep。
- checker/npm alias 不存在问题在 T001–T005 先以 inert scaffold + seeded fixture 做行为 RED；T015 只在 alias/dependency/checker 已存在后用 generated drift 见红。
- 新目标 test 必须在各 task 的 RED_SETUP 先创建并被收集；缺文件/import/0 tests 明确不算 RED。
- 预计划 atomic relocation=0；所有真实行为/契约/视觉迁移走 TDD。纯搬迁若实现时出现，T063 强制另拆五证 task。

## 3. TDD 审查摘要

- Checker：bad fixture 被错误接受是 RED oracle；不是缺 script。
- Gateway：snapshot/action/SSE/secret 各自 L4 RED；L3 只负责 deterministic composition。
- Frontend：adapter/decoder/page state/component/a11y 按 domain co-locate，不建 A/B 巨型测试。
- L1：每个 spec 的 RED 必须由已收集浏览器行为断言触发，server/import/selector 0-test 不合格。
- L1 RED 明确安排在对应页面 GREEN 前；Phase 7 只统一收口 GREEN/REFACTOR，避免“先实现、后补首次即绿 E2E”。
- Evidence：Implement 时保存 output/exit/UTC/HEAD/status/oracle/secret scan，Review 解析真实 command/evidence，不 grep 说明文字。

## 4. 测试分层摘要

| 层 | F149 归属 | 明确禁止 |
|---|---|---|
| L4 frontend | checkers、generated/adapter decoder、projection/state、page/component、a11y/secret | 用 Playwright 穷举业务分支、mock-self |
| L4 Gateway | REST/action/SSE/secret contract/runtime validation | 网络、真 LLM、宿主状态、固定 sleep |
| L3 | ASGI/DI fake/Echo 的 OpenAPI→snapshot/action→SSE/secret egress | 代替 L4 细分支、真外部系统 |
| L1 | 10-route 390 sweep、A EventSource/clipboard、B file chooser/modal/focus、auth交界 | 重复 component/DTO mapping |
| L2 | 不新增 | 用 live 代替确定性正确性 |

T050 不再从 pre-F151 Playwright config 制造 RED。它以独立 seeded negative fixtures 覆盖 retired SDK、漏任一保留 package、CI retry 与 ambient/host path，以精确七包 + Gateway、`retries=0` 为 accept control；actual post-F151 config 只作为额外 accept control，已满足时 production 零改动。

## 5. 架构分层摘要

- Gateway/Pydantic contract source → generated adapter-only types → unique API transport/application orchestration → pure projection/state → UI/composition。
- Boundary checker覆盖 direct fetch、token/header、page 401、generated→page、reverse import、fallback、duplicate state/transport 与 cycles。
- Page 可使用 WorkbenchContext/pure projection；Application Port 只在真实隔离价值存在时使用 structural type。
- Settings 维护用窄 section；Tasks 只保留“待处理事项”；不新增 management service、page registry、transport 或 state source。

## 6. 坏味道审查摘要

T064 继承 `code-smell-baseline.md` 并要求：

- mechanical：direct fetch `10→0`、token helper `2→0`、bypass cluster `4→0`、cycle `0→0`、secret leak `3→0`、index.css `4476→≤4476`、generated/raw/import/style/compat/dead branch AST report；
- adversarial：Settings/Agent God responsibility、mock-self、命名失真、概念泄漏、hidden global、silent/unreachable branches；
- 每项必须有 repo-relative path:line、baseline/current/delta、判定、owner、evidence；MUST FIX=0，ratchet 无回退，future Fix 有 exact seam。

## 7. FR/SC traceability

- `tasks.md` §7 覆盖 FR-001～FR-030，每行映射 task、层、主要文件、精确 command 字段与 failure oracle。
- `tasks.md` §8 覆盖 SC-001～SC-013。
- frontend authored executable changed-lines ≥90% 的 checker/config/最终 gate 分别是 T004/T005/T060；tests/generated/`.d.ts`/type-only 明确排除。
- Design 已关闭 FR-023 输入/输出，但 T065 保留 20-frame 防漂移，不重复设计或改旧 page 1–3。

## 8. 机械校验结果

- `HEAD == origin/master == 9d5e1e48691c5ae5a12b33f224d64ac03d5442fc`（2026-07-21 fetch 复核）。
- `plan.md` 与 `tasks.md` 已存在；未生成/修改生产或测试文件。
- Plan=220 lines；Tasks=586 lines。
- command parser：behavior tasks=32、command fields=96、bad count=0、bad post-F151 Python prefix=0、retired SDK path=0、banned command=0、non-root command=0。
- `plan.md`/`tasks.md` 无 `GATE_DESIGN=false`、旧 Web 作为视觉基线或 Spotify 继承文案。
- worktree 仍只有 `.specify/features/149-web-pages-v2/` untracked；未 stage/commit/push。

## 9. 剩余风险与 Gate 请求

1. F150/F151 稳定与 main Implement 放行仍是 T000 硬前置；F151 stable commit 后必须 rebase/recon 并重读 tests/AGENTS、Playwright config 与 stage profile。canonical package set 不一致即回 Gate，禁止兼容 retired SDK。
2. Spec Driver plugin path/init/orchestration 缺失，已按 skill 的制品回退恢复；不把此基础设施缺口当 TDD RED。
3. `openapi-typescript@7.13.0`、coverage provider 与 npm aliases只在未来 T005 实施；当前未安装/执行。
4. `GATE_TASKS=true` 不等于 Implement 放行；F150/F151 stable commit、rebase/recon 与 main 再次明确授权未齐前，T000 保持 CLOSED。

main 最终结论：PASS。当前硬停在 Implement 前置，不自行进入下一阶段。
