# F097 Phase A 实施报告

**日期**: 2026-05-10
**baseline**: cc64f0c

## 改动文件

- `octoagent/packages/core/src/octoagent/core/models/delegation.py`：+47 行（新增 SubagentDelegation class，含 11 字段 + 完整 docstring）
- `octoagent/packages/core/src/octoagent/core/models/__init__.py`：+2 行（import 导出 + `__all__` 新增 SubagentDelegation）
- `octoagent/packages/core/tests/test_subagent_delegation_model.py`：+233 行（新建测试文件，14 个测试函数）

## 净增减

- 实施代码：+49 行（两个现有文件）
- 测试代码：+233 行（新建文件）
- 总：+282 行

## 测试结果

- 新增测试：14 个，全 PASS
  ```
  14 passed in 0.18s
  ```
- packages/core/ 全量：411 passed / 0 failed（vs Phase 0 baseline 0 regression）
  ```
  411 passed, 1 warning in 2.62s
  ```
- 已知 warning：`ToolEntry.schema` shadow warning（F083 已知工程债，非 regression）

## 关联 AC 自查

- [x] **AC-A1**：SubagentDelegation 含所有 12 字段（含 child_agent_session_id，GATE_DESIGN C-1）
  - `delegation_id` / `parent_task_id` / `parent_work_id` / `child_task_id` / `child_agent_session_id` / `caller_agent_runtime_id` / `caller_project_id` / `caller_memory_namespace_ids` / `spawned_by` / `target_kind` / `created_at` / `closed_at`
- [x] **AC-A2**：round-trip 单测含 child_task.metadata 路径模拟（`test_child_task_metadata_path_simulation`）；target_kind 默认 SUBAGENT；closed_at 默认 None；child_agent_session_id 默认 None
- [x] **AC-A3**：持久化路径走 task metadata（无新 SQL 表 / 无 migration）——SubagentDelegation 无 save 方法，完全依赖 task.metadata 的 JSON 序列化路径

## 实施说明

- **不含 helper 方法**（to_metadata_json / from_metadata_json / mark_closed）：编排器指令明确禁止，Pydantic 自带 model_dump_json / model_validate_json 已满足 AC-A2 round-trip 要求
- tasks.md TA.1 要求 helper 方法与编排器约束冲突，按编排器指令执行（严格遵守 spec AC-A1/A2/A3，helper 未在 spec 中列出）
- `caller_memory_namespace_ids` 默认空列表（Field(default_factory=list)），spawn 时填充（Phase F 实施）
- model 位置：扩展 delegation.py 末尾（非新建文件），风格与同文件 Work / DelegationEnvelope / DelegationResult 一致
