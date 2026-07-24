# Feature Specification: F149 Web 其余页面 v2

**Feature Branch**: 未创建（Design Gate 前不建分支）
**Created**: 2026-07-20
**Status**: Design/Tasks Gate 已通过；生产实现仍由 T000 硬阻断
**Input**: M11 F149 A/B 波页面升级；生产实现等待 F150/F151 稳定与 main 放行。

**Product Surface Boundary**: 桌面端保留 Web 入口；手机产品只走原生 iOS App，不把 390px Web/手机浏览器作为移动产品入口。F149 只实施 Web，原生 iOS 实现不属于本 Feature；本文所有 `390px`、窄视口和响应式检查只验证 Web 在窄浏览器窗口中的健壮性，不构成手机产品、移动认证或 iOS 验收。Web 与 iOS 均以 Claude Design 最开始的方案为视觉/交互基线并保留同一视觉语言；iOS 由其 Feature 使用 SwiftUI、Apple 原生导航、手势、控件和无障碍语义适配，不复制 Web 组件结构。

## User Scenarios & Testing

### User Story 1 - A 波日常管理（Priority: P1）

普通用户可以在审批、任务、自动化和设置页面理解当前状态、完成已有动作，并在失败或无权限时知道下一步，而不需要理解内部协议。

**Independent Test**: 使用确定性 fixture 分别渲染四页的成功、空、失败和权限状态；在 390px Web 窄窗口中完成一次审批处理或任务查看旅程，主界面不出现禁用内部术语。该检查不覆盖原生 iOS 或移动认证。

**Acceptance Scenarios**:

1. **Given** 用户打开 A 波任一页面，**When** 数据正常返回，**Then** 页面用用户语言呈现现有能力和下一步动作。
2. **Given** 数据为空、失败或 origin 403 资源权限不足，**When** 页面稳定，**Then** 显示可区分状态和可执行恢复动作，且不泄漏原始异常或认证实现。
3. **Given** Web 浏览器窄窗口视口为 390px，**When** 用户导航、打开详情或 modal，**Then** 内容不横向溢出，焦点和主要动作可用；该场景不代表手机产品或 iOS 交互验收。
4. **Given** 用户查看 Tasks 或 Settings Advanced，**When** 待处理数与维护状态变化，**Then** Tasks 只以“待处理事项”呈现非零管理入口，恢复/备份/导出/更新/重启/验证只出现在 Settings 的“维护与恢复”。

### User Story 2 - 一致且安全的页面状态（Priority: P1）

普通用户在所有 F149 页面获得一致的 loading、empty、error、permission 体验；管理员仍可在明确的 Advanced 区查看必要技术信息。

**Independent Test**: component tests 注入标准状态，验证可感知名称、焦点、重试命令、权限提示和普通界面的术语 absence；无需浏览器穷举业务分支。

**Acceptance Scenarios**:

1. **Given** 页面尚未完成加载，**When** 用户使用键盘或读屏，**Then** loading 状态可感知且不会出现不可操作的主要按钮。
2. **Given** 已登录用户遇到 origin 403，**When** 页面显示资源权限态，**Then** shell 保持可用且 failure 与 permission 不混为一类；401/session 过期转交 F150 全局 Access Gate。
3. **Given** 管理员展开 Advanced，**When** 查看高级字段，**Then** 普通摘要仍保持用户语言；secret value 永不下发、显示或复制。

### User Story 3 - B 波资产与能力管理（Priority: P2）

用户可以在智能体、记忆、文件、技能和 MCP 页面理解已有资产、完成现有管理动作，并明确区分用户摘要与高级配置。

**Independent Test**: 使用确定性 adapter fixture 验证五页的 view model、动作和所有标准状态；在浏览器中完成一条需要真实 modal/file chooser/focus 行为的关键旅程。

**Acceptance Scenarios**:

1. **Given** 用户浏览 B 波页面，**When** 数据加载完成，**Then** 首屏优先显示名称、状态、用途和可执行动作，而不是 raw ID/path/command/schema。
2. **Given** 用户执行安装、保存、归档或恢复等现有动作，**When** action 成功或失败，**Then** UI 从单一状态源得到确定结果，并提供明确反馈。
3. **Given** 页面需要技术配置，**When** 用户未展开 Advanced，**Then** 主界面不暴露内部实现；展开后只允许已净化、非 secret 且有权限的字段按敏感级别截断或复制。

