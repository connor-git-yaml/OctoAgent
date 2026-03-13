import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ControlPlane from "./ControlPlane";

type FetchArgs = [RequestInfo | URL, RequestInit | undefined];

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function buildSnapshot(currentProjectId = "project-default") {
  const currentWorkspaceId =
    currentProjectId === "project-ops" ? "workspace-ops" : "workspace-default";
  return {
    contract_version: "1.0.0",
    generated_at: "2026-03-08T09:00:00Z",
    registry: {
      contract_version: "1.0.0",
      resource_type: "action_registry",
      resource_id: "actions:registry",
      schema_version: 1,
      generated_at: "2026-03-08T09:00:00Z",
      updated_at: "2026-03-08T09:00:00Z",
      status: "ready",
      degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
      warnings: [],
      capabilities: [],
      refs: {},
      actions: [
        {
          action_id: "project.select",
          label: "切换项目",
          description: "",
          category: "projects",
          supported_surfaces: ["web", "telegram", "system"],
          surface_aliases: { web: ["project.select"], telegram: ["/project select"] },
          support_status_by_surface: { web: "supported", telegram: "supported" },
          params_schema: {},
          result_schema: {},
          risk_hint: "low",
          approval_hint: "none",
          idempotency_hint: "request_id",
          resource_targets: ["project_selector"],
        },
        {
          action_id: "operator.approval.resolve",
          label: "处理审批",
          description: "",
          category: "operator",
          supported_surfaces: ["web", "telegram", "system"],
          surface_aliases: { web: ["operator.approval.resolve"], telegram: ["/approve"] },
          support_status_by_surface: { web: "supported", telegram: "supported" },
          params_schema: {},
          result_schema: {},
          risk_hint: "medium",
          approval_hint: "none",
          idempotency_hint: "request_id",
          resource_targets: ["session_projection"],
        },
        {
          action_id: "memory.query",
          label: "查询 Memory",
          description: "",
          category: "memory",
          supported_surfaces: ["web", "telegram", "system"],
          surface_aliases: { web: ["memory.query"], telegram: ["/memory query"] },
          support_status_by_surface: { web: "supported", telegram: "supported" },
          params_schema: {},
          result_schema: {},
          risk_hint: "low",
          approval_hint: "none",
          idempotency_hint: "request_id",
          resource_targets: ["memory_console"],
        },
        {
          action_id: "work.split",
          label: "拆分 Work",
          description: "",
          category: "delegation",
          supported_surfaces: ["web", "system"],
          surface_aliases: { web: ["work.split"] },
          support_status_by_surface: { web: "supported", telegram: "degraded" },
          params_schema: {},
          result_schema: {},
          risk_hint: "medium",
          approval_hint: "none",
          idempotency_hint: "request_id",
          resource_targets: ["delegation_plane"],
        },
        {
          action_id: "work.merge",
          label: "合并 Work",
          description: "",
          category: "delegation",
          supported_surfaces: ["web", "system"],
          surface_aliases: { web: ["work.merge"] },
          support_status_by_surface: { web: "supported", telegram: "degraded" },
          params_schema: {},
          result_schema: {},
          risk_hint: "medium",
          approval_hint: "none",
          idempotency_hint: "request_id",
          resource_targets: ["delegation_plane"],
        },
      ],
    },
    resources: {
      wizard: {
        contract_version: "1.0.0",
        resource_type: "wizard_session",
        resource_id: "wizard:default",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        session_version: 1,
        current_step: "doctor_live",
        resumable: true,
        blocking_reason: "",
        steps: [],
        summary: {},
        next_actions: [],
      },
      config: {
        contract_version: "1.0.0",
        resource_type: "config_schema",
        resource_id: "config:octoagent",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        schema: {},
        ui_hints: {
          "runtime.litellm_proxy_url": {
            field_path: "runtime.litellm_proxy_url",
            section: "runtime",
            label: "LiteLLM Proxy URL",
            description: "",
            widget: "text",
            placeholder: "http://localhost:4000",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 10,
          },
        },
        current_value: {
          runtime: {
            litellm_proxy_url: "http://localhost:4000",
          },
        },
        validation_rules: ["Provider ID 必须唯一"],
        bridge_refs: [],
        secret_refs_only: true,
      },
      project_selector: {
        contract_version: "1.0.0",
        resource_type: "project_selector",
        resource_id: "project:selector",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        current_project_id: currentProjectId,
        current_workspace_id: currentWorkspaceId,
        default_project_id: "project-default",
        fallback_reason: "",
        switch_allowed: true,
        available_projects: [
          {
            project_id: "project-default",
            slug: "default",
            name: "Default Project",
            is_default: true,
            status: "active",
            workspace_ids: ["workspace-default"],
            warnings: [],
          },
          {
            project_id: "project-ops",
            slug: "ops",
            name: "Ops Project",
            is_default: false,
            status: "active",
            workspace_ids: ["workspace-ops"],
            warnings: [],
          },
        ],
        available_workspaces: [
          {
            workspace_id: "workspace-default",
            project_id: "project-default",
            slug: "primary",
            name: "Primary",
            kind: "primary",
            root_path: "/tmp/default",
          },
          {
            workspace_id: "workspace-ops",
            project_id: "project-ops",
            slug: "ops",
            name: "Ops Primary",
            kind: "primary",
            root_path: "/tmp/ops",
          },
        ],
      },
      sessions: {
        contract_version: "1.0.0",
        resource_type: "session_projection",
        resource_id: "sessions:overview",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        focused_session_id: "",
        focused_thread_id: "",
        sessions: [
          {
            session_id: "thread-1",
            thread_id: "thread-1",
            task_id: "task-1",
            parent_task_id: "",
            parent_work_id: "",
            title: "网关升级失败",
            status: "FAILED",
            channel: "telegram",
            requester_id: "owner",
            project_id: currentProjectId,
            workspace_id: currentWorkspaceId,
            runtime_kind: "graph_agent",
            latest_message_summary: "请检查 update plan",
            latest_event_at: "2026-03-08T09:10:00Z",
            execution_summary: {
              runtime_kind: "graph_agent",
              work_id: "work-1",
              current_step: "graph.finalize",
            },
            capabilities: [],
            detail_refs: { task: "/tasks/task-1" },
          },
        ],
        operator_summary: {
          total_pending: 1,
          approvals: 1,
          alerts: 0,
          retryable_failures: 0,
          pairing_requests: 0,
          degraded_sources: [],
          generated_at: "2026-03-08T09:10:00Z",
        },
        operator_items: [
          {
            item_id: "approval:approval-1",
            kind: "approval",
            state: "pending",
            title: "允许执行 runtime verify",
            summary: "需要 owner 批准后继续",
            task_id: "task-1",
            thread_id: "thread-1",
            source_ref: "/tasks/task-1",
            created_at: "2026-03-08T09:10:00Z",
            expires_at: null,
            pending_age_seconds: 12,
            suggested_actions: ["approve_once", "deny"],
            quick_actions: [
              {
                kind: "approve_once",
                label: "批准一次",
                style: "primary",
                enabled: true,
              },
              {
                kind: "deny",
                label: "拒绝",
                style: "danger",
                enabled: true,
              },
            ],
            recent_action_result: null,
            metadata: {
              risk: "medium",
            },
          },
        ],
      },
      context_continuity: {
        contract_version: "1.0.0",
        resource_type: "context_continuity",
        resource_id: "context:overview",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: currentProjectId,
        active_workspace_id: currentWorkspaceId,
        sessions: [
          {
            session_id: "thread-1",
            thread_id: "thread-1",
            project_id: currentProjectId,
            workspace_id: currentWorkspaceId,
            rolling_summary: "最近一轮上下文已经压缩完成。",
            last_context_frame_id: "frame-context-1",
            updated_at: "2026-03-08T09:12:00Z",
          },
        ],
        frames: [
          {
            context_frame_id: "frame-context-1",
            task_id: "task-1",
            session_id: "thread-1",
            project_id: currentProjectId,
            workspace_id: currentWorkspaceId,
            agent_profile_id: "owner-profile",
            recent_summary: "围绕网关升级失败形成了新的上下文摘要。",
            memory_hit_count: 2,
            memory_hits: [
              {
                subject_key: "release.plan",
                layer: "sor",
              },
              {
                subject_key: "incident.runtime",
                layer: "fragment",
              },
            ],
            memory_recall: {
              summary: "命中 2 条记忆记录",
            },
            budget: {
              input_tokens: 1200,
            },
            source_refs: [
              {
                type: "memory_record",
                id: "record-1",
              },
            ],
            degraded_reason: "",
            created_at: "2026-03-08T09:11:30Z",
          },
        ],
      },
      capability_pack: {
        contract_version: "1.0.0",
        resource_type: "capability_pack",
        resource_id: "capability:bundled",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        pack: {
          pack_id: "bundled:default",
          version: "1.0.0",
          degraded_reason: "",
          generated_at: "2026-03-08T09:00:00Z",
          fallback_toolset: ["project.inspect", "task.inspect"],
          worker_profiles: [
            {
              worker_type: "general",
              capabilities: ["llm_generation", "general"],
              default_model_alias: "main",
              default_tool_profile: "minimal",
              default_tool_groups: ["project", "session"],
              bootstrap_file_ids: ["bootstrap:shared", "bootstrap:general"],
              runtime_kinds: ["worker", "subagent"],
              metadata: {},
            },
            {
              worker_type: "ops",
              capabilities: ["ops", "runtime"],
              default_model_alias: "main",
              default_tool_profile: "minimal",
              default_tool_groups: ["runtime", "session", "project"],
              bootstrap_file_ids: ["bootstrap:shared", "bootstrap:ops"],
              runtime_kinds: ["worker", "acp_runtime"],
              metadata: {},
            },
          ],
          bootstrap_files: [
            {
              file_id: "bootstrap:shared",
              path_hint: "bootstrap/shared.md",
              content: "shared bootstrap",
              applies_to_worker_types: ["general"],
              metadata: {},
            },
            {
              file_id: "bootstrap:ops",
              path_hint: "bootstrap/ops.md",
              content: "ops bootstrap",
              applies_to_worker_types: ["ops"],
              metadata: {},
            },
          ],
          skills: [
            {
              skill_id: "ops_triage",
              label: "Ops Triage",
              description: "bundled ops skill",
              model_alias: "main",
              worker_types: ["ops"],
              tools_allowed: ["runtime.inspect", "task.inspect"],
              pipeline_templates: ["delegation:preflight"],
              metadata: { tool_profile: "minimal" },
            },
          ],
          tools: [
            {
              tool_name: "project.inspect",
              label: "Project Inspect",
              description: "inspect project context",
              tool_group: "project",
              tool_profile: "minimal",
              tags: ["project", "workspace"],
              worker_types: ["general", "ops"],
              manifest_ref: "builtin://project.inspect",
              availability: "available",
              availability_reason: "",
              install_hint: "",
              entrypoints: ["agent_runtime", "web"],
              runtime_kinds: ["worker", "subagent", "graph_agent"],
              metadata: {},
            },
            {
              tool_name: "runtime.inspect",
              label: "Runtime Inspect",
              description: "inspect runtime summary",
              tool_group: "runtime",
              tool_profile: "minimal",
              tags: ["runtime", "diagnostics"],
              worker_types: ["ops"],
              manifest_ref: "builtin://runtime.inspect",
              availability: "available",
              availability_reason: "",
              install_hint: "",
              entrypoints: ["agent_runtime", "web"],
              runtime_kinds: ["worker", "acp_runtime"],
              metadata: {},
            },
          ],
        },
        selected_project_id: currentProjectId,
        selected_workspace_id:
          currentProjectId === "project-ops" ? "workspace-ops" : "workspace-default",
      },
      delegation: {
        contract_version: "1.0.0",
        resource_type: "delegation_plane",
        resource_id: "delegation:overview",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        summary: {
          total: 1,
          by_status: { assigned: 1 },
          by_worker_type: { ops: 1 },
        },
        works: [
          {
            work_id: "work-1",
            task_id: "task-1",
            parent_work_id: "",
            title: "诊断运行态",
            status: "assigned",
            target_kind: "acp_runtime",
            selected_worker_type: "ops",
            route_reason: "worker_type=ops | fallback=single_worker",
            owner_id: "orchestrator",
            selected_tools: ["runtime.inspect", "task.inspect"],
            pipeline_run_id: "run-1",
            runtime_id: "worker.llm.ops",
            project_id: currentProjectId,
            workspace_id:
              currentProjectId === "project-ops"
                ? "workspace-ops"
                : "workspace-default",
            requested_worker_profile_id: "project-default:ops-root",
            requested_worker_profile_version: 3,
            effective_worker_snapshot_id: "worker-profile:project-default:ops-root:v3",
            tool_resolution_mode: "profile_first_core",
            blocked_tools: [
              {
                tool_name: "subagents.spawn",
                status: "unavailable",
                reason_code: "task_runner_unbound",
              },
            ],
            child_work_ids: ["work-1-child-1", "work-1-child-2"],
            child_work_count: 2,
            merge_ready: true,
            runtime_summary: {
              requested_target_kind: "acp_runtime",
              requested_worker_type: "ops",
              runtime_status: "SUCCEEDED",
            },
            updated_at: "2026-03-08T09:12:00Z",
            capabilities: [],
          },
        ],
      },
      pipelines: {
        contract_version: "1.0.0",
        resource_type: "skill_pipeline",
        resource_id: "pipeline:overview",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        summary: {
          total: 1,
          paused: 0,
          running: 0,
        },
        runs: [
          {
            run_id: "run-1",
            pipeline_id: "delegation:preflight",
            task_id: "task-1",
            work_id: "work-1",
            status: "succeeded",
            current_node_id: "",
            pause_reason: "",
            retry_cursor: {},
            updated_at: "2026-03-08T09:12:00Z",
            replay_frames: [
              {
                frame_id: "frame-1",
                run_id: "run-1",
                node_id: "tool_index.select",
                status: "succeeded",
                summary: "tool index selected tools",
                checkpoint_id: "checkpoint-1",
                ts: "2026-03-08T09:11:30Z",
              },
            ],
          },
        ],
      },
      automation: {
        contract_version: "1.0.0",
        resource_type: "automation_job",
        resource_id: "automation:jobs",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        jobs: [],
        run_history_cursor: "",
      },
      diagnostics: {
        contract_version: "1.0.0",
        resource_type: "diagnostics_summary",
        resource_id: "diagnostics:runtime",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        overall_status: "ready",
        subsystems: [
          {
            subsystem_id: "runtime",
            label: "Runtime",
            status: "ok",
            summary: "TaskRunner / Execution runtime",
            detail_ref: "/health",
            warnings: [],
          },
        ],
        recent_failures: [],
        runtime_snapshot: {},
        recovery_summary: {},
        update_summary: {},
        channel_summary: {
          telegram: {
            enabled: true,
            mode: "webhook",
            dm_policy: "open",
            group_policy: "open",
            pending_pairings: 0,
            approved_users: 2,
            allowed_groups: ["chat-1"],
          },
        },
        deep_refs: {},
      },
      memory: {
        contract_version: "1.0.0",
        resource_type: "memory_console",
        resource_id: "memory:overview",
        schema_version: 1,
        generated_at: "2026-03-08T09:00:00Z",
        updated_at: "2026-03-08T09:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: currentProjectId,
        active_workspace_id: currentWorkspaceId,
        filters: {
          project_id: currentProjectId,
          workspace_id: currentWorkspaceId,
          scope_id: "scope-prod",
          partition: "",
          layer: "",
          query: "",
          include_history: false,
          include_vault_refs: true,
          limit: 50,
          cursor: "",
        },
        summary: {
          scope_count: 1,
          fragment_count: 1,
          sor_current_count: 1,
          sor_history_count: 1,
          vault_ref_count: 1,
          proposal_count: 1,
        },
        records: [
          {
            record_id: "sor-current-1",
            layer: "sor",
            project_id: currentProjectId,
            workspace_id: currentWorkspaceId,
            scope_id: "scope-prod",
            partition: "profile",
            subject_key: "user:alice",
            summary: "Alice current profile",
            status: "current",
            version: 2,
            created_at: "2026-03-08T09:00:00Z",
            updated_at: "2026-03-08T09:05:00Z",
            evidence_refs: [{ type: "task", id: "task-1" }],
            metadata: { source: "manual" },
            requires_vault_authorization: false,
          },
          {
            record_id: "vault-1",
            layer: "vault",
            project_id: currentProjectId,
            workspace_id: currentWorkspaceId,
            scope_id: "scope-prod",
            partition: "credential",
            subject_key: "credential:db",
            summary: "Database credential",
            status: "sealed",
            version: null,
            created_at: "2026-03-08T09:01:00Z",
            updated_at: null,
            evidence_refs: [{ type: "artifact", id: "vault-1" }],
            metadata: { owner: "ops" },
            requires_vault_authorization: true,
          },
        ],
        available_scopes: ["scope-prod"],
        available_partitions: ["profile", "credential"],
        available_layers: ["sor", "vault"],
      },
      imports: buildImportWorkbench(currentProjectId, currentWorkspaceId),
    },
  };
}

