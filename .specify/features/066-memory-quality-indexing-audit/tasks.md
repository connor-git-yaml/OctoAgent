# Tasks: Memory 提取质量、索引利用与审计优化

**Input**: Design documents from `.specify/features/066-memory-quality-indexing-audit/`
**Prerequisites**: spec.md, plan.md, data-model.md, contracts/agent-tools.md, contracts/control-plane-actions.md, research.md
**Feature Branch**: `claude/competent-pike`

**Tests**: spec.md 要求关键路径有 unit test 和 integration test。每个 Phase 包含对应测试任务。

**Organization**: 任务按 User Story 组织，支持增量交付。Phase 1 为共享基础设施，Phase 2 为阻塞性前置依赖，Phase 3+ 按 User Story 优先级排列。

---

## Phase 1: Setup (共享基础设施)

**Purpose**: 枚举扩展与新增数据模型——所有 User Story 的共同前置依赖

- [x] T001 在 `octoagent/packages/memory/src/octoagent/memory/enums.py` 中为 `SorStatus` 新增 `ARCHIVED = "archived"` 枚举值
- [x] T002 [P] 在 `octoagent/packages/memory/src/octoagent/memory/enums.py` 中为 `MemoryPartition` 新增 `SOLUTION = "solution"` 枚举值
- [x] T003 [P] 在 `octoagent/packages/memory/src/octoagent/memory/enums.py` 中为 `WriteAction` 新增 `MERGE = "merge"` 枚举值
- [x] T004 新建 `octoagent/packages/memory/src/octoagent/memory/models/browse.py`，定义 `BrowseItem`、`BrowseGroup`、`BrowseResult` 三个 Pydantic 模型（按 data-model.md 3.3 节规格）
- [x] T005 在 `octoagent/packages/memory/src/octoagent/memory/models/__init__.py` 中导出 `BrowseItem`、`BrowseGroup`、`BrowseResult`
- [x] T006 在 `octoagent/packages/memory/tests/test_models.py` 中新增枚举值测试：验证 `SorStatus.ARCHIVED`、`MemoryPartition.SOLUTION`、`WriteAction.MERGE` 存在且值正确
- [x] T007 [P] 在 `octoagent/packages/memory/tests/test_models.py` 中新增 `BrowseItem`/`BrowseGroup`/`BrowseResult` 模型序列化/反序列化测试

**Checkpoint**: 枚举和数据模型就绪，后续 Phase 可并行启动

---

## Phase 2: Foundational (阻塞性前置依赖)

**Purpose**: 存储层方法扩展 + Protocol 更新 + 索引优化——Agent 工具层和审计层的共同依赖

- [x] T008 在 `octoagent/packages/memory/src/octoagent/memory/store/protocols.py` 中新增 `browse_sor()` 方法签名（按 data-model.md 3.2 节规格）
- [x] T009 在 `octoagent/packages/memory/src/octoagent/memory/store/protocols.py` 中扩展 `search_sor()` 方法签名，新增可选参数 `partition`、`status`、`derived_type`、`updated_after`、`updated_before`
- [x] T010 在 `octoagent/packages/memory/src/octoagent/memory/store/memory_store.py` 中实现 `browse_sor()` 方法——基于 SQLite GROUP BY 查询，支持 `group_by`（partition/scope/prefix）、`prefix`、`partition`、`status` 筛选、`offset`/`limit` 分页
- [x] T011 在 `octoagent/packages/memory/src/octoagent/memory/store/memory_store.py` 中扩展 `search_sor()` 方法，新增可选参数过滤逻辑（partition/status/derived_type/updated_after/updated_before），未提供时行为不变
- [x] T012 在 `octoagent/packages/memory/src/octoagent/memory/store/memory_store.py` 中新增 `update_sor_status()` 方法——将指定 `memory_id` 的 SoR status 更新为目标值（用于归档/恢复）
- [x] T013 在 `octoagent/packages/memory/src/octoagent/memory/store/sqlite_init.py`（或 `backends/sqlite_backend.py`）中确认/新增 SQLite 索引：`idx_memory_sor_scope_partition_status(scope_id, partition, status)` 和 `idx_memory_sor_scope_subject_key(scope_id, subject_key)`
- [x] T014 在 `octoagent/packages/memory/tests/test_memory_store.py` 中新增 `browse_sor` 测试：空结果、按 partition 分组、按 prefix 筛选、分页（has_more/total_count）
- [x] T015 [P] 在 `octoagent/packages/memory/tests/test_memory_store.py` 中新增 `search_sor` 扩展参数测试：按 partition/status/derived_type/updated_after/updated_before 筛选，未提供时向后兼容
- [x] T016 [P] 在 `octoagent/packages/memory/tests/test_memory_store.py` 中新增 `update_sor_status` 测试：current->archived、archived->current、版本号不变