### User Story 4 - 可维护的协议边界（Priority: P1）

开发者可以从稳定 REST OpenAPI、SSE schema 与 action-specific schema 重生成 F149 网络 DTO，并通过现有 platform seam 映射为 UI view model；页面不再维护第二套 wire schema 或认证 transport。

**Independent Test**: codegen clean-diff/import gate 与 adapter mapping tests 通过；删除任一 schema 字段会使契约或 mapper 测试稳定失败，页面测试不依赖手写 wire fixture。

**Acceptance Scenarios**:

1. **Given** 稳定 REST/SSE/action artifacts，**When** 执行 types-only codegen，**Then** 结果可重复且无 `any`；有意义的开放 JSON 只停在命名 raw/metadata/schema-as-data boundary，并在进入 F149 domain/UI 前通过 decoder/type guard。
2. **Given** 页面加载或提交数据，**When** 检查依赖，**Then** UI 通过现有 WorkbenchContext/application orchestration 与 pure projection 取数，不直接 fetch、拼 token 或导入 generated wire type。
3. **Given** 后端枚举或 nullable 契约变化，**When** 运行 contract/mapping tests，**Then** 失败发生在 schema/adapter 边界而不是线上页面。

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 保持 F149 范围为 A 波 `/approvals`、`/work`、`/tasks/:taskId`、`/automation`、`/settings`，B 波 `/agents`、`/memory`、`/files`、`/skills`、`/mcp`；不得加入后端尚未支持的新动作。
- **FR-002**: 审批页 MUST 继续明确表示 F145 的三类候选，不得与通用 tool approval 无设计地合并。
- **FR-003**: 任务列表和详情 MUST 使用一致的用户状态语义；不得显示未映射 raw status，也不得建立第二个任务事件状态机。
- **FR-004**: 自动化页 MUST 保持当前 pause/resume 产品范围；原始 job/action/cron 值仅在 Advanced 可见。
- **FR-005**: 设置页 MUST 等待 F150/F151 稳定契约后适配；普通区域不得出现 LiteLLM、JWT、AUD、JWKS 或等价实现术语。现有 recovery summary、backup、chat export、update dry-run/apply、runtime restart/verify MUST 从 Tasks 移入 Settings → Advanced → 维护与恢复；不得新增独立页面、management service、registry 或第二状态源，后续 UI 必须以窄的 `MaintenanceRecoverySection` 一类组件由 Settings 组合，禁止继续扩大 Settings God component。
- **FR-006**: 智能体、记忆、文件、技能与 MCP 页 MUST 首先呈现用户摘要；raw ID/path/model alias/command/env/schema/tool name 仅能按 sensitivity policy 进入 Advanced。
- **FR-007**: 每个 F149 surface MUST 明确支持 loading、empty、recoverable error、origin 403 resource permission；若某状态不适用，MUST 在测试矩阵记录理由。401/Access 状态不属于页面状态。
- **FR-008**: 所有 Web 页面 MUST 支持 desktop 与 390px Web 窄窗口；drawer、modal、焦点、键盘、可感知名称、reduced motion 和长内容溢出 MUST 可验证。390px 只代表 Web 响应式健壮性，MUST NOT 被描述为手机产品、移动认证或原生 iOS 验收；原生 iOS 不属于 F149。
- **FR-009**: 已通过 Design Gate 的 Claude Design 最开始方案 MUST 作为 Web 与 iOS 的共同视觉/交互基线，保留原稿的层级、留白、卡片节奏、视觉张力、信息密度和排版；F149 Web 实现 MUST 适配设计稿，现有 Web UI 仅是 current-state/reference evidence，不是视觉基线。只有明确的功能合同、可用性、无障碍或适用于 iOS Feature 的 Apple 原生平台规范才允许调整；每项偏离 MUST 记录原因、影响和逐页审查证据，MUST NOT 以“实现方便”为理由。iOS 必须保持同一视觉语言但使用 SwiftUI、Apple 原生导航、手势、控件与无障碍语义，MUST NOT 复制 Web 组件结构；该 iOS 实现不属于 F149。F148 只约束 shell 边界、`--cp-*`、现有主题/组件和功能合同；即使 Claude Design 选择器显示 Spotify Design System，也 MUST NOT 导入或继承其 token、颜色、组件、排版或 theme。20 个 frame MUST 逐页提供 F148/`--cp-*` theme-token provenance 与 Spotify absence；MUST NOT 建立第二主题、硬编码浅色 palette 或增加 `src/index.css` 行数。
- **FR-010**: REST wire DTO MUST 由稳定 OpenAPI 生成；task SSE frame MUST 由 Gateway/web adapter 的唯一 schema 生成；F149 实际消费的 action params/result MUST 由同源 action contract 生成。F149 新增/触达的 contract/projection 代码 MUST NOT 使用 `any`；`unknown`/递归 `JsonValue` 只允许存在于明确命名的 raw/metadata/schema-as-data transport boundary，且必须经 runtime decoder/type guard 才能进入 domain/UI。不得伪造全 control-plane 闭合模型，也不得借机清扫不在 F149 slice 的全仓历史类型。
- **FR-011**: 除唯一底层认证 transport 与 F150 指定的 SSE URL builder 外，F149 触达闭包 MUST NOT 直接 fetch、拼 token/header/query-token 或解释 401。`api/approval-center.ts`、`api/memory-candidates-types.ts`、AgentCenter、SkillCenter 均在审计范围；404/409 特殊语义 MUST 留在 adapter/domain error mapper。
- **FR-012**: 依赖 MUST 映射到现有职责：`api/client` 是唯一 transport；`platform/queries/actions` 是 application orchestration；pure projection/state 不依赖 React/Gateway/transport；page 与 WorkbenchContext 是 UI/composition。页面可消费 WorkbenchContext 与 pure projection；port 只在真实替换/隔离 seam 引入。
- **FR-013**: 系统 MUST NOT 使用 optional fallback、class/global mutable injection、重复状态源、兼容层叠加或第二条网络主路径。
- **FR-014**: 每个行为改动 task MUST 按 RED → GREEN → REFACTOR 排列，并记录精确命令、预期失败原因和通过 oracle。
- **FR-015**: 纯机械搬迁 MUST 标记 atomic relocation，以前后契约、旧路径 absence 和 import gate 证明；不得伪装成 TDD。
- **FR-016**: view-model/state/DTO mapping/accessibility 分支 MUST 优先放在各 domain co-located Vitest/component tests；MUST NOT 用两个 A/B 巨型 test 文件承接十页。Playwright 仅包含轻量全路由 390px Web 窄窗口 geometry/a11y sweep、A/B 各一条关键旅程、一条 F150/F149 Web auth 交界与真实浏览器语义；MUST NOT 将其解释为移动认证或 iOS 测试。
- **FR-017**: Python 测试 MUST 从 `octoagent` cwd 使用 `PYTHONNOUSERSITE=1`、完整 worktree PYTHONPATH、`uv run --project . --no-sync python -m pytest`；worktree MUST NOT `uv sync`，测试 MUST NOT 使用固定 sleep、blanket rerun、宿主状态或复制生产算法。Tasks Gate 逐 task 解析 command 字段，不得用会命中禁令说明文字的负向 grep 代替审查。
- **FR-018**: 测试矩阵 MUST 将每条 FR/关键 task 映射到层级、文件、机械可运行命令和失败 oracle，并区分 L4/L3/L1/L2。frontend changed-lines gate只统计 authored executable TS/TSX，排除 tests、generated DTO、`.d.ts` 与纯 type-only 行；≥90% 只是最低门。
- **FR-019**: Review MUST 执行 God class/function、职责漂移、重复状态、兼容层叠加、命名失真、概念泄漏、隐藏全局状态、循环依赖、不可达分支、过宽 DTO、mock-self test 审计，并分为本 Feature 必修、ratchet、后续 Fix。
- **FR-020**: F149 生产实现 MUST 等待 F150/F151 契约稳定、Claude Design 关键稿可审查且 main 明确放行；Design Gate 前只能修改 Spec Driver 制品。
- **FR-021**: F150 MUST 唯一拥有未登录、401、session 过期、登出与全局 Access Gate；F149 MUST 只处理 origin 403/资源级 permission，并用 L4 穷举与一条交界 L1 验证 ownership。
- **FR-022**: F149 MUST 自行承担 Settings 与 MCP 共用安全语义的窄端到端 contract slice，并复用现有 config/secret store、application boundary 与唯一 transport，不建立全局 secret registry 或第二 transport。secret value MUST 在服务端边界移除，永不出现在 list/get/snapshot/action response/SSE/error/log、DOM、clipboard 或 TDD evidence。read 只允许 name/configured/redacted summary；create/edit 使用 ephemeral masked input；save 明确 keep/replace/remove，placeholder 不得回写，成功、失败或关闭后前端都必须清空。F151 只负责既定底层 runtime/package/secret reference 边界，不拥有 HTTP representation。path/command 仅在已净化、非 secret、权限允许时分级截断/复制。
- **FR-023**: Worktree/main MUST 先按 canonical `actual-input-manifest.md` 回存可审查的当前 10 页 Desktop/390 Web 窄窗口与 F148 shell Desktop/390 Web 窄窗口截图，逐图记录 route、viewport、日期、commit 与数据来源，并明确 deterministic fixture 不是真实后端。上传制品 MUST 在 Claude 可见路径实际提供与 canonical 字节/hash 一致的 `references/current/actual-input-manifest.md`；发送 Prompt 前必须在项目文件列表确认 manifest、metadata 与 22 张 PNG 共 24 个 Reference 文件可见，连接中断后重新核对。Claude Design 随后 MUST 基于已附文件回存 20 个唯一页面 frame、Shared-States 与 Advanced-Pattern；任一输入缺失时 GATE_DESIGN 不通过。
- **FR-024**: 默认 MUST 复用 `api/client → platform/contracts/actions/queries → domain projection → page`。Application Port 只在真实多实现或隔离测试价值存在时使用 structural type；MUST NOT 为分层新增每页 service/registry、第二状态容器或第二 transport。
- **FR-025**: `GET /api/control/snapshot` MUST 采用 raw envelope → F149 consumed projection；完整 envelope 的 status/degraded/resource_errors 与全部 resource 名必须被验证，但 F149 只强类型化实际消费字段，其他开放聚合字段不得直接进入业务 mapper。
- **FR-026**: F149 Agent MUST 保留真实可达的 `behavior.restore_version`；Memory MUST NOT 因不可达的 `MemoryActionsSection` 扩入 diagnostics/backup/export/operator/channel 管理动作。
- **FR-027**: action handler dispatch、runtime validation、registry discovery 与生成 artifact MUST 收敛到唯一同源 seam；只补 frontend union 不合格，且不得借 F149 闭合全部 registry。
- **FR-028**: Task SSE schema/decoder MUST 属于 Gateway/web adapter，不得放 core domain。业务 payload 只闭合 TaskDetail 实际消费的 `STATE_TRANSITION.to_status`；`ARTIFACT_CREATED` 只触发刷新；未知/历史事件只可经 scrubbed diagnostic decoder 进入 Advanced，不能进入业务 mapper。
- **FR-029**: 坏味道 Review MUST 对每项记录 source path+line、baseline/current metric、判定、处置和 owner；direct fetch、import graph、重复 transport/state、复杂度、compat/fallback、dead/unreachable 用真实代码/AST gate，职责漂移与 mock-self 另做 adversarial review，不得仅 grep 报告关键词。
- **FR-030**: main 已冻结 Tasks/Settings 管理能力归位：Operator inbox 继续属于 Tasks 管理区，但用户文案 MUST 为“待处理事项”，有待处理项时显著提示并可进入，0 项时折叠或隐藏，不占普通首屏，且不得与 F145 三类知识候选审批合并；Recovery summary、backup、chat export、update dry-run/apply、runtime restart/verify MUST 从 Tasks 移到 Settings → Advanced → 维护与恢复。backup、apply、restart 必须危险/强确认；export 必须说明范围；apply 必须展示并依赖先前 dry-run 摘要；restart 必须说明短暂不可用。现有 endpoint/handler 保留；F149 不新增 task cancel/resume。

