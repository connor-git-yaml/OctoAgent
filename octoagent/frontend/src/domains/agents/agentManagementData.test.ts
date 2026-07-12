/**
 * domains/agents/agentManagementData L4 直测 —— F143 件 3
 */
import { describe, expect, it } from "vitest";
import type { ControlPlaneSnapshot, WorkerProfileItem } from "../../types";
import {
  buildAgentEditorDraftFromProfile,
  buildAgentEditorDraftFromTemplate,
  buildAgentPayload,
  buildBlankAgentEditorDraft,
  buildCapabilityProviderEntries,
  buildCapabilitySelectionState,
  deriveAgentManagementView,
  formatAgentStatus,
  formatPermissionPreset,
  formatProjectName,
  formatTokenLabel,
  mergeCapabilitySelectionMetadata,
  parseAgentReview,
  uniqueStrings,
  type CapabilityProviderEntry,
} from "./agentManagementData";

function makeProfile(overrides: Partial<WorkerProfileItem> = {}): WorkerProfileItem {
  return {
    profile_id: "proj-1:agent-a",
    project_id: "proj-1",
    scope: "project",
    name: "研究员",
    summary: "查资料",
    status: "active",
    origin_kind: "custom",
    static_config: {
      model_alias: "cheap",
      tool_profile: "standard",
      permission_preset: "minimal",
      default_tool_groups: ["web"],
      selected_tools: ["web.search"],
      runtime_kinds: ["worker"],
    },
    dynamic_context: {
      active_work_count: 3,
      running_work_count: 1,
      attention_work_count: 1,
      updated_at: "2026-07-13T10:00:00Z",
    },
    ...overrides,
  } as unknown as WorkerProfileItem;
}

function makeEntry(overrides: Partial<CapabilityProviderEntry> = {}): CapabilityProviderEntry {
  return {
    providerId: "p1",
    label: "P1",
    description: "",
    selectionItemId: "skill:p1",
    kind: "skill",
    defaultSelected: true,
    enabled: true,
    availability: "available",
    editable: false,
    tags: [],
    ...overrides,
  } as CapabilityProviderEntry;
}

function makeSnapshot(profiles: WorkerProfileItem[]): ControlPlaneSnapshot {
  return {
    resources: {
      project_selector: {
        current_project_id: "proj-1",
        available_projects: [{ project_id: "proj-1", slug: "p", name: "项目甲" }],
      },
      worker_profiles: {
        summary: { default_profile_id: "proj-1:agent-a", default_profile_name: "研究员" },
        profiles,
      },
      skill_governance: { items: [] },
      mcp_provider_catalog: { items: [] },
    },
  } as unknown as ControlPlaneSnapshot;
}

describe("基础格式化", () => {
  it("uniqueStrings：trim/去空/去重且保序", () => {
    expect(uniqueStrings([" a ", "b", "a", null, undefined, ""])).toEqual(["a", "b"]);
  });

  it("formatTokenLabel/formatAgentStatus/formatPermissionPreset", () => {
    expect(formatTokenLabel("web.search_v2-beta")).toBe("web search v2 beta");
    expect(formatAgentStatus("draft")).toBe("草稿");
    expect(formatAgentStatus("custom_state")).toBe("custom state");
    expect(formatPermissionPreset("minimal")).toBe("保守模式");
    expect(formatPermissionPreset("odd_preset")).toBe("odd preset");
  });

  it("formatProjectName：命中取名，未命中回退 id", () => {
    const projects = [{ project_id: "p1", slug: "s", name: "甲" }] as never;
    expect(formatProjectName(projects, "p1")).toBe("甲");
    expect(formatProjectName(projects, "p2")).toBe("p2");
    expect(formatProjectName(null, "p3")).toBe("p3");
  });
});

