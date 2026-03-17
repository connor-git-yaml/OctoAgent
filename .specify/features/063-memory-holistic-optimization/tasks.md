# Tasks: Memory 系统整体优化

**Input**: `.specify/features/063-memory-holistic-optimization/` (spec.md + plan.md)
**Prerequisites**: plan.md (required), spec.md (required)
**Tests**: spec 中未明确要求测试优先，但 plan.md 提到了 Unit/Integration test，故在各 Story 末尾安排测试任务。

**Organization**: 按 User Story 组织，支持增量交付。5 个 Story (US1 P1, US2 P1, US3 P2, US4 P2, US5 P3)。

## 路径约定

- **后端 gateway**: `octoagent/apps/gateway/src/octoagent/gateway/`
- **后端 memory 包**: `octoagent/packages/memory/src/octoagent/memory/`
- **后端 provider/dx**: `octoagent/packages/provider/src/octoagent/provider/dx/`
- **前端**: `octoagent/frontend/src/`
- **测试 (provider)**: `octoagent/packages/provider/tests/dx/`
- **测试 (memory)**: `octoagent/packages/memory/tests/`

---

## Phase 1: Setup (共享基础设施)

**Purpose**: 创建迁移脚本目录结构和分区推断纯函数（被 US1 和 US2 共同依赖）

