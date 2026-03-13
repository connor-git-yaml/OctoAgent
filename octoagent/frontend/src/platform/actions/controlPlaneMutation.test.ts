import { beforeEach, describe, expect, it, vi } from "vitest";
import { executeWorkbenchActionWithRefresh } from "./controlPlaneActions";

const { executeControlActionMock } = vi.hoisted(() => ({
  executeControlActionMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  executeControlAction: executeControlActionMock,
}));

describe("executeWorkbenchActionWithRefresh", () => {
  beforeEach(() => {
    executeControlActionMock.mockReset();
  });

  it("project.select 统一走 full snapshot refresh", async () => {
    const refreshSnapshot = vi.fn().mockResolvedValue(undefined);
    const refreshResources = vi.fn().mockResolvedValue(undefined);
    executeControlActionMock.mockResolvedValue({
      action_id: "project.select",
      code: "OK",
      message: "已切换",
      status: "succeeded",
      handled_at: "2026-03-13T00:00:00Z",
      resource_refs: [],
      data: {},
    });

    await executeWorkbenchActionWithRefresh(
      "1.0.0",
      "project.select",
      { project_id: "project-default" },
      {
        refreshSnapshot,
        refreshResources,
      }
    );

    expect(refreshSnapshot).toHaveBeenCalledTimes(1);
    expect(refreshResources).not.toHaveBeenCalled();
  });

  it("普通 action 按 resource refs 局部刷新", async () => {
    const refreshSnapshot = vi.fn().mockResolvedValue(undefined);
    const refreshResources = vi.fn().mockResolvedValue(undefined);
    const refs = [
      {
        resource_type: "wizard_session",
        resource_id: "wizard:default",
        schema_version: 1,
      },
    ];
    executeControlActionMock.mockResolvedValue({
      action_id: "wizard.refresh",
      code: "OK",
      message: "已刷新",
      status: "succeeded",
      handled_at: "2026-03-13T00:00:00Z",
      resource_refs: refs,
      data: {},
    });

    await executeWorkbenchActionWithRefresh(
      "1.0.0",
      "wizard.refresh",
      {},
      {
        refreshSnapshot,
        refreshResources,
      }
    );

    expect(refreshResources).toHaveBeenCalledWith(refs);
    expect(refreshSnapshot).not.toHaveBeenCalled();
  });
});
