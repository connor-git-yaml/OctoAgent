# Tasks - Feature 050 Agent Management Simplification

## Phase 1: 结构冻结与对象适配

- [x] T001 冻结用户语言与页面 IA：主 Agent、已创建 Agent、模板、能力绑定
- [x] T002 在 `octoagent/frontend/src/domains/agents/` 设计 `MainAgent / ProjectAgent / BuiltinAgentTemplate / AgentEditorDraft` 视图模型
- [x] T003 审查现有 `worker_profiles` 与 `projects.default_agent_profile_id` 的映射边界，明确 builtin 默认 Agent 的迁移策略

## Phase 2: User Story 1 - 当前项目 Agent 列表（P1）

- [x] T004 [US1] 重构 `octoagent/frontend/src/pages/AgentCenter.tsx` 默认入口，使其落到当前项目 Agent 列表而不是模板工作台
- [x] T005 [US1] 新增当前项目主 Agent 卡片与普通 Agent 列表组件
- [x] T006 [US1] 为主 Agent / 普通 Agent 卡片补充 `编辑 / 删除 / 状态` 动作与 plain-language 摘要
- [x] T007 [US1] 增加首页列表与空状态测试

## Phase 3: User Story 2 - 模板型创建流（P1）

- [x] T008 [US2] 将内置 3 个 Agent 从默认列表中移除，只保留在“新建 Agent”流程中
- [x] T009 [US2] 新增“从模板创建 / 空白创建”流程组件
- [x] T010 [US2] 将当前“复制模板/另存模板”的技术话术改为面向普通用户的创建语言
- [x] T011 [US2] 增加模板创建成功、取消和空白创建测试

## Phase 4: User Story 3 - 结构化编辑页（P1）

- [x] T012 [US3] 重构编辑页，把 `name / persona / project / model / tools / capabilities` 提升为默认编辑区
- [x] T013 [US3] 将 `LLM` 改为单选/下拉，将 `默认工具组` 改为多选，将 `固定工具` 改为搜索多选
- [x] T014 [US3] 将 `runtime kinds / policy refs / tags / metadata` 收纳到高级设置折叠区
- [x] T015 [US3] 把当前 capability 绑定区重命名并重组为普通用户可理解的“能力绑定”
- [x] T016 [US3] 增加结构化控件与高级区折叠测试

## Phase 5: User Story 4 - 项目归属与默认 Agent 兼容（P2）

- [x] T017 [US4] 处理项目切换后的主 Agent / 普通 Agent 列表刷新
- [x] T018 [US4] 实现 builtin 默认 Agent 的迁移或建立主 Agent 引导
- [x] T019 [US4] 为主 Agent 删除限制、普通 Agent 删除确认、默认绑定兼容补测试

## Phase 6: Polish & Verification

- [x] T020 统一 `Agents` 页面空状态、确认文案和错误提示，确保都用普通用户语言
- [x] T021 运行前端相关测试，覆盖 `/agents` 与 `/chat` 默认 Agent 解析主链
- [x] T022 如本轮实现暴露出 `worker_profiles` 适配层过重，记录后续 canonical `agent-management` 资源跟进建议
