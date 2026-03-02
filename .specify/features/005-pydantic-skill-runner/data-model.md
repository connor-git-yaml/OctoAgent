# Data Model: Feature 005 — Pydantic Skill Runner

## 1. 枚举与策略模型

### 1.1 SkillRunStatus

- `SUCCEEDED`
- `FAILED`

### 1.2 ErrorCategory

- `repeat_error`
- `validation_error`
- `tool_execution_error`
- `loop_detected`
- `step_limit_exceeded`
- `input_validation_error`

### 1.3 RetryPolicy

- `max_attempts: int = 3`
- `backoff_ms: int = 500`
- `upgrade_model_on_fail: bool = false`

### 1.4 LoopGuardPolicy

- `max_steps: int = 8`
- `repeat_signature_threshold: int = 3`

### 1.5 ContextBudgetPolicy

- `max_chars: int = 1500`
- `summary_chars: int = 240`

## 2. 核心实体

### 2.1 SkillManifest

- `skill_id: str`
- `version: str`
- `input_model: type[BaseModel]`
- `output_model: type[BaseModel]`
- `model_alias: str`
- `tools_allowed: list[str]`
- `tool_profile: ToolProfile`
- `retry_policy: RetryPolicy`
- `loop_guard: LoopGuardPolicy`
- `context_budget: ContextBudgetPolicy`
- `description: str | None`
- `description_md: str | None`

### 2.2 SkillExecutionContext

- `task_id: str`
- `trace_id: str`
- `caller: str`
- `metadata: dict[str, Any]`

### 2.3 ToolCallSpec

- `tool_name: str`
- `arguments: dict[str, Any]`

### 2.4 SkillOutputEnvelope

- `content: str`
- `complete: bool = false`
- `skip_remaining_tools: bool = false`
- `tool_calls: list[ToolCallSpec] = []`
- `metadata: dict[str, Any] = {}`

### 2.5 ToolFeedbackMessage

- `tool_name: str`
- `is_error: bool`
- `output: str`
- `error: str | None`
- `duration_ms: int`
- `artifact_ref: str | None`
- `parts: list[dict[str, Any]] = []`

### 2.6 SkillRunResult

- `status: SkillRunStatus`
- `output: SkillOutputEnvelope | None`
- `attempts: int`
- `steps: int`
- `duration_ms: int`
- `error_category: ErrorCategory | None`
- `error_message: str | None`

## 3. Registry 实体

### 3.1 RegisteredSkill

- `manifest: SkillManifest`
- `prompt_template: str`

### 3.2 SkillRegistry

- `register(manifest, prompt_template)`
- `get(skill_id)`
- `list_skills()`
- `unregister(skill_id)`

## 4. 事件载荷（Skill 级）

### 4.1 SKILL_STARTED payload

- `skill_id`
- `skill_version`
- `model_alias`
- `max_attempts`
- `max_steps`

### 4.2 SKILL_COMPLETED payload

- `skill_id`
- `attempts`
- `steps`
- `duration_ms`

### 4.3 SKILL_FAILED payload

- `skill_id`
- `attempts`
- `steps`
- `duration_ms`
- `error_category`
- `error_message`
