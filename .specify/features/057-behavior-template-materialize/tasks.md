# Tasks: 行为文件模板落盘与 Agent 自主更新

**Input**: Design documents from `.specify/features/057-behavior-template-materialize/`
**Prerequisites**: plan.md (required), spec.md (required), contracts/behavior-tools.md

**Tests**: spec.md 的 Testing Strategy 要求完整单元测试覆盖，因此每个 Phase 包含对应的测试任务。

**Organization**: 任务按 User Story 优先级排序，支持增量交付。US1/US2/US3 为 P1 同优先级，US4 为 P2。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行执行（不同文件，无依赖）
- **[Story]**: 所属 User Story（US1, US2, US3, US4）
- 每个任务包含确切的文件路径

---

## Phase 1: Setup（共享基础设施）

**Purpose**: 提取共享辅助函数到 behavior_workspace.py，供后续所有 Phase 复用

- [x] T001 在 `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` 中新增 `validate_behavior_file_path(project_root: Path, file_path: str) -> Path` 函数 — 校验行为文件路径安全性（拒绝绝对路径、`..` traversal、behavior 目录边界外），返回 resolved 绝对路径，异常时 raise ValueError。从 control_plane.py `_handle_behavior_read_file` / `_handle_behavior_write_file` 中提取通用逻辑。
- [x] T002 在 `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` 中新增 `read_behavior_file_content(project_root, file_path, *, agent_slug, project_slug) -> tuple[str, bool, int]` 函数 — 读取行为文件内容，文件不存在时 fallback 到 `_default_content_for_file()` 返回默认模板，同时返回 `(content, exists_on_disk, budget_chars)`。
- [x] T003 在 `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` 中新增 `check_behavior_file_budget(file_path: str, content: str) -> dict` 函数 — 从 file_path 末段提取 file_id，在 BEHAVIOR_FILE_BUDGETS 中查找预算上限，返回 `{"within_budget": bool, "current_chars": int, "budget_chars": int, "exceeded_by": int}`。未知 file_id 默认不限制（within_budget=True）。
- [x] T004 在 `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` 模块顶部引入 `structlog`，添加 `log = structlog.get_logger(__name__)`（如尚未存在），为后续 materialize 日志和降级处理做准备。

---

## Phase 2: User Story 1 — 首次启动时行为文件自动写入磁盘 (Priority: P1)

**Goal**: 全新环境启动后，9 个默认行为文件模板自动写入磁盘对应目录，已存在的文件不被覆盖。

**Independent Test**: 在空 data 目录启动系统，检查 `behavior/system/`、`behavior/agents/butler/`、`projects/default/behavior/` 下是否自动生成了 9 个 `.md` 文件，内容与 `_default_content_for_file()` 返回值一致。

### Tests for User Story 1

> **NOTE: 先写测试，确认失败后再实现**

