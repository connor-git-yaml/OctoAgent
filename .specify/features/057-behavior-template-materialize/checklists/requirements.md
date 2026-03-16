# Quality Checklist: 057-behavior-template-materialize

**Feature**: 行为文件模板落盘与 Agent 自主更新
**Spec Version**: Draft (2026-03-16)
**Checked Against**: spec.md + constitution.md + blueprint.md + 现有代码库
**Checker**: Quality Gate Sub-Agent

---

## 1. Content Quality（内容质量）

- [x] **无实现细节泄漏**: spec 未指定具体语言/框架/API 实现方式
  - Notes: FR-004 提到 SHOULD 在 `ensure_filesystem_skeleton()` 中完成，FR-007 提到复用现有 control plane handler；Resolved Ambiguities 节明确引用了 `_handle_behavior_read_file` / `_handle_behavior_write_file`。这些**跨越了需求边界进入实现设计**，但考虑到项目 Spec-Driven 流程中 spec 允许携带 Resolved Ambiguities 作为实现指引，且主体 Requirements 节保持了抽象性，**整体可接受**。
- [x] **聚焦用户价值和业务需求**: 4 个 User Story 均从用户/Agent 视角出发描述
- [x] **面向非技术利益相关者可读**: 用户故事使用场景化语言描述
- [x] **所有必填章节已完成**: User Scenarios & Testing、Requirements、Success Criteria、Resolved Ambiguities 均已填写

## 2. Requirement Completeness（需求完整性）

- [x] **无 [NEEDS CLARIFICATION] 标记残留**: 全文搜索未发现任何残留标记
- [x] **需求可测试且无歧义**: 18 条 FR 均使用 MUST/SHOULD/MUST NOT 明确级别，且可映射到测试用例
- [x] **成功标准可测量**: SC-001 到 SC-006 均可通过自动化断言验证
- [x] **成功标准是技术无关的**: 成功标准描述的是功能行为而非实现方式
- [x] **所有验收场景已定义**: 4 个 User Story 共定义了 11 个验收场景（US-1: 3, US-2: 4, US-3: 3, US-4: 2），覆盖主流程
- [x] **边界条件已识别**: Edge Cases 节列出了 6 个边界场景
- [x] **范围边界清晰**: spec 聚焦于模板落盘 + LLM 工具注册 + system prompt 引导 + 幽灵引用清理四个子域
- [x] **依赖和假设已识别**: Resolved Ambiguities 节明确了两个关键假设（写入时机、LLM 工具复用现有实现）

## 3. Feature Readiness（特性就绪度）

- [x] **所有功能需求有明确的验收标准**: FR-001~FR-018 均通过 [关联 US-x] / [关联 Edge Case] 追溯到具体验收场景
- [x] **用户场景覆盖主要流程**: 首次启动落盘、Agent 读写行为文件、system prompt 引导、幽灵引用修复均已覆盖
- [x] **功能满足 Success Criteria 中定义的可测量成果**: SC-001~SC-006 与 FR 一一对应，无悬空成功标准
- [ ] **规范中无实现细节泄漏**: Resolved Ambiguities 节指名了 `_handle_behavior_read_file` / `_handle_behavior_write_file` 等内部函数名和 `ensure_filesystem_skeleton()` 函数名
  - Notes: 虽然 Resolved Ambiguities 在项目惯例中允许携带实现指引，但从纯需求规范角度看，这些内部函数名构成了对实现的耦合。**建议**: 将函数名引用改为描述性语言（如「复用现有 control plane 行为文件读写 handler 的核心逻辑」），或明确标注该节为「实现建议」而非需求约束。

## 4. Constitution Compliance（宪法合规性）