function buildImportSourceDocument(
  currentProjectId = "project-default",
  currentWorkspaceId = "workspace-default"
) {
  return {
    contract_version: "1.0.0",
    resource_type: "import_source",
    resource_id: "import-source:wechat-source-1",
    schema_version: 1,
    generated_at: "2026-03-08T09:00:00Z",
    updated_at: "2026-03-08T09:00:00Z",
    status: "detected",
    degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
    warnings: [],
    capabilities: [],
    refs: {},
    active_project_id: currentProjectId,
    active_workspace_id: currentWorkspaceId,
    source_id: "wechat-source-1",
    source_type: "wechat",
    input_ref: {
      source_type: "wechat",
      input_path: "/tmp/wechat-export.json",
      media_root: "/tmp/wechat-media",
      format_hint: "json",
      account_id: null,
      metadata: {},
    },
    detected_conversations: [
      {
        conversation_key: "team-alpha",
        label: "Team Alpha",
        message_count: 2,
        attachment_count: 1,
        last_message_at: "2026-03-08T09:00:00Z",
        participants: ["alice", "bob"],
        metadata: {},
      },
    ],
    detected_participants: [
      {
        source_sender_id: "alice",
        label: "Alice",
        message_count: 1,
        metadata: {},
      },
      {
        source_sender_id: "bob",
        label: "Bob",
        message_count: 1,
        metadata: {},
      },
    ],
    attachment_roots: ["/tmp/wechat-media"],
    errors: [],
    latest_mapping_id: "mapping-1",
    latest_run_id: "import-run:wechat-1",
    metadata: { format: "json" },
  };
}

