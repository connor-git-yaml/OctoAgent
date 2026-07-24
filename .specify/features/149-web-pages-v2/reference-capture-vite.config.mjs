// Design recon only: serves the current frontend with deterministic, non-live API fixtures.
// This file is not production code and must never be presented as a real backend capture.
import { defineConfig } from "/Users/connorlu/.codex/worktrees/3a30/OctoAgent/octoagent/frontend/node_modules/vite/dist/node/index.js";

const sharedModules = "/Users/connorlu/.codex/worktrees/3a30/OctoAgent/octoagent/frontend/node_modules";
const currentFrontend = "/Users/connorlu/.codex/worktrees/1925/OctoAgent/octoagent/frontend";
const referenceStubs = "/Users/connorlu/.codex/worktrees/1925/OctoAgent/.specify/features/149-web-pages-v2/reference-stubs";

const now = "2026-07-20T12:00:00+08:00";
const baseDocument = {
  contract_version: "f149-reference-v1",
  generated_at: now,
  warnings: [],
  capabilities: [],
  degraded: { is_degraded: false, reasons: [] },
};

const config = {
  ...baseDocument,
  resource_type: "config_schema",
  resource_id: "config:octoagent",
  schema: { type: "object", properties: {} },
  ui_hints: {},
  current_value: {
    runtime: { llm_mode: "echo", litellm_proxy_url: "", master_key_env: "" },
    providers: [],
    model_aliases: {},
    memory: {
      reasoning_model_alias: "",
      expand_model_alias: "",
      embedding_model_alias: "",
      rerank_model_alias: "",
    },
  },
  validation_rules: [],
  bridge_refs: [],
  secret_refs_only: true,
};

const projectSelector = {
  ...baseDocument,
  resource_type: "project_selector",
  resource_id: "project:selector",
  current_project_id: "f149-reference-project",
  current_workspace_id: "f149-reference-workspace",
  default_project_id: "f149-reference-project",
  fallback_reason: "",
  switch_allowed: false,
  available_projects: [{
    project_id: "f149-reference-project",
    slug: "reference",
    name: "参考项目",
    is_default: true,
    status: "active",
    warnings: [],
  }],
  available_workspaces: [],
};

const sessions = {
  ...baseDocument,
  resource_type: "session_projection",
  resource_id: "sessions:projection",
  sessions: [{
    session_id: "f149-reference-session",
    task_id: "f149-reference-task",
    title: "整理本周计划",
    alias: "本周计划",
    channel: "web",
    status: "idle",
    lane: "recent",
    project_id: "f149-reference-project",
    agent_profile_id: "f149-reference-agent",
    session_owner_profile_id: "f149-reference-agent",
    session_owner_name: "主助手",
    latest_message_summary: "已准备好继续处理。",
    updated_at: now,
  }],
  operator_summary: { total_pending: 0 },
  operator_items: [],
};

const agentProfiles = {
  ...baseDocument,
  resource_type: "agent_profiles",
  resource_id: "agents:profiles",
  active_project_id: "f149-reference-project",
  active_workspace_id: "f149-reference-workspace",
  default_profile_id: "f149-reference-agent",
  profiles: [],
};

const workerProfiles = {
  ...baseDocument,
  resource_type: "worker_profiles",
  resource_id: "workers:profiles",
  active_project_id: "f149-reference-project",
  profiles: [],
  templates: [],
};

const mcpCatalog = {
  ...baseDocument,
  resource_type: "mcp_provider_catalog",
  resource_id: "mcp-providers:catalog",
  active_project_id: "f149-reference-project",
  items: [],
  summary: { installed_count: 0, enabled_count: 0, healthy_count: 0 },
};

const skillGovernance = {
  ...baseDocument,
  resource_type: "skill_governance",
  resource_id: "skills:governance",
  active_project_id: "f149-reference-project",
  items: [],
  summary: {},
};