**Checkpoint**: 存储层就绪，Agent 工具层和审计层可并行开发

---

## Phase 3: User Story 1 — Agent 浏览记忆目录 (Priority: P1)

**Goal**: Agent 能通过 `memory.browse` 工具按 subject_key 前缀、partition、scope 等维度浏览记忆目录，获取分组统计和概览

**Independent Test**: Agent 对话中输入"你还记得我之前提过的技术偏好吗"，Agent 调用 `memory.browse` 列出匹配条目并给出准确回答

### Implementation

- [x] T017 [US1] 在 `octoagent/packages/provider/src/octoagent/provider/dx/memory_console_service.py` 中新增 `browse_memory()` 方法，调用 `SqliteMemoryStore.browse_sor()` 并格式化返回
- [x] T018 [US1] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` 中注册 `memory_browse` 工具函数——参数 schema 遵循 contracts/agent-tools.md 第 1 节，调用 `MemoryConsoleService.browse_memory()`，副作用等级 `none`
- [x] T019 [US1] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` 中验证 `memory_browse` 工具的 `tool_contract` 装饰器 schema 与函数签名一致（FR-023）
- [x] T020 [US1] 在 `octoagent/packages/memory/tests/test_memory_store.py` 中新增集成测试：browse 返回分组包含 items、count、latest_updated_at，空 scope 返回空列表不报错

**Checkpoint**: User Story 1 完成——Agent 可通过 `memory.browse` 浏览记忆目录

---

## Phase 4: User Story 2 — 用户通过 UI 编辑记忆内容 (Priority: P1)

**Goal**: 用户在 Memory UI 中编辑 SoR 记忆的 content 和/或 subject_key，保留完整审计链

**Independent Test**: Memory UI 中选择一条 SoR，点击"编辑"，修改内容后保存，验证新版本生效、旧版本 superseded、可查看版本历史

### Backend

- [x] T021 [US2] 在 `octoagent/packages/provider/src/octoagent/provider/dx/control_plane_models.py` 中新增 `MemorySorEditRequest` Pydantic 模型（字段：scope_id, subject_key, content, new_subject_key, expected_version, edit_summary）
- [x] T022 [US2] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 的 `_build_action_definitions()` 中注册 `memory.sor.edit` action 定义（category=memory, risk_hint=medium）
- [x] T023 [US2] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 中实现 `_handle_memory_sor_edit()` handler——流程：查找当前 SoR -> 乐观锁检查 expected_version -> Vault 敏感分区授权检查 -> propose_write(action=UPDATE, source="user_edit") -> validate_proposal -> commit_memory -> 返回新版本
- [x] T024 [US2] 在 handler 中实现乐观锁：当前版本与 expected_version 不匹配时，返回 `VERSION_CONFLICT` 错误码 + 提示"期望版本 X，当前版本 Y，请刷新后重试"
- [x] T025 [US2] 在 handler 中实现 Vault 层记忆额外授权检查：若记忆 partition 属于 `SENSITIVE_PARTITIONS`（HEALTH/FINANCE），需额外授权确认（FR-025）
- [x] T026 [US2] 在 handler 中生成审计事件——记录操作人、时间、变更内容摘要（FR-009, FR-024）

### Frontend

- [x] T027 [P] [US2] 在 `octoagent/frontend/src/domains/memory/MemoryDetailModal.tsx` 中新增"编辑"操作按钮（FR-010）
- [x] T028 [US2] 新建 `octoagent/frontend/src/domains/memory/MemoryEditDialog.tsx` 编辑对话框组件——inline 编辑 content 和 subject_key 字段，包含"保存"/"取消"按钮
- [x] T029 [US2] 在 `MemoryEditDialog.tsx` 中实现保存逻辑：调用 `memory.sor.edit` action，携带 expected_version 参数
- [x] T030 [US2] 在 `MemoryEditDialog.tsx` 中实现乐观锁冲突处理：收到 `VERSION_CONFLICT` 错误时显示"版本冲突，请刷新后重试"提示（FR-006 边缘场景）

### Tests

