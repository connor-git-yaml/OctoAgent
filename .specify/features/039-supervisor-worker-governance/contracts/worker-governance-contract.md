# Contract: Worker Governance Review / Apply

## Review Output

`workers.review` 与 `worker.review` 返回统一 proposal：

- `plan_id`
- `work_id`
- `task_id`
- `proposal_kind`
  - `split`
  - `repartition`
  - `merge`
- `objective`
- `summary`
- `requires_user_confirmation`
- `assignments[]`
  - `objective`
  - `worker_type`
  - `target_kind`
  - `tool_profile`
  - `title`
  - `reason`
- `merge_candidate_ids[]`
- `warnings[]`

## Apply Behavior

- `worker.apply` 输入 `plan`
- 对 `split / repartition`
  - 创建 child tasks / child works
  - metadata 写入：
    - `requested_worker_type`
    - `requested_target_kind`
    - `tool_profile`
    - `spawned_by`
- 对 `merge`
  - 调用 delegation merge
- 对 `repartition`
  - 先取消仍在运行的 child works，再按新计划重划分

## Runtime Truth

control plane work projection 必须直接暴露：

- `selected_worker_type`
- `target_kind`
- `runtime_summary.requested_tool_profile`

这样 Feature 035/Advanced UI 可以直接解释“谁被派去做什么，以及被授予了多大权限”。