describe("capability selection", () => {
  it("buildCapabilitySelectionState：显式 selected/disabled 覆盖默认", () => {
    const entries = [
      makeEntry({ selectionItemId: "skill:a", defaultSelected: false }),
      makeEntry({ selectionItemId: "skill:b", defaultSelected: true }),
      makeEntry({ selectionItemId: "skill:c", defaultSelected: true }),
    ];
    const state = buildCapabilitySelectionState(entries, {
      capability_provider_selection: {
        selected_item_ids: ["skill:a"],
        disabled_item_ids: ["skill:b"],
      },
    });
    expect(state).toEqual({ "skill:a": true, "skill:b": false, "skill:c": true });
  });

  it("mergeCapabilitySelectionMetadata：只记录与默认的差异，无差异时清空选择键", () => {
    const entries = [
      makeEntry({ selectionItemId: "skill:a", defaultSelected: false }),
      makeEntry({ selectionItemId: "skill:b", defaultSelected: true }),
    ];
    const diff = mergeCapabilitySelectionMetadata({ keep: 1 }, entries, {
      "skill:a": true,
      "skill:b": false,
    });
    expect(diff.keep).toBe(1);
    expect(diff.capability_provider_selection).toEqual({
      selected_item_ids: ["skill:a"],
      disabled_item_ids: ["skill:b"],
    });

    const same = mergeCapabilitySelectionMetadata(
      { skill_selection: { selected_item_ids: ["x"] } },
      entries,
      { "skill:a": false, "skill:b": true }
    );
    expect(same.capability_provider_selection).toBeUndefined();
    expect(same.skill_selection).toBeUndefined();
  });

  it("往返：selection state → metadata → selection state 收敛", () => {
    const entries = [
      makeEntry({ selectionItemId: "skill:a", defaultSelected: false }),
      makeEntry({ selectionItemId: "skill:b", defaultSelected: true }),
    ];
    const state = { "skill:a": true, "skill:b": false };
    const metadata = mergeCapabilitySelectionMetadata({}, entries, state);
    expect(buildCapabilitySelectionState(entries, metadata)).toEqual(state);
  });

  it("buildCapabilityProviderEntries：skill 来自治理项（排除 mcp 源），mcp 来自 catalog", () => {
    const snapshot = {
      resources: {
        skill_governance: {
          items: [
            {
              item_id: "skill:alpha",
              label: "Alpha",
              source_kind: "builtin",
              selected: true,
              availability: "available",
            },
            {
              item_id: "mcp:beta",
              label: "Beta(mcp 治理)",
              source_kind: "mcp",
              selected: true,
              availability: "available",
            },
          ],
        },
        mcp_provider_catalog: {
          items: [
            {
              provider_id: "beta",
              label: "Beta",
              description: "d",
              selection_item_id: "mcp:beta",
              enabled: true,
              status: "ready",
              editable: true,
              tool_count: 3,
            },
          ],
        },
      },
    } as unknown as ControlPlaneSnapshot;
    const entries = buildCapabilityProviderEntries(snapshot);
    expect(entries.map((e) => `${e.kind}:${e.providerId}`)).toEqual(["skill:alpha", "mcp:beta"]);
    expect(entries[1]).toMatchObject({ defaultSelected: true, tags: ["tools:3"] });
  });
});

describe("editor draft 与 payload", () => {
  it("buildAgentEditorDraftFromProfile：静态配置全量带入", () => {
    const draft = buildAgentEditorDraftFromProfile(makeProfile(), "proj-1", "项目甲", []);
    expect(draft).toMatchObject({
      profileId: "proj-1:agent-a",
      name: "研究员",
      modelAlias: "cheap",
      permissionPreset: "minimal",
      selectedTools: ["web.search"],
      runtimeKinds: ["worker"],
    });
  });

  it("buildAgentEditorDraftFromTemplate：清 profileId、默认命名、origin=cloned", () => {
    const draft = buildAgentEditorDraftFromTemplate(makeProfile(), "proj-2", "项目乙", []);
    expect(draft.profileId).toBe("");
    expect(draft.projectId).toBe("proj-2");
    expect(draft.name).toBe("项目乙 新 Agent");
    expect(draft.originKind).toBe("cloned");
    expect(
      buildAgentEditorDraftFromTemplate(null, "proj-2", "项目乙", [], { asMainAgent: true }).name
    ).toBe("项目乙 主 Agent");
  });

  it("buildBlankAgentEditorDraft：默认 runtime_kinds 防后端 fallback", () => {
    const draft = buildBlankAgentEditorDraft("proj-1", "项目甲", []);
    expect(draft.runtimeKinds).toEqual(["worker"]);
    expect(draft.originKind).toBe("custom");
  });

  it("buildAgentPayload：空 profileId 省略 + 列表去重 + 选择差异进 metadata", () => {
    const entries = [makeEntry({ selectionItemId: "skill:a", defaultSelected: false })];
    const draft = buildBlankAgentEditorDraft("proj-1", "项目甲", entries);
    draft.selectedTools = ["web.search", "web.search"];
    draft.capabilitySelection = { "skill:a": true };
    const payload = buildAgentPayload(draft, entries);
    expect(payload.profile_id).toBeUndefined();
    expect(payload.selected_tools).toEqual(["web.search"]);
    expect((payload.metadata as Record<string, unknown>).capability_provider_selection).toEqual({
      selected_item_ids: ["skill:a"],
      disabled_item_ids: [],
    });
  });
});

describe("deriveAgentManagementView / parseAgentReview", () => {
  it("默认 profile 成为主 Agent 卡片且不可删除，工作计数钳非负", () => {
    const view = deriveAgentManagementView(makeSnapshot([makeProfile()]));
    expect(view.mainAgent).toMatchObject({
      profileId: "proj-1:agent-a",
      isMainAgent: true,
      removable: false,
      activeWorkCount: 3,
      waitingWorkCount: 2,
      projectName: "项目甲",
    });
    expect(view.projectAgents).toEqual([]);
    expect(view.defaultProfileName).toBe("研究员");
  });

  it("无匹配主 profile 时给出待建立占位卡片", () => {
    const view = deriveAgentManagementView(makeSnapshot([]));
    expect(view.mainAgent.status).toBe("needs_setup");
    expect(view.mainAgent.profileStatus).toBe("待建立");
    expect(view.mainAgent.isMainAgent).toBe(true);
  });

  it("parseAgentReview：空对象 null，字段容错读取", () => {
    expect(parseAgentReview({})).toBeNull();
    expect(parseAgentReview(null)).toBeNull();
    expect(
      parseAgentReview({
        can_save: 1,
        ready: false,
        warnings: ["w", 2],
        blocking_reasons: null,
        next_actions: ["下一步"],
      })
    ).toEqual({
      canSave: true,
      ready: false,
      warnings: ["w"],
      blockingReasons: [],
      nextActions: ["下一步"],
    });
  });
});
