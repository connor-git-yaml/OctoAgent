# Data Model: Feature 033 Agent Profile + Bootstrap + Context Continuity

## 1. 核心对象

### 1.1 `AgentProfile`

主 Agent / automation / work 可选择的正式 profile。

关键字段：

- `profile_id`
- `scope` (`system` / `project`)
- `project_id`
- `name`
- `persona_summary`
- `instruction_overlays`
- `model_alias`
- `tool_profile`
- `policy_refs`
- `memory_access_policy`
- `context_budget_policy`
- `bootstrap_template_ids`
- `metadata`
- `version`

### 1.2 `OwnerProfile`

Owner 的全局基础身份与协作偏好基线。

关键字段：

- `owner_profile_id`
- `display_name`
- `preferred_address`
- `timezone`
- `locale`
- `working_style`
- `interaction_preferences`
- `boundary_notes`
- `main_session_only_fields`
- `version`

### 1.3 `OwnerProfileOverlay`

Owner 在 project / workspace 作用域下的覆盖层，用于表达“同一个 owner 在不同 project 中如何协作”。

关键字段：

- `owner_overlay_id`
- `owner_profile_id`
- `scope` (`project` / `workspace`)
- `project_id`
- `workspace_id`
- `assistant_identity_overrides`
- `working_style_override`
- `interaction_preferences_override`
- `boundary_notes_override`
- `bootstrap_template_ids`
- `main_session_only_overrides`
- `metadata`
- `version`

### 1.4 `BootstrapSession`

首启或 project-init 的引导状态。

关键字段：

- `bootstrap_id`
- `project_id`
- `workspace_id`
- `owner_profile_id`
- `owner_overlay_id`
- `agent_profile_id`
- `status`
- `current_step`
- `steps`
- `answers`
- `generated_profile_ids`
- `generated_owner_revision`
- `blocking_reason`
- `surface`

### 1.5 `SessionContextState`

短期上下文的 durable state。

关键字段：

- `session_id`
- `thread_id`
- `task_ids`
- `recent_turn_refs`
- `recent_artifact_refs`
- `rolling_summary`
- `summary_artifact_id`
- `last_context_frame_id`
- `updated_at`

### 1.6 `ContextFrame`

一次真实运行所消费的上下文快照。

关键字段：

- `context_frame_id`
- `task_id`
- `session_id`
- `project_id`
- `workspace_id`
- `agent_profile_id`
- `owner_profile_id`
- `owner_overlay_id`
- `owner_profile_revision`
- `bootstrap_session_id`
- `system_blocks`
- `recent_summary`
- `memory_hits`
- `delegation_context`
- `budget`
- `degraded_reason`
- `source_refs`
- `created_at`

## 2. 关系

```text
Project
  └── default AgentProfile
        ├── used by Session
        ├── used by AutomationJob
        └── inherited by Work

OwnerProfile
  └── OwnerProfileOverlay
        ├── referenced by BootstrapSession
        └── contributes to ContextFrame

SessionContextState
  └── emits ContextFrame
        ├── consumed by TaskService / LLMService
        ├── referenced by Work / Pipeline / AutomationRun
        └── projected by Control Plane
```

## 3. 设计原则

1. `AgentProfile` / `OwnerProfile` / `OwnerProfileOverlay` 是正式对象，不等同于 markdown 文件。
2. `SessionContextState` 负责短期 continuity，不替代长期 Memory。
3. `ContextFrame` 是运行时快照，保证“这次到底带了哪些上下文”可追溯。
4. owner 基线与 project overlay 必须分层建模，避免把 project 特有偏好塞进全局对象或不透明 `metadata`。
5. 长期事实若需要沉淀到 Memory，仍通过 `WriteProposal` 链路。