- [ ] T005 [P] [US1] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_materialize_creates_all_files` — 空 tmp 目录调用 `ensure_filesystem_skeleton()` 后断言 9 个行为文件全部存在，内容与 `_default_content_for_file()` 返回值一致。
- [ ] T006 [P] [US1] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_materialize_skips_existing` — 预填 USER.md 自定义内容后调用 `ensure_filesystem_skeleton()`，断言 USER.md 内容未被覆盖。
- [ ] T007 [P] [US1] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_materialize_skips_empty_file` — 预创建空 USER.md（0 字节）后调用 `ensure_filesystem_skeleton()`，断言文件仍为空。
- [ ] T008 [P] [US1] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_materialize_io_error_does_not_block` — Mock `Path.write_text` 抛 OSError，断言 `ensure_filesystem_skeleton()` 不抛异常，仅记录 warning 日志。
- [ ] T009 [P] [US1] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_materialize_preserves_readme` — 断言 materialize 逻辑不影响现有的 `instructions/README.md` 生成。

### Implementation for User Story 1

- [x] T010 [US1] 在 `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` 的 `ensure_filesystem_skeleton()` 函数中，在 `return created` 之前新增行为文件模板 materialize 循环：遍历 `ALL_BEHAVIOR_FILE_IDS`，对每个 file_id 调用 `_default_behavior_file_path()` 获取目标路径，若 `target.exists()` 则 skip，否则通过 `_default_content_for_file()` 获取内容并写入，失败时 `log.warning()` 降级（FR-001, FR-002, FR-003, FR-004, FR-017）。
- [x] T011 [US1] 确认 `ensure_filesystem_skeleton()` 中新增的 `_default_behavior_file_path()` 调用使用正确的 scope 映射 — SHARED 文件 -> `behavior/system/`，AGENT_PRIVATE 文件 -> `behavior/agents/{agent_slug}/`，PROJECT_SHARED 文件 -> `projects/{project_slug}/behavior/`。在 `behavior_workspace.py` 中新增辅助函数 `_template_scope_for_file(file_id: str) -> BehaviorWorkspaceScope` 返回每个 file_id 对应的 scope。

**Checkpoint**: 运行 `pytest test_behavior_materialize.py` 全部通过；9 个行为文件在空目录启动时自动创建。

---

## Phase 3: User Story 2 — Agent 通过 LLM 工具读写行为文件 (Priority: P1)

**Goal**: Agent 在对话中可调用 `behavior.read_file` / `behavior.write_file` 工具读写行为文件，遵循路径安全、字符预算、review_mode 治理规则。

**Independent Test**: 在对话中让 Agent 调用 `behavior.read_file` 读取 USER.md，确认返回内容和 budget 信息；调用 `behavior.write_file` 提出修改，确认 proposal 模式和 confirmed 写入均正常。

### Tests for User Story 2

> **NOTE: 先写测试，确认失败后再实现**

- [ ] T012 [P] [US2] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_validate_path_rejects_traversal` — `../etc/passwd` 路径 -> ValueError。
- [ ] T013 [P] [US2] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_validate_path_rejects_absolute` — `/etc/passwd` 路径 -> ValueError。
- [ ] T014 [P] [US2] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_validate_path_rejects_outside_behavior` — `src/main.py` 路径（在 project_root 内但不在 behavior 目录）-> ValueError。
- [ ] T015 [P] [US2] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_budget_check_within` — 内容字符数 < budget -> `within_budget=True`。
- [ ] T016 [P] [US2] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_budget_check_exceeded` — 内容字符数 > budget -> `within_budget=False, exceeded_by > 0`。
- [ ] T017 [P] [US2] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_read_file_fallback_default` — 文件不存在时 `read_behavior_file_content()` 返回默认模板内容 + `exists=False`。

### Implementation for User Story 2

- [x] T018 [US2] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` 的 `_register_builtin_tools()` 方法中注册 `behavior.read_file` 工具 — 使用 `@tool_contract` 装饰器，`side_effect_level=SideEffectLevel.NONE`，`tool_profile=ToolProfile.MINIMAL`，`tool_group="behavior"`。实现：调用 `validate_behavior_file_path()` + `read_behavior_file_content()`，返回 JSON 字符串（含 file_path, content, exists, budget_chars, current_chars）。参照 contracts/behavior-tools.md 的 schema。
- [x] T019 [US2] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` 的 `_register_builtin_tools()` 方法中注册 `behavior.write_file` 工具 — 使用 `@tool_contract` 装饰器，`side_effect_level=SideEffectLevel.REVERSIBLE`，`tool_profile=ToolProfile.STANDARD`，`tool_group="behavior"`。实现：(1) `validate_behavior_file_path()` (2) `check_behavior_file_budget()` 超出则拒绝 (3) 查找 file_id 对应的 review_mode (4) 若 `REVIEW_REQUIRED` 且 `confirmed=False` 则返回 proposal JSON (5) 若 confirmed 或 review_mode 非 REVIEW_REQUIRED 则写入磁盘 (6) 记录 TOOL_CALL 事件（FR-018）。参照 contracts/behavior-tools.md 的 schema。
- [x] T020 [US2] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` 顶部新增 import：从 `octoagent.core.behavior_workspace` 引入 `validate_behavior_file_path`、`read_behavior_file_content`、`check_behavior_file_budget`、`BEHAVIOR_FILE_BUDGETS`、`_build_file_templates`（或等效函数）用于查找 review_mode。
- [x] T021 [US2] [可选] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 的 `_handle_behavior_read_file` 和 `_handle_behavior_write_file` 中，将路径校验逻辑替换为调用共享的 `validate_behavior_file_path()`，减少重复代码。`_handle_behavior_write_file` 增加 `check_behavior_file_budget()` 预算检查。

**Checkpoint**: 两个 LLM 工具注册成功，Agent 可在对话中调用；路径校验和预算检查测试全部通过。

---

## Phase 4: User Story 3 — System Prompt 引导 Agent 理解行为文件用途 (Priority: P1)

**Goal**: Agent 的 system prompt 中包含 BehaviorToolGuide block——文件清单表、工具参数说明、修改时机建议、存储边界提示——使 Agent 自主判断何时更新行为文件。

**Independent Test**: 检查 Agent 的 system prompt 输出，确认包含 9 个 file_id 的清单表、`behavior.read_file` / `behavior.write_file` 参数说明、存储边界提示。

### Tests for User Story 3

> **NOTE: 先写测试，确认失败后再实现**

