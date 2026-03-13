import { describe, expect, it } from "vitest";
import type {
  CapabilityPackDocument,
  ControlPlaneDocumentBase,
  ContextContinuityDocument,
  WorkProjectionItem,
} from "../types";
import {
  buildFreshnessReadiness,
  describeFreshnessWorkPath,
  isFreshnessRelevantWork,
} from "./freshness";

const BASE_DOCUMENT: Omit<
  ControlPlaneDocumentBase,
  "resource_type" | "resource_id"
> = {
  contract_version: "1.0.0",
  schema_version: 1,
  generated_at: "2026-03-12T08:00:00Z",
  updated_at: "2026-03-12T08:00:00Z",
  status: "ready",
  degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
  warnings: [],
  capabilities: [],
  refs: {},
};

function buildContext(): ContextContinuityDocument {
  return {
    ...BASE_DOCUMENT,
    resource_type: "context_continuity",
    resource_id: "context:overview",
    active_project_id: "project-default",
    active_workspace_id: "workspace-default",
    sessions: [],
    frames: [],
  };
}

function buildCapabilityPack(): CapabilityPackDocument {
  return {
    ...BASE_DOCUMENT,
    resource_type: "capability_pack",
    resource_id: "capability:bundled",
    selected_project_id: "project-default",
    selected_workspace_id: "workspace-default",
    pack: {
      pack_id: "pack-default",
      version: "1.0.0",
      skills: [],
      tools: [],
      worker_profiles: [],
      bootstrap_files: [],
      fallback_toolset: [],
      degraded_reason: "",
      generated_at: "2026-03-12T08:00:00Z",
    },
  };
}

function buildWork(
  overrides: Partial<WorkProjectionItem> = {},
  runtimeSummary: Record<string, unknown> = {}
): WorkProjectionItem {
  return {
    work_id: "work-default",
    task_id: "task-default",
    parent_work_id: "",
    title: "默认 work",
    status: "running",
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
    runtime_summary: runtimeSummary,
    updated_at: "2026-03-12T08:00:00Z",
    capabilities: [],
    ...overrides,
  };
}

describe("freshness helpers", () => {
  it("不会把普通 ops/research work 误判为实时问题路径", () => {
    const work = buildWork({
      title: "分析本地日志并整理结论",
      selected_worker_type: "ops",
      route_reason: "worker_type=ops | fallback=single_worker",
    });

    expect(isFreshnessRelevantWork(work)).toBe(false);
    expect(describeFreshnessWorkPath(work)).toBe("");

    const readiness = buildFreshnessReadiness({
      context: buildContext(),
      capabilityPack: buildCapabilityPack(),
      works: [work],
    });

    expect(readiness.label).toBe("实时问题路径还没准备好");
    expect(readiness.relevantWorkSummary).toContain("当前没有相关 work");
  });

  it("会保留带 freshness 标题与工具的相关 work", () => {
    const work = buildWork(
      {
        title: "检查官网最新公告",
        selected_worker_type: "general",
        route_reason: "delegation_strategy=butler_owned_freshness",
      },
      {
        delegation_strategy: "butler_owned_freshness",
        research_route_reason: "worker_type=research | fallback=single_worker",
        research_tool_profile: "standard",
        research_a2a_conversation_id: "a2a-weather-1",
        research_worker_agent_session_id: "agent-session-worker-research-1",
        research_a2a_message_count: 2,
        research_child_status: "SUCCEEDED",
      }
    );

    expect(isFreshnessRelevantWork(work)).toBe(true);
    expect(describeFreshnessWorkPath(work)).toContain("Butler 会先接住这条实时问题");
    expect(describeFreshnessWorkPath(work)).toContain("内部协作链路已经建立");
    expect(describeFreshnessWorkPath(work)).toContain("Research Worker 会按标准工具面取证");
  });

  it("会把缺城市的天气问题解释成 Butler 补问位置", () => {
    const work = buildWork(
      {
        title: "今天天气怎么样",
        selected_worker_type: "general",
        route_reason: "delegation_strategy=butler_owned_freshness",
      },
      {
        delegation_strategy: "butler_owned_freshness",
        freshness_resolution: "location_required",
      }
    );

    expect(describeFreshnessWorkPath(work)).toContain("还缺城市 / 区县");
    expect(describeFreshnessWorkPath(work)).toContain("不是误答成系统没有实时能力");
  });

  it("会把 backend unavailable 解释成环境限制而不是系统无能力", () => {
    const work = buildWork(
      {
        title: "深圳今天天气怎么样",
        selected_worker_type: "general",
        route_reason: "delegation_strategy=butler_owned_freshness",
      },
      {
        delegation_strategy: "butler_owned_freshness",
        freshness_resolution: "backend_unavailable",
        freshness_degraded_reason: "web search failed: ConnectError: network down",
      }
    );

    expect(describeFreshnessWorkPath(work)).toContain("外部取证后端暂时不可用");
    expect(describeFreshnessWorkPath(work)).toContain("不是把问题说成系统整体没有能力");
    expect(describeFreshnessWorkPath(work)).toContain("web search failed");
  });
});