function buildImportRunDocument(
  currentProjectId = "project-default",
  currentWorkspaceId = "workspace-default"
) {
  return {
    contract_version: "1.0.0",
    resource_type: "import_run",
    resource_id: "import-run:wechat-1",
    schema_version: 1,
    generated_at: "2026-03-08T09:10:00Z",
    updated_at: "2026-03-08T09:10:00Z",
    status: "ready_to_run",
    degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
    warnings: [],
    capabilities: [],
    refs: {},
    active_project_id: currentProjectId,
    active_workspace_id: currentWorkspaceId,
    source_id: "wechat-source-1",
    source_type: "wechat",
    dry_run: true,
    mapping_id: "mapping-1",
    summary: {
      conversation_count: 1,
      scope_count: 1,
      imported_count: 2,
      duplicate_count: 0,
      window_count: 1,
      proposal_count: 0,
      committed_count: 0,
      attachment_count: 1,
      attachment_artifact_count: 1,
      attachment_fragment_count: 1,
    },
    errors: [],
    dedupe_details: [
      {
        reason: "duplicate_in_history",
        preview: "hello from history",
      },
    ],
    cursor: {
      scopes: {
        "chat:wechat_import:team-alpha": {
          source_id: "wechat-source-1",
          scope_id: "chat:wechat_import:team-alpha",
          cursor_value: "cursor-2",
          last_message_ts: "2026-03-08T09:02:00Z",
          last_message_key: "wechat-source-1:cursor-2",
          imported_count: 2,
          duplicate_count: 0,
          updated_at: "2026-03-08T09:10:00Z",
        },
      },
    },
    artifact_refs: ["artifact-1", "artifact-attachment-1"],
    memory_effects: {
      fragment_count: 2,
      proposal_count: 0,
      committed_count: 0,
      vault_ref_count: 0,
      memu_sync_count: 1,
      memu_degraded_count: 0,
    },
    report_refs: ["report-1"],
    resume_ref: "resume:wechat-source-1",
    metadata: { mode: "preview" },
    completed_at: "2026-03-08T09:10:00Z",
  };
}