### Key Entities

- **Generated Wire DTO**: 由固定 OpenAPI artifact 生成的网络结构，只在 infrastructure/adapter 边界使用。
- **Page View Model**: 面向用户的名称、摘要、状态、动作与 Advanced 分组；不保留认证或 transport 细节。
- **Page State**: loading、ready、empty、recoverable error、permission denied 等互斥状态及其恢复 command。
- **Application Port**: 必要时用于隔离真实多实现或测试 seam 的 structural contract；不是每页/每 endpoint 必建实体。
- **Advanced Detail**: 对管理员必要但不适合普通首屏的已净化字段，必须显式展开且不能驱动另一套业务状态；secret 不属于 Advanced 可见信息。

## Architectural Constraints

- `api/client` 是唯一底层 transport；现有 `platform/queries/actions` 承担 application orchestration；pure projection/state 不得 import React/Gateway/transport；page/WorkbenchContext 承担 UI/composition。
- page 可依赖 WorkbenchContext 与 pure projection，但不得 direct fetch 或直接读取 raw/generated DTO；上层不得复制下层状态机、权限判断和后端业务规则。
- 不为分层而增实体，不为每页/endpoint 创建 service/port/registry，不建第二状态容器或 transport。
- Tasks 只组合窄的“待处理事项”管理入口；Settings 只组合窄的维护与恢复 UI section，不把归位动作继续堆进 `SettingsPage`，也不复制 recovery/update 状态。
- F149 不借机迁移全仓 types/CSS，也不修改 F150/F151 生产逻辑。
- 缺失 OpenAPI schema 时必须先修契约并见红，不允许暂时恢复手写 DTO。

