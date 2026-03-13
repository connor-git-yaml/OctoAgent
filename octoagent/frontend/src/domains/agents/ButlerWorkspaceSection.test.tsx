import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import ButlerWorkspaceSection from "./ButlerWorkspaceSection";

function buildProps() {
  return {
    primaryDirty: true,
    butlerBusy: false,
    draft: {
      name: "OctoAgent",
      scope: "project",
      projectId: "project-default",
      workspaceId: "workspace-default",
      personaSummary: "默认 Butler",
      modelAlias: "main",
      toolProfile: "standard",
      llmMode: "litellm",
      proxyUrl: "http://localhost:4000",
      primaryProvider: "openrouter",
      policyProfileId: "default",
      memoryAccessPolicy: {
        allowVault: false,
        includeHistory: true,
      },
      memoryRecall: {
        postFilterMode: "keyword_overlap",
        rerankMode: "heuristic",
        minKeywordOverlap: "1",
        scopeLimit: "4",
        perScopeLimit: "3",
        maxHits: "4",
      },
    },
    scopeOptions: [
      { value: "project", label: "项目级默认" },
      { value: "workspace", label: "工作区级默认" },
    ] as const,
    primaryProjectOptions: [{ value: "project-default", label: "Default Project" }],
    primaryWorkspaceOptions: [{ value: "workspace-default", label: "Primary" }],
    review: {
      tone: "warning" as const,
      headline: "有 1 条提醒",
      summary: "当前配置还可以继续优化。",
      nextActions: ["先确认默认 Project。"],
    },
    summary: {
      primaryProjectName: "Default Project",
      primaryWorkspaceName: "Primary",
      currentPolicyLabel: "默认策略",
      primaryToolProfileLabel: "常用工具",
      primaryModelAliasHint: "平衡质量与速度，适合默认值。",
      recallPresetLabel: "平衡默认",
      recallPresetDescription: "适合大多数日常协作。",
      selectedPrimaryCapabilityCount: 2,
    },
    context: {
      contextProjectId: "project-default",
      contextWorkspaceId: "workspace-default",
      availableProjects: [{ project_id: "project-default", name: "Default Project" }],
      availableContextWorkspaces: [
        { workspace_id: "workspace-default", name: "Primary" },
      ],
      canSwitchContext: true,
    },
    projectFilter: "all",
    projectFilterStats: [
      {
        projectId: "project-default",
        name: "Default Project",
        instanceCount: 2,
        templateCount: 1,
      },
    ],
    totalWorkInstances: 2,
    totalWorkTemplates: 1,
    policyCards: <button type="button">默认策略</button>,
    modelAliasButtons: <button type="button">main</button>,
    toolProfileButtons: <button type="button">standard</button>,
    recallPresetButtons: <button type="button">平衡默认</button>,
    skillCapabilitySection: <div>Skills Provider</div>,
    mcpCapabilitySection: <div>MCP Provider</div>,
    onResetPrimary: vi.fn(),
    onReviewPrimary: vi.fn(),
    onApplyPrimary: vi.fn(),
    onUpdatePrimaryField: vi.fn(),
    onUpdatePrimaryProject: vi.fn(),
    onUpdatePrimaryMemoryAccess: vi.fn(),
    onUpdatePrimaryMemoryRecallField: vi.fn(),
    onContextProjectChange: vi.fn(),
    onContextWorkspaceChange: vi.fn(),
    onSwitchProjectContext: vi.fn(),
    onSetProjectFilter: vi.fn(),
  };
}

describe("ButlerWorkspaceSection", () => {
  it("提供 Butler 主配置交互并触发对应回调", async () => {
    const props = buildProps();

    render(
      <MemoryRouter>
        <ButlerWorkspaceSection {...props} />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByLabelText("Butler 名称"), {
      target: { value: "ATM Butler" },
    });
    expect(props.onUpdatePrimaryField).toHaveBeenCalledWith("name", "ATM Butler");

    await userEvent.click(screen.getByRole("button", { name: "检查 Butler 变更" }));
    expect(props.onReviewPrimary).toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "保存 Butler 配置" }));
    expect(props.onApplyPrimary).toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "切到这个视角" }));
    expect(props.onSwitchProjectContext).toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Default Project 实例 2 / 模板 1" }));
    expect(props.onSetProjectFilter).toHaveBeenCalledWith("project-default");
  });
});
