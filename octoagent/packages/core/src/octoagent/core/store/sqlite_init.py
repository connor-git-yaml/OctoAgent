"""SQLite 数据库初始化 -- 对齐 data-model.md §2

PRAGMA 配置 + 三张表 DDL + 索引创建。
使用 aiosqlite 异步操作。
"""

import aiosqlite

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

_WORKSPACES_DDL = """
CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    slug         TEXT NOT NULL,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'primary',
    root_path    TEXT NOT NULL DEFAULT '',
    metadata     TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,

    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
"""

_PROJECT_BINDINGS_DDL = """
CREATE TABLE IF NOT EXISTS project_bindings (
    binding_id        TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL,
    workspace_id      TEXT,
    binding_type      TEXT NOT NULL,
    binding_key       TEXT NOT NULL,
    binding_value     TEXT NOT NULL DEFAULT '',
    source            TEXT NOT NULL DEFAULT '',
    metadata          TEXT NOT NULL DEFAULT '{}',
    migration_run_id  TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,

    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
);
"""

_PROJECT_SECRET_BINDINGS_DDL = """
CREATE TABLE IF NOT EXISTS project_secret_bindings (
    binding_id         TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL,
    workspace_id       TEXT,
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

    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
);
"""

_PROJECT_SELECTOR_STATE_DDL = """
CREATE TABLE IF NOT EXISTS project_selector_state (
    selector_id         TEXT PRIMARY KEY,
    surface             TEXT NOT NULL UNIQUE,
    active_project_id   TEXT NOT NULL,
    active_workspace_id TEXT,
    source              TEXT NOT NULL DEFAULT '',
    warnings            TEXT NOT NULL DEFAULT '[]',
    updated_at          TEXT NOT NULL,

    FOREIGN KEY (active_project_id) REFERENCES projects(project_id),
    FOREIGN KEY (active_workspace_id) REFERENCES workspaces(workspace_id)
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
    workspace_id            TEXT NOT NULL DEFAULT '',
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
    base_archetype          TEXT NOT NULL DEFAULT 'general',
    instruction_overlays    TEXT NOT NULL DEFAULT '[]',
    model_alias             TEXT NOT NULL DEFAULT 'main',
    tool_profile            TEXT NOT NULL DEFAULT 'minimal',
    default_tool_groups     TEXT NOT NULL DEFAULT '[]',
    selected_tools          TEXT NOT NULL DEFAULT '[]',
    runtime_kinds           TEXT NOT NULL DEFAULT '[]',
    policy_refs             TEXT NOT NULL DEFAULT '[]',
    tags                    TEXT NOT NULL DEFAULT '[]',
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
    owner_profile_id         TEXT PRIMARY KEY,
    display_name             TEXT NOT NULL DEFAULT 'Owner',
    preferred_address        TEXT NOT NULL DEFAULT '你',
    timezone                 TEXT NOT NULL DEFAULT 'UTC',
    locale                   TEXT NOT NULL DEFAULT 'zh-CN',
    working_style            TEXT NOT NULL DEFAULT '',
    interaction_preferences  TEXT NOT NULL DEFAULT '[]',
    boundary_notes           TEXT NOT NULL DEFAULT '[]',
    main_session_only_fields TEXT NOT NULL DEFAULT '[]',
    metadata                 TEXT NOT NULL DEFAULT '{}',
    version                  INTEGER NOT NULL DEFAULT 1,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);
"""

_OWNER_PROFILE_OVERLAYS_DDL = """
CREATE TABLE IF NOT EXISTS owner_profile_overlays (
    owner_overlay_id                 TEXT PRIMARY KEY,
    owner_profile_id                 TEXT NOT NULL,
    scope                            TEXT NOT NULL DEFAULT 'project',
    project_id                       TEXT NOT NULL DEFAULT '',
    workspace_id                     TEXT NOT NULL DEFAULT '',
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
    workspace_id             TEXT NOT NULL DEFAULT '',
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
    workspace_id       TEXT NOT NULL DEFAULT '',
    agent_profile_id   TEXT NOT NULL DEFAULT '',
    worker_profile_id  TEXT NOT NULL DEFAULT '',
    role               TEXT NOT NULL DEFAULT 'butler',
    name               TEXT NOT NULL DEFAULT '',
    persona_summary    TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'active',
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
    kind                     TEXT NOT NULL DEFAULT 'butler_main',
    status                   TEXT NOT NULL DEFAULT 'active',
    project_id               TEXT NOT NULL DEFAULT '',
    workspace_id             TEXT NOT NULL DEFAULT '',
    surface                  TEXT NOT NULL DEFAULT 'chat',
    thread_id                TEXT NOT NULL DEFAULT '',
    legacy_session_id        TEXT NOT NULL DEFAULT '',
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
    workspace_id             TEXT NOT NULL DEFAULT '',
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
    workspace_id             TEXT NOT NULL DEFAULT '',
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
    workspace_id       TEXT NOT NULL DEFAULT '',
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
    workspace_id          TEXT NOT NULL DEFAULT '',
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
    workspace_id           TEXT NOT NULL DEFAULT '',
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
    workspace_id         TEXT NOT NULL DEFAULT '',
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

_PROJECT_INDEXES = [
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_single_default "
        "ON projects(is_default) WHERE is_default = 1;"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_workspaces_project_slug "
        "ON workspaces(project_id, slug);"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_workspaces_primary_per_project "
        "ON workspaces(project_id) WHERE kind = 'primary';"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_bindings_project_type_key "
        "ON project_bindings(project_id, binding_type, binding_key);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_project_bindings_workspace "
        "ON project_bindings(workspace_id);"
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
        "WHERE status = 'active' AND project_id != '';"
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
        "ON owner_profile_overlays(scope, project_id, workspace_id, updated_at DESC);"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_bootstrap_sessions_scope "
        "ON bootstrap_sessions(project_id, workspace_id, updated_at DESC);"
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
    if agent_session_columns and "parent_worker_runtime_id" not in agent_session_columns:
        await conn.execute(
            "ALTER TABLE agent_sessions "
            "ADD COLUMN parent_worker_runtime_id TEXT NOT NULL DEFAULT ''"
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
    await conn.execute(_WORKSPACES_DDL)
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
    await conn.execute(_BOOTSTRAP_SESSIONS_DDL)
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