## Test Matrix at Design Gate

以下文件名是 Plan/Tasks 的目标落点；Tasks Gate 必须先创建目标测试并得到行为断言 RED，再把每行拆成 RED/GREEN/REFACTOR。`gate-prerequisites.md` 标为 missing 的 script/npm command 当前不是可执行 Gate，不得以“文件不存在”冒充 RED。

| 覆盖 | 层 | 目标文件 | 精确命令 / Review | 失败 oracle |
|---|---|---|---|---|
| FR-002/007 Approvals | L4 component | `src/domains/approval-center/ApprovalCenterPage.test.tsx` | `cd octoagent/frontend && npx vitest run src/domains/approval-center/ApprovalCenterPage.test.tsx` | 三类候选、409/404、empty/error/origin-403/a11y 任一不符 |
| FR-003/007/030 Tasks | L4 component | `src/pages/TaskList.test.tsx`、`src/pages/TaskDetail.test.tsx` | `cd octoagent/frontend && npx vitest run src/pages/TaskList.test.tsx src/pages/TaskDetail.test.tsx` | raw status、state、SSE projection、资源权限映射不符；非零“待处理事项”入口不显著、0 项仍占首屏、出现 operator/ops 文案、与 F145 候选合并，或 Recovery 仍在 Tasks |
| FR-004/007 Automation | L4 component | `src/pages/AutomationCenter.test.tsx` | `cd octoagent/frontend && npx vitest run src/pages/AutomationCenter.test.tsx` | pause/resume、status、empty/error/origin-403 不符 |
| FR-005/007/022/030 Settings | L4 component | `src/domains/settings/SettingsPage.test.tsx`、目标 co-located `MaintenanceRecoverySection.test.tsx` | `cd octoagent/frontend && npx vitest run src/domains/settings/SettingsPage.test.tsx src/domains/settings/MaintenanceRecoverySection.test.tsx` | 内部术语/secret、review/apply、permission/a11y 不符；维护入口未落 Advanced、危险确认/范围说明/dry-run-before-apply/restart 不可用说明缺失，或状态被复制进第二来源 |
| FR-006/007 Agents | L4 component | `src/pages/AgentCenter.test.tsx` | `cd octoagent/frontend && npx vitest run src/pages/AgentCenter.test.tsx` | direct transport 分叉、projection、permission/敏感字段不符 |
| FR-006/007 Memory | L4 component | `src/domains/memory/MemoryPage.test.tsx` | `cd octoagent/frontend && npx vitest run src/domains/memory/MemoryPage.test.tsx` | `/advanced` 死链、state、action、permission 不符 |
| FR-006/007 Files | L4 component | `src/pages/FilesCenter.test.tsx`、`src/pages/WorkspaceGitView.test.tsx` | `cd octoagent/frontend && npx vitest run src/pages/FilesCenter.test.tsx src/pages/WorkspaceGitView.test.tsx` | file/git state、rollback、path sensitivity 不符 |
| FR-006/007 Skills | L4 component | `src/pages/SkillCenter.test.tsx` | `cd octoagent/frontend && npx vitest run src/pages/SkillCenter.test.tsx` | install/delete、permission、Advanced/secret 分支不符 |
| FR-006/007 MCP | L4 component | `src/pages/McpProviderCenter.test.tsx`、`src/components/McpInstallWizard.test.tsx` | `cd octoagent/frontend && npx vitest run src/pages/McpProviderCenter.test.tsx src/components/McpInstallWizard.test.tsx` | save/install/poll error、permission、env/command policy 不符 |
| FR-007/016 shared state pattern | L4 component | 各 domain co-located tests 的参数化 fixture | `cd octoagent/frontend && npx vitest run src/domains src/pages src/components` | 任一适用 surface 缺 loading/empty/error/origin-403/a11y；不允许巨型 A/B 文件代替 |
| FR-010/025 wire → projection | L4 | 各 domain `*Projection.test.ts` + Gateway contract tests | 前端逐 domain Vitest；Python 使用 `contract-manifest.md` §9 | raw/metadata 未经 decoder 进入 domain/UI，或实际消费字段未强类型；合法 schema-as-data 不误报 |
| FR-010 codegen 可重复 | contract | 目标 `frontend/scripts/check-openapi-generated.mjs` | 当前 BLOCKED：script/npm alias 不存在；未来先按 `gate-prerequisites.md` 建 checker behavior RED task，再运行 `cd octoagent/frontend && npm run openapi:check` | 重生成漂移、出现 `any`，或命名 raw boundary 外出现 unknown/JsonValue |
| FR-010/027/028 REST/SSE/action | Gateway L4 + L3 | `contract-manifest.md` §9 目标 tests | 四条 Python 命令均从仓库根独立执行 | snapshot envelope、F149 action registry/validation、SSE final/typed consumed payload、secret scrub 任一不符 |
| FR-011/012/013/024/029 主路径 | architecture | 目标 AST checker + `code-smell-baseline.md` | 当前 baseline 用 `code-smell-baseline.md` 的真实 source scan；未来 checker 先建行为 RED task，再运行 `cd octoagent/frontend && node scripts/check-f149-boundaries.mjs` | direct fetch/token/header、raw DTO 穿透、反向 import、重复 state/transport 或 fallback 命中 |
| FR-009 token/CSS ratchet | Design Gate + architecture | Claude 20 行逐页验收表、theme/token provenance；目标 style checker | Design Gate 逐 frame 核对 F148/`--cp-*` provenance 与 Spotify absence；当前 baseline `wc -l octoagent/frontend/src/index.css`=4476；未来 checker 先建行为 RED task，再运行 `cd octoagent/frontend && node scripts/check-f149-style-ratchet.mjs` | 任一 frame 使用/继承 Spotify token/theme/component 或 provenance 缺失；`index.css` 增长、硬编码 palette 或非 `--cp-*` token 命中 |
| FR-001/008/016 全路由 390 Web 窄窗口 sweep | L1 | `e2e/f149-responsive-a11y.spec.ts` | `cd octoagent/frontend && npx playwright test e2e/f149-responsive-a11y.spec.ts` | 10 个 Web surface 任一 geometry overflow、可感知名称或键盘导航失败；不验证业务排列组合、移动认证或原生 iOS |
| FR-001/008/016 A 波关键旅程 | L1 | `e2e/f149-a-wave.spec.ts` | `cd octoagent/frontend && npx playwright test e2e/f149-a-wave.spec.ts` | A 波一条关键用户旅程的外部可见结果不符 |
| FR-006/008/016 B 波关键旅程 | L1 | `e2e/f149-b-wave.spec.ts` | `cd octoagent/frontend && npx playwright test e2e/f149-b-wave.spec.ts` | B 波一条含真实 modal/file chooser/focus return 的旅程不符 |
| FR-021 F150/F149 auth 边界 | L1 | `e2e/f149-auth-boundary.spec.ts` | `cd octoagent/frontend && npx playwright test e2e/f149-auth-boundary.spec.ts` | 401 未进入全局 Access，或 origin 403 错误离开 shell/进入页面登录态 |
| FR-014 RED/GREEN/REFACTOR 证据 | process gate | `tasks.md`、`evidence/tdd/*`、`tdd-evidence-policy.md` | Tasks Gate 逐行为解析三个 command；Review 逐 task 核对实际输出、exit code、UTC 时间、HEAD SHA、工作树状态与 oracle | 只写 RED 文字/grep 命中、没有实际稳定失败输出，或 GREEN/REFACTOR 未复用同一行为 oracle |
| FR-015 atomic relocation | architecture/process | `tasks.md`、未来边界 checker | checker prerequisite 完成后运行 `cd octoagent/frontend && node scripts/check-f149-boundaries.mjs`；此前不得声明 Gate PASS | 机械搬迁未声明、旧路径仍可 import 或前后契约不等价 |
| FR-016 分层测试分布 | review gate | `tasks.md`、逐 domain Vitest/L3/L1 清单 | Review 逐 task 核对层级与浏览器独有理由，不用关键词计数 | view-model/state/a11y 被转移到大量 L1，或 L1 无浏览器语义理由 |
| FR-017 command policy | process gate | `tasks.md`、`tdd-evidence-policy.md` | Review 只解析 task 的 command 字段并逐条复制执行；Python 命令必须逐字满足完整 worktree 模板 | 禁用命令出现在实际 command、命令不可运行或缺 PYTHONPATH/PYTHONNOUSERSITE/`--no-sync` |
| FR-018 FR 矩阵/coverage | quality gate | `tasks.md`、目标 lcov/checker | 当前 BLOCKED：`test:coverage` 与 frontend checker 不存在；先建 checker behavior RED task。完成后独立运行 `cd octoagent/frontend && npm run test:coverage`，再从仓库根运行 `node repo-scripts/check-frontend-changed-lines-coverage.mjs --lcov octoagent/frontend/coverage/lcov.info --base origin/master --min-percent 90` | 任一 FR/task 无层/文件/命令/oracle，或 authored executable TS/TSX <90%；tests/generated/`.d.ts`/type-only 排除 |
| FR-019/029 坏味道审计 | architecture + adversarial review | `code-smell-baseline.md`、未来 AST report | 机械扫描记录真实 source/line/metric；职责漂移与 mock-self 逐 test 对 oracle 做 adversarial review | 任一 smell 缺 source/line、baseline/current、判定、处置、owner，或必修未清零 |
| FR-020 范围保护 | scope gate | Git diff/status | `git status --short && git diff --name-only origin/master` | main 放行前出现 `.specify/features/149-web-pages-v2/` 之外的修改，或触达 F150/F151 生产代码 |
| FR-022 secret egress/mutation | Gateway L4 + L3 + frontend L4/L1 | `contract-manifest.md` §8/§9、Settings/MCP tests | Python secret commands +对应 Vitest；L1 只测真实 DOM/clipboard | config/MCP response、SSE、error、log、DOM、clipboard、evidence 任一含 sentinel；keep/replace/remove 或清空语义漂移 |
| FR-023 actual input | Design Gate | canonical 与 Claude-upload `actual-input-manifest.md`、metadata、22 PNG、`claude-design-upload-checklist.md` | `cmp`/SHA-256 校验 manifest 副本；Claude 项目文件列表逐项核对 24 个 Reference 文件，连接中断后重做 | manifest 副本不一致；任一文件不可见、缺失、size/metadata 不符，或只上传 ZIP |
| FR-023 Claude output | Design Gate | Claude `.dc.html`/导出截图、20 行逐页验收表 | actual input 完整且 main 产品决定已填后，核对 20 个唯一 frame + Shared-States/Advanced-Pattern + References + theme/token provenance + 普通用户术语 absence | 任一输入/页面 frame/辅助 frame/provenance/逐页 absence 证据缺失 |
| FR-026 action reachability | L4 contract | Agent/Memory component + registry tests | Agent restore command 可达；Memory 生产 import graph 不包含 dead `MemoryActionsSection` | restore 缺契约，或 dead 管理动作被误纳入 Memory |
| FR-030 Tasks/Settings placement | L4 component + Design Gate | `pending-product-decisions.md`、TaskList/Settings co-located tests | 8/8 决策已冻结；Claude frame 与未来组件测试按决定核对 | Tasks/Settings 任一能力落点、文案、显隐、确认/顺序规则漂移，或新增独立页面/service/registry/state |
| 真实 LLM/外部系统 | L2 live | 不新增 | 不适用 | F149 不包含只能由 live 判断的事实 |
| 全量类型/单测/复杂度 | gate | 现有配置 | `cd octoagent/frontend && npx tsc -b && npm test && npm run check:complexity` | 任一命令失败 |

