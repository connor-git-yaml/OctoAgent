import { describe, expect, it } from "vitest";
import type {
  CapabilityPackDocument,
  ContextContinuityDocument,
  WorkProjectionItem,
} from "../types";
import {
  buildFreshnessReadiness,
  describeFreshnessWorkPath,
  isFreshnessRelevantWork,
} from "./freshness";

const BASE_DOCUMENT = {
  contract_version: "1.0.0",
  schema_version: 1,
  generated_at: "2026-03-12T08:00:00Z",
  updated_at: "2026-03-12T08:00:00Z",
  status: "ready",
  degraded: { is_degraded: false, reasons: [], unavailable_sections: [] },
  warnings: [],
  capabilities: [],
  refs: {},
} as const;

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
        selected_worker_type: "research",
        route_reason: "worker_type=research | fallback=single_worker",
        selected_tools: ["runtime.now", "web.search"],
      },
      {
        requested_tool_profile: "standard",
        requested_worker_type: "research",
      }
    );

    expect(isFreshnessRelevantWork(work)).toBe(true);
    expect(describeFreshnessWorkPath(work)).toContain(
      "Research Worker 会按标准工具面处理这条工作"
    );
  });
});