- [ ] T022 [P] [US3] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_tool_guide_contains_all_files` — 调用 `build_behavior_tool_guide_block()` 后断言输出包含全部 9 个 file_id（AGENTS.md, USER.md, PROJECT.md, KNOWLEDGE.md, TOOLS.md, BOOTSTRAP.md, SOUL.md, IDENTITY.md, HEARTBEAT.md）。
- [ ] T023 [P] [US3] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_tool_guide_bootstrap_pending` — `is_bootstrap_pending=True` 时，输出包含 "BOOTSTRAP 存储路由" 或等效标题 + 各存储路由指引。
- [ ] T024 [P] [US3] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_tool_guide_contains_storage_boundary` — 输出包含 "MemoryService"/"memory" 和 "SecretService"/"secret" 等存储边界关键词。

### Implementation for User Story 3

- [x] T025 [US3] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/butler_behavior.py` 中新增 `build_behavior_tool_guide_block(*, workspace: BehaviorWorkspace, is_bootstrap_pending: bool = False) -> str` 函数 — 从 `workspace.files` 动态生成结构化指南，包含：(a) file_id | 用途 | 修改时机 | path_hint 表格 (b) `behavior.read_file` / `behavior.write_file` 参数说明 (c) review_mode 行为说明 (d) 存储边界提示（复用 `StorageBoundaryHints` 模型内容）。若 `is_bootstrap_pending=True`，追加 BOOTSTRAP 存储路由 block（FR-012, FR-013, FR-014）。
- [x] T026 [US3] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` 中修改行为文件 system block 注入位置（约 L3795 附近）— 将 `render_behavior_system_block()` 的调用拆分为：先构建 `BehaviorWorkspace` 实例，再分别传给 `render_behavior_system_block()` 和 `build_behavior_tool_guide_block()`，在行为文件内容 block 之后追加 BehaviorToolGuide block。需要从 butler_behavior 导入 `build_behavior_tool_guide_block`。
- [x] T027 [US3] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` 中，为 `build_behavior_tool_guide_block()` 的 `is_bootstrap_pending` 参数传入正确的值 — 检查当前 bootstrap session 的 status 是否为 `BootstrapSessionStatus.PENDING`。

**Checkpoint**: Agent system prompt 中包含 BehaviorToolGuide block，测试全部通过。

---

## Phase 5: User Story 4 — 删除 bootstrap.answer 幽灵引用 (Priority: P2)

**Goal**: System prompt 中不再引用不存在的 `bootstrap.answer` 工具，bootstrap 引导改为使用 `behavior.write_file` 和 memory 工具。

**Independent Test**: 在 bootstrap PENDING 状态下检查 system prompt，搜索 "bootstrap.answer" 字样，预期不包含。

### Tests for User Story 4

- [ ] T028 [P] [US4] 在 `octoagent/packages/core/tests/test_behavior_materialize.py` 中编写 `test_bootstrap_prompt_no_phantom_tool` — 模拟 bootstrap PENDING 状态，断言生成的 bootstrap 引导 prompt 中不包含 "bootstrap.answer" 字样，但包含 "behavior.write_file" 字样。

### Implementation for User Story 4

