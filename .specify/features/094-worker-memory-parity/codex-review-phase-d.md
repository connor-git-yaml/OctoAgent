# Phase D Codex Adversarial Review 闭环

**Phase**: D（行为零变更清理）
**Review 时间**: 2026-05-09
**Model**: Codex CLI (model_reasoning_effort=high)
**输入**: tasks.md Phase D D1-D7 全部 staged diff
**Findings 总数**: 4（0 HIGH + 1 MED + 3 LOW）

## Findings 处理决议

### MED-1: D5 grep 0 残留不成立 ✅ 接受 + 闭环

**Evidence**: 源码注释中保留了 `_default_worker_memory_recall_preferences` 文字（虽然 import / 调用全部已删除），但 D5 验收 task 措辞是"全 codebase 0 命中"——严格 grep 仍 2 命中（注释里）。

**修复**:
- `core/models/agent_context.py` 注释段：把 `_default_worker_memory_recall_preferences()` 改为"私有硬编码默认值函数"——纯描述、不含原符号
- `gateway/services/agent_context.py:186` 注释：把 `_default_worker_memory_recall_preferences 函数已删除` 改为"私有硬编码 worker memory recall 默认值函数已删除"
- 验证：`grep -rn "_default_worker_memory_recall_preferences"` → 0 命中 ✅

### LOW-2: dict 常量可被未来调用方原地污染 ✅ 接受 + 闭环

**Evidence**: `DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES` 之前是 `dict[str, Any]`，理论上可被 mutate 污染。

**修复**:
- 改为 `MappingProxyType` + `Mapping[str, Any]` 类型——只读视图
- 内部 raw dict 改为 `_DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES_RAW` private（仍是 dict）；导出的常量是 read-only 视图
- 现有调用点 `{**DEFAULT_...}` 仍正常（dict unpacking 创建可变副本）
- 新增专项测试 `test_f094_d4_immutable_defaults_constant_cannot_be_mutated`：直接 mutate raise TypeError；dict unpacking 副本可改不污染常量

### LOW-3: TypedDict 类型约束 ✅ 接受 + 推迟到 F107

**Evidence**: 5 key 实际是 fixed schema（str/bool/int），但常量类型是 `dict[str, Any]`，没有强类型约束。

**决策**: F094 范围内 5 key 形态稳定，TypedDict 是 nice-to-have 但不是 must-have。F107 (WorkerProfile/AgentProfile 完全合并) 是 schema 重整时机，届时一并加 TypedDict。Commit message 显式 ignored 标注。

### LOW-4: D4 测试覆盖防御分支不足 ✅ 接受 + 闭环

**Evidence**: 缺 empty existing dict / 完整 5 key override / 非 dict 防御 / immutable 测试。

**修复**: 新增 2 个专项测试：
- `test_f094_d4_immutable_defaults_constant_cannot_be_mutated`（同 LOW-2 闭环）
- `test_f094_d5_existing_profile_edge_cases`：参数化覆盖 3 类 edge case
  - `memory_recall = {}` → 全 defaults
  - `memory_recall = 完整 5 key override` → 全 existing
  - `memory_recall = "not_a_dict"`（非法）→ baseline `_memory_recall_preferences` 防御 isinstance 后视为空 → 全 defaults

## Codex 验证无 finding 项

- D2 `worker_profile is None`：调用点 `_ensure_agent_profile_from_worker_profile` 在 line 2741-2742 早 return None / ARCHIVED；merge 代码不可达；当前无其他生产调用点绕过 gate
- D2 merge order：`{**defaults, **existing}` existing 覆盖 defaults，与 baseline 一致
- D3 import：未发现 Python import / 调用残留（D5 grep 闭环后）
- F107 命名：`DEFAULT_WORKER_MEMORY_RECALL_PREFERENCES` 与 Phase D 来源语义一致
- import cycle：未发现 core ↔ gateway 循环 import

## 闭环汇总

| Finding | 严重度 | 处理决议 | 落地章节 |
|---------|--------|----------|----------|
| MED-1 | MED | **接受** | 注释改写不含原函数符号 |
| LOW-2 | LOW | **接受** | MappingProxyType 锁只读 + 专项测试 |
| LOW-3 | LOW | **推迟到 F107** | TypedDict 是 schema 重整时机的事 |
| LOW-4 | LOW | **接受** | 2 个新增专项测试（含 edge cases）|

## 全量回归验证

- packages/ + apps/gateway/tests（不含 e2e_live）: **3006 passed + 2 skipped + 1 xfailed + 1 xpassed**——0 regression vs Phase C 末（3002 → 3004 +2 D 测试 → 3006 +2 D 防御测试）
- F094 Phase D 专项测试: 4 个全 PASSED
  - test_f094_d2_worker_default_memory_recall_matches_baseline
  - test_f094_d4_immutable_defaults_constant_cannot_be_mutated
  - test_f094_d5_existing_profile_edge_cases
  - test_f094_d5_existing_profile_overrides_module_defaults
- D5 grep 0 残留：✅ 通过

## Commit message 摘要

`Codex review (Phase D): 0 high / 1 medium 已处理（接受 D5 grep 修复）/ 3 low 已处理 2（接受 LOW-2 immutable + LOW-4 edge cases）+ 1 推迟（LOW-3 TypedDict 留 F107） + 0 wait`
