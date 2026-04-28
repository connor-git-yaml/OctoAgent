"""SQLite 数据库初始化 -- 对齐 data-model.md §2

PRAGMA 配置 + 三张表 DDL + 索引创建。
使用 aiosqlite 异步操作。
"""

from datetime import UTC, datetime

import aiosqlite
from ulid import ULID

# tasks 表 DDL
_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'CREATED',
    title       TEXT NOT NULL DEFAULT '',
    thread_id   TEXT NOT NULL DEFAULT 'default',
    scope_id    TEXT NOT NULL DEFAULT '',
    requester   TEXT NOT NULL DEFAULT '{}',
    risk_level  TEXT NOT NULL DEFAULT 'low',
    pointers    TEXT NOT NULL DEFAULT '{}',
    trace_id    TEXT NOT NULL DEFAULT ''
);
"""

_TASKS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_thread_id ON tasks(thread_id);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);",
]

# events 表 DDL
_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    task_seq        INTEGER NOT NULL,
    ts              TEXT NOT NULL,
    type            TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    actor           TEXT NOT NULL,
    payload         TEXT NOT NULL DEFAULT '{}',
    trace_id        TEXT NOT NULL DEFAULT '',
    span_id         TEXT NOT NULL DEFAULT '',
    parent_event_id TEXT,
    idempotency_key TEXT,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""

_EVENTS_INDEXES = [
    # 任务内事件序号唯一约束（确保 task_seq 严格单调递增）
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_task_seq ON events(task_id, task_seq);",
    # 任务内事件时间排序索引
    "CREATE INDEX IF NOT EXISTS idx_events_task_ts ON events(task_id, ts);",
    # 幂等键唯一约束（仅对非 NULL 值生效）
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_idempotency_key "
        "ON events(idempotency_key) WHERE idempotency_key IS NOT NULL;"
    ),
    # Feature 011: Watchdog 查询优化索引（支持 get_latest_event_ts 和 get_events_by_types_since）
    "CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(task_id, type, ts);",
]

# artifacts 表 DDL
_ARTIFACTS_DDL = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id  TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    ts           TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    parts        TEXT NOT NULL DEFAULT '[]',
    storage_ref  TEXT,
    size         INTEGER NOT NULL DEFAULT 0,
    hash         TEXT NOT NULL DEFAULT '',
    version      INTEGER NOT NULL DEFAULT 1,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""

_ARTIFACTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_artifacts_task_id ON artifacts(task_id);",
]

# task_jobs 表 DDL（后台任务可恢复执行）
_TASK_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS task_jobs (
    task_id      TEXT PRIMARY KEY,
    user_text    TEXT NOT NULL DEFAULT '',
    model_alias  TEXT,
    status       TEXT NOT NULL DEFAULT 'QUEUED',
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""

_TASK_JOBS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_task_jobs_status ON task_jobs(status);",
    "CREATE INDEX IF NOT EXISTS idx_task_jobs_updated_at ON task_jobs(updated_at DESC);",
]

# checkpoints 表 DDL（Feature 010）
_CHECKPOINTS_DDL = """
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id      TEXT PRIMARY KEY,
    task_id            TEXT NOT NULL,
    node_id            TEXT NOT NULL,
    status             TEXT NOT NULL,
    schema_version     INTEGER NOT NULL DEFAULT 1,
    state_snapshot     TEXT NOT NULL,
    side_effect_cursor TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""

_CHECKPOINTS_INDEXES = [
    (
        "CREATE INDEX IF NOT EXISTS idx_checkpoints_task_created "
        "ON checkpoints(task_id, created_at DESC);"
    ),
    ("CREATE INDEX IF NOT EXISTS idx_checkpoints_task_status ON checkpoints(task_id, status);"),
]

