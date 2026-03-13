# Tasks: Feature 047 Frontend Workbench Architecture Renewal

**Input**: `.specify/features/047-frontend-workbench-architecture/`  
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/frontend-workbench-renewal.md`, `verification/acceptance-matrix.md`

## Phase 1: Setup & Freeze (Shared Infrastructure)

**Purpose**: 冻结目标目录、数据层和设计系统边界，为后续域迁移提供稳定底座

- [x] T001 [P] 建立 `octoagent/frontend/src/domains/`、`octoagent/frontend/src/platform/`、`octoagent/frontend/src/ui/`、`octoagent/frontend/src/styles/` 目录骨架
- [x] T002 [P] 在 `octoagent/frontend/src/platform/queries/` 设计 shared resource query registry，并整理现有 snapshot/resource route 映射
- [x] T003 [P] 在 `octoagent/frontend/src/platform/actions/` 设计 shared action mutation registry，定义 action 成功后的 invalidation 规则
- [x] T004 [P] 在 `octoagent/frontend/src/styles/` 建立 `tokens.css / primitives.css / shell.css` 初始分层，并从 `index.css` 抽离公共 token
- [x] T005 定义前端复杂度治理规则，明确页面文件、共享 hook、共享 CSS 的体量阈值与 review 要求

---

## Phase 2: User Story 1 - 日常工作台与高级诊断彻底分层 (Priority: P1) 🎯 MVP

**Goal**: 让 `Home / Chat / Work / Agents / Settings / Memory` 成为稳定日常路径，`Advanced` 只承担高级诊断

**Independent Test**: 不进入 `/advanced`，用户仍可完成 readiness、provider、agent、chat、work 主路径；进入 `/advanced` 后可以看到深度诊断且不影响日常页面

### Tests for User Story 1

- [x] T006 [P] [US1] 在 `octoagent/frontend/src/pages/Home.test.tsx` 或新 domain 测试中覆盖首页 readiness 与下一步动作主路径
- [x] T007 [P] [US1] 在 `octoagent/frontend/src/pages/ControlPlane.test.tsx` 覆盖 `Advanced` 作为高级诊断面存在，但不承担日常引导职责

### Implementation for User Story 1

- [x] T008 [US1] 在 `octoagent/frontend/src/components/shell/WorkbenchLayout.tsx` 收口 daily navigation 的描述与入口层级
- [x] T009 [US1] 将 `octoagent/frontend/src/pages/AdvancedControlPlane.tsx` 改造为 shared data layer 上的高级视图入口
- [x] T010 [US1] 在 `octoagent/frontend/src/domains/home/` 重构首页的 readiness / next action / warning 结构
- [x] T011 [US1] 在 `octoagent/frontend/src/domains/work/` 与 `octoagent/frontend/src/domains/chat/` 统一状态 badge、warning callout 与 action bar pattern
- [x] T012 [US1] 移除日常页面中不必要的 debug / 内部治理话术，将帮助信息降级为 help/callout

**Checkpoint**: 日常路径和高级诊断面的产品边界已经稳定

---

## Phase 3: User Story 2 - 所有页面共享统一数据层 (Priority: P1)

**Goal**: 消除 `Workbench` 与 `Advanced` 的双轨 snapshot/resource/action orchestration

**Independent Test**: 修改一个 canonical resource 的消费逻辑时，只需更新 shared query/action 层与对应 domain，不再同步修两套逻辑

### Tests for User Story 2

- [x] T013 [P] [US2] 为 shared query registry 编写测试，覆盖 resource route -> query key -> fetch 行为
- [x] T014 [P] [US2] 为 shared action mutation 层编写测试，覆盖 action 成功后的资源失效与回退逻辑

### Implementation for User Story 2

- [x] T015 [US2] 在 `octoagent/frontend/src/hooks/useWorkbenchSnapshot.ts` 拆分出 shared data facade，并迁入 `octoagent/frontend/src/platform/queries/`
- [x] T016 [US2] 在 `octoagent/frontend/src/api/client.ts` 收口 control-plane resource fetch 与 action execute 的基础客户端职责
- [x] T017 [US2] 重构 `octoagent/frontend/src/pages/ControlPlane.tsx`，移除其独立 snapshot/resource/action orchestration，改为复用 shared layer
- [x] T018 [US2] 将 `octoagent/frontend/src/workbench/utils.ts` 中与资源映射相关的逻辑迁入 platform query/action registry
- [x] T019 [US2] 为 degraded resource、full snapshot fallback、resource ref invalidation 建立统一策略

**Checkpoint**: 前端数据编排单一化，不再有双轨工作台

---

## Phase 4: User Story 3 - 页面按域模块持续演化 (Priority: P1)

**Goal**: 将 `Agents / Settings / Memory / Advanced` 拆成可维护的域模块、section、inspector 与局部 hook

**Independent Test**: 新增一个 section 时，只改对应 domain module，不需要继续向巨型页面主文件追加数百行

### Tests for User Story 3

- [X] T020 [P] [US3] 为 `agents` domain 的关键 section/pattern 增加组件级测试
- [x] T021 [P] [US3] 为 `settings` domain 的 catalog/editor/inspector pattern 增加组件级测试
- [x] T022 [P] [US3] 为 `memory` domain 的筛选与详情 inspector 增加组件级测试

### Implementation for User Story 3

- [X] T023 [US3] 将 `octoagent/frontend/src/pages/AgentCenter.tsx` 拆分为 `octoagent/frontend/src/domains/agents/` 下的 catalog、detail、runtime-inspector、hooks、view-models
- [x] T024 [US3] 将 `octoagent/frontend/src/pages/SettingsCenter.tsx` 拆分为 `octoagent/frontend/src/domains/settings/` 下的 overview、providers、security、save-review 等模块
- [x] T025 [US3] 将 `octoagent/frontend/src/pages/MemoryCenter.tsx` 拆分为 `octoagent/frontend/src/domains/memory/` 下的 filters、results、subject-inspector、actions 模块
- [x] T026 [US3] 将 `octoagent/frontend/src/pages/ControlPlane.tsx` 拆分为 `octoagent/frontend/src/domains/advanced/` 下的 diagnostics、resources、audit、lineage 模块
- [x] T027 [US3] 将 `octoagent/frontend/src/index.css` 中的共享 pattern 抽离到 `octoagent/frontend/src/styles/` 与 `octoagent/frontend/src/ui/` 层
- [x] T028 [US3] 在 `octoagent/frontend/src/App.tsx` 上启用路由级 lazy loading，降低大页面首屏打包压力

**Checkpoint**: 主要复杂页面具备域边界和共享 pattern，不再以单页巨石演化

---

## Phase 5: User Story 4 - 前端质量治理进入长期模式 (Priority: P2)

**Goal**: 建立 contract sync、黄金路径回归和复杂度约束，避免前端在后续 Feature 中再次失控

**Independent Test**: 新增 Feature 时，如果页面膨胀、契约漂移或主路径回归，能在本地或 CI 中被发现

### Tests for User Story 4

- [x] T029 [P] [US4] 为 contract sync 产物增加快照或 schema 一致性测试
- [x] T030 [P] [US4] 为首页、设置、Agent、聊天、work 主路径建立黄金路径测试

### Implementation for User Story 4

- [x] T031 [US4] 在 `octoagent/frontend/src/types/` 或 `octoagent/frontend/src/platform/contracts/` 建立后端 canonical contract 的集中同步来源
- [x] T032 [US4] 补充 golden-path 测试基线，至少覆盖首页 readiness、provider 配置、Agent 检视、聊天主路径、work 列表
- [x] T033 [US4] 增加前端复杂度检查脚本或 lint 约束，限制主页面/共享 hook/共享样式持续单体膨胀
- [x] T034 [US4] 更新 `octoagent/frontend/README` 或相关开发文档，明确 domain / platform / ui / styles 的职责与扩展方式

**Checkpoint**: 前端不只完成了一次重构，而且具备持续治理机制

---

## Phase 6: Polish & Verification

- [x] T035 [P] 运行 `./node_modules/.bin/tsc -b`，确保分层重构后的类型闭环通过
- [x] T036 [P] 运行 `npx vitest run` 覆盖受影响 domain 的关键测试
- [x] T037 [P] 运行工作台黄金路径 smoke，验证 `/ /chat /agents /settings /memory /advanced`
- [x] T038 清理 legacy helper、重复 mapping 和过渡期兼容代码，确保双轨逻辑完全收口

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1**: 无依赖，先冻结边界
- **Phase 2**: 依赖 Phase 1，统一数据层后才能安全迁移页面
- **Phase 3**: 依赖 Phase 2，确保所有页面拆分建立在同一数据主链上
- **Phase 4**: 依赖 Phase 3，可与部分迁移收尾交错推进
- **Phase 6**: 在所有目标故事完成后执行

### Parallel Opportunities

- T001-T004 可并行
- T006-T007 可并行
- T013-T014 可并行
- T020-T022 可并行
- `agents / settings / memory / advanced` 的域拆分可按模块分批并行推进

## Implementation Strategy

### MVP First

1. 冻结架构与 IA
2. 统一数据层
3. 收口 `Advanced`
4. 拆 `Agents` 和 `Settings`
5. 验证 daily workbench 主路径

### Incremental Delivery

1. 先交付 shared data layer + advanced 收口
2. 再交付 agents/settings 域拆分
3. 再交付 memory 与 UI foundation 收口
4. 最后补 contract sync 和治理脚本

## Notes

- 047 的目标是“重整前端底座”，不是追求一次性大换栈
- 若实施中发现 contract sync 需要后端导出脚本支持，可在同一 Feature 内纳入最小必要后端辅助改动
- `project-context` 软链缺失需在实现前补修，但不阻断本 Feature 规划
