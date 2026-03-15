import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type {
  ControlPlaneCapability,
  ControlPlaneSnapshot,
  SessionProjectionItem,
  TaskDetailResponse,
  WorkProjectionItem,
} from "./types";

type FetchArgs = [RequestInfo | URL, RequestInit | undefined];

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function buildSnapshot(proxyUrl = "http://localhost:4000"): ControlPlaneSnapshot {
  return {
    contract_version: "1.0.0",
    generated_at: "2026-03-09T10:00:00Z",
    registry: {
      contract_version: "1.0.0",
      resource_type: "action_registry",
      resource_id: "actions:registry",
      schema_version: 1,
      generated_at: "2026-03-09T10:00:00Z",
      updated_at: "2026-03-09T10:00:00Z",
      status: "ready",
      degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
      warnings: [],
      capabilities: [],
      refs: {},
      actions: [],
    },
    resources: {
      wizard: {
        contract_version: "1.0.0",
        resource_type: "wizard_session",
        resource_id: "wizard:default",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        session_version: 1,
        current_step: "complete",
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
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        schema: {
          type: "object",
          properties: {
            runtime: {
              type: "object",
              properties: {
                llm_mode: { type: "string", enum: ["echo", "litellm"] },
                litellm_proxy_url: { type: "string" },
                master_key_env: { type: "string" },
              },
            },
            providers: { type: "array", items: { type: "object" } },
            model_aliases: { type: "object" },
          },
        },
        ui_hints: {
          "runtime.llm_mode": {
            field_path: "runtime.llm_mode",
            section: "runtime",
            label: "LLM 模式",
            description: "Gateway 当前运行模式",
            widget: "select",
            placeholder: "",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 5,
          },
          "runtime.litellm_proxy_url": {
            field_path: "runtime.litellm_proxy_url",
            section: "runtime",
            label: "LiteLLM Proxy URL",
            description: "通常保持本地默认地址即可。",
            widget: "text",
            placeholder: "http://localhost:4000",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 10,
          },
          "runtime.master_key_env": {
            field_path: "runtime.master_key_env",
            section: "runtime",
            label: "LiteLLM Master Key 环境变量名",
            description: "通常保持默认值 LITELLM_MASTER_KEY。",
            widget: "env-ref",
            placeholder: "LITELLM_MASTER_KEY",
            help_text: "",
            sensitive: false,
            multiline: false,
            order: 20,
          },
          providers: {
            field_path: "providers",
            section: "providers",
            label: "模型提供方列表",
            description: "",
            widget: "provider-list",
            placeholder: "[]",
            help_text: "",
            sensitive: false,
            multiline: true,
            order: 30,
          },
          model_aliases: {
            field_path: "model_aliases",
            section: "models",
            label: "模型别名",
            description: "",
            widget: "alias-map",
            placeholder: "{}",
            help_text: "",
            sensitive: false,
            multiline: true,
            order: 40,
          },
        },
        current_value: {
          runtime: {
            llm_mode: "echo",
            litellm_proxy_url: proxyUrl,
            master_key_env: "LITELLM_MASTER_KEY",
          },
          providers: [],
          model_aliases: {},
        },
        validation_rules: [],
        bridge_refs: [],
        secret_refs_only: true,
      },
      project_selector: {
        contract_version: "1.0.0",
        resource_type: "project_selector",
        resource_id: "project:selector",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        current_project_id: "project-default",
        current_workspace_id: "workspace-default",
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
        ],
      },
      sessions: {
        contract_version: "1.0.0",
        resource_type: "session_projection",
        resource_id: "sessions:overview",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        focused_session_id: "",
        focused_thread_id: "",
        new_conversation_token: "",
        new_conversation_project_id: "",
        new_conversation_workspace_id: "",
        new_conversation_agent_profile_id: "",
        sessions: [],
        operator_summary: {
          total_pending: 0,
          approvals: 0,
          alerts: 0,
          retryable_failures: 0,
          pairing_requests: 0,
          degraded_sources: [],
          generated_at: "2026-03-09T10:00:00Z",
        },
        operator_items: [],
      },
      agent_profiles: {
        contract_version: "1.0.0",
        resource_type: "agent_profiles",
        resource_id: "agent-profiles:overview",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        profiles: [
          {
            profile_id: "agent-profile-default",
            scope: "project",
            project_id: "project-default",
            name: "默认主 Agent",
            persona_summary: "适合首次使用。",
            model_alias: "main",
            tool_profile: "standard",
            metadata: {},
            updated_at: "2026-03-09T10:00:00Z",
          },
        ],
      },
      worker_profiles: {
        contract_version: "1.0.0",
        resource_type: "worker_profiles",
        resource_id: "worker-profiles:overview",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        profiles: [
          {
            profile_id: "project-default:main",
            name: "默认主 Agent",
            scope: "project",
            project_id: "project-default",
            mode: "singleton",
            origin_kind: "custom",
            status: "active",
            active_revision: 1,
            draft_revision: 1,
            effective_snapshot_id: "worker-profile:project-default:main:v1",
            editable: true,
            summary: "负责默认聊天入口。",
            static_config: {
              base_archetype: "general",
              summary: "负责默认聊天入口。",
              model_alias: "main",
              tool_profile: "standard",
              default_tool_groups: ["project", "session"],
              selected_tools: ["project.inspect"],
              runtime_kinds: ["worker"],
              policy_refs: ["default"],
              instruction_overlays: [],
              tags: [],
              capabilities: ["planner"],
              metadata: {},
            },
            dynamic_context: {
              active_project_id: "project-default",
              active_workspace_id: "workspace-default",
              active_work_count: 0,
              running_work_count: 0,
              attention_work_count: 0,
              latest_work_id: "",
              latest_task_id: "",
              latest_work_title: "",
              latest_work_status: "idle",
              latest_target_kind: "",
              current_selected_tools: ["project.inspect"],
              current_tool_resolution_mode: "profile_first_core",
              current_tool_warnings: [],
              current_mounted_tools: [],
              current_blocked_tools: [],
              current_discovery_entrypoints: [],
              updated_at: "2026-03-09T10:00:00Z",
            },
            warnings: [],
            capabilities: [],
          },
          {
            profile_id: "singleton:research",
            name: "Research Root Agent",
            scope: "system",
            project_id: "",
            mode: "singleton",
            origin_kind: "builtin",
            status: "active",
            active_revision: 1,
            draft_revision: 1,
            effective_snapshot_id: "worker-profile:singleton:research:v1",
            editable: false,
            summary: "适合资料整理、检索和信息提炼。",
            static_config: {
              base_archetype: "research",
              summary: "适合资料整理、检索和信息提炼。",
              model_alias: "main",
              tool_profile: "standard",
              default_tool_groups: ["web", "project"],
              selected_tools: ["web.search"],
              runtime_kinds: ["worker"],
              policy_refs: ["default"],
              instruction_overlays: [],
              tags: [],
              capabilities: ["research"],
              metadata: {},
            },
            dynamic_context: {
              active_project_id: "project-default",
              active_workspace_id: "workspace-default",
              active_work_count: 0,
              running_work_count: 0,
              attention_work_count: 0,
              latest_work_id: "",
              latest_task_id: "",
              latest_work_title: "",
              latest_work_status: "idle",
              latest_target_kind: "",
              current_selected_tools: ["web.search"],
              current_tool_resolution_mode: "profile_first_core",
              current_tool_warnings: [],
              current_mounted_tools: [],
              current_blocked_tools: [],
              current_discovery_entrypoints: [],
              updated_at: "2026-03-09T10:00:00Z",
            },
            warnings: [],
            capabilities: [],
          },
        ],
        summary: {
          default_profile_id: "project-default:main",
          default_profile_name: "默认主 Agent",
        },
      },
      owner_profile: {
        contract_version: "1.0.0",
        resource_type: "owner_profile",
        resource_id: "owner-profile:default",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        profile: {},
        overlays: [],
      },
      bootstrap_session: {
        contract_version: "1.0.0",
        resource_type: "bootstrap_session",
        resource_id: "bootstrap:current",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        session: {},
        resumable: true,
      },
      context_continuity: {
        contract_version: "1.0.0",
        resource_type: "context_continuity",
        resource_id: "context:overview",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: true, reasons: ["context_frames_empty"], unavailable_sections: [] },
        warnings: ["当前作用域还没有 context frames。"],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        sessions: [],
        frames: [],
      },
      policy_profiles: {
        contract_version: "1.0.0",
        resource_type: "policy_profiles",
        resource_id: "policy:profiles",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        active_profile_id: "default",
        profiles: [
          {
            profile_id: "strict",
            label: "谨慎",
            description: "更保守",
            allowed_tool_profile: "minimal",
            approval_policy: "可逆 / 不可逆操作都需要确认",
            risk_level: "warning",
            recommended_for: ["首次使用"],
            is_active: false,
          },
          {
            profile_id: "default",
            label: "平衡",
            description: "默认推荐",
            allowed_tool_profile: "standard",
            approval_policy: "仅不可逆操作需要确认",
            risk_level: "info",
            recommended_for: ["默认推荐"],
            is_active: true,
          },
        ],
      },
      capability_pack: {
        contract_version: "1.0.0",
        resource_type: "capability_pack",
        resource_id: "capability:bundled",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        selected_project_id: "project-default",
        selected_workspace_id: "workspace-default",
        pack: {
          pack_id: "bundled",
          version: "1.0.0",
          skills: [],
          tools: [],
          worker_profiles: [],
          bootstrap_files: [],
          fallback_toolset: [],
          degraded_reason: "",
          generated_at: "2026-03-09T10:00:00Z",
        },
      },
      skill_governance: {
        contract_version: "1.0.0",
        resource_type: "skill_governance",
        resource_id: "skills:governance",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        items: [
          {
            item_id: "skill:workers.review",
            label: "Worker Review",
            source_kind: "builtin",
            scope: "project",
            enabled_by_default: true,
            selected: true,
            selection_source: "default",
            availability: "available",
            trust_level: "trusted",
            blocking: false,
            required_secrets: [],
            missing_requirements: [],
            install_hint: "",
            details: {},
          },
        ],
        summary: {
          selected_count: 1,
          disabled_count: 0,
          builtin_skill_count: 1,
          mcp_item_count: 0,
        },
      },
      skill_provider_catalog: {
        contract_version: "1.0.0",
        resource_type: "skill_provider_catalog",
        resource_id: "skill-providers:catalog",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        items: [
          {
            provider_id: "workers.review",
            label: "Worker Review",
            description: "内置检查能力",
            source_kind: "builtin",
            editable: false,
            removable: false,
            enabled: true,
            availability: "available",
            trust_level: "trusted",
            model_alias: "main",
            worker_type: "general",
            tool_profile: "minimal",
            tools_allowed: [],
            selection_item_id: "skill:workers.review",
            prompt_template: "",
            install_hint: "",
            warnings: [],
            details: {},
          },
        ],
        summary: {
          installed_count: 1,
          custom_count: 0,
          builtin_count: 1,
        },
      },
      mcp_provider_catalog: {
        contract_version: "1.0.0",
        resource_type: "mcp_provider_catalog",
        resource_id: "mcp-providers:catalog",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        items: [],
        summary: {
          installed_count: 0,
          enabled_count: 0,
          healthy_count: 0,
        },
      },
      setup_governance: {
        contract_version: "1.0.0",
        resource_type: "setup_governance",
        resource_id: "setup:governance",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        project_scope: {
          section_id: "project_scope",
          label: "Project Scope",
          status: "ready",
          summary: "Default Project / Primary",
          warnings: [],
          blocking_reasons: [],
          details: {},
          source_refs: [],
        },
        provider_runtime: {
          section_id: "provider_runtime",
          label: "Provider Runtime",
          status: "ready",
          summary: "已启用 1 个 provider，runtime=proxy",
          warnings: [],
          blocking_reasons: [],
          details: {
            provider_entries: [],
            litellm_env_names: [],
            runtime_env_names: [],
            credential_profiles: [],
            openai_oauth_connected: false,
            openai_oauth_profile: "",
          },
          source_refs: [],
        },
        channel_access: {
          section_id: "channel_access",
          label: "Channel Access",
          status: "ready",
          summary: "web ready",
          warnings: [],
          blocking_reasons: [],
          details: {},
          source_refs: [],
        },
        agent_governance: {
          section_id: "agent_governance",
          label: "Agent Governance",
          status: "ready",
          summary: "默认主 Agent 已配置",
          warnings: [],
          blocking_reasons: [],
          details: {
            active_agent_profile: {
              profile_id: "agent-profile-default",
              scope: "project",
              project_id: "project-default",
              name: "默认主 Agent",
              persona_summary: "适合首次使用。",
              model_alias: "main",
              tool_profile: "standard",
              updated_at: "2026-03-09T10:00:00Z",
            },
          },
          source_refs: [],
        },
        tools_skills: {
          section_id: "tools_skills",
          label: "Tools & Skills",
          status: "ready",
          summary: "skills=1，mcp=0",
          warnings: [],
          blocking_reasons: [],
          details: {
            skill_summary: {
              builtin_skill_count: 1,
              mcp_item_count: 0,
            },
          },
          source_refs: [],
        },
        review: {
          ready: true,
          risk_level: "info",
          warnings: [],
          blocking_reasons: [],
          next_actions: ['检查已通过，可以点击“保存配置”。'],
          provider_runtime_risks: [],
          channel_exposure_risks: [],
          agent_autonomy_risks: [],
          tool_skill_readiness_risks: [],
          secret_binding_risks: [],
        },
      },
      delegation: {
        contract_version: "1.0.0",
        resource_type: "delegation_plane",
        resource_id: "delegation:overview",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        works: [],
        summary: { by_status: {} },
      },
      pipelines: {
        contract_version: "1.0.0",
        resource_type: "skill_pipeline",
        resource_id: "pipeline:overview",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        runs: [],
        summary: {},
      },
      automation: {
        contract_version: "1.0.0",
        resource_type: "automation_job",
        resource_id: "automation:jobs",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
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
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        overall_status: "ready",
        subsystems: [],
        recent_failures: [],
        runtime_snapshot: {},
        recovery_summary: {},
        update_summary: {},
        channel_summary: { telegram_enabled: false },
        deep_refs: {},
      },
      retrieval_platform: {
        contract_version: "1.0.0",
        resource_type: "retrieval_platform",
        resource_id: "retrieval:platform",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        profiles: [],
        corpora: [
          {
            corpus_kind: "memory",
            label: "Memory",
            active_generation_id: "gen-memory-default",
            pending_generation_id: "",
            active_profile_id: "builtin:engine-default",
            active_profile_target: "engine-default",
            desired_profile_id: "builtin:engine-default",
            desired_profile_target: "engine-default",
            state: "ready",
            summary: "当前 embedding 与在线索引保持一致。",
            last_cutover_at: "2026-03-09T10:00:00Z",
            warnings: [],
          },
          {
            corpus_kind: "knowledge_base",
            label: "知识库",
            active_generation_id: "",
            pending_generation_id: "",
            active_profile_id: "",
            active_profile_target: "",
            desired_profile_id: "builtin:engine-default",
            desired_profile_target: "engine-default",
            state: "reserved",
            summary: "知识库还没有接入内容。",
            last_cutover_at: null,
            warnings: [],
          },
        ],
        generations: [
          {
            generation_id: "gen-memory-default",
            corpus_kind: "memory",
            profile_id: "builtin:engine-default",
            profile_target: "engine-default",
            label: "Qwen3-Embedding-0.6B（默认）",
            status: "active",
            is_active: true,
            build_job_id: "",
            previous_generation_id: "",
            created_at: "2026-03-09T10:00:00Z",
            updated_at: "2026-03-09T10:00:00Z",
            activated_at: "2026-03-09T10:00:00Z",
            completed_at: "2026-03-09T10:00:00Z",
            rollback_deadline: null,
            warnings: [],
            metadata: {},
          },
        ],
        build_jobs: [],
        summary: {
          active_generation_count: 1,
          pending_generation_count: 0,
          profile_count: 1,
        },
      },
      memory: {
        contract_version: "1.0.0",
        resource_type: "memory_console",
        resource_id: "memory:overview",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        backend_id: "memu",
        retrieval_backend: "memu",
        backend_state: "healthy",
        index_health: {},
        filters: {
          project_id: "project-default",
          workspace_id: "workspace-default",
          scope_id: "",
          partition: "default",
          layer: "sor",
          query: "",
          include_history: false,
          include_vault_refs: false,
          limit: 20,
          cursor: "",
        },
        summary: {
          scope_count: 1,
          fragment_count: 2,
          sor_current_count: 3,
          sor_history_count: 0,
          vault_ref_count: 0,
          proposal_count: 1,
          pending_replay_count: 0,
        },
        records: [],
        available_scopes: [],
        available_partitions: [],
        available_layers: [],
        advanced_refs: {},
      },
      imports: {
        contract_version: "1.0.0",
        resource_type: "import_workbench",
        resource_id: "imports:workbench",
        schema_version: 1,
        generated_at: "2026-03-09T10:00:00Z",
        updated_at: "2026-03-09T10:00:00Z",
        status: "ready",
        degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
        warnings: [],
        capabilities: [],
        refs: {},
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        summary: {
          source_count: 0,
          recent_run_count: 0,
          resume_available_count: 0,
          warning_count: 0,
          error_count: 0,
        },
        sources: [],
        recent_runs: [],
        resume_entries: [],
      },
    },
  };
}