# side_effect_ledger 表 DDL（Feature 010）
_SIDE_EFFECT_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS side_effect_ledger (
    ledger_id        TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    step_key         TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    effect_type      TEXT NOT NULL,
    result_ref       TEXT,
    created_at       TEXT NOT NULL,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id),
    UNIQUE(task_id, step_key),
    UNIQUE(idempotency_key)
);
"""

_SIDE_EFFECT_LEDGER_INDEXES = [
    ("CREATE INDEX IF NOT EXISTS idx_side_effect_ledger_task_id ON side_effect_ledger(task_id);"),
]

# Feature 025: projects/workspaces/bindings/migration_runs
_PROJECTS_DDL = """
CREATE TABLE IF NOT EXISTS projects (
    project_id   TEXT PRIMARY KEY,
    slug         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',
    is_default   INTEGER NOT NULL DEFAULT 0,
    default_agent_profile_id TEXT NOT NULL DEFAULT '',
    metadata     TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    primary_agent_id TEXT NOT NULL DEFAULT ''
);
"""


_PROJECT_BINDINGS_DDL = """
CREATE TABLE IF NOT EXISTS project_bindings (
    binding_id        TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    binding_type      TEXT NOT NULL,
    binding_key       TEXT NOT NULL,
    binding_value     TEXT NOT NULL DEFAULT '',
    source            TEXT NOT NULL DEFAULT '',
    metadata          TEXT NOT NULL DEFAULT '{}',
    migration_run_id  TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,

    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
"""

_PROJECT_SECRET_BINDINGS_DDL = """
CREATE TABLE IF NOT EXISTS project_secret_bindings (
    binding_id         TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL,
    target_kind        TEXT NOT NULL,
    target_key         TEXT NOT NULL,
    env_name           TEXT NOT NULL,
    ref_source_type    TEXT NOT NULL,
    ref_locator        TEXT NOT NULL DEFAULT '{}',
    display_name       TEXT NOT NULL DEFAULT '',
    redaction_label    TEXT NOT NULL DEFAULT '***',
    status             TEXT NOT NULL DEFAULT 'draft',
    last_audited_at    TEXT,
    last_applied_at    TEXT,
    last_reloaded_at   TEXT,
    metadata           TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,

    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
"""

_PROJECT_SELECTOR_STATE_DDL = """
CREATE TABLE IF NOT EXISTS project_selector_state (
    selector_id         TEXT PRIMARY KEY,
    surface             TEXT NOT NULL UNIQUE,
    active_project_id   TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT '',
    warnings            TEXT NOT NULL DEFAULT '[]',
    updated_at          TEXT NOT NULL,

    FOREIGN KEY (active_project_id) REFERENCES projects(project_id)
);
"""

_PROJECT_MIGRATION_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS project_migration_runs (
    run_id          TEXT PRIMARY KEY,
    project_root    TEXT NOT NULL,
    status          TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    summary         TEXT NOT NULL DEFAULT '{}',
    validation      TEXT NOT NULL DEFAULT '{}',
    rollback_plan   TEXT NOT NULL DEFAULT '{}',
    error_message   TEXT NOT NULL DEFAULT ''
);
"""

# Feature 030: work / pipeline
_WORKS_DDL = """
CREATE TABLE IF NOT EXISTS works (
    work_id                 TEXT PRIMARY KEY,
    task_id                 TEXT NOT NULL,
    parent_work_id          TEXT,
    title                   TEXT NOT NULL DEFAULT '',
    kind                    TEXT NOT NULL DEFAULT 'delegation',
    status                  TEXT NOT NULL DEFAULT 'created',
    target_kind             TEXT NOT NULL DEFAULT 'worker',
    owner_id                TEXT NOT NULL DEFAULT '',
    requested_capability    TEXT NOT NULL DEFAULT '',
    selected_worker_type    TEXT NOT NULL DEFAULT 'general',
    route_reason            TEXT NOT NULL DEFAULT '',
    project_id              TEXT NOT NULL DEFAULT '',
    session_owner_profile_id TEXT NOT NULL DEFAULT '',
    inherited_context_owner_profile_id TEXT NOT NULL DEFAULT '',
    delegation_target_profile_id TEXT NOT NULL DEFAULT '',
    turn_executor_kind      TEXT NOT NULL DEFAULT 'worker',
    agent_profile_id        TEXT NOT NULL DEFAULT '',
    requested_worker_profile_id TEXT NOT NULL DEFAULT '',
    requested_worker_profile_version INTEGER NOT NULL DEFAULT 0,
    effective_worker_snapshot_id TEXT NOT NULL DEFAULT '',
    context_frame_id        TEXT NOT NULL DEFAULT '',
    tool_selection_id       TEXT NOT NULL DEFAULT '',
    selected_tools          TEXT NOT NULL DEFAULT '[]',
    pipeline_run_id         TEXT NOT NULL DEFAULT '',
    delegation_id           TEXT NOT NULL DEFAULT '',
    runtime_id              TEXT NOT NULL DEFAULT '',
    retry_count             INTEGER NOT NULL DEFAULT 0,
    escalation_count        INTEGER NOT NULL DEFAULT 0,
    metadata                TEXT NOT NULL DEFAULT '{}',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    completed_at            TEXT,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""

_AGENT_PROFILES_DDL = """
CREATE TABLE IF NOT EXISTS agent_profiles (
    profile_id              TEXT PRIMARY KEY,
    scope                   TEXT NOT NULL DEFAULT 'system',
    project_id              TEXT NOT NULL DEFAULT '',
    name                    TEXT NOT NULL,
    persona_summary         TEXT NOT NULL DEFAULT '',
    instruction_overlays    TEXT NOT NULL DEFAULT '[]',
    model_alias             TEXT NOT NULL DEFAULT 'main',
    tool_profile            TEXT NOT NULL DEFAULT 'standard',
    policy_refs             TEXT NOT NULL DEFAULT '[]',
    memory_access_policy    TEXT NOT NULL DEFAULT '{}',
    context_budget_policy   TEXT NOT NULL DEFAULT '{}',
    bootstrap_template_ids  TEXT NOT NULL DEFAULT '[]',
    metadata                TEXT NOT NULL DEFAULT '{}',
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
"""

_WORKER_PROFILES_DDL = """
CREATE TABLE IF NOT EXISTS worker_profiles (
    profile_id              TEXT PRIMARY KEY,
    scope                   TEXT NOT NULL DEFAULT 'project',
    project_id              TEXT NOT NULL DEFAULT '',
    name                    TEXT NOT NULL,
    summary                 TEXT NOT NULL DEFAULT '',
    model_alias             TEXT NOT NULL DEFAULT 'main',
    tool_profile            TEXT NOT NULL DEFAULT 'minimal',
    default_tool_groups     TEXT NOT NULL DEFAULT '[]',
    selected_tools          TEXT NOT NULL DEFAULT '[]',
    runtime_kinds           TEXT NOT NULL DEFAULT '[]',
    metadata                TEXT NOT NULL DEFAULT '{}',
    status                  TEXT NOT NULL DEFAULT 'draft',
    origin_kind             TEXT NOT NULL DEFAULT 'custom',
    draft_revision          INTEGER NOT NULL DEFAULT 0,
    active_revision         INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    archived_at             TEXT
);
"""

_WORKER_PROFILE_REVISIONS_DDL = """
CREATE TABLE IF NOT EXISTS worker_profile_revisions (
    revision_id             TEXT PRIMARY KEY,
    profile_id              TEXT NOT NULL,
    revision                INTEGER NOT NULL,
    change_summary          TEXT NOT NULL DEFAULT '',
    snapshot_payload        TEXT NOT NULL DEFAULT '{}',
    created_by              TEXT NOT NULL DEFAULT '',
    created_at              TEXT NOT NULL,

    FOREIGN KEY (profile_id) REFERENCES worker_profiles(profile_id),
    UNIQUE(profile_id, revision)
);
"""

_OWNER_PROFILES_DDL = """
CREATE TABLE IF NOT EXISTS owner_profiles (
    owner_profile_id              TEXT PRIMARY KEY,
    display_name                  TEXT NOT NULL DEFAULT 'Owner',
    preferred_address             TEXT NOT NULL DEFAULT '',
    timezone                      TEXT NOT NULL DEFAULT 'UTC',
    locale                        TEXT NOT NULL DEFAULT 'zh-CN',
    working_style                 TEXT NOT NULL DEFAULT '',
    interaction_preferences       TEXT NOT NULL DEFAULT '[]',
    boundary_notes                TEXT NOT NULL DEFAULT '[]',
    main_session_only_fields      TEXT NOT NULL DEFAULT '[]',
    metadata                      TEXT NOT NULL DEFAULT '{}',
    version                       INTEGER NOT NULL DEFAULT 1,
    last_synced_from_profile_at   TEXT,
    created_at                    TEXT NOT NULL,
    updated_at                    TEXT NOT NULL
);
"""
# Feature 082 P0：
# - preferred_address DEFAULT '你' → ''（伪默认值清理；Agent system prompt 层 fallback "Owner"）
# - 新增 last_synced_from_profile_at（P2 用：ProfileGenerator 回填时间戳追踪）
# - 历史已有 "你" 数据**不**在启动时静默清洗，留给 P4 `octo bootstrap migrate-082` 显式触发

_OWNER_PROFILE_OVERLAYS_DDL = """
CREATE TABLE IF NOT EXISTS owner_profile_overlays (
    owner_overlay_id                 TEXT PRIMARY KEY,
    owner_profile_id                 TEXT NOT NULL,
    scope                            TEXT NOT NULL DEFAULT 'project',
    project_id                       TEXT NOT NULL DEFAULT '',
    assistant_identity_overrides     TEXT NOT NULL DEFAULT '{}',
    working_style_override           TEXT NOT NULL DEFAULT '',
    interaction_preferences_override TEXT NOT NULL DEFAULT '[]',
    boundary_notes_override          TEXT NOT NULL DEFAULT '[]',
    bootstrap_template_ids           TEXT NOT NULL DEFAULT '[]',
    main_session_only_overrides      TEXT NOT NULL DEFAULT '[]',
    metadata                         TEXT NOT NULL DEFAULT '{}',
    version                          INTEGER NOT NULL DEFAULT 1,
    created_at                       TEXT NOT NULL,
    updated_at                       TEXT NOT NULL,

    FOREIGN KEY (owner_profile_id) REFERENCES owner_profiles(owner_profile_id)
);
"""

_BOOTSTRAP_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS bootstrap_sessions (
    bootstrap_id             TEXT PRIMARY KEY,
    project_id               TEXT NOT NULL DEFAULT '',
    owner_profile_id         TEXT NOT NULL DEFAULT '',
    owner_overlay_id         TEXT NOT NULL DEFAULT '',
    agent_profile_id         TEXT NOT NULL DEFAULT '',
    status                   TEXT NOT NULL DEFAULT 'pending',
    current_step             TEXT NOT NULL DEFAULT 'owner_basics',
    steps                    TEXT NOT NULL DEFAULT '[]',
    answers                  TEXT NOT NULL DEFAULT '{}',
    generated_profile_ids    TEXT NOT NULL DEFAULT '[]',
    generated_owner_revision INTEGER NOT NULL DEFAULT 0,
    blocking_reason          TEXT NOT NULL DEFAULT '',
    surface                  TEXT NOT NULL DEFAULT 'chat',
    metadata                 TEXT NOT NULL DEFAULT '{}',
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    completed_at             TEXT
);
"""

_AGENT_RUNTIMES_DDL = """
CREATE TABLE IF NOT EXISTS agent_runtimes (
    agent_runtime_id   TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL DEFAULT '',
    agent_profile_id   TEXT NOT NULL DEFAULT '',
    worker_profile_id  TEXT NOT NULL DEFAULT '',
    role               TEXT NOT NULL DEFAULT 'main',
    name               TEXT NOT NULL DEFAULT '',
    persona_summary    TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'active',
    permission_preset  TEXT NOT NULL DEFAULT 'normal',
    role_card          TEXT NOT NULL DEFAULT '',
    metadata           TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    archived_at        TEXT
);
"""

_AGENT_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    agent_session_id         TEXT PRIMARY KEY,
    agent_runtime_id         TEXT NOT NULL,
    kind                     TEXT NOT NULL DEFAULT 'main_bootstrap',
    status                   TEXT NOT NULL DEFAULT 'active',
    project_id               TEXT NOT NULL DEFAULT '',
    surface                  TEXT NOT NULL DEFAULT 'chat',
    thread_id                TEXT NOT NULL DEFAULT '',
    legacy_session_id        TEXT NOT NULL DEFAULT '',
    alias                    TEXT NOT NULL DEFAULT '',
    parent_agent_session_id  TEXT NOT NULL DEFAULT '',
    work_id                  TEXT NOT NULL DEFAULT '',
    a2a_conversation_id      TEXT NOT NULL DEFAULT '',
    last_context_frame_id    TEXT NOT NULL DEFAULT '',
    last_recall_frame_id     TEXT NOT NULL DEFAULT '',
    recent_transcript        TEXT NOT NULL DEFAULT '[]',
    rolling_summary          TEXT NOT NULL DEFAULT '',
    metadata                 TEXT NOT NULL DEFAULT '{}',
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    closed_at                TEXT,
    parent_worker_runtime_id TEXT NOT NULL DEFAULT '',
    memory_cursor_seq        INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (agent_runtime_id) REFERENCES agent_runtimes(agent_runtime_id)
);
"""

_AGENT_SESSION_TURNS_DDL = """
CREATE TABLE IF NOT EXISTS agent_session_turns (
    agent_session_turn_id    TEXT PRIMARY KEY,
    agent_session_id         TEXT NOT NULL,
    task_id                  TEXT NOT NULL DEFAULT '',
    turn_seq                 INTEGER NOT NULL DEFAULT 0,
    kind                     TEXT NOT NULL DEFAULT 'user_message',
    role                     TEXT NOT NULL DEFAULT '',
    tool_name                TEXT NOT NULL DEFAULT '',
    artifact_ref             TEXT NOT NULL DEFAULT '',
    summary                  TEXT NOT NULL DEFAULT '',
    dedupe_key               TEXT NOT NULL DEFAULT '',
    metadata                 TEXT NOT NULL DEFAULT '{}',
    created_at               TEXT NOT NULL,

    FOREIGN KEY (agent_session_id) REFERENCES agent_sessions(agent_session_id)
);
"""

_A2A_CONVERSATIONS_DDL = """
CREATE TABLE IF NOT EXISTS a2a_conversations (
    a2a_conversation_id      TEXT PRIMARY KEY,
    task_id                  TEXT NOT NULL DEFAULT '',
    work_id                  TEXT NOT NULL DEFAULT '',
    project_id               TEXT NOT NULL DEFAULT '',
    source_agent_runtime_id  TEXT NOT NULL DEFAULT '',
    source_agent_session_id  TEXT NOT NULL DEFAULT '',
    target_agent_runtime_id  TEXT NOT NULL DEFAULT '',
    target_agent_session_id  TEXT NOT NULL DEFAULT '',
    source_agent             TEXT NOT NULL DEFAULT '',
    target_agent             TEXT NOT NULL DEFAULT '',
    context_frame_id         TEXT NOT NULL DEFAULT '',
    request_message_id       TEXT NOT NULL DEFAULT '',
    latest_message_id        TEXT NOT NULL DEFAULT '',
    latest_message_type      TEXT NOT NULL DEFAULT '',
    status                   TEXT NOT NULL DEFAULT 'active',
    message_count            INTEGER NOT NULL DEFAULT 0,
    trace_id                 TEXT NOT NULL DEFAULT '',
    metadata                 TEXT NOT NULL DEFAULT '{}',
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    completed_at             TEXT,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""

_A2A_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS a2a_messages (
    a2a_message_id           TEXT PRIMARY KEY,
    a2a_conversation_id      TEXT NOT NULL,
    message_seq              INTEGER NOT NULL,
    task_id                  TEXT NOT NULL DEFAULT '',
    work_id                  TEXT NOT NULL DEFAULT '',
    project_id               TEXT NOT NULL DEFAULT '',
    source_agent_runtime_id  TEXT NOT NULL DEFAULT '',
    source_agent_session_id  TEXT NOT NULL DEFAULT '',
    target_agent_runtime_id  TEXT NOT NULL DEFAULT '',
    target_agent_session_id  TEXT NOT NULL DEFAULT '',
    direction                TEXT NOT NULL DEFAULT 'outbound',
    message_type             TEXT NOT NULL DEFAULT '',
    protocol_message_id      TEXT NOT NULL DEFAULT '',
    from_agent               TEXT NOT NULL DEFAULT '',
    to_agent                 TEXT NOT NULL DEFAULT '',
    idempotency_key          TEXT NOT NULL DEFAULT '',
    payload                  TEXT NOT NULL DEFAULT '{}',
    trace                    TEXT NOT NULL DEFAULT '{}',
    metadata                 TEXT NOT NULL DEFAULT '{}',
    raw_message              TEXT NOT NULL DEFAULT '{}',
    created_at               TEXT NOT NULL,

    FOREIGN KEY (a2a_conversation_id) REFERENCES a2a_conversations(a2a_conversation_id),
    UNIQUE(a2a_conversation_id, message_seq)
);
"""

_MEMORY_NAMESPACES_DDL = """
CREATE TABLE IF NOT EXISTS memory_namespaces (
    namespace_id       TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL DEFAULT '',
    agent_runtime_id   TEXT NOT NULL DEFAULT '',
    kind               TEXT NOT NULL DEFAULT 'project_shared',
    name               TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    memory_scope_ids   TEXT NOT NULL DEFAULT '[]',
    metadata           TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    archived_at        TEXT,

    FOREIGN KEY (agent_runtime_id) REFERENCES agent_runtimes(agent_runtime_id)
);
"""

_SESSION_CONTEXT_STATES_DDL = """
CREATE TABLE IF NOT EXISTS session_context_states (
    session_id            TEXT PRIMARY KEY,
    agent_runtime_id      TEXT NOT NULL DEFAULT '',
    agent_session_id      TEXT NOT NULL DEFAULT '',
    thread_id             TEXT NOT NULL DEFAULT '',
    project_id            TEXT NOT NULL DEFAULT '',
    task_ids              TEXT NOT NULL DEFAULT '[]',
    recent_turn_refs      TEXT NOT NULL DEFAULT '[]',
    recent_artifact_refs  TEXT NOT NULL DEFAULT '[]',
    rolling_summary       TEXT NOT NULL DEFAULT '',
    summary_artifact_id   TEXT NOT NULL DEFAULT '',
    last_context_frame_id TEXT NOT NULL DEFAULT '',
    last_recall_frame_id  TEXT NOT NULL DEFAULT '',
    updated_at            TEXT NOT NULL
);
"""

_CONTEXT_FRAMES_DDL = """
CREATE TABLE IF NOT EXISTS context_frames (
    context_frame_id       TEXT PRIMARY KEY,
    task_id                TEXT NOT NULL DEFAULT '',
    session_id             TEXT NOT NULL DEFAULT '',
    agent_runtime_id       TEXT NOT NULL DEFAULT '',
    agent_session_id       TEXT NOT NULL DEFAULT '',
    project_id             TEXT NOT NULL DEFAULT '',
    agent_profile_id       TEXT NOT NULL DEFAULT '',
    owner_profile_id       TEXT NOT NULL DEFAULT '',
    owner_overlay_id       TEXT NOT NULL DEFAULT '',
    owner_profile_revision INTEGER,
    bootstrap_session_id   TEXT,
    recall_frame_id        TEXT,
    system_blocks          TEXT NOT NULL DEFAULT '[]',
    recent_summary         TEXT NOT NULL DEFAULT '',
    memory_namespace_ids   TEXT NOT NULL DEFAULT '[]',
    memory_hits            TEXT NOT NULL DEFAULT '[]',
    delegation_context     TEXT NOT NULL DEFAULT '{}',
    budget                 TEXT NOT NULL DEFAULT '{}',
    degraded_reason        TEXT NOT NULL DEFAULT '',
    source_refs            TEXT NOT NULL DEFAULT '[]',
    created_at             TEXT NOT NULL
);
"""

_RECALL_FRAMES_DDL = """
CREATE TABLE IF NOT EXISTS recall_frames (
    recall_frame_id      TEXT PRIMARY KEY,
    agent_runtime_id     TEXT NOT NULL DEFAULT '',
    agent_session_id     TEXT NOT NULL DEFAULT '',
    context_frame_id     TEXT NOT NULL DEFAULT '',
    task_id              TEXT NOT NULL DEFAULT '',
    project_id           TEXT NOT NULL DEFAULT '',
    query                TEXT NOT NULL DEFAULT '',
    recent_summary       TEXT NOT NULL DEFAULT '',
    memory_namespace_ids TEXT NOT NULL DEFAULT '[]',
    memory_hits          TEXT NOT NULL DEFAULT '[]',
    source_refs          TEXT NOT NULL DEFAULT '[]',
    budget               TEXT NOT NULL DEFAULT '{}',
    degraded_reason      TEXT NOT NULL DEFAULT '',
    metadata             TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL
);
"""

_SKILL_PIPELINE_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS skill_pipeline_runs (
    run_id               TEXT PRIMARY KEY,
    pipeline_id          TEXT NOT NULL,
    task_id              TEXT NOT NULL,
    work_id              TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'created',
    current_node_id      TEXT NOT NULL DEFAULT '',
    pause_reason         TEXT NOT NULL DEFAULT '',
    retry_cursor         TEXT NOT NULL DEFAULT '{}',
    state_snapshot       TEXT NOT NULL DEFAULT '{}',
    input_request        TEXT NOT NULL DEFAULT '{}',
    approval_request     TEXT NOT NULL DEFAULT '{}',
    metadata             TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    completed_at         TEXT,

    FOREIGN KEY (task_id) REFERENCES tasks(task_id),
    FOREIGN KEY (work_id) REFERENCES works(work_id)
);
"""

_SKILL_PIPELINE_CHECKPOINTS_DDL = """
CREATE TABLE IF NOT EXISTS skill_pipeline_checkpoints (
    checkpoint_id       TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    task_id             TEXT NOT NULL,
    node_id             TEXT NOT NULL,
    status              TEXT NOT NULL,
    state_snapshot      TEXT NOT NULL DEFAULT '{}',
    side_effect_cursor  TEXT,
    replay_summary      TEXT NOT NULL DEFAULT '',
    retry_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,

    FOREIGN KEY (run_id) REFERENCES skill_pipeline_runs(run_id),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
"""

# Feature 084 Phase 2: snapshot_records 表（T019）
# 存储工具调用写入结果的摘要快照（TTL 30 天）
_SNAPSHOT_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS snapshot_records (
    id            TEXT PRIMARY KEY,
    tool_call_id  TEXT NOT NULL UNIQUE,
    result_summary TEXT NOT NULL,
    timestamp     TEXT NOT NULL,
    ttl_days      INTEGER NOT NULL DEFAULT 30,
    expires_at    TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_SNAPSHOT_RECORDS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_snapshot_records_expires_at ON snapshot_records(expires_at);",
]

# Feature 084 Phase 2: observation_candidates 表（T020）
# 存储 Observation Routine 产生的候选事实（待用户确认），TTL 30 天
_OBSERVATION_CANDIDATES_DDL = """
CREATE TABLE IF NOT EXISTS observation_candidates (
    id                TEXT PRIMARY KEY,
    fact_content      TEXT NOT NULL,
    fact_content_hash TEXT NOT NULL,
    category          TEXT,
    confidence        REAL,
    status            TEXT NOT NULL DEFAULT 'pending',
    source_turn_id    TEXT,
    edited            INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at        TEXT NOT NULL,
    promoted_at       TEXT,
    user_id           TEXT NOT NULL
);
"""

_OBSERVATION_CANDIDATES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_obs_candidates_status ON observation_candidates(status);",
    "CREATE INDEX IF NOT EXISTS idx_obs_candidates_expires_at ON observation_candidates(expires_at);",
    (
        "CREATE INDEX IF NOT EXISTS idx_obs_dedup "
        "ON observation_candidates(source_turn_id, fact_content_hash);"
    ),
]

# Feature 061: 审批覆盖持久化表
# 存储用户 "always" 授权决策，绑定到 Agent 实例
_APPROVAL_OVERRIDES_DDL = """
CREATE TABLE IF NOT EXISTS approval_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_runtime_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'always',
    created_at TEXT NOT NULL,
    UNIQUE(agent_runtime_id, tool_name)
);
"""

_APPROVAL_OVERRIDES_INDEXES = [
    # 按 Agent 实例查询索引
    (
        "CREATE INDEX IF NOT EXISTS idx_overrides_agent "
        "ON approval_overrides(agent_runtime_id);"
    ),
    # 按工具名查询索引（管理界面用）
    (
        "CREATE INDEX IF NOT EXISTS idx_overrides_tool "
        "ON approval_overrides(tool_name);"
    ),
]

_PROJECT_INDEXES = [
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_single_default "
        "ON projects(is_default) WHERE is_default = 1;"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_bindings_project_type_key "
        "ON project_bindings(project_id, binding_type, binding_key);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_project_bindings_run_id "
        "ON project_bindings(migration_run_id, created_at DESC);"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_secret_bindings_target "
        "ON project_secret_bindings(project_id, target_kind, target_key);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_project_secret_bindings_env_name "
        "ON project_secret_bindings(project_id, env_name);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_project_migration_runs_root_started "
        "ON project_migration_runs(project_root, started_at DESC);"
    ),
]

_WORK_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_works_task_created ON works(task_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_works_status_updated ON works(status, updated_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_works_parent_work ON works(parent_work_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_works_agent_profile ON works(agent_profile_id);",
    (
        "CREATE INDEX IF NOT EXISTS idx_works_requested_worker_profile "
        "ON works(requested_worker_profile_id, requested_worker_profile_version);"
    ),
    "CREATE INDEX IF NOT EXISTS idx_works_context_frame ON works(context_frame_id);",
    (
        "CREATE INDEX IF NOT EXISTS idx_skill_pipeline_runs_work_updated "
        "ON skill_pipeline_runs(work_id, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_skill_pipeline_runs_task_updated "
        "ON skill_pipeline_runs(task_id, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_skill_pipeline_checkpoints_run_created "
        "ON skill_pipeline_checkpoints(run_id, created_at ASC);"
    ),
]

_AGENT_CONTEXT_INDEXES = [
    (
        "CREATE INDEX IF NOT EXISTS idx_agent_profiles_scope_project "
        "ON agent_profiles(scope, project_id, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_agent_runtimes_role_project "
        "ON agent_runtimes(role, project_id, updated_at DESC);"
    ),
    # 逻辑身份单写约束（防并发 race 双写）：同一 (project, role, profile) 在 active
    # 状态下只能有一条 runtime row。worker 按 worker_profile_id 区分，main 按
    # agent_profile_id 区分；profile_id 为空时不参与（兼容历史脏数据）。
    # 排除 `subagent-%`：subagent 是 worker spawn 的独立 child runtime，与 parent
    # 共享 worker_profile_id 是合法语义（subagent_lifecycle.spawn_subagent 已经
    # 用 `subagent-{ULID}` 作为 PK 前缀来区分）。
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runtimes_active_worker_unique "
        "ON agent_runtimes(project_id, worker_profile_id) "
        "WHERE status = 'active' AND role = 'worker' "
        "AND worker_profile_id != '' AND agent_runtime_id NOT LIKE 'subagent-%';"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runtimes_active_main_unique "
        "ON agent_runtimes(project_id, agent_profile_id) "
        "WHERE status = 'active' AND role = 'main' "
        "AND agent_profile_id != '' AND agent_runtime_id NOT LIKE 'subagent-%';"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_agent_sessions_runtime_updated "
        "ON agent_sessions(agent_runtime_id, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_agent_sessions_legacy "
        "ON agent_sessions(legacy_session_id, updated_at DESC);"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_sessions_project_active "
        "ON agent_sessions(project_id) "
        "WHERE status = 'active' AND project_id != '' AND kind = 'main_bootstrap';"
    ),
    # DIRECT_WORKER 同样要求 project 内唯一 active session（与 main_bootstrap 对齐）。
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_sessions_direct_worker_active "
        "ON agent_sessions(project_id) "
        "WHERE status = 'active' AND project_id != '' AND kind = 'direct_worker';"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_agent_sessions_parent_worker "
        "ON agent_sessions(parent_worker_runtime_id, status) "
        "WHERE parent_worker_runtime_id != '';"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_agent_session_turns_session_seq "
        "ON agent_session_turns(agent_session_id, turn_seq ASC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_agent_session_turns_task_created "
        "ON agent_session_turns(task_id, created_at DESC);"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_session_turns_dedupe "
        "ON agent_session_turns(agent_session_id, dedupe_key) "
        "WHERE dedupe_key != '';"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_a2a_conversations_work_updated "
        "ON a2a_conversations(work_id, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_a2a_conversations_project_updated "
        "ON a2a_conversations(project_id, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_a2a_messages_conversation_seq "
        "ON a2a_messages(a2a_conversation_id, message_seq ASC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_a2a_messages_task_created "
        "ON a2a_messages(task_id, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_a2a_messages_work_created "
        "ON a2a_messages(work_id, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_namespaces_project_kind "
        "ON memory_namespaces(project_id, kind, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_namespaces_runtime_kind "
        "ON memory_namespaces(agent_runtime_id, kind, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_worker_profiles_scope_project "
        "ON worker_profiles(scope, project_id, status, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_worker_profile_revisions_profile_created "
        "ON worker_profile_revisions(profile_id, revision DESC, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_owner_profile_overlays_scope "
        "ON owner_profile_overlays(scope, project_id, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_session_context_states_thread "
        "ON session_context_states(thread_id, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_session_context_states_runtime "
        "ON session_context_states(agent_runtime_id, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_session_context_states_project "
        "ON session_context_states(project_id, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_context_frames_session_created "
        "ON context_frames(session_id, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_context_frames_agent_session_created "
        "ON context_frames(agent_session_id, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_context_frames_task_created "
        "ON context_frames(task_id, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_context_frames_project_created "
        "ON context_frames(project_id, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_recall_frames_agent_session_created "
        "ON recall_frames(agent_session_id, created_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_recall_frames_context_created "
        "ON recall_frames(context_frame_id, created_at DESC);"
    ),
]


async def _table_columns(conn: aiosqlite.Connection, table_name: str) -> set[str]:
    """读取表列名集合。"""
    cursor = await conn.execute(f"PRAGMA table_info({table_name})")
    rows = await cursor.fetchall()
    return {str(row[1]) for row in rows}


async def _migrate_legacy_tables(conn: aiosqlite.Connection) -> None:
    """对已有旧表执行最小 schema 迁移。"""
    task_columns = await _table_columns(conn, "tasks")
    if task_columns and "trace_id" not in task_columns:
        await conn.execute("ALTER TABLE tasks ADD COLUMN trace_id TEXT NOT NULL DEFAULT ''")

    # Feature 064: Subagent Child Task 的 parent_task_id 字段
    if task_columns and "parent_task_id" not in task_columns:
        await conn.execute("ALTER TABLE tasks ADD COLUMN parent_task_id TEXT DEFAULT NULL")
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_parent_task_id ON tasks(parent_task_id)"
        )

    project_columns = await _table_columns(conn, "projects")
    if project_columns and "default_agent_profile_id" not in project_columns:
        await conn.execute(
            "ALTER TABLE projects ADD COLUMN default_agent_profile_id TEXT NOT NULL DEFAULT ''"
        )
    if project_columns and "primary_agent_id" not in project_columns:
        await conn.execute(
            "ALTER TABLE projects ADD COLUMN primary_agent_id TEXT NOT NULL DEFAULT ''"
        )

    work_columns = await _table_columns(conn, "works")
    if work_columns and "agent_profile_id" not in work_columns:
        await conn.execute(
            "ALTER TABLE works ADD COLUMN agent_profile_id TEXT NOT NULL DEFAULT ''"
        )
    if work_columns and "session_owner_profile_id" not in work_columns:
        await conn.execute(
            "ALTER TABLE works ADD COLUMN session_owner_profile_id TEXT NOT NULL DEFAULT ''"
        )
    if work_columns and "inherited_context_owner_profile_id" not in work_columns:
        await conn.execute(
            "ALTER TABLE works "
            "ADD COLUMN inherited_context_owner_profile_id TEXT NOT NULL DEFAULT ''"
        )
    if work_columns and "delegation_target_profile_id" not in work_columns:
        await conn.execute(
            "ALTER TABLE works ADD COLUMN delegation_target_profile_id TEXT NOT NULL DEFAULT ''"
        )
    if work_columns and "turn_executor_kind" not in work_columns:
        await conn.execute(
            "ALTER TABLE works ADD COLUMN turn_executor_kind TEXT NOT NULL DEFAULT 'worker'"
        )
    if work_columns and "requested_worker_profile_id" not in work_columns:
        await conn.execute(
            "ALTER TABLE works ADD COLUMN requested_worker_profile_id TEXT NOT NULL DEFAULT ''"
        )
    if work_columns and "requested_worker_profile_version" not in work_columns:
        await conn.execute(
            "ALTER TABLE works "
            "ADD COLUMN requested_worker_profile_version INTEGER NOT NULL DEFAULT 0"
        )
    if work_columns and "effective_worker_snapshot_id" not in work_columns:
        await conn.execute(
            "ALTER TABLE works ADD COLUMN effective_worker_snapshot_id TEXT NOT NULL DEFAULT ''"
        )
    if work_columns and "context_frame_id" not in work_columns:
        await conn.execute(
            "ALTER TABLE works ADD COLUMN context_frame_id TEXT NOT NULL DEFAULT ''"
        )

    # Feature 082 P0：owner_profiles 加 last_synced_from_profile_at 列（P2 用）
    owner_profile_columns = await _table_columns(conn, "owner_profiles")
    if owner_profile_columns and "last_synced_from_profile_at" not in owner_profile_columns:
        await conn.execute(
            "ALTER TABLE owner_profiles ADD COLUMN last_synced_from_profile_at TEXT"
        )

    session_context_columns = await _table_columns(conn, "session_context_states")
    if session_context_columns and "agent_runtime_id" not in session_context_columns:
        await conn.execute(
            "ALTER TABLE session_context_states "
            "ADD COLUMN agent_runtime_id TEXT NOT NULL DEFAULT ''"
        )
    if session_context_columns and "agent_session_id" not in session_context_columns:
        await conn.execute(
            "ALTER TABLE session_context_states "
            "ADD COLUMN agent_session_id TEXT NOT NULL DEFAULT ''"
        )
    if session_context_columns and "last_recall_frame_id" not in session_context_columns:
        await conn.execute(
            "ALTER TABLE session_context_states "
            "ADD COLUMN last_recall_frame_id TEXT NOT NULL DEFAULT ''"
        )

    agent_session_columns = await _table_columns(conn, "agent_sessions")
    if agent_session_columns and "recent_transcript" not in agent_session_columns:
        await conn.execute(
            "ALTER TABLE agent_sessions "
            "ADD COLUMN recent_transcript TEXT NOT NULL DEFAULT '[]'"
        )
    if agent_session_columns and "rolling_summary" not in agent_session_columns:
        await conn.execute(
            "ALTER TABLE agent_sessions "
            "ADD COLUMN rolling_summary TEXT NOT NULL DEFAULT ''"
        )
    if agent_session_columns and "alias" not in agent_session_columns:
        await conn.execute(
            "ALTER TABLE agent_sessions "
            "ADD COLUMN alias TEXT NOT NULL DEFAULT ''"
        )
    if agent_session_columns and "parent_worker_runtime_id" not in agent_session_columns:
        await conn.execute(
            "ALTER TABLE agent_sessions "
            "ADD COLUMN parent_worker_runtime_id TEXT NOT NULL DEFAULT ''"
        )
    # Feature 067: 记忆提取游标
    if agent_session_columns and "memory_cursor_seq" not in agent_session_columns:
        await conn.execute(
            "ALTER TABLE agent_sessions "
            "ADD COLUMN memory_cursor_seq INTEGER NOT NULL DEFAULT 0"
        )

    agent_session_turn_columns = await _table_columns(conn, "agent_session_turns")
    if not agent_session_turn_columns:
        await conn.execute(_AGENT_SESSION_TURNS_DDL)

    context_frame_columns = await _table_columns(conn, "context_frames")
    if context_frame_columns and "agent_runtime_id" not in context_frame_columns:
        await conn.execute(
            "ALTER TABLE context_frames ADD COLUMN agent_runtime_id TEXT NOT NULL DEFAULT ''"
        )
    if context_frame_columns and "agent_session_id" not in context_frame_columns:
        await conn.execute(
            "ALTER TABLE context_frames ADD COLUMN agent_session_id TEXT NOT NULL DEFAULT ''"
        )
    if context_frame_columns and "recall_frame_id" not in context_frame_columns:
        await conn.execute(
            "ALTER TABLE context_frames ADD COLUMN recall_frame_id TEXT"
        )
    if context_frame_columns and "memory_namespace_ids" not in context_frame_columns:
        await conn.execute(
            "ALTER TABLE context_frames "
            "ADD COLUMN memory_namespace_ids TEXT NOT NULL DEFAULT '[]'"
        )

    # Feature 062: 资源限制字段迁移
    agent_profile_columns = await _table_columns(conn, "agent_profiles")
    if agent_profile_columns and "resource_limits" not in agent_profile_columns:
        await conn.execute(
            "ALTER TABLE agent_profiles ADD COLUMN resource_limits TEXT NOT NULL DEFAULT '{}'"
        )

    worker_profile_columns = await _table_columns(conn, "worker_profiles")
    if worker_profile_columns and "resource_limits" not in worker_profile_columns:
        await conn.execute(
            "ALTER TABLE worker_profiles ADD COLUMN resource_limits TEXT NOT NULL DEFAULT '{}'"
        )

    # Feature 061: agent_runtimes 新增 permission_preset 和 role_card 列
    agent_runtime_columns = await _table_columns(conn, "agent_runtimes")
    if agent_runtime_columns and "permission_preset" not in agent_runtime_columns:
        await conn.execute(
            "ALTER TABLE agent_runtimes "
            "ADD COLUMN permission_preset TEXT NOT NULL DEFAULT 'normal'"
        )
    if agent_runtime_columns and "role_card" not in agent_runtime_columns:
        await conn.execute(
            "ALTER TABLE agent_runtimes "
            "ADD COLUMN role_card TEXT NOT NULL DEFAULT ''"
        )

    # 修正 agent_sessions 的 UNIQUE 索引：只对 main_bootstrap 生效，
    # worker_internal / subagent_internal sessions 不受一个 project 一个 session 的限制。
    if agent_session_columns:
        try:
            await conn.execute(
                "DROP INDEX IF EXISTS idx_agent_sessions_project_active"
            )
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_sessions_project_active "
                "ON agent_sessions(project_id) "
                "WHERE status = 'active' AND project_id != '' AND kind = 'main_bootstrap'"
            )
        except Exception:
            pass  # 旧索引可能已被 CREATE TABLE 阶段正确创建

    # 历史双写清理：把 composite-key 的 agent_runtimes / agent_sessions row 合并到
    # ULID canonical row（如没有就地 rename 成新 ULID），消除侧栏会话重复、删除后残留等问题。
    if agent_runtime_columns and agent_session_columns:
        await _merge_composite_agent_identity_rows(conn)

    # F084 Phase 4 T068：DROP bootstrap_sessions 表（bootstrap_session 状态机已退役）
    # bootstrap 完成状态由 owner_profile.bootstrap_completed + USER.md 实质填充判断替代。
    await conn.execute("DROP TABLE IF EXISTS bootstrap_sessions")
    await conn.execute("DROP INDEX IF EXISTS idx_bootstrap_sessions_owner")
    await conn.execute("DROP INDEX IF EXISTS idx_bootstrap_sessions_scope")