- [x] T031 [US2] 在 `octoagent/apps/gateway/tests/` 下新增 `test_control_plane_memory_edit.py`——测试编辑流程：正常编辑、版本冲突、Vault 授权拒绝、审计事件生成

**Checkpoint**: User Story 2 完成——用户可在 UI 中编辑 SoR 记忆

---

## Phase 5: User Story 3 — 用户通过 UI 归档/恢复记忆 (Priority: P1)

**Goal**: 用户可归档不需要的记忆（从 recall 排除），可在"已归档"视图中恢复

**Independent Test**: 归档一条记忆 -> 默认列表消失 -> Agent recall 不命中 -> "已归档"视图可见 -> 恢复后回到正常列表

### Backend

- [x] T032 [US3] 在 `octoagent/packages/provider/src/octoagent/provider/dx/control_plane_models.py` 中新增 `MemorySorArchiveRequest` 和 `MemorySorRestoreRequest` Pydantic 模型
- [x] T033 [US3] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 的 `_build_action_definitions()` 中注册 `memory.sor.archive`（risk_hint=medium）和 `memory.sor.restore`（risk_hint=low）action 定义
- [x] T034 [US3] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 中实现 `_handle_memory_sor_archive()` handler——流程：查找 SoR(status=current) -> 乐观锁检查 -> Vault 敏感分区授权检查 -> update_sor_status(archived) -> 生成审计事件
- [x] T035 [US3] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 中实现 `_handle_memory_sor_restore()` handler——流程：查找 SoR(status=archived) -> 检查同 subject_key 下是否已有 current 记录（有则提示冲突） -> update_sor_status(current) -> 生成审计事件
- [x] T036 [US3] 确认 `search_sor()` 和 recall 流程默认排除 `status=archived` 的记忆（与排除 superseded/deleted 一致），在 `octoagent/packages/memory/src/octoagent/memory/store/memory_store.py` 中验证/修改默认过滤逻辑

### Frontend

- [x] T037 [P] [US3] 在 `octoagent/frontend/src/domains/memory/MemoryDetailModal.tsx` 中新增"归档"操作按钮，点击后弹出二次确认对话框（FR-011）
- [x] T038 [US3] 在 `octoagent/frontend/src/domains/memory/MemoryFiltersSection.tsx` 中新增 status 筛选选项：current（默认）/ archived / all（FR-012）
- [x] T039 [US3] 在 `octoagent/frontend/src/domains/memory/MemoryResultsSection.tsx` 中支持"已归档"筛选标签展示，归档记忆显示视觉区分样式
- [x] T040 [US3] 在已归档视图中，为每条归档记忆显示"恢复"按钮，调用 `memory.sor.restore` action

### Backend 测试

- [x] T041 [US3] 在 `octoagent/apps/gateway/tests/` 下新增 `test_control_plane_memory_archive.py`——测试：归档流程、恢复流程、归档后 recall 排除、恢复后 recall 命中、版本冲突、同 subject_key 冲突

### Frontend 测试

- [x] T042 [P] [US3] 在 `octoagent/frontend/src/domains/memory/MemoryPage.test.tsx` 中新增归档/恢复操作 UI 测试

**Checkpoint**: User Story 3 完成——用户可归档/恢复记忆，审计闭环完整

---

## Phase 6: User Story 4 — Consolidation 全生活域覆盖 (Priority: P1)

**Goal**: Consolidation 提取覆盖用户生活各维度（人物关系、健康、消费、兴趣等），信息主体正确归因

**Independent Test**: 含生活场景的对话后触发 consolidate，提取的 SoR 覆盖 5+ 不同维度，人物归因正确

### Implementation

- [x] T043 [US4] 在 `octoagent/packages/provider/src/octoagent/provider/dx/consolidation_service.py` 中扩展 `_CONSOLIDATE_SYSTEM_PROMPT`，新增全生活域提取维度指令——至少覆盖：人物关系、家庭事件、情感状态、健康信息、消费习惯、技术选型、项目决策、生活习惯、兴趣爱好、日程安排（FR-014）
- [x] T044 [US4] 在 prompt 中添加主体归因规则：当 A 提到关于 B 的信息时，subject_key 信息主体指向 B 而非 A（FR-015）
- [x] T045 [US4] 在 prompt 中添加安全规则：当对话不包含某维度信息时，不强制生成空 SoR

### Tests

- [x] T046 [US4] 在 `octoagent/packages/provider/tests/` 下新增 `test_consolidation_quality.py`——测试：全生活域提取（含 5+ 维度场景）、主体归因正确性、空维度不强制生成