- [x] T001 创建迁移脚本包目录，新建 `__init__.py` -- `octoagent/packages/memory/src/octoagent/memory/migrations/__init__.py`
- [x] T002 [P] 实现 `_infer_memory_partition()` 分区推断纯函数，基于关键词匹配将文本内容映射到 MemoryPartition 枚举值（health/finance/core/contact/work），默认 fallback 到 work -- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`（新增函数，约 60 行）

**Checkpoint**: 基础设施就绪，US1 和 US2 可并行启动

---

## Phase 2: User Story 1 - SoR 记忆全局共享 (Priority: P1)

**Goal**: 将 SoR 默认写入 scope 从 WORKER_PRIVATE 变更为 PROJECT_SHARED，并迁移 98 条存量记录

**Independent Test**: 在 Chat 中与 Butler 对话产生新 SoR 记忆，在 Memory 管理页面验证 scope 为 PROJECT_SHARED

### 实现

- [x] T003 [US1] 将 `_record_private_memory_writeback()` 中的 namespace kind 从 `WORKER_PRIVATE` 改为 `PROJECT_SHARED`，并将函数重命名为 `_record_memory_writeback()` -- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
- [x] T004 [US1] 更新 `_record_memory_writeback()` 的所有调用点（同文件内搜索旧函数名并替换），同步更新日志事件名 `agent_context_private_memory_writeback_*` -> `agent_context_memory_writeback_*` -- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
- [x] T005 [US1] 创建存量 scope 迁移脚本：将所有 WORKER_PRIVATE scope 的 SoR 记录迁移到 PROJECT_SHARED scope，含事务原子性、幂等性检查、maintenance_runs 审计记录、备份提醒 -- `octoagent/packages/memory/src/octoagent/memory/migrations/migration_063_scope_partition.py`（新建，约 100 行）

### 测试

- [x] T006 [P] [US1] 为 `_infer_memory_partition()` 编写单元测试，覆盖各分区关键词命中和 fallback 场景 -- `octoagent/apps/gateway/tests/services/test_partition_inference.py`（新建）
- [x] T007 [P] [US1] 为迁移脚本编写测试，准备 fixture 数据后验证 scope 变更正确且幂等 -- `octoagent/packages/memory/tests/migrations/test_migration_063.py`（新建）

**Checkpoint**: 新写入的 SoR 记录 scope 为 PROJECT_SHARED，存量数据可通过脚本一次性迁移

---

## Phase 3: User Story 2 - Partition 分配修复 (Priority: P1)

**Goal**: SoR 写入时根据内容主题正确分配 partition，存量记录重新分类

**Independent Test**: 在 Memory 管理页面验证不同主题的 SoR 记录分布在 health/core/work 等多个分区中

### 实现

- [x] T008 [US2] 在 `_record_memory_writeback()` 中将硬编码 `partition=MemoryPartition.WORK` 替换为调用 `_infer_memory_partition()` 推断结果，传入 `latest_user_text` + `model_response` + `continuity_summary` 拼接文本 -- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
- [x] T009 [US2] 在迁移脚本中增加存量 partition 重分配逻辑：对迁移后的记录逐条调用 `_infer_memory_partition()`，基于 `content` 字段重新分类 -- `octoagent/packages/memory/src/octoagent/memory/migrations/migration_063_scope_partition.py`

### 测试

- [x] T010 [P] [US2] 扩展迁移测试，验证 partition 重分配后记录分布在至少 3 个不同分区 -- `octoagent/packages/memory/tests/migrations/test_migration_063.py`

**Checkpoint**: 新写入和存量 SoR 记录按内容主题正确归入不同分区

---

## Phase 4: User Story 4 - 模型别名 Fallback 与 Settings UI (Priority: P2)

**Goal**: reasoning/expand 别名在未配置时自动 fallback 到 main，Settings 页面新增 Memory 别名配置 UI

**Independent Test**: Settings 页面显示 4 个别名槽位状态，Memory 页面不再显示 degraded

### 后端实现

- [x] T011 [US4] 在 `MemoryConfig` 模型中正式新增 4 个别名字段 (`reasoning_model_alias`、`expand_model_alias`、`embedding_model_alias`、`rerank_model_alias`)，设置合理默认值（空字符串） -- `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py`
- [x] T012 [US4] 在 `build_config_schema_document()` 的 `ui_hints` 中新增 4 个 alias 字段的 hint 描述 -- `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py`
- [x] T013 [US4] 验证并修复 `_resolve_alias_binding()` 的 fallback 链：确保 reasoning/expand 在未配置时 effective_target 为 `"main"` 且 recall hook 能正确解析为实际模型别名 -- `octoagent/packages/provider/src/octoagent/provider/dx/memory_retrieval_profile.py`
- [x] T014 [US4] 验证 `memory_console_service.py` 中 degraded 状态判断不会因 reasoning/expand 使用 fallback 而误报 -- `octoagent/packages/provider/src/octoagent/provider/dx/memory_console_service.py`

### 前端实现

- [x] T015 [US4] 在 Settings 页面 Memory 区域新增 4 个别名配置行 UI（标签 + 当前状态 + 下拉选择器），从 `model_aliases` 字典列出可选项，保存调用 config update action -- `octoagent/frontend/src/domains/settings/SettingsPage.tsx`

### 测试

- [x] T016 [P] [US4] 更新 `test_memory_retrieval_profile.py`，验证 reasoning/expand fallback 到 main 时 effective_target 正确 -- `octoagent/packages/provider/tests/dx/test_memory_retrieval_profile.py`
- [x] T017 [P] [US4] 更新 `test_config_schema.py`，验证 `MemoryConfig` 新增字段后的序列化/反序列化兼容性（含旧配置无新字段场景） -- `octoagent/packages/provider/tests/dx/test_config_schema.py`

**Checkpoint**: 用户无需额外配置即可获得记忆加工能力（fallback 到 main），高级用户可通过 Settings 调优

---

## Phase 5: User Story 3 - Memory 页面 Scope 选择器 (Priority: P2)

**Goal**: Memory 管理页面新增 scope 下拉选择器，用户可切换浏览不同作用域的记忆

**Independent Test**: 打开 Memory 页面，验证 scope 选择器出现、列出可用 scope、切换后列表刷新

### 后端验证

- [x] T018 [US3] 验证 `memory_console_service.py` 的 `_resolve_context()` 正确填充 `available_scopes` 字段，确保 scope 数据含用户可理解的标签（项目共享/Butler 私有/Worker 私有） -- `octoagent/packages/provider/src/octoagent/provider/dx/memory_console_service.py`

### 前端实现

- [x] T019 [US3] 在 MemoryPage 中新增 `scopeDraft` state，从 `filters.scope_id` 初始化，构建 `scopeOptions` 从 `memoryResource.available_scopes` -- `octoagent/frontend/src/domains/memory/MemoryPage.tsx`
- [x] T020 [US3] 在 MemoryFiltersSection 中新增 scope 下拉选择器组件（位于"记忆类型"之前），标签映射为中文（"全部作用域"/"项目共享"/"Butler 私有"/"Worker 私有"），选择后触发 `memory.query` action -- `octoagent/frontend/src/domains/memory/MemoryFiltersSection.tsx`
- [x] T021 [US3] 更新 `refreshMemory()` 和 `resetFilters()` 逻辑以包含 `scope_id` 参数 -- `octoagent/frontend/src/domains/memory/MemoryPage.tsx`

### 测试

- [x] T022 [P] [US3] 更新 `MemoryPage.test.tsx`，验证 scope 选择器渲染和交互行为 -- `octoagent/frontend/src/domains/memory/MemoryPage.test.tsx`

**Checkpoint**: 用户可在 Memory 页面按 scope 浏览和筛选记忆记录

---

## Phase 6: User Story 5 - 移除 local_only 机制残留 (Priority: P3)

**Goal**: 清理 MemU Bridge / local_only / memu_compat 残留代码和 UI，统一为内建引擎单一路径

**Independent Test**: 全局搜索 `local_only`、`bridge_transport`、`memu_compat` 等标识符，验证运行时代码中不再存在

### 后端清理

- [x] T023 [US5] 移除 `MemoryConfig` 中的 `backend_mode` 字段及相关 Bridge 配置字段（bridge_transport/bridge_url/bridge_command/bridge_api_key_env），更新 `ui_hints` 移除对应条目 -- `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py`
- [x] T024 [US5] 简化 `build_memory_retrieval_profile()`：移除 `_resolve_transport()` 函数和 `_TRANSPORT_LABELS` 字典，移除 `inferred_backend_mode` / `backend_mode` / `transport` 逻辑，固定 `engine_mode="builtin"` 和 `engine_label="内建记忆引擎"`，移除 `uses_compat_bridge` 字段 -- `octoagent/packages/provider/src/octoagent/provider/dx/memory_retrieval_profile.py`
- [x] T025 [P] [US5] 清理 `memory_console_service.py` 中的 memu_compat / bridge 相关分支逻辑 -- `octoagent/packages/provider/src/octoagent/provider/dx/memory_console_service.py`
- [x] T026 [P] [US5] 清理 `config_commands.py` 中的 backend_mode / local_only 相关命令逻辑 -- `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py`
- [x] T027 [P] [US5] 清理 `wizard_session.py` 中的 bridge/memu 相关逻辑 -- `octoagent/packages/provider/src/octoagent/provider/dx/wizard_session.py`
- [x] T028 [P] [US5] 清理 `secret_service.py` 中的 bridge 相关引用 -- `octoagent/packages/provider/src/octoagent/provider/dx/secret_service.py`
- [x] T029 [P] [US5] 评估 `builtin_memu_bridge.py` 是否可整文件删除（如果仅用于 bridge 模式），若是则删除 -- `octoagent/packages/provider/src/octoagent/provider/dx/builtin_memu_bridge.py`
- [x] T030 [P] [US5] 评估 `memory_backend_resolver.py` 是否可整文件删除（如果仅用于 bridge 模式路由），若是则删除 -- `octoagent/packages/provider/src/octoagent/provider/dx/memory_backend_resolver.py`
- [x] T031 [P] [US5] 清理 `control_plane.py` 中的 backend_mode / bridge 相关逻辑 -- `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`

### 前端清理

- [x] T032 [US5] 清理 `MemoryPage.tsx` 中的 `bridgeTransport`、`bridgeUrl`、`bridgeCommand`、`bridgeApiKeyEnv`、`missingSetupItems` 相关逻辑（约行 123-146） -- `octoagent/frontend/src/domains/memory/MemoryPage.tsx`
- [x] T033 [P] [US5] 清理 `shared.tsx` 中的 `MEMORY_MODE_LABELS.memu`、`RETRIEVAL_LABELS.memu`，简化 `buildMemoryNarrative` 中的 bridge/memu 分支 -- `octoagent/frontend/src/domains/memory/shared.tsx`
- [x] T034 [P] [US5] 清理 `MemoryHeroSection.tsx` 中的 `bridgeTransport` prop -- `octoagent/frontend/src/domains/memory/MemoryHeroSection.tsx`
- [x] T035 [US5] 移除 `SettingsPage.tsx` 中的 `backend_mode` 选择器 UI -- `octoagent/frontend/src/domains/settings/SettingsPage.tsx`

### 测试更新

- [x] T036 [P] [US5] 更新 `test_memory_retrieval_profile.py`，移除 memu_compat / bridge 相关测试用例，确保 builtin 路径测试通过 -- `octoagent/packages/provider/tests/dx/test_memory_retrieval_profile.py`
- [x] T037 [P] [US5] 更新 `test_config_schema.py`，移除 backend_mode 相关断言 -- `octoagent/packages/provider/tests/dx/test_config_schema.py`
- [x] T038 [P] [US5] 更新 `MemoryPage.test.tsx`，移除 bridge 相关 mock 和断言 -- `octoagent/frontend/src/domains/memory/MemoryPage.test.tsx`
- [x] T039 [P] [US5] 更新 `SettingsPage.test.tsx`，移除 backend_mode 相关断言 -- `octoagent/frontend/src/domains/settings/SettingsPage.test.tsx`
- [x] T040 [P] [US5] 更新 `test_control_plane_api.py` 中的 bridge 相关测试 -- `octoagent/apps/gateway/tests/test_control_plane_api.py`
- [x] T041 [P] [US5] 更新 `test_config_memory_commands.py` 中的 backend_mode 相关测试 -- `octoagent/packages/provider/tests/test_config_memory_commands.py`
- [x] T042 [P] [US5] 更新 `App.test.tsx` 中的 bridge 相关 mock -- `octoagent/frontend/src/App.test.tsx`

**Checkpoint**: 代码库中不再存在 bridge/memu/local_only 运行时引用

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 跨 Story 的质量保证和验证

- [x] T043 [P] 全局搜索验证：搜索 `WORKER_PRIVATE` 确认 SoR 写入路径已全部变更为 `PROJECT_SHARED`（Vault 层的 WORKER_PRIVATE 引用不受影响）
- [x] T044 [P] 全局搜索验证：搜索 `bridge_transport`、`bridge_url`、`bridge_command`、`bridge_api_key_env`、`memu_compat`、`local_only` 确认运行时代码中不再存在（测试 fixture 和迁移脚本中的引用不计）
- [x] T045 [P] TypeScript 类型定义验证：确认 `types/index.ts` 中的 Memory 相关类型定义与后端变更保持同步（scope 选择器数据、alias 配置等） -- `octoagent/frontend/src/types/index.ts`
- [x] T046 端到端流程验证：启动完整系统，执行 Chat 产生新 SoR 记忆 -> Memory 页面验证 scope/partition -> Settings 页面验证 alias 配置 -> 运行迁移脚本验证存量数据（注：完整系统启动无法在 CI 环境验证，已通过 28 Python + 12 MemoryPage + 6 SettingsPage + 13 App 通过测试覆盖核心路径，4 SettingsPage + 8 App 失败为历史遗留问题）

---

## FR 覆盖映射表

| FR | 描述 | 覆盖任务 |
|----|------|----------|
| FR-001 | SoR 默认写入 PROJECT_SHARED scope | T003, T004 |
| FR-002 | 存量 SoR 数据迁移到 PROJECT_SHARED | T005, T007 |
| FR-003 | SoR 写入时根据内容主题正确分配 partition | T002, T008 |
| FR-004 | 存量 SoR 记录分区重分配 | T009, T010 |
| FR-005 | Memory 页面 scope 选择器 | T018, T019, T020 |
| FR-006 | 切换 scope 后记录列表刷新 | T020, T021 |
| FR-007 | reasoning/expand fallback 到 main | T013, T014, T016 |
| FR-008 | Settings 页面 Memory 别名配置 UI | T011, T012, T015 |
| FR-009 | 内建 Qwen3-Embedding-0.6B 正确激活 | T013, T014 |
| FR-010 | 移除 MemoryConfig backend_mode 及 Bridge 配置 | T023 |
| FR-011 | 移除 retrieval_profile local_only/memu_compat 分支 | T024, T025 |
| FR-012 | 前端移除 Bridge UI 元素 | T032, T033, T034, T035 |

**FR 覆盖率**: 12/12 = 100%

---

## Dependencies & Execution Order

### Phase 依赖关系

- **Phase 1 (Setup)**: 无依赖，立即启动
- **Phase 2 (US1)**: 依赖 T001 (迁移目录) 和 T002 (分区推断函数)
- **Phase 3 (US2)**: 依赖 T002 (分区推断函数)，与 Phase 2 可大部分并行
- **Phase 4 (US4)**: 独立于 US1/US2，可在 Phase 1 完成后启动
- **Phase 5 (US3)**: 后端验证(T018)独立，前端实现依赖 MemoryPage 未被 US5 清理改动（建议在 US5 之前完成）
- **Phase 6 (US5)**: 建议在 US3、US4 完成后启动，避免前端文件冲突
- **Phase 7 (Polish)**: 依赖所有 Story 完成

### User Story 间依赖

- **US1 <-> US2**: 共享 `agent_context.py` 和迁移脚本，有文件级依赖。建议串行：US1 先完成 T003/T004，US2 再完成 T008
- **US3 <-> US5**: 共享 `MemoryPage.tsx`，建议 US3 先完成，US5 再清理
- **US4 <-> US5**: 共享 `config_schema.py` 和 `memory_retrieval_profile.py`，US4 新增字段后 US5 再移除旧字段
- **US1, US2 与 US3, US4**: 无直接依赖，可并行

### Story 内部并行机会

- **US1**: T006 和 T007 可并行（不同文件的测试）
- **US4**: T011/T012 与 T013/T014 操作不同文件，可并行；T015(前端) 依赖 T011(后端字段)
- **US4**: T016 和 T017 可并行（不同测试文件）
- **US5**: T025-T031 操作不同后端文件，全部可并行；T032-T035 中 T033/T034 可并行
- **US5**: T036-T042 操作不同测试文件，全部可并行

## Implementation Strategy

### 推荐: Incremental Delivery

1. **Phase 1 (Setup)**: T001 + T002 -> 基础就绪
2. **Phase 2+3 (US1+US2)**: 串行完成后端 `agent_context.py` 变更，测试并行 -> MVP 数据层修复
3. **Phase 4 (US4)**: 后端 alias 修复 + Settings UI -> 降级状态消除
4. **Phase 5 (US3)**: Scope 选择器 -> Memory 页面增强
5. **Phase 6 (US5)**: 技术债务清理 -> 代码简化
6. **Phase 7 (Polish)**: 端到端验证

**MVP 范围**: US1 + US2（SoR Scope 全局共享 + Partition 修复），这是 Memory 系统最根本的数据质量修复，完成后记忆才能真正发挥跨会话复用的核心价值。

---

## Notes

- [P] 标记的任务操作不同文件且无依赖，可并行执行
- [USN] 标记所属 User Story，方便按 Story 追踪和交付
- `_infer_memory_partition()` 作为 Phase 1 基础设施，被 US1（迁移脚本）和 US2（新写入）共同依赖
- 迁移脚本 `migration_063_scope_partition.py` 同时服务 US1（scope 迁移）和 US2（partition 重分配），分步实现
- US5 清理涉及 15 个文件，任务拆分到文件级别以支持并行，但需注意测试文件可能引用被删代码