_COMPOSITE_RUNTIME_ID_PATTERN = "role:%"
_COMPOSITE_SESSION_ID_PATTERN = "runtime:%"

# (table_name, column_name) pairs storing agent_runtime_id values
_RUNTIME_FOREIGN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("agent_sessions", "agent_runtime_id"),
    ("agent_sessions", "parent_worker_runtime_id"),
    ("memory_namespaces", "agent_runtime_id"),
    ("recall_frames", "agent_runtime_id"),
    ("context_frames", "agent_runtime_id"),
    ("session_context_states", "agent_runtime_id"),
    ("a2a_conversations", "source_agent_runtime_id"),
    ("a2a_conversations", "target_agent_runtime_id"),
    ("a2a_messages", "source_agent_runtime_id"),
    ("a2a_messages", "target_agent_runtime_id"),
    ("projects", "primary_agent_id"),
    # 用户「always」授权按 agent_runtime_id 精确查询（Feature 061），
    # 迁移时漏改这里会导致 runtime rename / merge 后历史授权静默失联。
    ("approval_overrides", "agent_runtime_id"),
)

# (table_name, column_name) pairs storing agent_session_id values
_SESSION_FOREIGN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("agent_session_turns", "agent_session_id"),
    ("recall_frames", "agent_session_id"),
    ("context_frames", "agent_session_id"),
    ("session_context_states", "agent_session_id"),
    ("a2a_conversations", "source_agent_session_id"),
    ("a2a_conversations", "target_agent_session_id"),
    ("a2a_messages", "source_agent_session_id"),
    ("a2a_messages", "target_agent_session_id"),
    ("agent_sessions", "parent_agent_session_id"),
)