function buildImportWorkbench(
  currentProjectId = "project-default",
  currentWorkspaceId = "workspace-default"
) {
  return {
    contract_version: "1.0.0",
    resource_type: "import_workbench",
    resource_id: "imports:workbench",
    schema_version: 1,
    generated_at: "2026-03-08T09:00:00Z",
    updated_at: "2026-03-08T09:00:00Z",
    status: "ready",
    degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
    warnings: [],
    capabilities: [],
    refs: {},
    active_project_id: currentProjectId,
    active_workspace_id: currentWorkspaceId,
    summary: {
      source_count: 1,
      recent_run_count: 1,
      resume_available_count: 1,
      warning_count: 0,
      error_count: 0,
    },
    sources: [buildImportSourceDocument(currentProjectId, currentWorkspaceId)],
    recent_runs: [buildImportRunDocument(currentProjectId, currentWorkspaceId)],
    resume_entries: [
      {
        resume_id: "resume:wechat-source-1",
        source_id: "wechat-source-1",
        source_type: "wechat",
        project_id: currentProjectId,
        workspace_id: currentWorkspaceId,
        scope_id: "chat:wechat_import:team-alpha",
        last_cursor: "cursor-2",
        last_batch_id: "report-1",
        state: "ready",
        blocking_reason: "",
        next_action: "import.resume",
        updated_at: "2026-03-08T09:10:00Z",
      },
    ],
  };
}

function buildMemorySubjectHistory() {
  return {
    contract_version: "1.0.0",
    resource_type: "memory_subject_history",
    resource_id: "memory-subject:overview",
    schema_version: 1,
    generated_at: "2026-03-08T09:10:00Z",
    updated_at: "2026-03-08T09:10:00Z",
    status: "ready",
    degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
    warnings: [],
    capabilities: [],
    refs: {},
    active_project_id: "project-default",
    active_workspace_id: "workspace-default",
    scope_id: "scope-prod",
    subject_key: "user:alice",
    current_record: {
      record_id: "sor-current-1",
      layer: "sor",
      project_id: "project-default",
      workspace_id: "workspace-default",
      scope_id: "scope-prod",
      partition: "profile",
      subject_key: "user:alice",
      summary: "Alice current profile",
      status: "current",
      version: 2,
      created_at: "2026-03-08T09:00:00Z",
      updated_at: "2026-03-08T09:05:00Z",
      evidence_refs: [{ type: "task", id: "task-1" }],
      metadata: { source: "manual" },
      requires_vault_authorization: false,
    },
    history: [
      {
        record_id: "sor-old-1",
        layer: "sor",
        project_id: "project-default",
        workspace_id: "workspace-default",
        scope_id: "scope-prod",
        partition: "profile",
        subject_key: "user:alice",
        summary: "Alice superseded profile",
        status: "superseded",
        version: 1,
        created_at: "2026-03-08T08:00:00Z",
        updated_at: "2026-03-08T08:30:00Z",
        evidence_refs: [{ type: "task", id: "task-0" }],
        metadata: { source: "import" },
        requires_vault_authorization: false,
      },
    ],
  };
}