function buildWorkspace(
  workspaceId: string,
  projectId: string,
  name: string
) {
  return {
    workspace_id: workspaceId,
    project_id: projectId,
    slug: workspaceId,
    name,
    kind: "primary",
    root_path: `/tmp/${workspaceId}`,
  };
}

function buildSession(taskId: string, workId: string): SessionProjectionItem {
  return {
    session_id: `thread-${taskId}`,
    thread_id: `thread-${taskId}`,
    task_id: taskId,
    agent_profile_id: "",
    parent_task_id: "",
    parent_work_id: "",
    title: `Task ${taskId}`,
    status: "RUNNING",
    channel: "web",
    requester_id: "owner",
    project_id: "project-default",
    workspace_id: "workspace-default",
    runtime_kind: "worker",
    latest_message_summary: "正在处理中",
    latest_event_at: "2026-03-09T10:06:00Z",
    execution_summary: {
      work_id: workId,
    },
    capabilities: [],
    detail_refs: {},
  };
}

function buildWork(
  workId: string,
  status: string,
  options?: {
    title?: string;
    capabilities?: ControlPlaneCapability[];
    runtimeSummary?: Record<string, unknown>;
  }
): WorkProjectionItem {
  return {
    work_id: workId,
    task_id: `task-${workId}`,
    parent_work_id: "",
    title: options?.title ?? `Work ${workId}`,
    status,
    target_kind: "worker",
    selected_worker_type: "general",
    route_reason: "planner",
    owner_id: "owner",
    selected_tools: [],
    pipeline_run_id: "",
    runtime_id: "",
    project_id: "project-default",
    workspace_id: "workspace-default",
    requested_worker_profile_id: "",
    requested_worker_profile_version: 0,
    effective_worker_snapshot_id: "",
    child_work_ids: [],
    child_work_count: 0,
    merge_ready: false,
    runtime_summary: options?.runtimeSummary ?? {},
    updated_at: "2026-03-09T10:05:00Z",
    capabilities: options?.capabilities ?? [],
  };
}

function buildTaskDetail(taskId: string, title: string): TaskDetailResponse {
  return {
    task: {
      task_id: taskId,
      created_at: "2026-03-09T10:00:00Z",
      updated_at: "2026-03-09T10:05:00Z",
      status: "RUNNING",
      title,
      thread_id: `thread-${taskId}`,
      scope_id: "scope-default",
      requester: {
        channel: "web",
        sender_id: "owner",
      },
      risk_level: "low",
    },
    events: [],
    artifacts: [],
  };
}