async def _merge_composite_agent_identity_rows(conn: aiosqlite.Connection) -> None:
    """合并历史 composite-key agent_runtimes / agent_sessions 到 ULID canonical。

    执行流程（整体在 FK 临时关闭 + 单事务下完成）：
    1. 扫 composite runtime（`agent_runtime_id LIKE 'role:%'`）。
    2. 按 (project_id, role, worker_profile_id / agent_profile_id) 找 canonical ULID。
       - 有 canonical：把所有外键列从 composite 改指向 canonical，删 composite。
       - 无 canonical：就地 rename 到 `runtime-{ULID}`（同步 UPDATE 所有外键列）。
    3. 对 agent_sessions 同样处理（`agent_session_id LIKE 'runtime:%'`），
       canonical 按 (project_id, kind, status=active) 优先；dedupe_key 冲突时
       先删 composite 侧重复 turn 再迁移。
    4. 收尾：对每个逻辑身份分组只保留最新的 1 条 active，其余 archive，让后续
       partial unique index 能创建（含历史脏数据兼容）。
    """
    cursor = await conn.execute("PRAGMA foreign_keys")
    row = await cursor.fetchone()
    fk_was_on = bool(row and int(row[0]) == 1)
    if fk_was_on:
        await conn.execute("PRAGMA foreign_keys = OFF")
    try:
        await _merge_composite_runtimes(conn)
        await _merge_composite_sessions(conn)
        await _archive_extra_active_rows(conn)
    finally:
        if fk_was_on:
            await conn.execute("PRAGMA foreign_keys = ON")