const setupGovernance = {
  ...baseDocument,
  resource_type: "setup_governance",
  resource_id: "setup:governance",
  active_project_id: "f149-reference-project",
  provider_runtime: { section_id: "provider_runtime", label: "Provider", status: "ready", summary: "", warnings: [], blocking_reasons: [], details: {}, source_refs: [] },
  project_scope: { section_id: "project_scope", label: "Project", status: "ready", summary: "", warnings: [], blocking_reasons: [], details: {}, source_refs: [] },
  channel_access: { section_id: "channel_access", label: "Channel", status: "ready", summary: "", warnings: [], blocking_reasons: [], details: {}, source_refs: [] },
  agent_governance: { section_id: "agent_governance", label: "Agent", status: "ready", summary: "", warnings: [], blocking_reasons: [], details: {}, source_refs: [] },
  tools_skills: { section_id: "tools_skills", label: "Tools", status: "ready", summary: "", warnings: [], blocking_reasons: [], details: {}, source_refs: [] },
  review: { ready: true, risk_level: "info", warnings: [], blocking_reasons: [], blocking_reasons_detail: [], next_actions: [], provider_runtime_risks: [], channel_exposure_risks: [], agent_autonomy_risks: [], tool_skill_readiness_risks: [], secret_binding_risks: [] },
};

const memory = {
  ...baseDocument,
  resource_type: "memory_console",
  resource_id: "memory:console",
  active_project_id: "f149-reference-project",
  active_workspace_id: "f149-reference-workspace",
  retrieval_backend: "builtin",
  backend_state: "healthy",
  backend_id: "memory-local",
  filters: { query: "", scope_id: "", layer: "", partition: "", include_history: false, include_vault_refs: false, limit: 50, derived_type: "", status: "", updated_after: "", updated_before: "" },
  summary: { sor_current_count: 0, sor_readable_count: 0, sor_history_count: 0, fragment_count: 0, pending_consolidation_count: 0, pending_replay_count: 0, vault_ref_count: 0, proposal_count: 0, next_consolidation_at: "", scope_count: 0 },
  records: [],
  available_scopes: [],
  available_layers: [],
  available_partitions: [],
};

const retrieval = { ...baseDocument, resource_type: "retrieval_platform", resource_id: "retrieval:platform", corpora: [], generations: [], build_jobs: [] };
const diagnostics = { ...baseDocument, resource_type: "diagnostics_summary", resource_id: "diagnostics:runtime", overall_status: "ready", subsystems: [], recent_failures: [], runtime_snapshot: {}, recovery_summary: {}, update_summary: {}, channel_summary: {}, deep_refs: {} };
const delegation = { ...baseDocument, resource_type: "delegation_plane", resource_id: "delegation:overview", works: [], summary: {} };
const automation = { ...baseDocument, resource_type: "automation_jobs", resource_id: "automation:jobs", jobs: [], summary: {} };

const snapshot = {
  status: "ready",
  contract_version: "f149-reference-v1",
  resources: {
    config,
    project_selector: projectSelector,
    sessions,
    agent_profiles: agentProfiles,
    worker_profiles: workerProfiles,
    owner_profile: { ...baseDocument, resource_type: "owner_profile", resource_id: "owner:profile" },
    bootstrap_session: { ...baseDocument, resource_type: "bootstrap_session", resource_id: "bootstrap:session" },
    context_continuity: { ...baseDocument, resource_type: "context_continuity", resource_id: "context:continuity" },
    capability_pack: { ...baseDocument, resource_type: "capability_pack", resource_id: "capability:bundled", items: [] },
    skill_governance: skillGovernance,
    mcp_provider_catalog: mcpCatalog,
    setup_governance: setupGovernance,
    delegation,
    diagnostics,
    retrieval_platform: retrieval,
    memory,
  },
  registry: { ...baseDocument, resource_type: "action_registry", resource_id: "actions:registry", actions: [] },
  degraded_sections: [],
  resource_errors: {},
  generated_at: now,
};

