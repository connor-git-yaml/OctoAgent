# Research: 行为文件模板落盘与 Agent 自主更新

**Feature**: 057-behavior-template-materialize
**Date**: 2026-03-16
**Mode**: codebase-scan（外部调研已内嵌在 spec 中）

---

## Decision 1: 模板落盘位置 — ensure_filesystem_skeleton() vs ensure_startup_records()

**Decision**: 在 `ensure_filesystem_skeleton()` 中实现模板落盘

**Rationale**:
- `ensure_filesystem_skeleton()` 已负责创建目录骨架和最小 scaffold 文件（`README.md`、`secret-bindings.json`），行为文件模板是同类制品
- 该函数是同步纯文件操作，不依赖数据库或 store；模板写入逻辑与目录创建在同一抽象层
- `ensure_startup_records()` 是异步函数，依赖 `StoreGroup`（SQLite），主要处理 owner_profile / agent_profile / bootstrap_session 等数据库记录
- 调用顺序：lifespan 中先调用 `ensure_filesystem_skeleton()`，再调用 `ensure_startup_records()`，所以模板文件在 bootstrap session 创建前已就绪

**Alternatives Rejected**:
- ensure_startup_records()：增加不必要的数据库依赖；违反 SRP（单一职责）
- 独立的 materialize_behavior_templates() 函数：不必要的抽象，当前 9 个文件写入逻辑简单，直接嵌入 ensure_filesystem_skeleton() 更内聚

---

## Decision 2: LLM 工具注册方式 — capability_pack 内联 vs control_plane 委派

**Decision**: 在 `capability_pack.py` 的 `_register_builtin_tools()` 中用 `@tool_contract` 装饰器注册两个新工具，核心逻辑从 `control_plane.py` 的 `_handle_behavior_read_file` / `_handle_behavior_write_file` 提取为共享函数

**Rationale**:
- `capability_pack.py` 是所有 LLM 工具的注册中心，已有 40+ 个 `@tool_contract` 工具
- `@tool_contract` 装饰器提供 `side_effect_level`、`tool_profile`、`tool_group`、`tags`、`worker_types` 等声明式元数据，符合宪法原则 3（工具即契约）
- control_plane handler 通过 `ActionRequestEnvelope` / `ActionResultEnvelope` 的 RPC 模式工作，与 LLM 工具的直接函数调用模式不同，不能直接复用
- 但路径校验 + 文件读写的核心逻辑应提取为共享辅助函数，避免重复实现

**Alternatives Rejected**:
- 直接在 LLM 工具中调用 control_plane dispatch：增加不必要的间接层，且 control_plane 的 ActionEnvelope 序列化/反序列化开销无意义
- 完全独立实现：与 control_plane handler 重复路径校验逻辑，容易导致安全漏洞

---

## Decision 3: review_mode MVP — 对话内 proposal vs ApprovalService

**Decision**: 对话内 proposal 确认（MVP 方案）

**Rationale**:
- 行为文件修改是 `reversible` 操作，不属于宪法原则 4 要求的不可逆操作审批范畴
- 当前所有 9 个文件的 `review_mode` 均为 `REVIEW_REQUIRED`，但 MVP 阶段不需要完整的 ApprovalService 集成
- 实现方式：`behavior.write_file` 在 `review_mode == REVIEW_REQUIRED` 时检查 `confirmed` 参数；如果 `confirmed=false`（默认），返回 `{proposal: true, diff_summary: "..."}` 提示 Agent 向用户展示并等待确认；用户确认后 Agent 再次调用并携带 `confirmed=true`
- 宪法原则 13A 支持此方案：优先通过上下文引导模型决策

**Alternatives Rejected**:
- ApprovalService 集成：需要扩展 WAITING_APPROVAL 状态机、审批面板 UI、Telegram 审批按钮，MVP 阶段过度工程
- 完全不检查 review_mode：违反宪法原则 7（用户可控），用户对行为文件修改失去感知

---

## Decision 4: 字符预算检查 — 拒绝 vs 截断

**Decision**: 拒绝写入 + 返回详细错误

**Rationale**:
- 已有的 `_apply_behavior_budget()` 用于 prompt 注入时的静默截断，保护上下文窗口，这是合理的
- 但 LLM 工具写入时，静默截断意味着数据丢失，违反宪法原则 7（用户可控）
- 拒绝写入并返回 `{current_chars, budget_chars, exceeded_by}` 让 Agent 自行精简重试，体现原则 10（Bias to Action）
- 两个场景行为不同：读时截断（安全）vs 写时拒绝（保护数据完整性）

**Alternatives Rejected**:
- 静默截断：数据丢失，用户不知情
- 写入但标注截断：Agent 可能不检查返回值，导致不可预期的内容丢失

---

## Decision 5: 共享辅助函数的归属位置

**Decision**: 在 `behavior_workspace.py` 中新增路径校验和文件读写辅助函数

**Rationale**:
- `behavior_workspace.py` 已是行为文件路径、内容、预算的单一事实源
- 新增函数：`validate_behavior_file_path(project_root, file_path) -> Path`、`read_behavior_file_safe(project_root, file_path) -> tuple[str, bool]`、`write_behavior_file_safe(project_root, file_path, content) -> dict`
- control_plane.py 和 capability_pack.py 共同引用这些辅助函数
- 路径校验逻辑集中维护，减少安全漏洞面

**Alternatives Rejected**:
- 放在 butler_behavior.py：该文件是 prompt 构建层，不应承担文件 IO 职责
- 放在 control_plane.py 并 import 到 capability_pack.py：方向反了，control_plane 是更高层抽象

---

## Decision 6: BehaviorToolGuide block 的生成策略

**Decision**: 在 `butler_behavior.py` 中新增 `build_behavior_tool_guide_block()` 函数，从 `BehaviorWorkspace.files` 动态生成结构化文本

**Rationale**:
- 独立函数便于单元测试
- 动态生成确保文件集变化时指南自动更新（如后续增加新行为文件）
- 在 `agent_context.py` 的 `_build_agent_context_blocks()` 中作为独立 system block 注入
- 内容包括：文件清单表（file_id | 用途 | 修改时机 | path_hint）+ 工具参数说明 + review_mode 说明 + 存储边界提示

**Alternatives Rejected**:
- 硬编码在 agent_context.py 中：不可测试、不可维护
- 从外部配置文件加载：过度工程，当前阶段文件清单固定

---

## Decision 7: bootstrap.answer 引用的替换策略

**Decision**: 直接修改 agent_context.py L3874 的硬编码字符串，将 bootstrap.answer 引用替换为 behavior.write_file + memory tools 的引导

**Rationale**:
- 当前 L3874 是唯一引用 bootstrap.answer 的位置
- 修改范围最小，风险最低
- 替换后的引导文案与新增的 BehaviorToolGuide block 形成互补：BehaviorToolGuide 提供通用指南，bootstrap 引导指令提供 PENDING 状态特定的存储路由建议

**Alternatives Rejected**:
- 删除整个 bootstrap 引导 block：破坏初始化问卷流程
- 保留 bootstrap.answer 但标注为 deprecated：仍会误导 LLM 尝试调用