function buildMemoryProposals() {
  return {
    contract_version: "1.0.0",
    resource_type: "memory_proposal_audit",
    resource_id: "memory-proposals:overview",
    schema_version: 1,
    generated_at: "2026-03-08T09:10:00Z",
    updated_at: "2026-03-08T09:10:00Z",
    status: "ready",
    degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
    warnings: [],
    capabilities: [],
    refs: {},
    active_project_id: "project-default",
    active_workspace_id: "workspace-default",
    summary: {
      pending: 1,
      validated: 0,
      rejected: 0,
      committed: 1,
    },
    items: [
      {
        proposal_id: "proposal-1",
        scope_id: "scope-prod",
        partition: "profile",
        action: "upsert",
        subject_key: "user:alice",
        status: "PENDING",
        confidence: 0.92,
        rationale: "新的联系人画像",
        is_sensitive: false,
        evidence_refs: [{ type: "task", id: "task-1" }],
        created_at: "2026-03-08T09:00:00Z",
        validated_at: null,
        committed_at: null,
        metadata: { source: "manual" },
      },
    ],
  };
}

function buildVaultAuthorization(grantStatus = "ACTIVE") {
  return {
    contract_version: "1.0.0",
    resource_type: "vault_authorization",
    resource_id: "vault:authorization",
    schema_version: 1,
    generated_at: "2026-03-08T09:10:00Z",
    updated_at: "2026-03-08T09:10:00Z",
    status: "ready",
    degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
    warnings: [],
    capabilities: [],
    refs: {},
    active_project_id: "project-default",
    active_workspace_id: "workspace-default",
    active_requests:
      grantStatus === "ACTIVE"
        ? []
        : [
            {
              request_id: "vault-request-1",
              project_id: "project-default",
              workspace_id: "workspace-default",
              scope_id: "scope-prod",
              partition: "credential",
              subject_key: "credential:db",
              reason: "排查生产数据库连接",
              requester_actor_id: "user:web",
              requester_actor_label: "Owner",
              status: "pending",
              decision: "",
              requested_at: "2026-03-08T09:10:00Z",
              resolved_at: null,
              resolver_actor_id: "",
              resolver_actor_label: "",
            },
          ],
    active_grants: [
      {
        grant_id: "vault-grant-1",
        request_id: "vault-request-1",
        project_id: "project-default",
        workspace_id: "workspace-default",
        scope_id: "scope-prod",
        partition: "credential",
        subject_key: "credential:db",
        granted_to_actor_id: "user:web",
        granted_to_actor_label: "Owner",
        granted_by_actor_id: "system:owner",
        granted_by_actor_label: "Owner",
        granted_at: "2026-03-08T09:12:00Z",
        expires_at: "2026-03-08T10:12:00Z",
        status: grantStatus,
      },
    ],
    recent_retrievals: [
      {
        retrieval_id: "retrieval-1",
        project_id: "project-default",
        workspace_id: "workspace-default",
        scope_id: "scope-prod",
        partition: "credential",
        subject_key: "credential:db",
        query: "db credential",
        grant_id: "vault-grant-1",
        actor_id: "user:web",
        actor_label: "Owner",
        authorized: true,
        reason_code: "VAULT_RETRIEVE_AUTHORIZED",
        result_count: 1,
        retrieved_vault_ids: ["vault-1"],
        evidence_refs: [{ type: "grant", id: "vault-grant-1" }],
        created_at: "2026-03-08T09:13:00Z",
      },
    ],
  };
}

function buildEvents() {
  return {
    contract_version: "1.0.0",
    events: [
      {
        event_id: "evt-1",
        contract_version: "1.0.0",
        event_type: "control.action.completed",
        request_id: "req-1",
        correlation_id: "req-1",
        causation_id: "req-1",
        actor: { actor_id: "user:web", actor_label: "Owner" },
        surface: "web",
        occurred_at: "2026-03-08T09:15:00Z",
        payload_summary: "project selected",
        resource_ref: null,
        resource_refs: [],
        target_refs: [],
        metadata: {},
      },
    ],
  };
}