const task = {
  task_id: "f149-reference-task",
  created_at: now,
  updated_at: now,
  status: "SUCCEEDED",
  title: "整理本周计划",
  alias: "本周计划",
  thread_id: "f149-reference-thread",
  scope_id: "f149-reference-scope",
  requester: { channel: "web", user_id: "reference-user" },
  risk_level: "low",
};

function responseFor(url) {
  const path = new URL(url, "http://reference.local").pathname;
  if (path === "/api/control/snapshot") return snapshot;
  if (path === "/api/control/resources/automation") return automation;
  if (path === "/api/control/resources/config") return config;
  if (path === "/api/control/resources/project-selector") return projectSelector;
  if (path === "/api/control/resources/agent-profiles") return agentProfiles;
  if (path === "/api/control/resources/worker-profiles") return workerProfiles;
  if (path === "/api/control/resources/skill-governance") return skillGovernance;
  if (path === "/api/control/resources/mcp-provider-catalog") return mcpCatalog;
  if (path === "/api/control/resources/setup-governance") return setupGovernance;
  if (path === "/api/control/resources/retrieval-platform") return retrieval;
  if (path === "/api/control/resources/memory") return memory;
  if (path === "/api/approval-center/summary") return { memory_pending: 0, consolidation_pending: 0, behavior_compact_pending: 0, total_pending: 0 };
  if (path === "/api/memory/candidates") return { candidates: [], total: 0, pending_count: 0 };
  if (path === "/api/consolidation/candidates") return { candidates: [], pending_count: 0 };
  if (path === "/api/behavior/compact/candidates") return { candidates: [], pending_count: 0 };
  if (path === "/api/tasks") return { tasks: [task] };
  if (path === "/api/tasks/f149-reference-task") return { task, events: [], artifacts: [] };
  if (path === "/api/operator/inbox") return { summary: { total_pending: 0, approvals: 0, alerts: 0, retryable_failures: 0, pairing_requests: 0, degraded_sources: [] }, items: [] };
  if (path === "/api/ops/recovery") return { ready_for_restore: false, latest_backup: null, latest_recovery_drill: null, backup_count: 0, warnings: [] };
  if (path === "/api/ops/update/status") return { overall_status: "NOT_STARTED", phases: [], warnings: [] };
  if (path === "/api/files/tasks") return { tasks: [] };
  if (path === "/api/workspace-git/projects") return { projects: [] };
  if (path === "/api/skills") return { items: [], total: 0 };
  if (path === "/api/approval-overrides") return { overrides: [] };
  if (path === "/api/ops/frontend-version") return { build_id: "dev", served_at: now };
  if (path === "/api/control/actions") return { contract_version: "f149-reference-v1", result: { request_id: "reference", action_id: "noop", status: "completed", code: "REFERENCE_ONLY", message: "Reference fixture", data: {}, resource_refs: [] } };
  return {};
}

function deterministicApiPlugin() {
  return {
    name: "f149-reference-api",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith("/api/")) return next();
        res.statusCode = 200;
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.end(JSON.stringify(responseFor(req.url)));
      });
    },
  };
}

export default defineConfig({
  root: currentFrontend,
  plugins: [deterministicApiPlugin()],
  define: { __BUILD_ID__: JSON.stringify("dev") },
  resolve: {
    alias: {
      "@fontsource-variable/figtree": `${referenceStubs}/empty.css`,
      "remixicon/fonts/remixicon.css": `${referenceStubs}/empty.css`,
      diff: `${referenceStubs}/diff.mjs`,
      marked: `${sharedModules}/marked/lib/marked.esm.js`,
      dompurify: `${sharedModules}/dompurify/dist/purify.es.mjs`,
      react: `${sharedModules}/react`,
      "react-dom": `${sharedModules}/react-dom`,
      "react-router-dom": `${sharedModules}/react-router-dom`,
    },
  },
  server: { host: "127.0.0.1", port: 4179, strictPort: true },
});