**Checkpoint**: User Story 4 完成——Consolidation 覆盖全生活域

---

## Phase 7: User Story 7 — 扩展 memory.search 结构化筛选 (Priority: P2)

**Goal**: Agent 搜索记忆时可按 derived_type、时间范围、SoR 状态等结构化维度筛选

**Independent Test**: Agent 调用 `memory.search(derived_type="profile", status="current")` 只返回符合条件的记忆

### Implementation

- [x] T047 [US7] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` 中扩展 `memory_search` 工具函数参数——新增 `derived_type`、`status`、`updated_after`、`updated_before` 可选参数（按 contracts/agent-tools.md 第 2 节）
- [x] T048 [US7] 确保 `memory_search` 工具的 `tool_contract` 装饰器 schema 更新后与函数签名一致（FR-023），所有新参数默认空字符串，向后兼容（FR-005）
- [x] T049 [US7] 在 `octoagent/packages/provider/src/octoagent/provider/dx/memory_console_service.py` 中扩展对应的搜索方法，将新参数透传到 `SqliteMemoryStore.search_sor()`

### Tests

- [x] T050 [US7] 在 `octoagent/packages/memory/tests/test_memory_store.py` 中新增集成测试：组合使用 derived_type + status + 时间范围筛选，验证结果正确且向后兼容

**Checkpoint**: User Story 7 完成——memory.search 支持结构化筛选

---

## Phase 8: User Story 6 — Consolidation 策略丰富化 (Priority: P2)

**Goal**: Consolidation 除 ADD/UPDATE 外，支持 MERGE（多条合并为一条）和 REPLACE（语义矛盾替换）策略

**Independent Test**: 触发 consolidate 后检查输出 action 列表，MERGE 和 REPLACE 在合适场景被正确选择和执行

### Implementation

- [x] T051 [US6] 在 `octoagent/packages/provider/src/octoagent/provider/dx/consolidation_service.py` 的 prompt 中扩展 LLM 输出 JSON 格式——新增 `action` 字段支持 `"add"` / `"update"` / `"merge"` / `"replace"` 值（FR-016, FR-017）
- [x] T052 [US6] 在 `octoagent/packages/provider/src/octoagent/provider/dx/consolidation_service.py` 中实现 MERGE 执行逻辑：解析 `action="merge"` + `merge_source_ids` -> 批量 supersede 源 SoR -> 创建综合 SoR（evidence_refs 指向所有原始）-> 走 propose-validate-commit 流程
- [x] T053 [US6] 在 `octoagent/packages/provider/src/octoagent/provider/dx/consolidation_service.py` 中实现 REPLACE 执行逻辑：解析 `action="replace"` -> 复用 UPDATE 流程 + `metadata.reason="replace"`
- [x] T054 [US6] 在 `octoagent/packages/memory/src/octoagent/memory/service.py` 中扩展 `commit_memory()` 对 MERGE action 的处理——批量 supersede `metadata.merge_source_ids` 中的所有 SoR
- [x] T055 [US6] 实现回退安全：LLM 未输出 action 字段时默认为 `add`/`update`（现有行为），不破坏向后兼容

### Tests

- [x] T056 [US6] 在 `octoagent/packages/provider/tests/` 下新增 `test_consolidation_strategies.py`——测试：MERGE 策略（多条 -> 一条综合）、REPLACE 策略（语义矛盾替换）、被合并/替换记忆保留 superseded 状态（FR-018）、回退安全

**Checkpoint**: User Story 6 完成——Consolidation 支持 MERGE/REPLACE 策略

---

## Phase 9: User Story 5 — Solution 记忆提取与自动匹配 (Priority: P2)

**Goal**: Agent 积累的成功方案被单独提取和存储（partition=solution），遇到类似错误时自动搜索匹配

**Independent Test**: Agent 解决 Docker 构建错误后 Solution 被提取；之后遇到类似错误时系统自动注入匹配 Solution

### Implementation

- [x] T057 [US5] 在 `octoagent/packages/provider/src/octoagent/provider/dx/consolidation_service.py` 中新增 Phase 1.5：Solution 检测阶段——从 committed_sors 中识别 problem-solution 模式
- [x] T058 [US5] 编写 Solution 检测 prompt：指导 LLM 从已提交的 SoR 中识别问题解决方案模式，输出结构化的 problem + solution + context
- [x] T059 [US5] 实现 Solution SoR 写入：`partition=SOLUTION`，content 格式按 data-model.md 2.3 节约定（`问题:` / `解决方案:` / `上下文:`），走 propose-validate-commit 流程
- [x] T060 [US5] 在 `octoagent/packages/provider/src/octoagent/provider/dx/memory_runtime_service.py`（或对应的 recall 逻辑所在文件）中实现自动匹配：Agent 遇到工具执行错误时，自动搜索 `partition=solution` 的匹配记忆（FR-020）
- [x] T061 [US5] 实现匹配阈值：相似度 < 0.7 不注入（FR-021），匹配结果通过 recall frame 扩展注入 Agent 下一轮上下文
- [x] T062 [US5] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 中注册 `memory.browse` action 定义（category=memory, risk_hint=none）并实现 `_handle_memory_browse()` handler，调用 `MemoryConsoleService.browse_memory()` 供前端使用

### Tests

- [x] T063 [US5] 在 `octoagent/packages/provider/tests/` 下新增 `test_solution_extraction.py`——测试：Solution 提取（problem-solution 识别）、自动匹配触发、阈值过滤（< 0.7 不注入）

**Checkpoint**: User Story 5 完成——Solution 记忆提取与自动匹配就位

---

## Phase 10: User Story 8 — Profile 信息密度提升 (Priority: P3)

**Goal**: Profile 每个维度支持多段详细描述，信息密度显著高于"1-3 句话"的旧模式

**Independent Test**: 触发 profile_generate 后，至少 3 个维度包含多段描述

### Implementation

- [x] T064 [US8] 在 `octoagent/packages/provider/src/octoagent/provider/dx/profile_generator_service.py` 中修改 `_PROFILE_SYSTEM_PROMPT`——将"1-3 句话"限制改为"多段详细描述"，允许每个维度输出多段落覆盖子维度（FR-022）
- [x] T065 [US8] 更新 prompt 规则：允许每个维度随时间积累逐步丰富，新版本信息密度不低于旧版本
- [x] T066 [US8] 确认输出 JSON 格式不变（`string | null`），保持下游消费方兼容

### Tests

- [x] T067 [US8] 在 `octoagent/packages/provider/tests/` 下新增 `test_profile_density.py`——测试：生成的 Profile 信息密度验证（多维度多段落 vs 旧模式单句），JSON 格式兼容

**Checkpoint**: User Story 8 完成——Profile 信息密度显著提升

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: 跨 Story 的集成验证、文档、清理

- [x] T068 [P] 在 `octoagent/packages/memory/src/octoagent/memory/store/memory_store.py` 中确认 recall 流程（`recall_sor` 或等效方法）默认排除 `status=archived`——全局验证所有读取路径的过滤一致性
- [x] T069 [P] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` 中确认 `memory_browse` 和 `memory_search` 工具的降级行为：LanceDB 不可用时 browse 正常工作（纯 SQLite），search 降级到结构化查询（FR-026）
- [x] T070 运行全量现有测试套件，确认新增枚举/参数/方法不导致任何回归
- [x] T071 [P] 更新 `.specify/features/066-memory-quality-indexing-audit/quickstart.md`——补充 browse/edit/archive 功能的使用说明
- [x] T072 代码审查：检查所有新增文件的类型注解完整性、docstring、structlog 日志记录
- [x] T073 [P] 在前端确认所有新增组件（MemoryEditDialog、归档确认、恢复按钮、状态筛选）的可访问性和样式一致性