- [x] T029 [US4] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` 中修改 bootstrap 引导指令（约 L3868-3880）— 将第 3 条规则 `"3. 用户回答后，通过 bootstrap.answer 工具保存答案\n"` 替换为 `"3. 用户回答后，根据信息类型选择正确的存储方式：\n"` + `"   - 称呼/偏好/规则 -> behavior.write_file 写入对应行为文件\n"` + `"   - 稳定事实 -> memory tools\n"` + `"   - 敏感值 -> SecretService\n"`。（FR-015, FR-016）

**Checkpoint**: Bootstrap PENDING 状态下 system prompt 不包含 "bootstrap.answer"；Agent 使用正确的存储路由。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 代码清理、control_plane 复用、完整性检查

- [x] T030 [P] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 的 `_handle_behavior_write_file` 中增加事件记录 — 写入成功后记录 `EventType.TOOL_CALL` 事件（含 source="control_plane"、file_path、内容摘要），与 LLM 工具的事件记录保持一致（FR-018）。
- [x] T031 [P] 在 `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` 中为 3 个新增共享函数（`validate_behavior_file_path`、`read_behavior_file_content`、`check_behavior_file_budget`）补充 `__all__` 导出声明（如果模块使用 `__all__`）。
- [x] T032 [P] 运行完整测试套件 `pytest octoagent/packages/core/tests/test_behavior_materialize.py -v`，确认所有 13 个测试用例通过。
- [x] T033 确认 `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py` 中 Orchestrator 的行为文件注入（L1168 附近的 `render_behavior_system_block` 调用）也注入了 BehaviorToolGuide block — 若 Orchestrator 不注册 behavior 工具则可跳过。

---

## FR 覆盖映射表

| FR | 描述 | 覆盖任务 |
|----|------|----------|
| FR-001 | 启动时 9 个行为文件模板写入磁盘 | T010, T011 |
| FR-002 | writeFileIfMissing 策略 | T010 (exists check), T006, T007 |
| FR-003 | 内容与 `_default_content_for_file()` 一致 | T010, T005 |
| FR-004 | 在 `ensure_filesystem_skeleton()` 中完成 | T010 |
| FR-005 | 注册 behavior.read_file LLM 工具 | T018 |
| FR-006 | 注册 behavior.write_file LLM 工具 | T019 |
| FR-007 | 工具 schema 与 control plane handler 参数一致 | T018, T019 |
| FR-008 | write_file side_effect=reversible, read_file side_effect=none | T018, T019 |
| FR-009 | write_file 遵循 review_mode（proposal 确认） | T019 |
| FR-010 | 路径限制在 behavior 目录内 | T001, T012, T013, T014 |
| FR-011 | 写入前字符预算检查，超出拒绝 | T003, T015, T016, T019 |
| FR-012 | System prompt 注入 BehaviorToolGuide block | T025, T026, T022 |
| FR-013 | 存储边界提示 | T025, T024 |
| FR-014 | bootstrap PENDING 额外强调存储路由 | T025, T027, T023 |
| FR-015 | 移除 bootstrap.answer 引用 | T029, T028 |
| FR-016 | bootstrap 改用 behavior.write_file + memory | T029, T028 |
| FR-017 | 写入失败记录事件且不阻塞启动 | T010, T008 |
| FR-018 | LLM 工具修改行为文件记录事件 | T019, T030 |

**覆盖率**: 18/18 FR = **100%**

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup)        <- 无依赖，可立即开始
  |
  v
Phase 2 (US1)          <- 依赖 Phase 1（T010/T011 使用 T004 的 structlog + T001 的辅助函数理念，但 T010 自身是独立实现）
  |
  +-- Phase 3 (US2)    <- 依赖 Phase 1（T018/T019 调用 T001/T002/T003）；与 Phase 2 可并行
  |
  +-- Phase 4 (US3)    <- 依赖 Phase 3（T25 需要 workspace 实例，T26 注入依赖 guide 函数存在）
  |
  +-- Phase 5 (US4)    <- 可独立于 Phase 2/3/4，但建议在 Phase 4 之后（guide block 已注入时 bootstrap prompt 更完整）
  |
  v
Phase 6 (Polish)       <- 依赖 Phase 2-5 全部完成
```

### User Story 间依赖

- **US1（模板落盘）**: 独立，无依赖其他 Story。为 US2 提供磁盘文件但非硬依赖（US2 有 fallback）。
- **US2（LLM 工具）**: 依赖 Phase 1 共享函数。与 US1 可并行（工具有 default fallback）。
- **US3（System Prompt 引导）**: 依赖 US2 完成（guide block 引用工具名称和参数）。
- **US4（删除幽灵引用）**: 技术上独立，但建议在 US3 之后（guide block 提供了替代引导）。

### Story 内部并行机会

- **Phase 1**: T001/T002/T003/T004 全部操作同一文件（behavior_workspace.py），建议顺序执行。
- **Phase 2**: T005-T009 测试任务可并行（标记 [P]）；T010-T011 实现任务顺序执行。
- **Phase 3**: T012-T017 测试任务可并行（标记 [P]）；T018/T019/T020 操作同一文件需顺序。
- **Phase 4**: T022-T024 测试任务可并行（标记 [P]）；T025/T026/T027 操作不同文件可部分并行（T025 在 butler_behavior.py，T026/T027 在 agent_context.py）。
- **Phase 5**: T028 测试可先行；T029 单独完成。
- **Phase 6**: T030/T031/T032/T033 之间可并行（标记 [P]）。

### Recommended Implementation Strategy

**推荐: Incremental Delivery**

1. Phase 1 (Setup) -> 共享函数就位
2. Phase 2 (US1) -> 模板落盘验证 -> 第一个可交付增量
3. Phase 3 (US2) -> LLM 工具注册 -> Agent 可读写行为文件
4. Phase 4 (US3) -> System Prompt 引导 -> Agent 知道何时用工具
5. Phase 5 (US4) -> Bug fix -> 清除幽灵引用
6. Phase 6 (Polish) -> 代码清理 + 完整性检查

每个 Phase 完成后都可以独立验证，不需要等待所有 Phase 完成。

---

## Notes

- [P] 任务 = 不同文件或无依赖，可并行执行
- [Story] 标签映射到 spec.md 中的 User Story
- 每个 Story 独立可完成可测试
- 提交策略：每个 Phase 完成后一次 commit，或每个 Story 一次 commit
- 总计 **33 个任务**，覆盖 **4 个 User Stories**，约 **45% 可并行**（15/33 标记 [P]）