describe("ControlPlane", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("使用 snapshot 渲染正式控制台首页与主导航", async () => {
    const snapshot = buildSnapshot();
    vi.spyOn(globalThis, "fetch").mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(jsonResponse(buildEvents()));
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(
      <MemoryRouter>
        <ControlPlane />
      </MemoryRouter>
    );

    expect(
      await screen.findByRole("heading", { name: "OctoAgent Control Plane" })
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Dashboard/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Projects/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Memory/i })).toBeInTheDocument();
    expect(screen.getAllByText(/project-default/).length).toBeGreaterThan(0);
    expect(screen.getByText("网关升级失败")).toBeInTheDocument();
    expect(screen.getByText("TaskRunner / Execution runtime")).toBeInTheDocument();
  });

  it("Dashboard 和 Delegation 会显示实时问题能力与对应 work 路径", async () => {
    const snapshot = buildSnapshot();
    snapshot.resources.context_continuity.degraded = {
      is_degraded: true,
      reasons: ["owner_timezone_invalid"] as never[],
      unavailable_sections: [] as never[],
    };
    snapshot.resources.context_continuity.frames[0] = {
      ...snapshot.resources.context_continuity.frames[0],
      degraded_reason: "owner_timezone_invalid",
    };
    snapshot.resources.capability_pack.pack.worker_profiles.push({
      worker_type: "research",
      capabilities: ["research", "web"],
      default_model_alias: "main",
      default_tool_profile: "standard",
      default_tool_groups: ["network", "browser", "session"],
      bootstrap_file_ids: ["bootstrap:shared", "bootstrap:research"],
      runtime_kinds: ["worker", "subagent"],
      metadata: {},
    });
    snapshot.resources.capability_pack.pack.tools.push(
      {
        tool_name: "runtime.now",
        label: "Runtime Now",
        description: "return local datetime",
        tool_group: "session",
        tool_profile: "minimal",
        tags: ["time"],
        worker_types: ["general", "research", "ops"],
        manifest_ref: "builtin://runtime.now",
        availability: "available",
        availability_reason: "",
        install_hint: "",
        entrypoints: ["agent_runtime", "web"],
        runtime_kinds: ["worker", "subagent"],
        metadata: {},
      },
      {
        tool_name: "web.search",
        label: "Web Search",
        description: "search web",
        tool_group: "network",
        tool_profile: "standard",
        tags: ["web"],
        worker_types: ["research", "ops"],
        manifest_ref: "builtin://web.search",
        availability: "available",
        availability_reason: "",
        install_hint: "",
        entrypoints: ["agent_runtime", "web"],
        runtime_kinds: ["worker", "subagent"],
        metadata: {},
      },
      {
        tool_name: "browser.status",
        label: "Browser Status",
        description: "inspect browser session",
        tool_group: "browser",
        tool_profile: "standard",
        tags: ["browser"],
        worker_types: ["research", "ops"],
        manifest_ref: "builtin://browser.status",
        availability: "degraded",
        availability_reason: "browser_controller_missing",
        install_hint: "",
        entrypoints: ["agent_runtime", "web"],
        runtime_kinds: ["worker", "subagent"],
        metadata: {},
      }
    );
    snapshot.resources.delegation.works[0] = {
      ...snapshot.resources.delegation.works[0],
      title: "检查官网最新公告",
      selected_worker_type: "research",
      route_reason: "worker_type=research | fallback=single_worker",
      selected_tools: ["runtime.now", "web.search"],
      runtime_summary: {
        ...snapshot.resources.delegation.works[0].runtime_summary,
        requested_worker_type: "research",
        requested_tool_profile: "standard",
      },
    } as (typeof snapshot.resources.delegation.works)[number];

    vi.spyOn(globalThis, "fetch").mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(jsonResponse(buildEvents()));
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(
      <MemoryRouter>
        <ControlPlane />
      </MemoryRouter>
    );

    expect(await screen.findByText("实时问题能力已经部分可用")).toBeInTheDocument();
    expect(screen.getAllByText(/owner timezone 配置无效/).length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: /Delegation/i }));

    expect(
      await screen.findByText(/Research Worker 会按标准工具面处理这条工作/)
    ).toBeInTheDocument();
  });

  it("project.select 会提交统一 action 并按 resource_refs 回刷 project selector", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const beforeSnapshot = buildSnapshot("project-default");
    const afterSelector = buildSnapshot("project-ops").resources.project_selector;

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(beforeSnapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(jsonResponse(buildEvents()));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            result: {
              contract_version: "1.0.0",
              request_id: "req-project-select",
              correlation_id: "req-project-select",
              action_id: "project.select",
              status: "completed",
              code: "PROJECT_SELECTED",
              message: "已切换当前 project",
              data: {
                project_id: "project-ops",
                workspace_id: "workspace-ops",
              },
              resource_refs: [
                {
                  resource_type: "project_selector",
                  resource_id: "project:selector",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-08T09:20:00Z",
              audit_event_id: "evt-project-select",
            },
          })
        );
      }
      if (url.includes("/api/control/resources/project-selector")) {
        return Promise.resolve(jsonResponse(afterSelector));
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(
      <MemoryRouter>
        <ControlPlane />
      </MemoryRouter>
    );

    expect((await screen.findAllByText(/project-default/)).length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: /Projects/i }));
    expect(await screen.findByText("Ops Project")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "切换到 Ops Primary" })
    );

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("已切换当前 project");
      expect(screen.getByRole("status")).toHaveTextContent("PROJECT_SELECTED");
    });
    await waitFor(() => {
      expect(screen.getAllByText(/project-ops/).length).toBeGreaterThan(0);
    });

    const actionRequest = fetchMock.mock.calls.find((call) => {
      const [url, init] = call as FetchArgs;
      return String(url).includes("/api/control/actions") && init?.method === "POST";
    });
    expect(actionRequest).toBeTruthy();
    expect(String(actionRequest?.[1]?.body)).toContain('"action_id":"project.select"');
    expect(String(actionRequest?.[1]?.body)).toContain('"project_id":"project-ops"');

    expect(
      fetchMock.mock.calls.some((call) =>
        String((call as FetchArgs)[0]).includes("/api/control/resources/project-selector")
      )
    ).toBe(true);
  });

  it("Operator 快捷动作会走统一 action 语义并回刷 sessions 资源", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const snapshot = buildSnapshot();
    const nextSessions = {
      ...snapshot.resources.sessions,
      operator_summary: {
        ...snapshot.resources.sessions.operator_summary,
        total_pending: 0,
        approvals: 0,
      },
      operator_items: [],
    };

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(jsonResponse(buildEvents()));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            result: {
              contract_version: "1.0.0",
              request_id: "req-approval",
              correlation_id: "req-approval",
              action_id: "operator.approval.resolve",
              status: "completed",
              code: "APPROVAL_RESOLVED",
              message: "审批已处理",
              data: {},
              resource_refs: [
                {
                  resource_type: "session_projection",
                  resource_id: "sessions:overview",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-08T09:30:00Z",
              audit_event_id: "evt-approval",
            },
          })
        );
      }
      if (url.includes("/api/control/resources/sessions")) {
        return Promise.resolve(jsonResponse(nextSessions));
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(
      <MemoryRouter>
        <ControlPlane />
      </MemoryRouter>
    );

    expect((await screen.findAllByText(/project-default/)).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: /Operator/i }));
    await userEvent.click(screen.getByRole("button", { name: "批准一次" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("审批已处理");
      expect(screen.getByRole("status")).toHaveTextContent("APPROVAL_RESOLVED");
    });
    await waitFor(() => {
      expect(screen.getByText(/Approvals 0/)).toBeInTheDocument();
    });

    const actionRequest = fetchMock.mock.calls.find((call) => {
      const [url, init] = call as FetchArgs;
      return String(url).includes("/api/control/actions") && init?.method === "POST";
    });
    expect(String(actionRequest?.[1]?.body)).toContain(
      '"action_id":"operator.approval.resolve"'
    );
    expect(String(actionRequest?.[1]?.body)).toContain('"approval_id":"approval-1"');
    expect(
      fetchMock.mock.calls.some((call) =>
        String((call as FetchArgs)[0]).includes("/api/control/resources/sessions")
      )
    ).toBe(true);
  });

  it("Memory section 会加载 subject history / proposal / vault 明细并执行授权动作", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const snapshot = buildSnapshot();
    const subjectHistory = buildMemorySubjectHistory();
    const memoryProposals = buildMemoryProposals();
    const initialVault = buildVaultAuthorization("ACTIVE");
    const afterRequestVault = buildVaultAuthorization("PENDING");
    afterRequestVault.active_grants = [];
    afterRequestVault.active_requests[0].reason = "临时排障";
    let requestCreated = false;

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(jsonResponse(buildEvents()));
      }
      if (url.includes("/api/control/resources/memory-proposals")) {
        return Promise.resolve(jsonResponse(memoryProposals));
      }
      if (url.includes("/api/control/resources/vault-authorization")) {
        return Promise.resolve(jsonResponse(requestCreated ? afterRequestVault : initialVault));
      }
      if (url.includes("/api/control/resources/memory-subjects/user%3Aalice")) {
        return Promise.resolve(jsonResponse(subjectHistory));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        requestCreated = true;
        return Promise.resolve(
          jsonResponse({
            result: {
              contract_version: "1.0.0",
              request_id: "req-vault-request",
              correlation_id: "req-vault-request",
              action_id: "vault.access.request",
              status: "completed",
              code: "VAULT_ACCESS_REQUEST_CREATED",
              message: "已创建 Vault 授权申请。",
              data: {
                request_id: "vault-request-1",
              },
              resource_refs: [
                {
                  resource_type: "vault_authorization",
                  resource_id: "vault:authorization",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-08T09:15:00Z",
              audit_event_id: "evt-vault-request",
            },
          })
        );
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(
      <MemoryRouter>
        <ControlPlane />
      </MemoryRouter>
    );

    expect((await screen.findAllByText(/project-default/)).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: /Memory/i }));

    expect(await screen.findByText(/Memory Console/)).toBeInTheDocument();
    expect((await screen.findAllByText("Alice current profile")).length).toBeGreaterThan(0);
    expect(await screen.findByText("新的联系人画像")).toBeInTheDocument();
    expect(await screen.findByText(/Grants 1/)).toBeInTheDocument();

    await userEvent.click(screen.getAllByRole("button", { name: "查看历史" })[0]);
    expect(await screen.findByText("Alice superseded profile")).toBeInTheDocument();

    const accessSubjectInput = screen.getByRole("textbox", { name: "申请目标条目" });
    await userEvent.clear(accessSubjectInput);
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "申请内容类型" }),
      "credential"
    );
    await userEvent.type(
      accessSubjectInput,
      "credential:db"
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "申请原因" }),
      "临时排障"
    );
    await userEvent.click(screen.getByRole("button", { name: "发起授权申请" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("已创建 Vault 授权申请。");
      expect(screen.getByRole("status")).toHaveTextContent(
        "VAULT_ACCESS_REQUEST_CREATED"
      );
    });
    await waitFor(() => {
      expect(screen.getByText("临时排障")).toBeInTheDocument();
    });

    const actionRequest = fetchMock.mock.calls.find((call) => {
      const [url, init] = call as FetchArgs;
      return String(url).includes("/api/control/actions") && init?.method === "POST";
    });
    expect(String(actionRequest?.[1]?.body)).toContain('"action_id":"vault.access.request"');
    expect(String(actionRequest?.[1]?.body)).toContain('"subject_key":"credential:db"');
  });

  it("memory.query 回刷 memory 资源时会保留当前过滤参数", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const snapshot = buildSnapshot();
    const filteredMemory = {
      ...snapshot.resources.memory,
      generated_at: "2026-03-08T09:20:00Z",
      updated_at: "2026-03-08T09:20:00Z",
      filters: {
        ...snapshot.resources.memory.filters,
        partition: "credential",
        query: "Database",
      },
      records: [snapshot.resources.memory.records[1]],
    };

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(jsonResponse(buildEvents()));
      }
      if (url.includes("/api/control/resources/memory?")) {
        return Promise.resolve(jsonResponse(filteredMemory));
      }
      if (url.includes("/api/control/resources/memory-proposals")) {
        return Promise.resolve(jsonResponse(buildMemoryProposals()));
      }
      if (url.includes("/api/control/resources/vault-authorization")) {
        return Promise.resolve(jsonResponse(buildVaultAuthorization("ACTIVE")));
      }
      if (url.includes("/api/control/resources/memory-subjects/user%3Aalice")) {
        return Promise.resolve(jsonResponse(buildMemorySubjectHistory()));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            result: {
              contract_version: "1.0.0",
              request_id: "req-memory-query",
              correlation_id: "req-memory-query",
              action_id: "memory.query",
              status: "completed",
              code: "MEMORY_QUERY_COMPLETED",
              message: "已刷新 Memory 总览。",
              data: {},
              resource_refs: [
                {
                  resource_type: "memory_console",
                  resource_id: "memory:overview",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-08T09:20:00Z",
              audit_event_id: "evt-memory-query",
            },
          })
        );
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(
      <MemoryRouter>
        <ControlPlane />
      </MemoryRouter>
    );

    expect((await screen.findAllByText(/project-default/)).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: /Memory/i }));
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "想看哪类内容" }),
      "credential"
    );
    await userEvent.type(screen.getByRole("textbox", { name: "关键词" }), "Database");
    await userEvent.click(screen.getByRole("button", { name: "刷新 Memory 视图" }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("已刷新 Memory 总览。");
      expect(screen.getByRole("status")).toHaveTextContent("MEMORY_QUERY_COMPLETED");
    });

    const memoryRefreshCall = fetchMock.mock.calls.find((call) =>
      String((call as FetchArgs)[0]).includes("/api/control/resources/memory?")
    );
    expect(memoryRefreshCall).toBeTruthy();
    const refreshUrl = String((memoryRefreshCall as FetchArgs)[0]);
    expect(refreshUrl).toContain("partition=credential");
    expect(refreshUrl).toContain("query=Database");
    expect(refreshUrl).toContain("scope_id=scope-prod");
  });

  it("030 新增面板会展示 capability pack / delegation / pipeline 数据", async () => {
    const snapshot = buildSnapshot();
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(jsonResponse(buildEvents()));
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(
      <MemoryRouter>
        <ControlPlane />
      </MemoryRouter>
    );

    expect((await screen.findAllByText(/project-default/)).length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: /Capability/i }));
    expect(await screen.findByText("bundled:default")).toBeInTheDocument();
    expect(screen.getByText("runtime.inspect")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Delegation/i }));
    expect(await screen.findByText("诊断运行态")).toBeInTheDocument();
    expect(screen.getByText(/worker_type=ops/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Pipelines/i }));
    expect(await screen.findByText("delegation:preflight")).toBeInTheDocument();
    expect(screen.getByText("tool index selected tools")).toBeInTheDocument();
  });

  it("Delegation section 会显示 Worker 模板 lineage 并允许从运行态提炼 profile", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const snapshot = buildSnapshot();

    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(jsonResponse(buildEvents()));
      }
      if (url.includes("/api/control/actions")) {
        return Promise.resolve(
          jsonResponse({
            result: {
              action_id: "worker.extract_profile_from_runtime",
              status: "completed",
              message: "已提炼 Worker 模板",
              code: "WORKER_PROFILE_EXTRACTED",
              handled_at: "2026-03-08T09:12:30Z",
              resource_refs: [
                {
                  resource_type: "worker_profiles",
                  resource_id: "worker-profiles:overview",
                },
              ],
            },
          })
        );
      }
      if (url.includes("/api/control/resources/worker-profiles")) {
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            resource_type: "worker_profiles",
            resource_id: "worker-profiles:overview",
            schema_version: 1,
            generated_at: "2026-03-08T09:12:31Z",
            updated_at: "2026-03-08T09:12:31Z",
            status: "ready",
            degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
            warnings: [],
            capabilities: [],
            refs: {},
            active_project_id: "project-default",
            active_workspace_id: "workspace-default",
            profiles: [
              {
                profile_id: "project-default:ops-root",
                name: "Ops Root",
                scope: "project",
                project_id: "project-default",
                mode: "singleton",
                origin_kind: "custom",
                status: "active",
                active_revision: 3,
                draft_revision: 3,
                effective_snapshot_id: "worker-profile:project-default:ops-root:v3",
                editable: true,
                summary: "已从运行态同步出的 Worker 模板。",
                static_config: {
                  base_archetype: "ops",
                  summary: "已从运行态同步出的 Worker 模板。",
                  model_alias: "main",
                  tool_profile: "minimal",
                  default_tool_groups: ["runtime", "project"],
                  selected_tools: ["runtime.inspect"],
                  runtime_kinds: ["worker", "acp_runtime"],
                  policy_refs: [],
                  instruction_overlays: [],
                  tags: ["ops"],
                  capabilities: ["ops", "runtime"],
                },
                dynamic_context: {
                  active_project_id: "project-default",
                  active_workspace_id: "workspace-default",
                  active_work_count: 1,
                  running_work_count: 0,
                  attention_work_count: 0,
                  latest_work_id: "work-1",
                  latest_task_id: "task-1",
                  latest_work_title: "诊断运行态",
                  latest_work_status: "assigned",
                  latest_target_kind: "acp_runtime",
                  current_selected_tools: ["runtime.inspect", "task.inspect"],
                  current_tool_resolution_mode: "profile_first_core",
                  current_blocked_tools: [
                    {
                      tool_name: "subagents.spawn",
                      status: "unavailable",
                      reason_code: "task_runner_unbound",
                    },
                  ],
                  current_discovery_entrypoints: ["workers.review"],
                  updated_at: "2026-03-08T09:12:00Z",
                },
                warnings: [],
                capabilities: [],
              },
            ],
            summary: {},
          })
        );
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(
      <MemoryRouter>
        <ControlPlane />
      </MemoryRouter>
    );

    expect((await screen.findAllByText(/project-default/)).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: /Delegation/i }));

    expect(await screen.findByText(/使用的模板 project-default:ops-root/)).toBeInTheDocument();
    expect(
      screen.getByText(/Revision 3 \/ Snapshot worker-profile:project-default:ops-root:v3/)
    ).toBeInTheDocument();
    expect(screen.getByText(/工具分配 profile_first_core/)).toBeInTheDocument();
    expect(screen.getByText(/当前不可用工具: subagents\.spawn/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "提炼模板" }));

    await waitFor(() => {
      const extractCall = fetchMock.mock.calls.find((call) =>
        String((call as FetchArgs)[0]).includes("/api/control/actions") &&
        String((call as FetchArgs)[1]?.body).includes("worker.extract_profile_from_runtime")
      );
      expect(extractCall).toBeTruthy();
      expect(String((extractCall as FetchArgs)[1]?.body)).toContain("\"work_id\":\"work-1\"");
    });
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some((call) =>
          String((call as FetchArgs)[0]).includes("/api/control/resources/worker-profiles")
        )
      ).toBe(true);
    });
  });

  it("Imports section 会加载 workbench/source/run 明细", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const snapshot = buildSnapshot();

    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(jsonResponse(buildEvents()));
      }
      if (url.includes("/api/control/resources/import-sources/")) {
        return Promise.resolve(jsonResponse(buildImportSourceDocument()));
      }
      if (url.includes("/api/control/resources/import-runs/")) {
        return Promise.resolve(jsonResponse(buildImportRunDocument()));
      }
      return Promise.resolve(jsonResponse({}));
    });

    render(
      <MemoryRouter>
        <ControlPlane />
      </MemoryRouter>
    );

    expect((await screen.findAllByText(/project-default/)).length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: /Imports/i }));

    expect(await screen.findByText("Import Workbench")).toBeInTheDocument();
    expect(await screen.findByText("Source Detail")).toBeInTheDocument();
    expect((await screen.findAllByText("wechat-source-1")).length).toBeGreaterThan(0);
    expect(await screen.findByText("Team Alpha")).toBeInTheDocument();
    expect(await screen.findByText("Run Detail")).toBeInTheDocument();
    expect((await screen.findAllByText("ready_to_run")).length).toBeGreaterThan(0);
    expect(await screen.findByRole("button", { name: "Resume" })).toBeInTheDocument();
  });
});
