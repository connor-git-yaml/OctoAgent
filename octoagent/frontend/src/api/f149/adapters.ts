import type {
  SkillDeleteResponse,
  SkillDetail,
  SkillItem,
  SkillInstallResponse,
  SkillListResponse,
} from "../../types";
import type { components } from "../../generated/f149/rest";
import { apiErrorFromResponse, frontDoorRequest } from "../client";

export interface AgentApprovalOverride {
  agentRuntimeId: string;
  toolName: string;
  decision: string;
  createdAt: string;
}

type ApprovalOverrideListWire =
  components["schemas"]["ApprovalOverrideListResponse"];
type ApprovalOverrideDeleteWire =
  components["schemas"]["ApprovalOverrideDeleteResponse"];
type SkillListWire = components["schemas"]["SkillListResponse"];
type SkillDetailWire = components["schemas"]["SkillDetailResponse"];
type SkillInstallWire = components["schemas"]["SkillInstallResponse"];
type SkillDeleteWire = components["schemas"]["SkillDeleteResponse"];

async function requestJson<Wire>(
  path: string,
  init?: RequestInit,
): Promise<Wire> {
  const response = init
    ? await frontDoorRequest(path, init)
    : await frontDoorRequest(path);
  if (!response.ok) {
    throw await apiErrorFromResponse(response);
  }
  return response.json() as Promise<Wire>;
}

function decodeSkillSource(source: string): SkillItem["source"] {
  if (source === "builtin" || source === "user" || source === "project") {
    return source;
  }
  throw new Error(`Skill来源无效: ${source}`);
}

function decodeSkillItem(
  wire: components["schemas"]["SkillItemResponse"],
): SkillItem {
  return {
    name: wire.name,
    description: wire.description,
    version: wire.version,
    author: wire.author || undefined,
    tags: wire.tags ?? [],
    source: decodeSkillSource(wire.source),
  };
}

export async function fetchAgentApprovalOverrides(): Promise<
  AgentApprovalOverride[]
> {
  const wire = await requestJson<ApprovalOverrideListWire>(
    "/api/approval-overrides",
  );
  return wire.overrides.map((item) => ({
    agentRuntimeId: item.agent_runtime_id,
    toolName: item.tool_name,
    decision: item.decision,
    createdAt: item.created_at,
  }));
}

export async function revokeAgentApprovalOverride(
  agentRuntimeId: string,
  toolName: string,
): Promise<boolean> {
  const wire = await requestJson<ApprovalOverrideDeleteWire>(
    `/api/approval-overrides/${encodeURIComponent(agentRuntimeId)}/${encodeURIComponent(toolName)}`,
    { method: "DELETE" },
  );
  return wire.success;
}

export async function fetchF149Skills(): Promise<SkillListResponse> {
  const wire = await requestJson<SkillListWire>("/api/skills");
  return {
    items: wire.items.map(decodeSkillItem),
    total: wire.total,
  };
}

export async function fetchF149SkillDetail(name: string): Promise<SkillDetail> {
  const wire = await requestJson<SkillDetailWire>(
    `/api/skills/${encodeURIComponent(name)}`,
  );
  return {
    ...decodeSkillItem(wire),
    trigger_patterns: wire.trigger_patterns ?? [],
    tools_required: wire.tools_required ?? [],
    content: wire.content,
  };
}

export async function installF149Skill(
  name: string,
  content: string,
): Promise<SkillInstallResponse> {
  const wire = await requestJson<SkillInstallWire>("/api/skills", {
    method: "POST",
    body: JSON.stringify({ name, content }),
  });
  return wire;
}

export async function uninstallF149Skill(
  name: string,
): Promise<SkillDeleteResponse> {
  const wire = await requestJson<SkillDeleteWire>(
    `/api/skills/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );
  return wire;
}