- [x] **原则 1 - Durability First**: FR-001~FR-004 确保行为文件落盘持久化；FR-002 writeFileIfMissing 保护已有数据；SC-004 验证重启后不丢失
- [x] **原则 2 - Everything is an Event**: FR-017 要求落盘失败记录为结构化事件；FR-018 要求 LLM 工具修改生成事件记录
- [x] **原则 3 - Tools are Contracts**: FR-007 明确要求 LLM 工具 schema 与 handler 参数行为一致；FR-008 要求声明 side_effect_level
- [x] **原则 4 - Side-effect Two-Phase**: FR-009 要求 proposal_required 模式下修改需用户确认（Plan -> Gate -> Execute）
- [x] **原则 5 - Least Privilege**: FR-010 限制文件路径在 behavior 目录内，不得读写任意路径
- [x] **原则 6 - Degrade Gracefully**: FR-017 明确要求落盘失败不阻塞启动，降级到内存默认模板
- [x] **原则 7 - User-in-Control**: FR-009 确保 review_mode 为 REVIEW_REQUIRED 时写入需要用户确认
- [x] **原则 8 - Observability**: FR-017 + FR-018 覆盖了事件记录要求
- [x] **原则 9 - 不猜关键配置**: FR-005/FR-006 让 Agent 通过工具查询后再修改（read -> propose -> execute）
- [x] **原则 12 - 记忆写入治理**: FR-014 明确区分了存储边界（事实进 Memory，规则进 behavior files，敏感值进 SecretService）
- [x] **原则 13A - 优先上下文**: FR-012/FR-013 通过 system prompt 提供上下文引导 Agent 自主判断，而非硬编码流程
- [ ] **原则 2 - 事件记录完整性**: `_handle_behavior_write_file` 当前实现中**未生成事件记录**（未调用 `_publish_action_event` 或类似机制记录修改来源和摘要），FR-018 的要求在现有代码中缺少支撑
  - Notes: 这不是 spec 本身的缺陷，而是现有代码与 spec 要求之间的 gap。实现阶段需补齐事件发布逻辑。

## 5. Testability（测试可行性）

- [x] **US-1 验收场景可自动化**: 可在 tmp 目录创建 clean environment，启动后断言文件存在和内容匹配
- [x] **US-2 验收场景可自动化**: 可通过 mock LLM 调用验证工具注册和调用链路
- [x] **US-3 验收场景可自动化**: 可断言 system prompt 输出包含指定关键字和结构
- [x] **US-4 验收场景可自动化**: 可断言 bootstrap PENDING 状态下 prompt 不含 "bootstrap.answer"
- [x] **Edge Case 可自动化**: 权限模拟、并发写入、预算检查均可在测试中构造
- [ ] **US-2 Scenario 4 验收标准不够精确**: 「写入被拒绝或截断（具体行为由治理策略决定）」留有二义性——测试需要知道确切的预期行为
  - Notes: **建议**: 明确为「写入被拒绝并返回错误，提示 Agent 精简内容」或「写入被截断到预算上限并附带截断提示」之一。当前代码 `_apply_behavior_budget` 实现的是截断策略，spec 应与之对齐。

## 6. Codebase Compatibility（与已有代码的兼容性）

- [x] **BehaviorWorkspaceFile / BehaviorPackFile / _BehaviorFileTemplate / BEHAVIOR_FILE_BUDGETS 均已存在**: Key Entities 引用的模型和常量在 `behavior_workspace.py` 和 `models/behavior.py` 中已定义
- [x] **ensure_filesystem_skeleton() 已存在**: 当前实现只创建目录和 scaffold 文件（README.md、secret-bindings.json），spec 要求在此基础上补写行为文件模板，扩展点明确
- [x] **_default_content_for_file() 已存在**: 覆盖全部 9 个 file_id，返回默认模板内容
- [x] **control plane handler 已存在**: `_handle_behavior_read_file` 和 `_handle_behavior_write_file` 已实现基本读写，但缺少 review_mode 检查和事件记录
- [x] **bootstrap.answer 幽灵引用已定位**: 在 `agent_context.py:3874` 行存在硬编码引用，需在实现中清理
- [ ] **_handle_behavior_write_file 缺少 review_mode / editable_mode 检查**: 当前实现直接写入磁盘，未检查文件的治理属性。FR-009 要求尊重这些配置，实现阶段需补齐
  - Notes: 这是 spec 与现有代码之间的已知 gap，属于实现任务而非 spec 缺陷。但 spec 应意识到这不是纯「复用」，而需要增强现有 handler。