describe("App workbench routing", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.sessionStorage.clear();
    window.localStorage.clear();
    window.history.pushState({}, "", "/");
  });

  it("默认根路由进入 Home，而不是旧控制台首页", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(buildSnapshot()));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "先连上一个真实模型" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Settings/ })).toBeInTheDocument();
  });

  it("Agents 路由提供主 Agent 与 Work Agent 管理入口", async () => {
    window.history.pushState({}, "", "/agents");

    const snapshot = buildSnapshot();
    snapshot.resources.delegation.works = [
      buildWork("work-ui", "running", {
        title: "UI Builder",
        runtimeSummary: {
          requested_tool_profile: "standard",
          requested_model_alias: "main",
        },
      }),
    ];
    snapshot.resources.capability_pack.pack.worker_profiles = [
      {
        worker_type: "dev",
        capabilities: ["frontend", "handoff"],
        default_model_alias: "main",
        default_tool_profile: "standard",
        default_tool_groups: ["filesystem", "web"],
        bootstrap_file_ids: [],
        runtime_kinds: ["worker"],
        metadata: {},
      },
    ];

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "当前项目的 Agent 管理" })
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "新建 Agent" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "当前项目默认会先用这一个" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "按职责拆开的辅助 Agent" })).toBeInTheDocument();
  });

  it("Advanced 路由默认先展示高级概览，再按需展开 legacy 控制台", async () => {
    window.history.pushState({}, "", "/advanced");

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(buildSnapshot()));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            events: [],
          })
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "高级诊断与恢复" })).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "OctoAgent Control Plane" })
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "打开详细控制台" }));

    expect(
      await screen.findByRole("heading", { name: "OctoAgent Control Plane" })
    ).toBeInTheDocument();
  });

  it("黄金路径 smoke 覆盖 Home / Chat / Agents / Settings / Memory / Advanced / Work", async () => {
    const snapshot = buildSnapshot();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            events: [],
          })
        );
      }
      return Promise.resolve(jsonResponse({}));
    });

    const routeChecks = [
      {
        path: "/",
        assertRoute: async () => {
          expect(
            await screen.findByRole("heading", { name: "先连上一个真实模型" })
          ).toBeInTheDocument();
        },
      },
      {
        path: "/chat",
        assertRoute: async () => {
          expect(
            await screen.findByRole("heading", { name: "开始一段对话" })
          ).toBeInTheDocument();
        },
      },
      {
        path: "/agents",
        assertRoute: async () => {
          expect(
            await screen.findByRole("heading", { name: "当前项目的 Agent 管理" })
          ).toBeInTheDocument();
        },
      },
      {
        path: "/settings",
        assertRoute: async () => {
          expect(
            await screen.findByRole("heading", { name: "先添加可用的模型 Provider" })
          ).toBeInTheDocument();
        },
      },
      {
        path: "/memory",
        assertRoute: async () => {
          expect(await screen.findByText("筛选与刷新")).toBeInTheDocument();
        },
      },
      {
        path: "/advanced",
        assertRoute: async () => {
          expect(await screen.findByRole("heading", { name: "高级诊断与恢复" })).toBeInTheDocument();
        },
      },
      {
        path: "/work",
        assertRoute: async () => {
          expect(await screen.findByText("现在最该看")).toBeInTheDocument();
        },
      },
    ] satisfies Array<{ path: string; assertRoute: () => Promise<void> }>;

    for (const route of routeChecks) {
      window.history.pushState({}, "", route.path);
      const view = render(<App />);
      await route.assertRoute();
      view.unmount();
    }

    expect(
      fetchMock.mock.calls.some((call) =>
        String((call as FetchArgs)[0]).includes("/api/control/snapshot")
      )
    ).toBe(true);
  });

  it("设置页会先执行 setup.review，再通过 setup.apply 提交并按 resource_refs 回刷", async () => {
    window.history.pushState({}, "", "/settings");

    const nextSnapshot = buildSnapshot();
    nextSnapshot.resources.skill_governance.items[0] = {
      ...nextSnapshot.resources.skill_governance.items[0],
      selected: false,
      selection_source: "project_override",
    };
    nextSnapshot.resources.skill_governance.summary = {
      ...nextSnapshot.resources.skill_governance.summary,
      selected_count: 0,
      disabled_count: 1,
    };
    nextSnapshot.resources.setup_governance.review = {
      ...nextSnapshot.resources.setup_governance.review,
      next_actions: ['检查已通过，可以点击“保存配置”。'],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, _init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(buildSnapshot()));
      }
      if (url.includes("/api/control/actions")) {
        const body = String((_init as RequestInit | undefined)?.body ?? "");
        if (body.includes('"action_id":"setup.review"')) {
          return Promise.resolve(
            jsonResponse({
              contract_version: "1.0.0",
              result: {
                contract_version: "1.0.0",
                request_id: "req-setup-review",
                correlation_id: "req-setup-review",
                action_id: "setup.review",
                status: "completed",
                code: "SETUP_REVIEW_READY",
                message: "配置检查已完成。",
                data: {
                  review: nextSnapshot.resources.setup_governance.review,
                },
                resource_refs: [
                  {
                    resource_type: "setup_governance",
                    resource_id: "setup:governance",
                    schema_version: 1,
                  },
                ],
                target_refs: [],
                handled_at: "2026-03-09T10:01:00Z",
              },
            })
          );
        }
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            result: {
              contract_version: "1.0.0",
              request_id: "req-setup-apply",
              correlation_id: "req-setup-apply",
              action_id: "setup.apply",
              status: "completed",
              code: "SETUP_APPLIED",
              message: "配置已保存，主 Agent 与系统设置已同步。",
              data: {
                review: nextSnapshot.resources.setup_governance.review,
              },
              resource_refs: [
                {
                  resource_type: "config_schema",
                  resource_id: "config:octoagent",
                  schema_version: 1,
                },
                {
                  resource_type: "diagnostics_summary",
                  resource_id: "diagnostics:runtime",
                  schema_version: 1,
                },
                {
                  resource_type: "setup_governance",
                  resource_id: "setup:governance",
                  schema_version: 1,
                },
                {
                  resource_type: "policy_profiles",
                  resource_id: "policy:profiles",
                  schema_version: 1,
                },
                {
                  resource_type: "skill_governance",
                  resource_id: "skills:governance",
                  schema_version: 1,
                },
                {
                  resource_type: "agent_profiles",
                  resource_id: "agent-profiles:overview",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-09T10:02:00Z",
            },
          })
        );
      }
      if (url.includes("/api/control/resources/config")) {
        return Promise.resolve(jsonResponse(nextSnapshot.resources.config));
      }
      if (url.includes("/api/control/resources/diagnostics")) {
        return Promise.resolve(jsonResponse(nextSnapshot.resources.diagnostics));
      }
      if (url.includes("/api/control/resources/setup-governance")) {
        return Promise.resolve(jsonResponse(nextSnapshot.resources.setup_governance));
      }
      if (url.includes("/api/control/resources/policy-profiles")) {
        return Promise.resolve(jsonResponse(nextSnapshot.resources.policy_profiles));
      }
      if (url.includes("/api/control/resources/skill-governance")) {
        return Promise.resolve(jsonResponse(nextSnapshot.resources.skill_governance));
      }
      if (url.includes("/api/control/resources/agent-profiles")) {
        return Promise.resolve(jsonResponse(nextSnapshot.resources.agent_profiles));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    await screen.findByRole("heading", { name: "先添加可用的模型 Provider" });
    await userEvent.click(screen.getByRole("button", { name: "添加 OpenAI" }));
    expect(await screen.findByDisplayValue("OpenAI")).toBeInTheDocument();
    expect(await screen.findByText("Agent 能力管理已移到 Agents")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "保存配置" })[0]!);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter((call) =>
          String((call as FetchArgs)[0]).includes("/api/control/actions")
        ).length >= 2
      ).toBe(true)
    );

    const actionBodies = fetchMock.mock.calls
      .filter((call) => String((call as FetchArgs)[0]).includes("/api/control/actions"))
      .map((call) => String((call as FetchArgs)[1]?.body ?? ""));

    expect(
      actionBodies.some((body) => body.includes('"action_id":"setup.review"'))
    ).toBe(true);
    expect(
      actionBodies.some((body) => body.includes('"action_id":"setup.apply"'))
    ).toBe(true);
    expect(
      actionBodies.some((body) => body.includes('"id":"openai"'))
    ).toBe(true);
    expect(
      actionBodies.some((body) => body.includes('"llm_mode":"litellm"'))
    ).toBe(true);
    expect(
      actionBodies.some((body) => body.includes('"litellm_proxy_url":"http://localhost:4000"'))
    ).toBe(true);
    expect(
      actionBodies.some((body) => body.includes('"master_key_env":"LITELLM_MASTER_KEY"'))
    ).toBe(true);
    expect(
      actionBodies.some((body) => body.includes('"LITELLM_MASTER_KEY":"'))
    ).toBe(true);
    expect(await screen.findByText(/主 Agent 与系统设置已同步/)).toBeInTheDocument();
  });

  it("设置页可以一键连接并启用真实模型", async () => {
    window.history.pushState({}, "", "/settings");

    const nextSnapshot = buildSnapshot("http://localhost:4000");
    nextSnapshot.resources.setup_governance.review = {
      ...nextSnapshot.resources.setup_governance.review,
      next_actions: ["真实模型已就绪。"],
    };

    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(buildSnapshot()));
      }
      if (url.includes("/api/control/actions")) {
        const body = String((init as RequestInit | undefined)?.body ?? "");
        if (body.includes('"action_id":"setup.review"')) {
          return Promise.resolve(
            jsonResponse({
              contract_version: "1.0.0",
              result: {
                contract_version: "1.0.0",
                request_id: "req-setup-review",
                correlation_id: "req-setup-review",
                action_id: "setup.review",
                status: "completed",
                code: "SETUP_REVIEW_READY",
                message: "配置检查已完成。",
                data: {
                  review: {
                    ...nextSnapshot.resources.setup_governance.review,
                    ready: true,
                  },
                },
                resource_refs: [
                  {
                    resource_type: "setup_governance",
                    resource_id: "setup:governance",
                    schema_version: 1,
                  },
                ],
                target_refs: [],
                handled_at: "2026-03-09T10:01:00Z",
              },
            })
          );
        }
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            result: {
              contract_version: "1.0.0",
              request_id: "req-quick-connect",
              correlation_id: "req-quick-connect",
              action_id: "setup.quick_connect",
              status: "completed",
              code: "SETUP_QUICK_CONNECTED",
              message: "已启动 LiteLLM Proxy，当前实例会在几秒内自动重启并切到真实模型。",
              data: {
                review: {
                  ...nextSnapshot.resources.setup_governance.review,
                  ready: true,
                },
                activation: {
                  proxy_url: "http://localhost:4000",
                  runtime_reload_mode: "managed_restart_scheduled",
                  runtime_reload_message:
                    "已启动 LiteLLM Proxy，当前实例会在几秒内自动重启并切到真实模型。",
                },
              },
              resource_refs: [
                {
                  resource_type: "config_schema",
                  resource_id: "config:octoagent",
                  schema_version: 1,
                },
                {
                  resource_type: "diagnostics_summary",
                  resource_id: "diagnostics:runtime",
                  schema_version: 1,
                },
                {
                  resource_type: "setup_governance",
                  resource_id: "setup:governance",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-09T10:02:00Z",
            },
          })
        );
      }
      if (url.includes("/api/control/resources/config")) {
        return Promise.resolve(jsonResponse(nextSnapshot.resources.config));
      }
      if (url.includes("/api/control/resources/diagnostics")) {
        return Promise.resolve(jsonResponse(nextSnapshot.resources.diagnostics));
      }
      if (url.includes("/api/control/resources/setup-governance")) {
        return Promise.resolve(jsonResponse(nextSnapshot.resources.setup_governance));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    await screen.findByRole("heading", { name: "先添加可用的模型 Provider" });
    await userEvent.click(screen.getAllByRole("button", { name: "连接真实模型" })[0]);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) =>
          String((call as FetchArgs)[1]?.body ?? "").includes('"action_id":"setup.quick_connect"')
        )
      ).toBe(true)
    );
    expect(
      await screen.findByText(/当前实例会在几秒内自动重启并切到真实模型/)
    ).toBeInTheDocument();
  });

  it("Agents 页在保存失败时会保留未提交的 Agent 草稿", async () => {
    window.history.pushState({}, "", "/agents");

    const snapshot = buildSnapshot() as any;
    snapshot.resources.capability_pack.pack.tools = [
      {
        tool_name: "project.inspect",
        label: "项目检查",
        tool_group: "project",
        availability: "available",
      },
      {
        tool_name: "runtime.inspect",
        label: "运行检查",
        tool_group: "runtime",
        availability: "available",
      },
    ];
    snapshot.resources.worker_profiles = {
      generated_at: "2026-03-09T10:00:00Z",
      profiles: [
        {
          profile_id: "project-default:main",
          name: "默认主 Agent",
          scope: "project",
          project_id: "project-default",
          mode: "singleton",
          origin_kind: "custom",
          status: "active",
          active_revision: 2,
          draft_revision: 2,
          effective_snapshot_id: "worker-profile:project-default:main:v2",
          editable: true,
          summary: "负责默认聊天入口。",
          static_config: {
            base_archetype: "general",
            summary: "负责默认聊天入口。",
            model_alias: "main",
            tool_profile: "standard",
            default_tool_groups: ["project"],
            selected_tools: ["project.inspect"],
            runtime_kinds: ["worker"],
            policy_refs: ["default"],
            instruction_overlays: [],
            tags: [],
            capabilities: ["planner"],
            metadata: {},
          },
          dynamic_context: {
            active_project_id: "project-default",
            active_workspace_id: "workspace-default",
            active_work_count: 0,
            running_work_count: 0,
            attention_work_count: 0,
            latest_work_id: "",
            latest_task_id: "",
            latest_work_title: "",
            latest_work_status: "idle",
            latest_target_kind: "",
            current_selected_tools: ["project.inspect"],
            current_tool_resolution_mode: "profile_first_core",
            current_tool_warnings: [],
            current_mounted_tools: [],
            current_blocked_tools: [],
            current_discovery_entrypoints: [],
            updated_at: "2026-03-09T10:00:00Z",
          },
          warnings: [],
          capabilities: [],
        },
        {
          profile_id: "singleton:general",
          name: "Butler Root Agent",
          scope: "system",
          project_id: "",
          mode: "singleton",
          origin_kind: "builtin",
          status: "active",
          active_revision: 1,
          draft_revision: 1,
          effective_snapshot_id: "worker-profile:singleton:general:v1",
          editable: false,
          summary: "适合主入口。",
          static_config: {
            base_archetype: "general",
            summary: "适合主入口。",
            model_alias: "main",
            tool_profile: "standard",
            default_tool_groups: ["project"],
            selected_tools: ["project.inspect"],
            runtime_kinds: ["worker"],
            policy_refs: ["default"],
            instruction_overlays: [],
            tags: [],
            capabilities: ["planner"],
            metadata: {},
          },
          dynamic_context: {
            active_project_id: "project-default",
            active_workspace_id: "workspace-default",
            active_work_count: 0,
            running_work_count: 0,
            attention_work_count: 0,
            latest_work_id: "",
            latest_task_id: "",
            latest_work_title: "",
            latest_work_status: "idle",
            latest_target_kind: "",
            current_selected_tools: ["project.inspect"],
            current_tool_resolution_mode: "profile_first_core",
            current_tool_warnings: [],
            current_mounted_tools: [],
            current_blocked_tools: [],
            current_discovery_entrypoints: [],
            updated_at: "2026-03-09T10:00:00Z",
          },
          warnings: [],
          capabilities: [],
        },
      ],
      summary: {
        default_profile_id: "project-default:main",
        default_profile_name: "默认主 Agent",
      },
    };

    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/events")) {
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            events: [],
          })
        );
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        const body = String(init.body ?? "");
        if (body.includes('"action_id":"worker_profile.review"')) {
          return Promise.resolve(
            jsonResponse({
              contract_version: "1.0.0",
              result: {
                contract_version: "1.0.0",
                request_id: "req-worker-profile-review",
                correlation_id: "req-worker-profile-review",
                action_id: "worker_profile.review",
                status: "completed",
                code: "WORKER_PROFILE_REVIEW_READY",
                message: "检查完成。",
                data: {
                  review: {
                    can_save: true,
                    ready: true,
                    warnings: [],
                    blocking_reasons: [],
                    next_actions: ["可以保存。"],
                  },
                },
                resource_refs: [],
                target_refs: [],
                handled_at: "2026-03-09T10:01:00Z",
              },
            })
          );
        }
        return Promise.resolve(
          jsonResponse(
            {
              error: {
                message: "保存失败",
              },
            },
            500
          )
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "当前项目的 Agent 管理" })
    ).toBeInTheDocument();
    await userEvent.click((await screen.findAllByRole("button", { name: "编辑主 Agent" }))[0]!);
    const nameInput = (await screen.findByLabelText(/名称/)) as HTMLInputElement;
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "新的主 Agent");
    await userEvent.click(screen.getByRole("button", { name: "保存主 Agent" }));

    await screen.findByText("刚才的操作没有成功。");
    expect(nameInput.value).toBe("新的主 Agent");
  });

  it("设置页会在未连接真实模型时展示 Provider 提醒和别名编辑器", async () => {
    window.history.pushState({}, "", "/settings");

    const snapshot = buildSnapshot() as any;
    snapshot.resources.setup_governance.review = {
      ready: false,
      risk_level: "warning",
      warnings: ["当前还没有连接真实模型。"],
      blocking_reasons: ["agent_profile_name_missing"],
      next_actions: ["先填写主 Agent 名称，再重新保存。"],
      provider_runtime_risks: [
        {
          risk_id: "provider_missing",
          severity: "warning",
          title: "还没有可用 Provider",
          summary: "当前还没有连接真实模型；没配好前系统会先自动回退。",
          blocking: false,
          recommended_action: "先添加一个 Provider，并填 API Key 或完成 OAuth。",
          source_ref: "config:octoagent",
        },
      ],
      channel_exposure_risks: [],
      agent_autonomy_risks: [
        {
          risk_id: "agent_profile_name_missing",
          severity: "high",
          title: "主 Agent 名称不能为空",
          summary: "当前草稿缺少主 Agent 名称。",
          blocking: true,
          recommended_action: '先填写主 Agent 名称，再点击“检查配置”。',
          source_ref: "agent-profiles:overview",
        },
      ],
      tool_skill_readiness_risks: [],
      secret_binding_risks: [],
    };

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    await screen.findByRole("heading", { name: "先添加可用的模型 Provider" });
    expect(screen.getByText("还没有连接真实模型")).toBeInTheDocument();
    expect(screen.queryByText("agent_profile_name_missing")).not.toBeInTheDocument();
    expect(screen.queryByText(/主 Agent 的身份与边界只在 Agents 维护/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "去 Agents 调 Butler" })).not.toBeInTheDocument();
    expect(screen.queryByText("体验模式")).not.toBeInTheDocument();
    expect(screen.getByText("Gateway 地址、内部代理密钥和运行参数都由系统自己处理，不需要手动填写。")).toBeInTheDocument();
    expect(screen.queryByText("LiteLLM 代理地址")).not.toBeInTheDocument();
    expect(screen.queryByText("LiteLLM Master Key 值")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加 OpenRouter" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "添加 OpenRouter" }));
    await waitFor(() => expect(screen.getByDisplayValue("OPENROUTER_API_KEY")).toBeInTheDocument());
    expect(screen.getByDisplayValue("main")).toBeInTheDocument();
    expect(screen.getByDisplayValue("cheap")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "新增别名" }));
    await waitFor(() => expect(screen.getByDisplayValue("alias_3")).toBeInTheDocument());
  });

  it("设置页支持触发 OpenAI Auth 连接动作", async () => {
    window.history.pushState({}, "", "/settings");

    const snapshot = buildSnapshot();
    const refreshedSetup = {
      ...snapshot.resources.setup_governance,
      generated_at: "2026-03-09T10:01:00Z",
      updated_at: "2026-03-09T10:01:00Z",
      provider_runtime: {
        ...snapshot.resources.setup_governance.provider_runtime,
        details: {
          provider_entries: [
            {
              id: "openai-codex",
              name: "OpenAI Codex (ChatGPT Pro OAuth)",
              auth_type: "oauth",
              api_key_env: "OPENAI_API_KEY",
              enabled: true,
            },
          ],
          litellm_env_names: ["OPENAI_API_KEY"],
          runtime_env_names: [],
          credential_profiles: [
            {
              name: "openai-codex-default",
              provider: "openai-codex",
              auth_mode: "oauth",
            },
          ],
          openai_oauth_connected: true,
          openai_oauth_profile: "openai-codex-default",
        },
      },
    };

    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            result: {
              contract_version: "1.0.0",
              request_id: "req-oauth",
              correlation_id: "req-oauth",
              action_id: "provider.oauth.openai_codex",
              status: "completed",
              code: "OPENAI_OAUTH_CONNECTED",
              message: "OpenAI Auth 已连接，已写入本地凭证。",
              data: {
                provider_id: "openai-codex",
                profile_name: "openai-codex-default",
                env_name: "OPENAI_API_KEY",
              },
              resource_refs: [
                {
                  resource_type: "setup_governance",
                  resource_id: "setup:governance",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-09T10:01:00Z",
            },
          })
        );
      }
      if (url.includes("/api/control/resources/setup-governance")) {
        return Promise.resolve(jsonResponse(refreshedSetup));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    await screen.findByRole("heading", { name: "先添加可用的模型 Provider" });
    await userEvent.click(screen.getByRole("button", { name: "添加 OpenAI Auth" }));
    await userEvent.click(screen.getByRole("button", { name: "连接 OpenAI Auth" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) => {
          const body = String((call as FetchArgs)[1]?.body ?? "");
          return body.includes('"action_id":"provider.oauth.openai_codex"');
        })
      ).toBe(true)
    );
    expect(await screen.findByText("已授权")).toBeInTheDocument();
  });

  it("Agents 页会把结构化模型和工具选择提交到 worker_profile.review", async () => {
    window.history.pushState({}, "", "/agents");

    const snapshot = buildSnapshot() as any;
    snapshot.resources.capability_pack.pack.tools = [
      {
        tool_name: "project.inspect",
        label: "项目检查",
        tool_group: "project",
        availability: "available",
      },
      {
        tool_name: "runtime.inspect",
        label: "运行检查",
        tool_group: "runtime",
        availability: "available",
      },
    ];
    snapshot.resources.worker_profiles = {
      generated_at: "2026-03-09T10:00:00Z",
      profiles: [
        {
          profile_id: "project-default:main",
          name: "默认主 Agent",
          scope: "project",
          project_id: "project-default",
          mode: "singleton",
          origin_kind: "custom",
          status: "active",
          active_revision: 2,
          draft_revision: 2,
          effective_snapshot_id: "worker-profile:project-default:main:v2",
          editable: true,
          summary: "负责默认聊天入口。",
          static_config: {
            base_archetype: "general",
            summary: "负责默认聊天入口。",
            model_alias: "main",
            tool_profile: "standard",
            default_tool_groups: ["project"],
            selected_tools: ["project.inspect"],
            runtime_kinds: ["worker"],
            policy_refs: ["default"],
            instruction_overlays: [],
            tags: [],
            capabilities: ["planner"],
            metadata: {},
          },
          dynamic_context: {
            active_project_id: "project-default",
            active_workspace_id: "workspace-default",
            active_work_count: 0,
            running_work_count: 0,
            attention_work_count: 0,
            latest_work_id: "",
            latest_task_id: "",
            latest_work_title: "",
            latest_work_status: "idle",
            latest_target_kind: "",
            current_selected_tools: ["project.inspect"],
            current_tool_resolution_mode: "profile_first_core",
            current_tool_warnings: [],
            current_mounted_tools: [],
            current_blocked_tools: [],
            current_discovery_entrypoints: [],
            updated_at: "2026-03-09T10:00:00Z",
          },
          warnings: [],
          capabilities: [],
        },
        {
          profile_id: "singleton:general",
          name: "Butler Root Agent",
          scope: "system",
          project_id: "",
          mode: "singleton",
          origin_kind: "builtin",
          status: "active",
          active_revision: 1,
          draft_revision: 1,
          effective_snapshot_id: "worker-profile:singleton:general:v1",
          editable: false,
          summary: "适合主入口。",
          static_config: {
            base_archetype: "general",
            summary: "适合主入口。",
            model_alias: "main",
            tool_profile: "standard",
            default_tool_groups: ["project"],
            selected_tools: ["project.inspect"],
            runtime_kinds: ["worker"],
            policy_refs: ["default"],
            instruction_overlays: [],
            tags: [],
            capabilities: ["planner"],
            metadata: {},
          },
          dynamic_context: {
            active_project_id: "project-default",
            active_workspace_id: "workspace-default",
            active_work_count: 0,
            running_work_count: 0,
            attention_work_count: 0,
            latest_work_id: "",
            latest_task_id: "",
            latest_work_title: "",
            latest_work_status: "idle",
            latest_target_kind: "",
            current_selected_tools: ["project.inspect"],
            current_tool_resolution_mode: "profile_first_core",
            current_tool_warnings: [],
            current_mounted_tools: [],
            current_blocked_tools: [],
            current_discovery_entrypoints: [],
            updated_at: "2026-03-09T10:00:00Z",
          },
          warnings: [],
          capabilities: [],
        },
      ],
      summary: {
        default_profile_id: "project-default:main",
        default_profile_name: "默认主 Agent",
      },
    };

    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        const body = String(init.body ?? "");
        if (body.includes('"action_id":"worker_profile.review"')) {
          return Promise.resolve(
            jsonResponse({
              contract_version: "1.0.0",
              result: {
                contract_version: "1.0.0",
                request_id: "req-worker-profile-review",
                correlation_id: "req-worker-profile-review",
                action_id: "worker_profile.review",
                status: "completed",
                code: "WORKER_PROFILE_REVIEW_READY",
                message: "检查完成。",
                data: {
                  review: {
                    can_save: true,
                    ready: true,
                    warnings: [],
                    blocking_reasons: [],
                    next_actions: ["可以保存。"],
                  },
                },
                resource_refs: [],
                target_refs: [],
                handled_at: "2026-03-09T10:01:00Z",
              },
            })
          );
        }
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            result: {
              contract_version: "1.0.0",
              request_id: "req-worker-profile-apply",
              correlation_id: "req-worker-profile-apply",
              action_id: "worker_profile.apply",
              status: "completed",
              code: "WORKER_PROFILE_APPLIED",
              message: "已保存。",
              data: {
                profile_id: "project-default:main",
              },
              resource_refs: [],
              target_refs: [],
              handled_at: "2026-03-09T10:01:00Z",
            },
          })
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "当前项目的 Agent 管理" })
    ).toBeInTheDocument();
    await userEvent.click((await screen.findAllByRole("button", { name: "编辑主 Agent" }))[0]!);
    await userEvent.click(screen.getByRole("checkbox", { name: /运行检查/ }));
    await userEvent.click(screen.getByRole("button", { name: "保存主 Agent" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) =>
          String((call as FetchArgs)[1]?.body ?? "").includes('"action_id":"worker_profile.review"')
        )
      ).toBe(true)
    );

    const actionBody = fetchMock.mock.calls
      .filter((call) => String((call as FetchArgs)[0]).includes("/api/control/actions"))
      .map((call) => String((call as FetchArgs)[1]?.body ?? ""))
      .find((body) => body.includes('"action_id":"worker_profile.review"'));

    expect(actionBody).toContain('"default_tool_groups":["project"]');
    expect(actionBody).toContain('"selected_tools":["project.inspect","runtime.inspect"]');
    expect(actionBody).toContain('"base_archetype":"general"');
  });

  it("从带 hash 的 Settings 链接进入时会滚动到 Memory 分区", async () => {
    window.history.pushState({}, "", "/settings#settings-group-memory");

    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(buildSnapshot()));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "先添加可用的模型 Provider" })
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(scrollIntoView).toHaveBeenCalled();
    });
  });

  it("Work 页面会先展示 worker.review 方案，再批准 worker.apply", async () => {
    window.history.pushState({}, "", "/work");

    const snapshot = buildSnapshot();
    snapshot.resources.delegation.works = [
      buildWork("work-review", "running", {
        title: "拆分调研和开发",
        runtimeSummary: {
          requested_tool_profile: "standard",
        },
        capabilities: [
          {
            capability_id: "worker.review",
            label: "评审 Worker 方案",
            action_id: "worker.review",
            enabled: true,
            support_status: "supported",
            reason: "",
          },
        ],
      }),
    ] as typeof snapshot.resources.delegation.works;

    const refreshedDelegation = structuredClone(snapshot.resources.delegation);
    refreshedDelegation.works = [
      buildWork("work-review", "running", {
        title: "拆分调研和开发",
        capabilities: [
          {
            capability_id: "worker.review",
            label: "评审 Worker 方案",
            action_id: "worker.review",
            enabled: true,
            support_status: "supported",
            reason: "",
          },
        ],
      }),
      buildWork("child-research", "assigned", { title: "Research Child" }),
      buildWork("child-dev", "assigned", { title: "Dev Child" }),
    ] as typeof snapshot.resources.delegation.works;

    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        const body = String(init.body ?? "");
        if (body.includes('"action_id":"worker.review"')) {
          return Promise.resolve(
            jsonResponse({
              contract_version: "1.0.0",
              result: {
                contract_version: "1.0.0",
                request_id: "req-worker-review",
                correlation_id: "req-worker-review",
                action_id: "worker.review",
                status: "completed",
                code: "WORKER_REVIEW_READY",
                message: "已生成 Worker 评审方案。",
                data: {
                  plan: {
                    plan_id: "plan-1",
                    work_id: "work-review",
                    task_id: "task-work-review",
                    proposal_kind: "split",
                    objective: "拆分调研和开发",
                    summary: "建议拆成 research 和 dev 两条 worker。",
                    requires_user_confirmation: true,
                    assignments: [
                      {
                        objective: "先调研 API",
                        worker_type: "research",
                        target_kind: "subagent",
                        tool_profile: "minimal",
                        title: "Research",
                        reason: "先收集事实",
                      },
                      {
                        objective: "再补代码和测试",
                        worker_type: "dev",
                        target_kind: "subagent",
                        tool_profile: "standard",
                        title: "Dev",
                        reason: "需要实现和验证",
                      },
                    ],
                    merge_candidate_ids: [],
                    warnings: ["批准前请检查权限级别"],
                  },
                },
                resource_refs: [
                  {
                    resource_type: "delegation_plane",
                    resource_id: "delegation:overview",
                    schema_version: 1,
                  },
                ],
                target_refs: [],
                handled_at: "2026-03-09T10:06:00Z",
              },
            })
          );
        }
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            result: {
              contract_version: "1.0.0",
              request_id: "req-worker-apply",
              correlation_id: "req-worker-apply",
              action_id: "worker.apply",
              status: "completed",
              code: "WORKER_PLAN_APPLIED",
              message: "已按批准的 Worker 方案执行。",
              data: {
                child_tasks: ["child-research", "child-dev"],
              },
              resource_refs: [
                {
                  resource_type: "delegation_plane",
                  resource_id: "delegation:overview",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-09T10:07:00Z",
            },
          })
        );
      }
      if (url.includes("/api/control/resources/delegation")) {
        return Promise.resolve(jsonResponse(refreshedDelegation));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByText("拆分调研和开发")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "评审 Worker 方案" }));

    expect(await screen.findByText("建议拆成 research 和 dev 两条 worker。")).toBeInTheDocument();
    expect(screen.getByText("Research · minimal")).toBeInTheDocument();
    expect(screen.getByText("Dev · standard")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "批准并执行" }));

    expect(await screen.findByText(/已按批准的 Worker 方案执行/)).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some((call) =>
        String((call as FetchArgs)[1]?.body ?? "").includes('"action_id":"worker.apply"')
      )
    ).toBe(true);
  });

  it("Memory 页面会收口成记忆主路径，不再混入 operator 与备份入口", async () => {
    window.history.pushState({}, "", "/memory");

    const snapshot = buildSnapshot();
    const focusedSession = buildSession("task-memory-1", "work-memory-1");
    snapshot.resources.sessions.sessions = [focusedSession];
    snapshot.resources.sessions.focused_session_id = focusedSession.session_id;
    snapshot.resources.sessions.focused_thread_id = focusedSession.thread_id;
    const initialOperatorSummary = snapshot.resources.sessions.operator_summary!;
    snapshot.resources.sessions.operator_summary = {
      ...initialOperatorSummary,
      total_pending: 1,
      approvals: 1,
      alerts: initialOperatorSummary.alerts,
      retryable_failures: initialOperatorSummary.retryable_failures,
      pairing_requests: initialOperatorSummary.pairing_requests,
      degraded_sources: initialOperatorSummary.degraded_sources,
      generated_at: initialOperatorSummary.generated_at,
    };
    snapshot.resources.sessions.operator_items = [
      {
        item_id: "approval:ap-1",
        kind: "approval",
        state: "pending",
        title: "允许读取受限记忆",
        summary: "主 Agent 想读取一条受限的 Vault 引用。",
        task_id: "task-memory-1",
        thread_id: "thread-memory-1",
        source_ref: "vault:subject-1",
        created_at: "2026-03-09T10:08:00Z",
        expires_at: null,
        pending_age_seconds: 30,
        suggested_actions: ["approve_once"],
        quick_actions: [
          {
            kind: "approve_once",
            label: "允许一次",
            style: "primary",
            enabled: true,
          },
        ],
        recent_action_result: null,
        metadata: {
          tool_name: "vault.retrieve",
        },
      },
    ];
    snapshot.resources.diagnostics.recovery_summary = {
      latest_backup: null,
      latest_recovery_drill: null,
      ready_for_restore: false,
    };

    let operatorResolved = false;
    let backupCreated = false;
    let sessionExported = false;
    let exportRequestBody: Record<string, unknown> | null = null;

    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        const body = String(init.body ?? "");
        if (body.includes('"action_id":"operator.approval.resolve"')) {
          operatorResolved = true;
          return Promise.resolve(
            jsonResponse({
              contract_version: "1.0.0",
              result: {
                contract_version: "1.0.0",
                request_id: "req-operator-approve",
                correlation_id: "req-operator-approve",
                action_id: "operator.approval.resolve",
                status: "completed",
                code: "OPERATOR_APPROVAL_RESOLVED",
                message: "已处理审批。",
                data: {},
                resource_refs: [
                  {
                    resource_type: "session_projection",
                    resource_id: "sessions:overview",
                    schema_version: 1,
                  },
                ],
                target_refs: [],
                handled_at: "2026-03-09T10:09:00Z",
              },
            })
          );
        }
        if (body.includes('"action_id":"backup.create"')) {
          backupCreated = true;
          return Promise.resolve(
            jsonResponse({
              contract_version: "1.0.0",
              result: {
                contract_version: "1.0.0",
                request_id: "req-backup-create",
                correlation_id: "req-backup-create",
                action_id: "backup.create",
                status: "completed",
                code: "BACKUP_CREATED",
                message: "已创建 backup bundle",
                data: {
                  output_path: "/tmp/backup.zip",
                },
                resource_refs: [
                  {
                    resource_type: "diagnostics_summary",
                    resource_id: "diagnostics:runtime",
                    schema_version: 1,
                  },
                ],
                target_refs: [],
                handled_at: "2026-03-09T10:10:00Z",
              },
            })
          );
        }
        if (body.includes('"action_id":"session.export"')) {
          sessionExported = true;
          exportRequestBody = JSON.parse(body) as Record<string, unknown>;
          return Promise.resolve(
            jsonResponse({
              contract_version: "1.0.0",
              result: {
                contract_version: "1.0.0",
                request_id: "req-session-export",
                correlation_id: "req-session-export",
                action_id: "session.export",
                status: "completed",
                code: "SESSION_EXPORTED",
                message: "已导出会话数据",
                data: {
                  output_path: "/tmp/chats.jsonl",
                  tasks: [{ task_id: focusedSession.task_id }],
                },
                resource_refs: [
                  {
                    resource_type: "session_projection",
                    resource_id: "sessions:overview",
                    schema_version: 1,
                  },
                ],
                target_refs: [],
                handled_at: "2026-03-09T10:11:00Z",
              },
            })
          );
        }
        if (body.includes('"action_id":"diagnostics.refresh"')) {
          return Promise.resolve(
            jsonResponse({
              contract_version: "1.0.0",
              result: {
                contract_version: "1.0.0",
                request_id: "req-diagnostics-refresh",
                correlation_id: "req-diagnostics-refresh",
                action_id: "diagnostics.refresh",
                status: "completed",
                code: "DIAGNOSTICS_REFRESHED",
                message: "已刷新 diagnostics",
                data: {},
                resource_refs: [
                  {
                    resource_type: "diagnostics_summary",
                    resource_id: "diagnostics:runtime",
                    schema_version: 1,
                  },
                ],
                target_refs: [],
                handled_at: "2026-03-09T10:12:00Z",
              },
            })
          );
        }
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            result: {
              contract_version: "1.0.0",
              request_id: "req-operator-approve",
              correlation_id: "req-operator-approve",
              action_id: "operator.approval.resolve",
              status: "completed",
              code: "OPERATOR_APPROVAL_RESOLVED",
              message: "已处理审批。",
              data: {},
              resource_refs: [
                {
                  resource_type: "session_projection",
                  resource_id: "sessions:overview",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-09T10:09:00Z",
            },
          })
        );
      }
      if (url.includes("/api/control/resources/sessions")) {
        const nextSessions = structuredClone(snapshot.resources.sessions);
        if (operatorResolved) {
          const nextOperatorSummary = nextSessions.operator_summary!;
          nextSessions.operator_summary = {
            ...nextOperatorSummary,
            total_pending: 0,
            approvals: 0,
            alerts: nextOperatorSummary.alerts,
            retryable_failures: nextOperatorSummary.retryable_failures,
            pairing_requests: nextOperatorSummary.pairing_requests,
            degraded_sources: nextOperatorSummary.degraded_sources,
            generated_at: nextOperatorSummary.generated_at,
          };
          nextSessions.operator_items = [];
        }
        return Promise.resolve(jsonResponse(nextSessions));
      }
      if (url.includes("/api/control/resources/diagnostics")) {
        const nextDiagnostics = structuredClone(snapshot.resources.diagnostics);
        if (backupCreated) {
          nextDiagnostics.recovery_summary = {
            latest_backup: {
              bundle_id: "bundle-1",
              output_path: "/tmp/backup.zip",
              created_at: "2026-03-09T10:10:00Z",
              size_bytes: 1024,
              manifest: {
                manifest_version: 1,
                bundle_id: "bundle-1",
                created_at: "2026-03-09T10:10:00Z",
                source_project_root: "/tmp/project",
                scopes: ["all"],
                files: [],
                warnings: [],
                excluded_paths: [],
                sensitivity_level: "metadata_only",
                notes: [],
              },
            },
            latest_recovery_drill: null,
            ready_for_restore: false,
          };
        }
        return Promise.resolve(
          jsonResponse(nextDiagnostics)
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByRole("button", { name: "整理最新记忆" })).toBeInTheDocument();
    expect((await screen.findAllByText("打开 Settings > Memory")).length).toBeGreaterThan(0);
    expect(screen.queryByText("待确认事项")).not.toBeInTheDocument();
    expect(screen.queryByText("备份与恢复")).not.toBeInTheDocument();
    expect(screen.queryByText("允许读取受限记忆")).not.toBeInTheDocument();
    expect(screen.queryByText("创建备份")).not.toBeInTheDocument();
    expect(screen.queryByText("导出当前会话")).not.toBeInTheDocument();
    expect(operatorResolved).toBe(false);
    expect(backupCreated).toBe(false);
    expect(sessionExported).toBe(false);
    expect(exportRequestBody).toBeNull();
  });

  it("Work 页面会禁用 terminal work 的 worker.review 按钮", async () => {
    window.history.pushState({}, "", "/work");

    const snapshot = buildSnapshot();
    snapshot.resources.delegation.works = [
      buildWork("work-done", "succeeded", {
        title: "Done Work",
        capabilities: [
          {
            capability_id: "worker.review",
            label: "评审 Worker 方案",
            action_id: "worker.review",
            enabled: false,
            support_status: "supported",
            reason: "",
          },
        ],
      }),
    ] as typeof snapshot.resources.delegation.works;

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    const button = await screen.findByRole("button", { name: "评审 Worker 方案" });
    expect(button).toBeDisabled();
  });

  it("project.select 后会全量回刷工作台并同步 Project 摘要", async () => {
    const beforeSnapshot = buildSnapshot();
    beforeSnapshot.resources.project_selector.available_projects.push({
      project_id: "project-ops",
      slug: "ops",
      name: "Ops Project",
      is_default: false,
      status: "active",
      workspace_ids: ["workspace-ops"],
      warnings: [],
    });
    beforeSnapshot.resources.project_selector.available_workspaces.push(
      buildWorkspace("workspace-ops", "project-ops", "Ops Primary")
    );

    const afterSnapshot = buildSnapshot();
    afterSnapshot.resources.project_selector.current_project_id = "project-ops";
    afterSnapshot.resources.project_selector.current_workspace_id = "workspace-ops";
    afterSnapshot.resources.project_selector.available_projects =
      beforeSnapshot.resources.project_selector.available_projects;
    afterSnapshot.resources.project_selector.available_workspaces =
      beforeSnapshot.resources.project_selector.available_workspaces;
    afterSnapshot.resources.sessions.operator_summary = {
      total_pending: 4,
      approvals: 3,
      alerts: 0,
      retryable_failures: 0,
      pairing_requests: 1,
      degraded_sources: [],
      generated_at: "2026-03-09T10:03:00Z",
    };
    afterSnapshot.resources.delegation.works = [
      buildWork("work-ops", "running", { title: "Ops Work" }),
    ] as typeof afterSnapshot.resources.delegation.works;
    afterSnapshot.resources.delegation.summary = { by_status: { running: 1 } };
    afterSnapshot.resources.memory.summary = {
      ...afterSnapshot.resources.memory.summary,
      sor_current_count: 8,
    };

    let snapshotCallCount = 0;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        snapshotCallCount += 1;
        return Promise.resolve(
          jsonResponse(snapshotCallCount === 1 ? beforeSnapshot : afterSnapshot)
        );
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
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
              handled_at: "2026-03-09T10:03:00Z",
            },
          })
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    await screen.findByRole("heading", { name: "先连上一个真实模型" });
    await userEvent.selectOptions(screen.getByLabelText("切换 Project"), "project-ops");
    await userEvent.click(screen.getByRole("button", { name: "切换" }));

    await waitFor(() =>
      expect(screen.getAllByText("Ops Project").length).toBeGreaterThan(0)
    );
    expect(screen.getAllByText("Ops Primary").length).toBeGreaterThan(0);
    const actionCall = fetchMock.mock.calls.find((call) => {
      const [url, init] = call as FetchArgs;
      return String(url).includes("/api/control/actions") && init?.method === "POST";
    }) as FetchArgs | undefined;
    expect(String(actionCall?.[1]?.body)).toContain('"project_id":"project-ops"');
    expect(String(actionCall?.[1]?.body)).toContain('"workspace_id":"workspace-ops"');
    expect(
      fetchMock.mock.calls.filter((call) =>
        String((call as FetchArgs)[0]).includes("/api/control/snapshot")
      )
    ).toHaveLength(2);
  });

  it("首页允许在同一 Project 内切换 Workspace", async () => {
    const beforeSnapshot = buildSnapshot();
    beforeSnapshot.resources.project_selector.available_workspaces.push(
      buildWorkspace("workspace-analysis", "project-default", "Analysis")
    );

    const afterSnapshot = buildSnapshot();
    afterSnapshot.resources.project_selector.current_workspace_id = "workspace-analysis";
    afterSnapshot.resources.project_selector.available_workspaces =
      beforeSnapshot.resources.project_selector.available_workspaces;

    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        const snapshot =
          fetchMock.mock.calls.filter((call) =>
            String((call as FetchArgs)[0]).includes("/api/control/snapshot")
          ).length === 1
            ? beforeSnapshot
            : afterSnapshot;
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            result: {
              contract_version: "1.0.0",
              request_id: "req-workspace-select",
              correlation_id: "req-workspace-select",
              action_id: "project.select",
              status: "completed",
              code: "PROJECT_SELECTED",
              message: "已切换当前 project",
              data: {
                project_id: "project-default",
                workspace_id: "workspace-analysis",
              },
              resource_refs: [
                {
                  resource_type: "project_selector",
                  resource_id: "project:selector",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-09T10:04:00Z",
            },
          })
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    await screen.findByRole("heading", { name: "先连上一个真实模型" });
    await userEvent.selectOptions(screen.getByLabelText("切换 Workspace"), "workspace-analysis");
    await userEvent.click(screen.getByRole("button", { name: "切换" }));

    const actionCall = fetchMock.mock.calls.find((call) => {
      const [url, init] = call as FetchArgs;
      return String(url).includes("/api/control/actions") && init?.method === "POST";
    }) as FetchArgs | undefined;

    expect(String(actionCall?.[1]?.body)).toContain('"project_id":"project-default"');
    expect(String(actionCall?.[1]?.body)).toContain('"workspace_id":"workspace-analysis"');
    expect(await screen.findByText("workspace-analysis")).toBeInTheDocument();
  });

  it("重新进入 Chat 时会恢复当前聚焦会话的历史消息", async () => {
    window.history.pushState({}, "", "/chat");

    const snapshot = buildSnapshot();
    const focusedSession = buildSession("task-chat-restore", "work-chat-restore");
    snapshot.resources.sessions.sessions = [focusedSession];
    snapshot.resources.sessions.focused_session_id = focusedSession.session_id;
    snapshot.resources.sessions.focused_thread_id = focusedSession.thread_id;

    const detail = buildTaskDetail("task-chat-restore", "历史对话");
    detail.events = [
      {
        event_id: "evt-user-1",
        task_seq: 1,
        ts: "2026-03-09T10:01:00Z",
        type: "USER_MESSAGE",
        actor: "user",
        payload: {
          text: "帮我整理这周的发布安排",
        },
      },
      {
        event_id: "evt-agent-hidden",
        task_seq: 2,
        ts: "2026-03-09T10:01:30Z",
        type: "MODEL_CALL_COMPLETED",
        actor: "system",
        payload: {
          skill_id: "chat.general.inline",
          response_summary: "这是中间 skill 的内部摘要。",
        },
      },
      {
        event_id: "evt-agent-1",
        task_seq: 3,
        ts: "2026-03-09T10:02:00Z",
        type: "MODEL_CALL_COMPLETED",
        actor: "system",
        payload: {
          response_summary: "这周先锁定范围，再排发布时间表。",
        },
      },
    ];

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/tasks/task-chat-restore")) {
        return Promise.resolve(jsonResponse(detail));
      }
      if (url.includes("/api/control/resources/sessions")) {
        return Promise.resolve(jsonResponse(snapshot.resources.sessions));
      }
      if (url.includes("/api/control/resources/delegation")) {
        return Promise.resolve(jsonResponse(snapshot.resources.delegation));
      }
      if (url.includes("/api/control/resources/context-frames")) {
        return Promise.resolve(jsonResponse(snapshot.resources.context_continuity));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByText("帮我整理这周的发布安排")).toBeInTheDocument();
    expect(await screen.findByText("这周先锁定范围，再排发布时间表。")).toBeInTheDocument();
    expect(screen.queryByText("这是中间 skill 的内部摘要。")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "历史对话" })).toBeInTheDocument();
  });

  it("重新进入 Chat 时会优先恢复最近的 Web 会话", async () => {
    window.history.pushState({}, "", "/chat");

    const snapshot = buildSnapshot();
    const telegramSession = {
      ...buildSession("task-telegram-1", "work-telegram-1"),
      channel: "telegram",
      title: "Telegram 会话",
    };
    const webSession = {
      ...buildSession("task-chat-web", "work-chat-web"),
      title: "Web 会话",
      latest_event_at: "2026-03-09T10:09:00Z",
    };
    snapshot.resources.sessions.sessions = [telegramSession, webSession];
    snapshot.resources.sessions.focused_session_id = "";
    snapshot.resources.sessions.focused_thread_id = "";

    const detail = buildTaskDetail("task-chat-web", "Web 会话");
    detail.events = [
      {
        event_id: "evt-user-web",
        task_seq: 1,
        ts: "2026-03-09T10:08:00Z",
        type: "USER_MESSAGE",
        actor: "user",
        payload: {
          text: "帮我回顾今天的 Web 对话",
        },
      },
      {
        event_id: "evt-agent-web",
        task_seq: 2,
        ts: "2026-03-09T10:09:00Z",
        type: "MODEL_CALL_COMPLETED",
        actor: "system",
        payload: {
          response_summary: "这里是最近一轮 Web 对话的摘要。",
        },
      },
    ];

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/tasks/task-chat-web")) {
        return Promise.resolve(jsonResponse(detail));
      }
      if (url.includes("/api/control/resources/sessions")) {
        return Promise.resolve(jsonResponse(snapshot.resources.sessions));
      }
      if (url.includes("/api/control/resources/delegation")) {
        return Promise.resolve(jsonResponse(snapshot.resources.delegation));
      }
      if (url.includes("/api/control/resources/context-frames")) {
        return Promise.resolve(jsonResponse(snapshot.resources.context_continuity));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByText("帮我回顾今天的 Web 对话")).toBeInTheDocument();
    expect(await screen.findByText("这里是最近一轮 Web 对话的摘要。")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: "Web 会话" }).length).toBeGreaterThan(0);
  });

  it("刷新 Chat 时会在失效的 active task 之后回退到最近的 Web 会话", async () => {
    window.history.pushState({}, "", "/chat");
    window.sessionStorage.setItem("octoagent.chat.activeTaskId", "task-missing");

    const snapshot = buildSnapshot();
    const webSession = {
      ...buildSession("task-chat-fallback", "work-chat-fallback"),
      title: "回退会话",
      latest_event_at: "2026-03-09T10:12:00Z",
    };
    snapshot.resources.sessions.sessions = [webSession];

    const detail = buildTaskDetail("task-chat-fallback", "回退会话");
    detail.events = [
      {
        event_id: "evt-user-fallback",
        task_seq: 1,
        ts: "2026-03-09T10:11:00Z",
        type: "USER_MESSAGE",
        actor: "user",
        payload: {
          text: "帮我恢复最近的聊天",
        },
      },
      {
        event_id: "evt-agent-fallback",
        task_seq: 2,
        ts: "2026-03-09T10:11:30Z",
        type: "MODEL_CALL_COMPLETED",
        actor: "system",
        payload: {
          response_summary: "已经按最近的 Web 会话恢复。",
        },
      },
    ];

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/tasks/task-missing")) {
        return Promise.resolve(
          jsonResponse(
            {
              error: {
                code: "TASK_NOT_FOUND",
                message: "task-missing 不存在",
              },
            },
            404
          )
        );
      }
      if (url.includes("/api/tasks/task-chat-fallback")) {
        return Promise.resolve(jsonResponse(detail));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByText("帮我恢复最近的聊天")).toBeInTheDocument();
    expect(await screen.findByText("已经按最近的 Web 会话恢复。")).toBeInTheDocument();
    expect(window.sessionStorage.getItem("octoagent.chat.activeTaskId")).toBe("task-chat-fallback");
  });

  it("Work 看板会覆盖完整状态并在 split 失败时保留草稿", async () => {
    window.history.pushState({}, "", "/work");

    const snapshot = buildSnapshot();
    snapshot.resources.delegation.works = [
      buildWork("work-assigned", "assigned", { title: "Assigned Work" }),
      buildWork("work-escalated", "escalated", { title: "Escalated Work" }),
      buildWork("work-timeout", "timed_out", { title: "Timed Out Work" }),
      buildWork("work-split", "running", {
        title: "Split Work",
        capabilities: [
          {
            capability_id: "work.split",
            label: "拆分 Work",
            action_id: "work.split",
            enabled: true,
            support_status: "supported",
            reason: "",
          },
        ],
      }),
    ] as typeof snapshot.resources.delegation.works;

    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse(
            {
              error: {
                code: "WORK_SPLIT_FAILED",
                message: "拆分失败",
              },
            },
            500
          )
        );
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    await screen.findByRole("heading", {
      name: "3 条后台工作还没收尾",
    });

    const activeCard = screen.getAllByText("后台未收尾")[0]?.closest("article");
    const doneCard = screen.getAllByText("已结束")[0]?.closest("article");
    expect(activeCard).not.toBeNull();
    expect(doneCard).not.toBeNull();
    expect(within(activeCard!).getByText("3")).toBeInTheDocument();
    expect(within(doneCard!).getByText("1")).toBeInTheDocument();

    const textarea = await screen.findByLabelText("拆分成子目标");
    await userEvent.type(textarea, "整理依赖\n补测试");
    await userEvent.click(screen.getByRole("button", { name: "拆成子工作" }));

    expect(await screen.findByText("拆分失败")).toBeInTheDocument();
    expect(textarea).toHaveValue("整理依赖\n补测试");
  });

  it("Work 看板会把实时问题能力和相关运行真相翻译成可读摘要", async () => {
    window.history.pushState({}, "", "/work");

    const snapshot = buildSnapshot();
    snapshot.resources.context_continuity.degraded = {
      is_degraded: true,
      reasons: ["owner_timezone_missing"],
      unavailable_sections: [],
    };
    snapshot.resources.context_continuity.frames = [
      {
        context_frame_id: "frame-freshness-1",
        task_id: "task-work-weather",
        session_id: "session-work-weather",
        project_id: "project-default",
        workspace_id: "workspace-default",
        agent_profile_id: "agent-profile-default",
        recent_summary: "围绕天气查询更新了当前运行事实。",
        memory_hit_count: 0,
        memory_hits: [],
        memory_recall: {},
        budget: {},
        source_refs: [],
        degraded_reason: "owner_timezone_missing",
        created_at: "2026-03-12T08:00:00Z",
      },
    ];
    snapshot.resources.capability_pack.pack.worker_profiles = [
      {
        worker_type: "research",
        capabilities: ["research", "web"],
        default_model_alias: "main",
        default_tool_profile: "standard",
        default_tool_groups: ["network", "browser", "session"],
        bootstrap_file_ids: ["bootstrap:shared", "bootstrap:research"],
        runtime_kinds: ["worker", "subagent"],
        metadata: {},
      },
    ];
    snapshot.resources.capability_pack.pack.tools = [
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
      },
    ];
    snapshot.resources.delegation.works = [
      buildWork("work-weather", "running", {
        title: "查询北京今天会不会下雨",
        runtimeSummary: {
          delegation_strategy: "butler_owned_freshness",
          final_speaker: "butler",
          research_route_reason: "worker_type=research | fallback=single_worker",
          research_tool_profile: "standard",
          research_a2a_conversation_id: "a2a-weather-1",
          research_worker_agent_session_id: "agent-session-worker-research-1",
          research_a2a_message_count: 2,
          research_child_status: "SUCCEEDED",
        },
      }),
    ];
    snapshot.resources.delegation.works[0]!.selected_worker_type = "general";
    snapshot.resources.delegation.works[0]!.route_reason =
      "delegation_strategy=butler_owned_freshness";
    snapshot.resources.delegation.works[0]!.selected_tools = [];

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/actions") && input) {
        return Promise.resolve(jsonResponse({}));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByText("实时问题能力已经部分可用")).toBeInTheDocument();
    expect(
      screen.getByText(/主链已经存在，但当前还有降级或环境限制/)
    ).toBeInTheDocument();
    expect(screen.getByText(/Research Worker · 标准工具面 · network \/ browser \/ session/)).toBeInTheDocument();
    expect(screen.getAllByText(/owner timezone 未配置/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Butler 会先接住这条实时问题/).length).toBeGreaterThan(0);
  });

  it("Work 看板会显示后台 embedding 迁移，并允许切换到新索引", async () => {
    window.history.pushState({}, "", "/work");

    const snapshot = buildSnapshot();
    snapshot.resources.retrieval_platform!.corpora[0] = {
      ...snapshot.resources.retrieval_platform!.corpora[0],
      pending_generation_id: "gen-memory-next",
      desired_profile_id: "alias:knowledge-embed",
      desired_profile_target: "knowledge-embed",
      state: "migration_pending",
      summary: "新的 embedding 已准备好切换，但当前查询仍继续使用旧索引。",
      warnings: ["embedding 迁移尚未 cutover；当前仍使用 engine-default。"],
    };
    snapshot.resources.retrieval_platform!.generations.push({
      generation_id: "gen-memory-next",
      corpus_kind: "memory",
      profile_id: "alias:knowledge-embed",
      profile_target: "knowledge-embed",
      label: "knowledge-embed",
      status: "ready_to_cutover",
      is_active: false,
      build_job_id: "job-memory-next",
      previous_generation_id: "gen-memory-default",
      created_at: "2026-03-09T10:10:00Z",
      updated_at: "2026-03-09T10:15:00Z",
      activated_at: null,
      completed_at: "2026-03-09T10:15:00Z",
      rollback_deadline: null,
      warnings: [],
      metadata: {},
    });
    snapshot.resources.retrieval_platform!.build_jobs.push({
      job_id: "job-memory-next",
      corpus_kind: "memory",
      generation_id: "gen-memory-next",
      stage: "ready_to_cutover",
      summary: "新索引已经准备好，等待切换。",
      total_items: 120,
      processed_items: 120,
      percent_complete: 100,
      can_cancel: true,
      eta_seconds: 0,
      created_at: "2026-03-09T10:10:00Z",
      updated_at: "2026-03-09T10:15:00Z",
      completed_at: "2026-03-09T10:15:00Z",
      latest_error: "",
      latest_maintenance_run_id: "run-memory-next",
      metadata: {},
    });

    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
            result: {
              contract_version: "1.0.0",
              request_id: "req-retrieval-cutover",
              correlation_id: "req-retrieval-cutover",
              action_id: "retrieval.index.cutover",
              status: "completed",
              code: "RETRIEVAL_GENERATION_CUTOVER_READY",
              message: "已切换到新的 embedding 索引。",
              data: {},
              resource_refs: [
                {
                  resource_type: "retrieval_platform",
                  resource_id: "retrieval:platform",
                  schema_version: 1,
                },
              ],
              target_refs: [],
              handled_at: "2026-03-09T10:16:00Z",
            },
          })
        );
      }
      if (url.includes("/api/control/resources/retrieval-platform")) {
        return Promise.resolve(jsonResponse(snapshot.resources.retrieval_platform!));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByText("后台索引任务")).toBeInTheDocument();
    expect(screen.getByText("Embedding 迁移正在后台准备，不会中断当前检索")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "切换到新索引" }));

    expect(
      fetchMock.mock.calls.some((call) =>
        String((call as FetchArgs)[1]?.body ?? "").includes('"action_id":"retrieval.index.cutover"')
      )
    ).toBe(true);
    expect(
      fetchMock.mock.calls.some((call) =>
        String((call as FetchArgs)[1]?.body ?? "").includes('"generation_id":"gen-memory-next"')
      )
    ).toBe(true);
  });

  it("Memory 页面会按当前筛选条件提交查询并展示可读摘要", async () => {
    window.history.pushState({}, "", "/memory");

    const snapshot = buildSnapshot();
    snapshot.resources.memory.available_layers = ["sor", "fragment"];
    snapshot.resources.memory.available_partitions = ["contact"];
    snapshot.resources.memory.summary = {
      ...snapshot.resources.memory.summary,
      pending_replay_count: 1,
    };

    const nextMemory = {
      ...snapshot.resources.memory,
      warnings: ["memory backlog"],
      filters: {
        ...snapshot.resources.memory.filters,
        query: "Alice",
        layer: "sor",
        partition: "contact",
        include_history: true,
        limit: 50,
      },
      available_scopes: ["scope-contact"],
      records: [
        {
          record_id: "record-alice",
          layer: "sor",
          project_id: "project-default",
          workspace_id: "workspace-default",
          scope_id: "scope-contact",
          partition: "contact",
          subject_key: "Alice",
          summary: "Alice 偏好异步沟通",
          status: "current",
          version: 3,
          created_at: "2026-03-09T10:00:00Z",
          updated_at: "2026-03-09T10:05:00Z",
          evidence_refs: [{ type: "message", id: "msg-1" }],
          derived_refs: ["derived-1"],
          proposal_refs: ["proposal-1"],
          metadata: {
            source: "chat",
            owner: "Connor",
          },
          requires_vault_authorization: false,
          retrieval_backend: "memu",
        },
      ],
    };

    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      if (url.includes("/api/control/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({
            contract_version: "1.0.0",
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
              handled_at: "2026-03-09T10:06:00Z",
            },
          })
        );
      }
      if (url.includes("/api/control/resources/memory")) {
        return Promise.resolve(jsonResponse(nextMemory));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    await screen.findByRole("heading", { name: "Memory 在工作，只是当前筛选太窄" });
    expect(screen.getAllByRole("link", { name: "打开 Settings > Memory" })[0]!).toHaveAttribute(
      "href",
      "/settings#settings-group-memory"
    );
    expect(screen.queryByText("Active Scope")).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("记忆类型"), "sor");
    await userEvent.selectOptions(screen.getByLabelText("主题分区"), "contact");
    await userEvent.selectOptions(screen.getByLabelText("最多显示"), "50");
    await userEvent.type(screen.getByLabelText("关键词"), "Alice");
    await userEvent.click(screen.getByLabelText("包含历史版本"));
    await userEvent.click(screen.getByRole("button", { name: "重新查看" }));

    const actionCall = fetchMock.mock.calls.find((call) => {
      const [url, init] = call as FetchArgs;
      return String(url).includes("/api/control/actions") && init?.method === "POST";
    }) as FetchArgs | undefined;

    expect(String(actionCall?.[1]?.body)).toContain('"action_id":"memory.query"');
    expect(String(actionCall?.[1]?.body)).toContain('"query":"Alice"');
    expect(String(actionCall?.[1]?.body)).toContain('"layer":"sor"');
    expect(String(actionCall?.[1]?.body)).toContain('"partition":"contact"');
    expect(String(actionCall?.[1]?.body)).toContain('"include_history":true');
    expect(String(actionCall?.[1]?.body)).toContain('"limit":50');

    const inspectorHeading = await screen.findByRole("heading", { name: "Alice" });
    const inspectorSection = inspectorHeading.closest("section");
    expect(inspectorSection).not.toBeNull();
    expect(within(inspectorSection!).getByText("Alice 偏好异步沟通")).toBeInTheDocument();
    expect(await screen.findByText("当前有需要注意的情况")).toBeInTheDocument();
  });

  it("Memory 页面会把增强模式缺失的最小配置直接指给用户", async () => {
    window.history.pushState({}, "", "/memory");

    const snapshot = buildSnapshot();
    snapshot.resources.config.ui_hints = {
      "memory.bridge_url": {
        field_path: "memory.bridge_url",
        section: "memory-basic",
        label: "MemU Bridge 地址",
        description: "",
        widget: "text",
        placeholder: "https://memory.example.com",
        help_text: "",
        sensitive: false,
        multiline: false,
        order: 10,
      },
      "memory.bridge_api_key_env": {
        field_path: "memory.bridge_api_key_env",
        section: "memory-basic",
        label: "MemU API Key 环境变量",
        description: "",
        widget: "env-ref",
        placeholder: "MEMU_API_KEY",
        help_text: "",
        sensitive: false,
        multiline: false,
        order: 20,
      },
    };
    snapshot.resources.config.current_value = {
      memory: {
        backend_mode: "memu",
        bridge_url: "",
        bridge_api_key_env: "",
      },
    };
    snapshot.resources.memory.summary = {
      ...snapshot.resources.memory.summary,
      sor_current_count: 0,
      fragment_count: 0,
      vault_ref_count: 0,
      pending_replay_count: 0,
    };

    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/control/snapshot")) {
        return Promise.resolve(jsonResponse(snapshot));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "增强记忆还没配完整" })).toBeInTheDocument();
    expect(screen.getByText("补齐 MemU Bridge 地址")).toBeInTheDocument();
    expect(screen.getByText("补齐 MemU API Key 环境变量")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "打开 Settings > Memory" })[0]!).toHaveAttribute(
      "href",
      "/settings#settings-group-memory"
    );
  });
});
