import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ActionResultEnvelope,
  ControlPlaneResourceRef,
  ControlPlaneSnapshot,
} from "../../types";
import { useWorkbenchData } from "./useWorkbenchData";

const {
  executeWorkbenchActionWithRefreshMock,
  fetchWorkbenchSnapshotMock,
  refreshWorkbenchSnapshotResourcesMock,
} = vi.hoisted(() => ({
  executeWorkbenchActionWithRefreshMock: vi.fn(),
  fetchWorkbenchSnapshotMock: vi.fn(),
  refreshWorkbenchSnapshotResourcesMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  ApiError: class ApiError extends Error {},
  isFrontDoorApiError: () => false,
}));

vi.mock("../actions", () => ({
  executeWorkbenchActionWithRefresh: executeWorkbenchActionWithRefreshMock,
}));

vi.mock("./controlPlaneResources", () => ({
  fetchWorkbenchSnapshot: fetchWorkbenchSnapshotMock,
  refreshWorkbenchSnapshotResources: refreshWorkbenchSnapshotResourcesMock,
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
          scope_id: "scope:legacy",
          partition: "facts",
          layer: "sor",
          query: "legacy",
          include_history: false,
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
    } as unknown as ControlPlaneSnapshot["resources"],
  };
}

function buildActionResult(
  actionId: string,
  refs: ControlPlaneResourceRef[]
): ActionResultEnvelope {
  return {
    contract_version: "1.0.0",
    request_id: "req-1",
    correlation_id: "req-1",
    action_id: actionId,
    code: "OK",
    message: "ok",
    status: "completed",
    handled_at: "2026-03-13T00:05:00Z",
    resource_refs: refs,
    target_refs: [],
    data: {},
  };
}

describe("useWorkbenchData", () => {
  beforeEach(() => {
    executeWorkbenchActionWithRefreshMock.mockReset();
    fetchWorkbenchSnapshotMock.mockReset();
    refreshWorkbenchSnapshotResourcesMock.mockReset();
  });

  it("memory.query 局部刷新使用本次提交的筛选参数", async () => {
    const initialSnapshot = buildSnapshot();
    const refreshedSnapshot = {
      ...initialSnapshot,
      generated_at: "2026-03-13T00:10:00Z",
    };
    const refs: ControlPlaneResourceRef[] = [
      {
        resource_type: "memory_console",
        resource_id: "memory:default",
        schema_version: 1,
      },
    ];

    refreshWorkbenchSnapshotResourcesMock.mockResolvedValue({
      snapshot: refreshedSnapshot,
      mode: "resource-refs",
      routes: ["memory"],
    });
    executeWorkbenchActionWithRefreshMock.mockImplementation(
      async (
        _contractVersion: string | undefined,
        actionId: string,
        _params: Record<string, unknown>,
        handlers: {
          refreshSnapshot: () => Promise<void>;
          refreshResources: (nextRefs: ControlPlaneResourceRef[]) => Promise<void>;
        }
      ) => {
        await handlers.refreshResources(refs);
        return buildActionResult(actionId, refs);
      }
    );

    const { result } = renderHook(() =>
      useWorkbenchData({
        initialSnapshot,
        autoRefresh: false,
      })
    );

    await act(async () => {
      await result.current.submitAction("memory.query", {
        project_id: "project-next",
        workspace_id: "workspace-next",
        query: "alice",
        layer: "fragment",
        partition: "chat",
        include_history: true,
        include_vault_refs: true,
        limit: 10,
        derived_type: "tom",
        status: "derived",
        updated_after: "2026-03-01T00:00:00Z",
        updated_before: "2026-03-31T23:59:59Z",
      });
    });

    expect(refreshWorkbenchSnapshotResourcesMock).toHaveBeenCalledWith(
      initialSnapshot,
      refs,
      {
        memoryQuery: {
          projectId: "project-next",
          workspaceId: "workspace-next",
          scopeId: "scope:legacy",
          partition: "chat",
          layer: "fragment",
          query: "alice",
          includeHistory: true,
          includeVaultRefs: true,
          limit: 10,
          derivedType: "tom",
          status: "derived",
          updatedAfter: "2026-03-01T00:00:00Z",
          updatedBefore: "2026-03-31T23:59:59Z",
        },
      }
    );

    await waitFor(() => {
      expect(result.current.snapshot?.generated_at).toBe("2026-03-13T00:10:00Z");
    });
  });

  it("非 query-backed action 仍按默认局部刷新逻辑执行", async () => {
    const initialSnapshot = buildSnapshot();
    const refs: ControlPlaneResourceRef[] = [
      {
        resource_type: "wizard_session",
        resource_id: "wizard:default",
        schema_version: 1,
      },
    ];

    refreshWorkbenchSnapshotResourcesMock.mockResolvedValue({
      snapshot: initialSnapshot,
      mode: "resource-refs",
      routes: ["wizard"],
    });
    executeWorkbenchActionWithRefreshMock.mockImplementation(
      async (
        _contractVersion: string | undefined,
        actionId: string,
        _params: Record<string, unknown>,
        handlers: {
          refreshSnapshot: () => Promise<void>;
          refreshResources: (nextRefs: ControlPlaneResourceRef[]) => Promise<void>;
        }
      ) => {
        await handlers.refreshResources(refs);
        return buildActionResult(actionId, refs);
      }
    );

    const { result } = renderHook(() =>
      useWorkbenchData({
        initialSnapshot,
        autoRefresh: false,
      })
    );

    await act(async () => {
      await result.current.submitAction("wizard.refresh", {});
    });

    expect(refreshWorkbenchSnapshotResourcesMock).toHaveBeenCalledWith(
      initialSnapshot,
      refs,
      undefined
    );
  });
});