async def _archive_extra_active_rows(conn: aiosqlite.Connection) -> None:
    """对违反单写约束的历史脏数据做 archive，让 partial unique index 能创建。

    保留每组最新一条 active：
    - agent_runtimes by (project_id, role='worker', worker_profile_id)
    - agent_runtimes by (project_id, role='main', agent_profile_id)
    - agent_sessions by (project_id, kind='direct_worker')
    """
    now_iso = datetime.now(UTC).isoformat()
    # 排除 subagent runtime：与 parent 同 worker_profile_id 是合法的，不参与单写约束。
    await conn.execute(
        """
        UPDATE agent_runtimes
        SET status = 'archived', archived_at = ?, updated_at = ?
        WHERE status = 'active'
          AND role = 'worker'
          AND worker_profile_id != ''
          AND agent_runtime_id NOT LIKE 'subagent-%'
          AND agent_runtime_id NOT IN (
              SELECT agent_runtime_id FROM (
                  SELECT agent_runtime_id,
                         ROW_NUMBER() OVER (
                             PARTITION BY project_id, worker_profile_id
                             ORDER BY updated_at DESC, created_at DESC
                         ) AS rn
                  FROM agent_runtimes
                  WHERE status = 'active' AND role = 'worker'
                    AND worker_profile_id != ''
                    AND agent_runtime_id NOT LIKE 'subagent-%'
              ) WHERE rn = 1
          )
        """,
        (now_iso, now_iso),
    )
    await conn.execute(
        """
        UPDATE agent_runtimes
        SET status = 'archived', archived_at = ?, updated_at = ?
        WHERE status = 'active'
          AND role = 'main'
          AND agent_profile_id != ''
          AND agent_runtime_id NOT LIKE 'subagent-%'
          AND agent_runtime_id NOT IN (
              SELECT agent_runtime_id FROM (
                  SELECT agent_runtime_id,
                         ROW_NUMBER() OVER (
                             PARTITION BY project_id, agent_profile_id
                             ORDER BY updated_at DESC, created_at DESC
                         ) AS rn
                  FROM agent_runtimes
                  WHERE status = 'active' AND role = 'main'
                    AND agent_profile_id != ''
                    AND agent_runtime_id NOT LIKE 'subagent-%'
              ) WHERE rn = 1
          )
        """,
        (now_iso, now_iso),
    )
    await conn.execute(
        """
        UPDATE agent_sessions
        SET status = 'closed', closed_at = ?, updated_at = ?
        WHERE status = 'active'
          AND kind = 'direct_worker'
          AND project_id != ''
          AND agent_session_id NOT IN (
              SELECT agent_session_id FROM (
                  SELECT agent_session_id,
                         ROW_NUMBER() OVER (
                             PARTITION BY project_id
                             ORDER BY updated_at DESC, created_at DESC
                         ) AS rn
                  FROM agent_sessions
                  WHERE status = 'active' AND kind = 'direct_worker' AND project_id != ''
              ) WHERE rn = 1
          )
        """,
        (now_iso, now_iso),
    )