---

## FR 覆盖映射表

| FR | 描述 | 覆盖任务 |
|----|------|----------|
| FR-001 | `memory.browse` 工具 | T004, T010, T017, T018 |
| FR-002 | browse `group_by` 参数 | T010, T014 |
| FR-003 | browse 分页 | T010, T014 |
| FR-004 | search 新增可选参数 | T009, T011, T047, T048, T049 |
| FR-005 | 新增参数向后兼容 | T011, T015, T048, T055 |
| FR-006 | SoR 编辑能力 | T021, T022, T023, T024, T028, T029, T030 |
| FR-007 | SoR 归档能力 | T001, T034, T036, T037 |
| FR-008 | SoR 恢复能力 | T035, T040 |
| FR-009 | 审计日志记录 | T026, T034, T035 |
| FR-010 | UI "编辑"/"归档"按钮 | T027, T037 |
| FR-011 | 归档二次确认 | T037 |
| FR-012 | "已归档"筛选视图 | T038, T039, T040 |
| FR-013 | 自动写入保持全自动 | T055（回退安全保证现有自动行为不受干扰） |
| FR-014 | Consolidation 全生活域 | T043 |
| FR-015 | 信息主体归因 | T044 |
| FR-016 | MERGE 策略 | T003, T051, T052, T054 |
| FR-017 | REPLACE 策略 | T053 |
| FR-018 | MERGE/REPLACE 审计链 | T052, T054, T056 |
| FR-019 | Solution 记忆提取 | T002, T057, T058, T059 |
| FR-020 | Solution 自动匹配 | T060, T061 |
| FR-021 | Solution 匹配阈值 | T061 |
| FR-022 | Profile 信息密度 | T064, T065, T066 |
| FR-023 | 工具注册规范 | T019, T048 |
| FR-024 | 审计日志 | T026, T034, T035 |
| FR-025 | Vault 层额外授权 | T025 |
| FR-026 | 降级行为 | T069 |