## Success Criteria

- **SC-001**: canonical 与 Claude-upload manifest 的 SHA-256 一致，Claude 项目文件列表可见 manifest、metadata 与 22/22 当前参考图；随后 10 个目标 surface 均有唯一命名 Desktop/390 frame，并回存 Shared-States、Advanced-Pattern、References 与逐页 theme/token provenance。不足 24 个可见输入或 20/20 输出时 GATE_DESIGN 不通过。
- **SC-002**: F149 20 个 frame 均提供逐页普通用户术语 absence 证据；普通区域对 debug/JWT/AUD/JWKS/LiteLLM/operator/ops 及等价内部词的渲染测试为零命中，现有 2 pages 仅作反例且不得继承。
- **SC-003**: 除唯一底层 transport/F150 SSE URL builder 外，F149 触达闭包 direct fetch、token/header 拼接、手写 wire DTO、UI generated DTO import、页面 401 解释均为零。
- **SC-004**: `src/index.css` 相对基线 4,476 行零增长，新样式只复用 `--cp-*`。
- **SC-005**: 所有行为改动均有可复现 RED → GREEN → REFACTOR 证据；所有机械搬迁均有 atomic relocation 证据。
- **SC-006**: L4/L3/L1/L2 矩阵完整；L1 只有全路由 390 Web 窄窗口 sweep、A/B 关键旅程、Web auth 交界和真实浏览器语义，不包含移动认证或原生 iOS；authored executable TS/TSX changed-lines coverage ≥90%。
- **SC-007**: 坏味道清单无未分类项，本 Feature 必修项清零，ratchet 无回退，后续 Fix 有独立边界。
- **SC-008**: 未经 main 放行前，F149 不修改 F150/F151 或任何生产代码，不 commit、不 push。
- **SC-009**: secret sentinel 不出现在 config/MCP response、task SSE、error、log、generated fixture、adapter output、DOM、clipboard 或 TDD evidence；env read 只剩 name/configured/redacted summary，mutation 通过 keep/replace/remove 且 input 清空，path/command 满足 sensitivity policy。
- **SC-010**: 401/session 过期/登出只有 F150 全局路径；F149 十个 surface 的 origin 403 有 L4，且一条交界 L1 通过。
- **SC-011**: F149 新增/触达的 contract/projection 中，命名 raw/metadata/schema-as-data boundary 外没有 `unknown`/`JsonValue`，且无 `any`；实际消费字段均经 decoder/type guard，开放 JSON 未被伪闭合；非 F149 历史代码不纳入本次清扫。
- **SC-012**: action registry 对 F149 可达 handler 无缺项，dispatch/validation/discovery/artifact 同源；Task SSE contract 只在 Gateway/web adapter，core 无对应模型。
- **SC-013**: Tasks 普通首屏不再承载 Recovery；非零“待处理事项”可进入且 0 项不占首屏。Settings Advanced 的“维护与恢复”完整覆盖 summary/backup/export/update/restart/verify，满足确认、范围、dry-run 顺序和短暂不可用说明，并由窄组件复用现有状态源。