async def _fetchall(conn: aiosqlite.Connection, sql: str, args: tuple = ()) -> list:
    cursor = await conn.execute(sql, args)
    return list(await cursor.fetchall())


async def _update_runtime_id_references(
    conn: aiosqlite.Connection, *, old_id: str, new_id: str
) -> None:
    existing_tables = await _fetchall(
        conn, "SELECT name FROM sqlite_master WHERE type='table'"
    )
    table_names = {str(r[0]) for r in existing_tables}
    for table, column in _RUNTIME_FOREIGN_COLUMNS:
        if table not in table_names:
            continue
        cols = await _table_columns(conn, table)
        if column not in cols:
            continue
        if table == "approval_overrides" and column == "agent_runtime_id":
            # UNIQUE(agent_runtime_id, tool_name)：合并到 canonical 时若 tool_name
            # 已经在 canonical 上存在，先删 composite 侧重复 row 避免 UNIQUE 冲突
            # （canonical 上的 always 授权权威优先）。
            await conn.execute(
                """
                DELETE FROM approval_overrides
                WHERE agent_runtime_id = ?
                  AND tool_name IN (
                      SELECT tool_name FROM approval_overrides
                      WHERE agent_runtime_id = ?
                  )
                """,
                (old_id, new_id),
            )
        await conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
            (new_id, old_id),
        )


