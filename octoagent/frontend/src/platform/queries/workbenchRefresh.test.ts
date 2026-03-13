import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ControlPlaneSnapshot } from "../../types";
import {
  buildSnapshotRefreshOptions,
  refreshWorkbenchSnapshotResources,
} from "./controlPlaneResources";

const {
  fetchControlSnapshotMock,
  fetchWorkbenchResourceMock,
} = vi.hoisted(() => ({
  fetchControlSnapshotMock: vi.fn(),
  fetchWorkbenchResourceMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  fetchControlSnapshot: fetchControlSnapshotMock,
  fetchWorkbenchResource: fetchWorkbenchResourceMock,
}));

function buildSnapshot(): ControlPlaneSnapshot {
  return {
    contract_version: "1.0.0",
    generated_at: "2026-03-13T00:00:00Z",
    registry: {} as ControlPlaneSnapshot["registry"],
    resources: {
      memory: {
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        filters: {
          project_id: "project-default",
          workspace_id: "workspace-default",
          scope_id: "scope:alpha",
          partition: "facts",
          layer: "sor",
          query: "alice",
          include_history: true,
          include_vault_refs: false,
          limit: 25,
        },
        records: [],
      },
      imports: {
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        sources: [],
        recent_runs: [],
      },
      wizard: {
        contract_version: "1.0.0",
        resource_type: "wizard_session",
        resource_id: "wizard:default",
        schema_version: 1,
        generated_at: "2026-03-13T00:00:00Z",
        updated_at: "2026-03-13T00:00:00Z",
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
    } as unknown as ControlPlaneSnapshot["resources"],
  };
}

describe("workbenchRefresh", () => {
  beforeEach(() => {
    fetchControlSnapshotMock.mockReset();
    fetchWorkbenchResourceMock.mockReset();
  });

  it("根据当前 snapshot 生成 memory/import 局部刷新参数", () => {
    const snapshot = buildSnapshot();

    expect(buildSnapshotRefreshOptions(snapshot)).toEqual({
      memoryQuery: {
        projectId: "project-default",
        workspaceId: "workspace-default",
        scopeId: "scope:alpha",
        partition: "facts",
        layer: "sor",
        query: "alice",
        includeHistory: true,
        includeVaultRefs: false,
        limit: 25,
      },
      importQuery: {
        projectId: "project-default",
        workspaceId: "workspace-default",
      },
    });
  });

  it("当 resource ref 为空时统一回退到 full snapshot", async () => {
    const refreshedSnapshot = {
      ...buildSnapshot(),
      generated_at: "2026-03-13T01:00:00Z",
    };
    fetchControlSnapshotMock.mockResolvedValue(refreshedSnapshot);

    const result = await refreshWorkbenchSnapshotResources(buildSnapshot(), []);

    expect(result.mode).toBe("full-snapshot");
    expect(result.snapshot.generated_at).toBe("2026-03-13T01:00:00Z");
  });

  it("memory 局部刷新会沿用当前 query 参数", async () => {
    fetchWorkbenchResourceMock.mockResolvedValue({
      resource_type: "memory_console",
      resource_id: "memory:default",
    });

    await refreshWorkbenchSnapshotResources(buildSnapshot(), [
      {
        resource_type: "memory_console",
        resource_id: "memory:default",
        schema_version: 1,
      },
    ]);

    expect(fetchWorkbenchResourceMock).toHaveBeenCalledWith("memory", {
      memoryQuery: expect.objectContaining({
        query: "alice",
        includeHistory: true,
      }),
      importQuery: undefined,
    });
  });

  it("当局部资源刷新返回非法 payload 时统一回退到 full snapshot", async () => {
    const refreshedSnapshot = {
      ...buildSnapshot(),
      generated_at: "2026-03-13T02:00:00Z",
    };
    fetchWorkbenchResourceMock.mockResolvedValue({ invalid: true });
    fetchControlSnapshotMock.mockResolvedValue(refreshedSnapshot);

    const result = await refreshWorkbenchSnapshotResources(buildSnapshot(), [
      {
        resource_type: "wizard_session",
        resource_id: "wizard:default",
        schema_version: 1,
      },
    ]);

    expect(result.mode).toBe("full-snapshot");
    expect(result.snapshot.generated_at).toBe("2026-03-13T02:00:00Z");
  });
});
