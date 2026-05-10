# F097 Phase G 实施报告

## 概述

Phase G 是纯测试新增 Phase（无实施代码），验证 Phase C 的自动副产品：`_build_ephemeral_subagent_profile` 返回 `kind="subagent"` 的 AgentProfile，`make_behavior_pack_loaded_payload` 以 `str(agent_profile.kind)` 填充 `agent_kind` 字段，自动派生 `"subagent"` 值。

## 新建测试文件

**路径**：`octoagent/apps/gateway/tests/services/test_behavior_pack_loaded_phase_g.py`

**测试数**：9 个（3 个测试类，覆盖 AC-G1 / AC-AUDIT-1 / AC-COMPAT-1）

## AC 验证结果

### AC-G1：Subagent 路径 BEHAVIOR_PACK_LOADED.agent_kind == "subagent"

| 测试 | 结果 |
|------|------|
| `test_ephemeral_subagent_profile_yields_agent_kind_subagent` | PASS |
| `test_subagent_agent_kind_not_worker_or_main` | PASS |
| `test_subagent_load_profile_is_minimal` | PASS（MINIMAL load_profile 写入事件正确）|

验证方法：使用 `AgentContextService._build_ephemeral_subagent_profile(project=None)` 直接构造 ephemeral profile（plan P2-2 闭环：该方法已提为 staticmethod），调用 `resolve_behavior_pack` + `make_behavior_pack_loaded_payload` 验证 `payload.agent_kind == "subagent"`。

### AC-AUDIT-1：四层 audit chain（层 1 ↔ 层 2 可直接验证部分）

| 测试 | 结果 |
|------|------|
| `test_subagent_payload_agent_id_equals_profile_id` | PASS（payload.agent_id == profile_id）|
| `test_subagent_profile_id_has_expected_prefix` | PASS（前缀 `agent-prf-subagent-` + ULID）|
| `test_each_ephemeral_profile_has_unique_profile_id` | PASS（每次 ULID 唯一）|

验证范围：
- 层 1 ↔ 层 2：`AgentProfile.profile_id == BEHAVIOR_PACK_LOADED.agent_id` — 直接断言 ✅
- 层 2 ↔ 层 3：`BEHAVIOR_PACK_LOADED.agent_id → AgentRuntime.profile_id` — 运行时关联，由 `test_task_service_context_integration.py` 覆盖 [E2E_DEFERRED 到 Verify 全量回归]
- 层 3 ↔ 层 4：`AgentRuntime.profile_id → RecallFrame.agent_runtime_id` — 同上

### AC-COMPAT-1：main / worker 路径 agent_kind 不受 F097 影响

| 测试 | 结果 |
|------|------|
| `test_main_path_agent_kind_unchanged` | PASS（agent_kind == "main"）|
| `test_worker_path_agent_kind_unchanged` | PASS（agent_kind == "worker"）|
| `test_three_agent_kinds_are_distinct` | PASS（三值互不相同）|

另外：`test_agent_decision_envelope.py::TestMakeBehaviorPackLoadedPayload::test_payload_fields_completeness`（line 640: `assert payload.agent_kind == "worker"`）继续 PASS，验证 F097 未破坏 Worker 路径。

## 回归验证

- **命令**：`pytest -p no:rerunfailures octoagent/ -q --tb=short -m "not e2e_full and not e2e_smoke"`
- **退出码**：0
- **输出摘要**：3355 passed, 12 skipped（Phase F baseline 3346 + 新增 9 个测试）
- **0 regression**：已确认

## 设计决策说明

测试使用 `AgentContextService._build_ephemeral_subagent_profile` 静态方法直接构造 ephemeral profile（plan Phase C P2-2 Codex review 闭环：该方法从内联代码提取为独立 staticmethod，供测试直接调用真实 helper，避免 mock）。

无需走完整 dispatch 路径，原因：
1. Phase C 的端到端 dispatch 路径已在 `test_agent_context_phase_c.py` 覆盖
2. `make_behavior_pack_loaded_payload` 是纯函数，输入 AgentProfile 即决定 agent_kind 输出，无需 EventStore 验证
3. plan §3.3 实测确认：`agent_kind` 字段是 `str` 类型无枚举约束，`str(agent_profile.kind)` 直接派生，无中间层

## Phase G 状态

- TG.1：✅ 完成（新建测试文件，9 个测试全 PASS）
- TG.2：✅ 完成（Worker 路径兼容性确认 + 额外 main/worker/subagent 三值互区分测试）
- TG.3：✅ 完成（全量回归 3355 passed，0 regression）
- TG.4：等编排器 commit（无 Codex review，测试新增命中"不需要做的节点"）
