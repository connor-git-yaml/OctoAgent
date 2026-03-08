# Data Model: Feature 030 — Built-in Capability Pack + Delegation Plane + Skill Pipeline

## 1. Bundled Capability Pack

### 1.1 `BundledCapabilityPack`

- `pack_id`
- `version`
- `skills[]`
- `tools[]`
- `worker_profiles[]`
- `bootstrap_files[]`
- `fallback_toolset[]`
- `degraded_reason`

### 1.2 `BundledToolDefinition`

- `tool_name`
- `label`
- `description`
- `tool_group`
- `tool_profile`
- `tags[]`
- `worker_types[]`
- `manifest_ref`
- `metadata`

### 1.3 `BundledSkillDefinition`

- `skill_id`
- `label`
- `description`
- `model_alias`
- `worker_types[]`
- `tools_allowed[]`
- `pipeline_templates[]`

### 1.4 `WorkerCapabilityProfile`

- `worker_type` (`ops` / `research` / `dev`)
- `capabilities[]`
- `default_model_alias`
- `default_tool_profile`
- `default_tool_groups[]`
- `bootstrap_file_ids[]`
- `runtime_kinds[]`

### 1.5 `WorkerBootstrapFile`

- `file_id`
- `path_hint`
- `content`
- `applies_to_worker_types[]`
- `metadata`

## 2. ToolIndex

### 2.1 `ToolIndexRecord`

- `record_id`
- `tool_name`
- `search_text`
- `embedding`
- `tool_group`
- `tool_profile`
- `worker_types[]`
- `tags[]`
- `manifest_ref`
- `metadata`
- `updated_at`

### 2.2 `ToolIndexQuery`

- `query`
- `limit`
- `tool_groups[]`
- `worker_type`
- `tool_profile`
- `tags[]`
- `project_id`
- `workspace_id`

### 2.3 `ToolIndexHit`

- `tool_name`
- `score`
- `match_reason`
- `matched_filters`
- `tool_group`
- `tool_profile`
- `metadata`

### 2.4 `DynamicToolSelection`

- `selection_id`
- `query`
- `selected_tools[]`
- `hits[]`
- `backend`
- `is_fallback`
- `warnings[]`

## 3. Work / Delegation

### 3.1 `Work`

- `work_id`
- `task_id`
- `parent_work_id`
- `title`
- `kind`
- `status`
- `target_kind`
- `owner_id`
- `requested_capability`
- `selected_worker_type`
- `route_reason`
- `project_id`
- `workspace_id`
- `tool_selection_id`
- `pipeline_run_id`
- `metadata`
- `created_at`
- `updated_at`
- `completed_at`

### 3.2 `WorkStatus`

- `CREATED`
- `ASSIGNED`
- `RUNNING`
- `WAITING_INPUT`
- `WAITING_APPROVAL`
- `PAUSED`
- `MERGED`
- `ESCALATED`
- `TIMED_OUT`
- `SUCCEEDED`
- `FAILED`
- `CANCELLED`

### 3.3 `DelegationEnvelope`

- `delegation_id`
- `work_id`
- `task_id`
- `target_kind`
- `requested_capability`
- `payload`
- `route_reason`
- `selected_worker_type`
- `bootstrap_context`
- `selected_tools[]`
- `project_id`
- `workspace_id`
- `timeout_seconds`
- `metadata`

### 3.4 `DelegationResult`

- `delegation_id`
- `work_id`
- `status`
- `summary`
- `retryable`
- `runtime_id`
- `target_kind`
- `worker_type`
- `route_reason`
- `metadata`

## 4. Skill Pipeline

### 4.1 `SkillPipelineDefinition`

- `pipeline_id`
- `label`
- `version`
- `entry_node_id`
- `nodes[]`
- `metadata`

### 4.2 `SkillPipelineNode`

- `node_id`
- `label`
- `node_type` (`skill` / `tool` / `transform` / `gate` / `delegation`)
- `handler_id`
- `next_node_id`
- `retry_limit`
- `timeout_seconds`
- `metadata`

### 4.3 `SkillPipelineRun`

- `run_id`
- `pipeline_id`
- `task_id`
- `work_id`
- `status`
- `current_node_id`
- `pause_reason`
- `retry_cursor`
- `state_snapshot`
- `input_request`
- `approval_request`
- `created_at`
- `updated_at`
- `completed_at`

### 4.4 `PipelineCheckpoint`

- `checkpoint_id`
- `run_id`
- `node_id`
- `status`
- `state_snapshot`
- `side_effect_cursor`
- `created_at`
- `updated_at`

### 4.5 `PipelineReplayFrame`

- `frame_id`
- `run_id`
- `node_id`
- `status`
- `summary`
- `checkpoint_id`
- `ts`

## 5. Control Plane Projection

### 5.1 `CapabilityPackDocument`

- `resource_type = "capability_pack"`
- `resource_id = "capability:bundled"`
- `pack`
- `worker_profiles`
- `bootstrap_files`
- `fallback_toolset`

### 5.2 `DelegationPlaneDocument`

- `resource_type = "delegation_plane"`
- `resource_id = "delegation:overview"`
- `works[]`
- `status_summary`
- `worker_statuses[]`
- `recent_route_reasons[]`

### 5.3 `SkillPipelineDocument`

- `resource_type = "skill_pipeline"`
- `resource_id = "pipeline:overview"`
- `runs[]`
- `replay_refs`
- `paused_runs`
- `degraded_reason`

## 6. Store Layout

新增 SQLite 表：

- `works`
- `skill_pipeline_runs`
- `skill_pipeline_checkpoints`

说明：

- ToolIndex 可按 backend 决定具体存储；fallback backend 可仅驻内存并按 capability pack 重建。
- capability pack 自身可由代码生成，不要求单独表持久化。