- [ ] **_handle_behavior_write_file 缺少 side_effect_level 声明**: FR-008 要求工具声明副作用等级，当前 action handler 注册路径中未见 side_effect_level 字段。实现需在工具注册时补充
  - Notes: 建议在 plan 阶段明确工具注册 schema 中 side_effect_level 的填充方式。

## 7. Edge Case Coverage（边界情况覆盖度）

- [x] **磁盘写入失败**: Edge Case 已识别，对应 FR-017
- [x] **并发写入**: Edge Case 已识别（后写者覆盖策略）
- [x] **前端 + Agent 同时修改**: Edge Case 已识别，通过事件记录修改来源
- [x] **文件尚未 materialize 时读取**: Edge Case 已识别，返回默认模板而非错误
- [x] **文件内容被清空**: Edge Case 已识别，视为有效操作
- [x] **特殊字符路径**: Edge Case 已识别
- [ ] **Agent 尝试写入不存在的 file_id**: 未覆盖 — 当 Agent 传入一个非标准 file_id（如 `CUSTOM.md`）时，系统应如何处理？
  - Notes: **建议**: 补充边界条件 — LLM 工具应校验 file_id 属于 ALL_BEHAVIOR_FILE_IDS 集合内，否则拒绝操作。
- [ ] **模板内容函数更新但磁盘文件已存在旧模板**: 未覆盖 — 当默认模板在系统升级后发生变化，但磁盘上已有旧版模板文件时，writeFileIfMissing 策略不会更新。这是预期行为还是需要 migration 策略？
  - Notes: **建议**: 在 Edge Cases 中明确说明：模板升级不自动覆盖已有文件，用户需手动更新或通过版本 migration 工具处理。

---

## Summary

| Dimension | Total | Passed | Failed | Notes |
|-----------|-------|--------|--------|-------|
| Content Quality | 4 | 4 | 0 | |
| Requirement Completeness | 8 | 8 | 0 | |
| Feature Readiness | 4 | 3 | 1 | Resolved Ambiguities 包含内部函数名 |
| Constitution Compliance | 12 | 11 | 1 | 现有代码缺少事件记录支撑 FR-018 |
| Testability | 6 | 5 | 1 | US-2 Scenario 4 预算超限行为二义 |
| Codebase Compatibility | 6 | 4 | 2 | write handler 缺少治理检查 + side_effect |
| Edge Case Coverage | 8 | 6 | 2 | 非标准 file_id + 模板升级策略未覆盖 |
| **Total** | **48** | **41** | **7** | |

---

## Verdict: CONDITIONAL PASS

spec 整体质量良好，覆盖了核心功能需求和宪法合规性。7 项未通过中：

- **3 项为 spec 本身可改进项**（建议在下一轮 spec 修订中修复）:
  1. Resolved Ambiguities 中的实现细节耦合 -> 改为描述性语言或标注为「实现建议」
  2. US-2 Scenario 4 预算超限行为二义 -> 明确为截断或拒绝之一
  3. 补充 2 个边界条件（非标准 file_id 校验、模板升级策略说明）

- **4 项为现有代码 gap**（实现阶段必须解决，非 spec 缺陷）:
  1. `_handle_behavior_write_file` 需补齐 review_mode / editable_mode 检查
  2. `_handle_behavior_write_file` 需补齐事件发布
  3. 工具注册需补充 side_effect_level 声明
  4. 以上 3 项应在 plan.md 中作为明确的实现任务列出

**建议**: 修复 3 项 spec 可改进项后即可进入技术规划阶段。4 项代码 gap 在 plan.md 中作为实现任务跟踪。