async def _update_session_id_references(
    conn: aiosqlite.Connection, *, old_id: str, new_id: str
) -> None:
    existing_tables = await _fetchall(
        conn, "SELECT name FROM sqlite_master WHERE type='table'"
    )
    table_names = {str(r[0]) for r in existing_tables}
    for table, column in _SESSION_FOREIGN_COLUMNS:
        if table not in table_names:
            continue
        cols = await _table_columns(conn, table)
        if column not in cols:
            continue
        if table == "agent_session_turns" and column == "agent_session_id":
            # dedupe_key 唯一索引 (agent_session_id, dedupe_key)：冲突时丢弃 composite turn
            await conn.execute(
                """
                DELETE FROM agent_session_turns
                WHERE agent_session_id = ?
                  AND dedupe_key != ''
                  AND dedupe_key IN (
                      SELECT dedupe_key FROM agent_session_turns
                      WHERE agent_session_id = ? AND dedupe_key != ''
                  )
                """,
                (old_id, new_id),
            )
        await conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
            (new_id, old_id),
        )


async def _merge_composite_runtimes(conn: aiosqlite.Connection) -> None:
    rows = await _fetchall(
        conn,
        """
        SELECT agent_runtime_id, project_id, role,
               agent_profile_id, worker_profile_id
        FROM agent_runtimes
        WHERE agent_runtime_id LIKE ?
        """,
        (_COMPOSITE_RUNTIME_ID_PATTERN,),
    )
    for composite_id, project_id, role, agent_profile_id, worker_profile_id in rows:
        composite_id = str(composite_id)
        role = str(role or "").strip() or "main"
        project_id = str(project_id or "")
        agent_profile_id = str(agent_profile_id or "")
        worker_profile_id = str(worker_profile_id or "")
        # 找 canonical ULID runtime
        if role == "worker" and worker_profile_id:
            canonical_row = await _fetchall(
                conn,
                """
                SELECT agent_runtime_id FROM agent_runtimes
                WHERE agent_runtime_id LIKE 'runtime-%'
                  AND project_id = ? AND role = ? AND worker_profile_id = ?
                ORDER BY updated_at DESC, created_at DESC LIMIT 1
                """,
                (project_id, role, worker_profile_id),
            )
        elif role != "worker" and agent_profile_id:
            canonical_row = await _fetchall(
                conn,
                """
                SELECT agent_runtime_id FROM agent_runtimes
                WHERE agent_runtime_id LIKE 'runtime-%'
                  AND project_id = ? AND role = ? AND agent_profile_id = ?
                ORDER BY updated_at DESC, created_at DESC LIMIT 1
                """,
                (project_id, role, agent_profile_id),
            )
        else:
            canonical_row = await _fetchall(
                conn,
                """
                SELECT agent_runtime_id FROM agent_runtimes
                WHERE agent_runtime_id LIKE 'runtime-%'
                  AND project_id = ? AND role = ?
                ORDER BY updated_at DESC, created_at DESC LIMIT 1
                """,
                (project_id, role),
            )
        if canonical_row:
            canonical_id = str(canonical_row[0][0])
            await _update_runtime_id_references(
                conn, old_id=composite_id, new_id=canonical_id
            )
            await conn.execute(
                "DELETE FROM agent_runtimes WHERE agent_runtime_id = ?",
                (composite_id,),
            )
        else:
            new_id = f"runtime-{ULID()}"
            await _update_runtime_id_references(
                conn, old_id=composite_id, new_id=new_id
            )
            await conn.execute(
                "UPDATE agent_runtimes SET agent_runtime_id = ? WHERE agent_runtime_id = ?",
                (new_id, composite_id),
            )