## Dependencies & Blockers

- Worktree 已按 `actual-input-manifest.md` 回存 22/22 deterministic current screenshots（明确非真实后端），并回存 Claude Design 最终 `.dc.html`：20 个逐页 frame、Shared-States、Advanced-Pattern、References、20 行逐 frame 表与 10×7 状态矩阵均经 main 独立复审通过。视觉永久约束为：Claude Design 最开始方案的层级、留白、卡片节奏、信息密度、视觉张力和排版是 Web/iOS 共同基线；现有 Web 只作 current-state evidence，F148 只约束 `--cp-*`、主题/组件边界与功能合同，不要求外观复刻旧 Web。除明确功能合同、可用性、无障碍或 iOS 原生平台规范外不得偏离原稿；偏离必须记录原因与影响，禁止以实现方便为理由。iOS 保留同一视觉语言但用 SwiftUI/Apple 原生交互语义，不复制 Web 组件结构，且不属于 F149 实现范围。
- Tasks/Settings 8 项产品位置已由 main 全部决定并写入 `pending-product-decisions.md`；设计不得偏离“待处理事项”与 Settings Advanced“维护与恢复”落点。
- F150/F151 尚是生产实现硬前置。
- control-plane raw snapshot projection、Gateway SSE、action 同源 registry 与 F149-owned write-only secret mutation contract 尚待生产阶段按 TDD 补齐；逐项缺口见 `contract-manifest.md`，不再依赖独立 security Fix。
- Blueprint auth/contract/sensitivity 待同步文案见 `blueprint-sync.md`；当前范围限制下未修改权威 Blueprint。
- Design Gate 已通过且只单次放行 Plan/Tasks；F150/F151 契约稳定与 main 明确放行仍是生产实现硬前置。
