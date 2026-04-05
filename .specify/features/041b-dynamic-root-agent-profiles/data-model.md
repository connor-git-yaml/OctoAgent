# Data Model: Feature 041 Dynamic Root Agent Profiles

## 1. WorkerArchetype

系统内建 archetype，不再承担用户世界里的完整 Agent 身份。

### 字段

- `worker_type`
- `label`
- `default_model_alias`
- `default_tool_profile`
- `default_tool_groups[]`
- `runtime_kinds[]`
- `bootstrap_file_ids[]`
- `capabilities[]`

### 说明

- 第一阶段继续复用现有 `general / ops / research / dev`
- 在产品面上，这些对象主要作为 starter templates 出现

## 2. WorkerProfile

Root Agent 的正式静态配置对象。

### 字段

- `profile_id`
- `scope`
- `project_id`
- `name`
- `base_archetype`
- `persona_summary`
- `model_alias`
- `tool_profile`
- `default_tool_groups[]`
- `selected_tools[]`
- `runtime_kinds[]`
- `policy_refs[]`
- `metadata`
- `version`
- `created_at`
- `updated_at`

### 说明

- 第一阶段采用 `singleton` 产品模式
- 一个 `WorkerProfile` 对应一个可观察的单例运行槽位
- 未来如要扩到多实例，不改变 `WorkerProfile` 作为静态配置对象的地位

## 3. WorkerProfileRevision

`WorkerProfile` 的已发布版本。

### 字段

- `profile_id`
- `revision`
- `snapshot_payload`
- `change_summary`
- `created_at`
- `published_by`

### 说明

- 第一阶段可以先弱化为 profile 本身的版本字段 + effective snapshot
- 但 runtime truth 必须能追溯到具体生效版本

## 4. WorkerSingletonContext

Root Agent 当前的动态运行上下文。

### 字段

- `profile_id`
- `active_project_id`
- `active_workspace_id`
- `active_work_count`
- `running_work_count`
- `attention_work_count`
- `latest_work_id`
- `latest_task_id`
- `latest_work_title`
- `latest_work_status`
- `latest_target_kind`
- `current_selected_tools[]`
- `updated_at`

### 说明

- 这是 UI 第一阶段最关键的运行时对象
- 它不是独立多实例 registry，而是 Root Agent 在当前作用域下的单例动态状态摘要

## 5. WorkerProfileView

前端控制面的一等展示对象。

### 字段

- `profile_id`
- `name`
- `scope`
- `project_id`
- `mode` = `singleton`
- `static_config`
- `dynamic_context`
- `warnings[]`
- `capabilities[]`

### 说明

- `static_config` 负责解释“它被设计成什么样”
- `dynamic_context` 负责解释“它现在正在做什么”

## 6. Work Projection 扩展

第一阶段仍保留 legacy `selected_worker_type`，但逐步补上 profile lineage 字段：

- `requested_worker_profile_id`
- `requested_worker_profile_version`
- `effective_worker_snapshot_id`

### 说明

- 旧 work 没有这些字段时，用兼容模式回显 legacy runtime
- 新 work 应优先使用 profile lineage 字段解释运行真相