async def _merge_composite_sessions(conn: aiosqlite.Connection) -> None:
    rows = await _fetchall(
        conn,
        """
        SELECT agent_session_id, project_id, kind, agent_runtime_id,
               thread_id, legacy_session_id
        FROM agent_sessions
        WHERE agent_session_id LIKE ?
        """,
        (_COMPOSITE_SESSION_ID_PATTERN,),
    )
    for (
        composite_id,
        project_id,
        kind,
        agent_runtime_id,
        thread_id,
        legacy_session_id,
    ) in rows:
        composite_id = str(composite_id)
        project_id = str(project_id or "")
        kind = str(kind or "").strip() or "direct_worker"
        # 找 canonical ULID session
        canonical_row: list = []
        if kind in ("direct_worker", "main_bootstrap") and project_id:
            canonical_row = await _fetchall(
                conn,
                """
                SELECT agent_session_id FROM agent_sessions
                WHERE agent_session_id LIKE 'session-%'
                  AND project_id = ? AND kind = ?
                ORDER BY
                    CASE status WHEN 'active' THEN 0 ELSE 1 END,
                    updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (project_id, kind),
            )
        elif kind == "worker_internal" and agent_runtime_id:
            canonical_row = await _fetchall(
                conn,
                """
                SELECT agent_session_id FROM agent_sessions
                WHERE agent_session_id LIKE 'session-%'
                  AND agent_runtime_id = ? AND kind = ?
                  AND (legacy_session_id = ? OR thread_id = ?)
                ORDER BY
                    CASE status WHEN 'active' THEN 0 ELSE 1 END,
                    updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (
                    str(agent_runtime_id),
                    kind,
                    str(legacy_session_id or ""),
                    str(thread_id or ""),
                ),
            )
        if canonical_row:
            canonical_id = str(canonical_row[0][0])
            await _update_session_id_references(
                conn, old_id=composite_id, new_id=canonical_id
            )
            await conn.execute(
                "DELETE FROM agent_sessions WHERE agent_session_id = ?",
                (composite_id,),
            )
        else:
            new_id = f"session-{ULID()}"
            await _update_session_id_references(
                conn, old_id=composite_id, new_id=new_id
            )
            await conn.execute(
                "UPDATE agent_sessions SET agent_session_id = ? WHERE agent_session_id = ?",
                (new_id, composite_id),
            )


async def init_db(conn: aiosqlite.Connection) -> None:
    """初始化数据库：设置 PRAGMA + 创建表 + 创建索引

    Args:
        conn: aiosqlite 数据库连接
    """
    # 设置 PRAGMA
    await conn.execute("PRAGMA journal_mode = WAL;")
    await conn.execute("PRAGMA foreign_keys = ON;")
    await conn.execute("PRAGMA busy_timeout = 5000;")

    # 创建表
    await conn.execute(_TASKS_DDL)
    await conn.execute(_EVENTS_DDL)
    await conn.execute(_ARTIFACTS_DDL)
    await conn.execute(_TASK_JOBS_DDL)
    await conn.execute(_CHECKPOINTS_DDL)
    await conn.execute(_SIDE_EFFECT_LEDGER_DDL)
    await conn.execute(_PROJECTS_DDL)
    await conn.execute(_PROJECT_BINDINGS_DDL)
    await conn.execute(_PROJECT_SECRET_BINDINGS_DDL)
    await conn.execute(_PROJECT_SELECTOR_STATE_DDL)
    await conn.execute(_PROJECT_MIGRATION_RUNS_DDL)
    await conn.execute(_WORKS_DDL)
    await conn.execute(_AGENT_PROFILES_DDL)
    await conn.execute(_WORKER_PROFILES_DDL)
    await conn.execute(_WORKER_PROFILE_REVISIONS_DDL)
    await conn.execute(_OWNER_PROFILES_DDL)
    await conn.execute(_OWNER_PROFILE_OVERLAYS_DDL)
    # F084 Phase 4 T068：不再 CREATE bootstrap_sessions（已退役，_migrate_legacy_tables 会 DROP 旧表）
    await conn.execute(_AGENT_RUNTIMES_DDL)
    await conn.execute(_AGENT_SESSIONS_DDL)
    await conn.execute(_AGENT_SESSION_TURNS_DDL)
    await conn.execute(_A2A_CONVERSATIONS_DDL)
    await conn.execute(_A2A_MESSAGES_DDL)
    await conn.execute(_MEMORY_NAMESPACES_DDL)
    await conn.execute(_SESSION_CONTEXT_STATES_DDL)
    await conn.execute(_CONTEXT_FRAMES_DDL)
    await conn.execute(_RECALL_FRAMES_DDL)
    await conn.execute(_SKILL_PIPELINE_RUNS_DDL)
    await conn.execute(_SKILL_PIPELINE_CHECKPOINTS_DDL)
    await conn.execute(_APPROVAL_OVERRIDES_DDL)
    # Feature 084 Phase 2: 新增 snapshot_records + observation_candidates 表（T019/T020）
    await conn.execute(_SNAPSHOT_RECORDS_DDL)
    await conn.execute(_OBSERVATION_CANDIDATES_DDL)
    await _migrate_legacy_tables(conn)

    # 创建索引
    for idx_sql in (
        _TASKS_INDEXES
        + _EVENTS_INDEXES
        + _ARTIFACTS_INDEXES
        + _TASK_JOBS_INDEXES
        + _CHECKPOINTS_INDEXES
        + _SIDE_EFFECT_LEDGER_INDEXES
        + _PROJECT_INDEXES
        + _WORK_INDEXES
        + _AGENT_CONTEXT_INDEXES
        + _APPROVAL_OVERRIDES_INDEXES
        + _SNAPSHOT_RECORDS_INDEXES
        + _OBSERVATION_CANDIDATES_INDEXES
    ):
        await conn.execute(idx_sql)

    await conn.commit()


async def verify_wal_mode(conn: aiosqlite.Connection) -> bool:
    """验证 WAL 模式是否生效

    Returns:
        True 如果 WAL 模式已启用
    """
    cursor = await conn.execute("PRAGMA journal_mode;")
    row = await cursor.fetchone()
    return row is not None and row[0].lower() == "wal"