**FR 覆盖率**: 26/26 = 100%

---

## Dependencies & Execution Order

### Phase 依赖关系

```
Phase 1 (Setup: 枚举+模型)
  |
  v
Phase 2 (Foundational: 存储层)
  |
  +---> Phase 3 (US1: browse 工具) ----+
  |                                     |
  +---> Phase 4 (US2: 编辑) ----+      |
  |                              |      |
  +---> Phase 5 (US3: 归档) ----+      |
  |                              |      |
  +---> Phase 6 (US4: 全生活域) -+----> Phase 11 (Polish)
  |                              |
  +---> Phase 7 (US7: search 扩展)
  |
  +---> Phase 8 (US6: MERGE/REPLACE) -- 依赖 Phase 6 的 prompt 基础
  |     |
  |     v
  +---> Phase 9 (US5: Solution) -------- 依赖 Phase 6 (全生活域 prompt) + Phase 8 (MERGE 能力)
  |
  +---> Phase 10 (US8: Profile) -------- 独立，仅依赖 Phase 2
```

### User Story 间依赖

| Story | 依赖 | 说明 |
|-------|------|------|
| US1 (browse) | Phase 2 only | 独立 |
| US2 (编辑) | Phase 2 only | 独立 |
| US3 (归档) | Phase 2 only | 独立，与 US2 共享 control_plane.py 文件但不同 handler |
| US4 (全生活域) | Phase 2 only | 独立 |
| US5 (Solution) | US4 + US6 | Solution 检测在全生活域 prompt 之后；MERGE 能力用于 solution 写入 |
| US6 (MERGE/REPLACE) | US4 | prompt 扩展依赖全生活域 prompt 基础 |
| US7 (search 扩展) | Phase 2 only | 独立 |
| US8 (Profile) | Phase 2 only | 独立 |

### Story 内部并行机会

- **Phase 1**: T001/T002/T003 可并行（不同枚举类，同一文件但不同区块）；T004/T006/T007 可并行
- **Phase 2**: T014/T015/T016 可并行（同文件不同测试方法）
- **Phase 4**: T027(前端) 与 T021~T026(后端) 可并行
- **Phase 5**: T037/T038(前端) 与 T032~T036(后端) 可并行
- **Phase 3/7/10**: 各自独立，可与 Phase 4/5 并行

### 推荐实现策略

**Incremental Delivery（推荐）**:

1. Phase 1 + Phase 2 -> 基础设施就绪
2. Phase 3 (US1: browse) + Phase 4 (US2: 编辑) + Phase 5 (US3: 归档) 并行 -> **MVP 交付点**（核心审计 + 浏览能力）
3. Phase 6 (US4: 全生活域) + Phase 7 (US7: search 扩展) 并行
4. Phase 8 (US6: MERGE/REPLACE) -> Phase 9 (US5: Solution) 顺序
5. Phase 10 (US8: Profile)
6. Phase 11 (Polish)

**MVP 范围**: US1 + US2 + US3（Phase 1-5），交付"浏览 + 编辑 + 归档"核心审计闭环。

---

## Notes

- [P] 标记 = 不同文件、无依赖，可并行执行
- [USN] 标记 = 所属 User Story，用于追踪
- 实际文件路径以 `octoagent/` 为 monorepo 根目录前缀
- 所有 control_plane action handler 实现在同一文件 `control_plane.py` 中，US2/US3 的 handler 开发需协调避免冲突，但函数级别互不依赖
- Consolidation prompt 变更（US4/US6/US5）在同一文件 `consolidation_service.py` 中，建议按 Phase 6 -> Phase 8 -> Phase 9 严格顺序执行
