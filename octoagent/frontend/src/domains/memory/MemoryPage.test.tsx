import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import MemoryPage from "./MemoryPage";

let mockWorkbench: {
  snapshot: unknown;
  submitAction: ReturnType<typeof vi.fn>;
  busyActionId: string | null;
};

vi.mock("../../components/shell/WorkbenchLayout", () => ({
  useWorkbench: () => mockWorkbench,
}));

function buildMemorySnapshot(): any {
  return {
    resources: {
      memory: {
        active_project_id: "project-default",
        active_workspace_id: "workspace-default",
        retrieval_backend: "memu",
        backend_state: "ready",
        backend_id: "memory-local",
        filters: {
          query: "",
          layer: "",
          partition: "",
          include_history: false,
          include_vault_refs: false,
          limit: 50,
        },
        summary: {
          sor_current_count: 2,
          fragment_count: 1,
          vault_ref_count: 0,
          pending_replay_count: 1,
          scope_count: 1,
        },
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
          {
            record_id: "record-bob",
            layer: "fragment",
            project_id: "project-default",
            workspace_id: "workspace-default",
            scope_id: "scope-contact",
            partition: "contact",
            subject_key: "Bob",
            summary: "Bob 需要每周汇总。",
            status: "current",
            version: 1,
            created_at: "2026-03-09T10:10:00Z",
            updated_at: "2026-03-09T10:12:00Z",
            evidence_refs: [],
            derived_refs: [],
            proposal_refs: [],
            metadata: {
              source: "import",
            },
            requires_vault_authorization: false,
            retrieval_backend: "memu",
          },
          {
            record_id: "record-internal",
            layer: "sor",
            project_id: "project-default",
            workspace_id: "workspace-default",
            scope_id: "scope-contact",
            partition: "work",
            subject_key: "worker_tool:bash:artifact-7",
            summary:
              "tool_name: bash\noutput_summary: rg -n MemoryPage\nartifact_ref: artifact-7\ntask_id: task-123",
            status: "current",
            version: 1,
            created_at: "2026-03-09T10:15:00Z",
            updated_at: "2026-03-09T10:16:00Z",
            evidence_refs: [{ type: "artifact", id: "artifact-7" }],
            derived_refs: [],
            proposal_refs: [],
            metadata: {
              source: "agent_context.worker_tool_writeback",
              tool_name: "bash",
            },
            requires_vault_authorization: false,
            retrieval_backend: "memu",
          },
        ],
        available_scopes: ["scope-contact"],
        available_partitions: ["contact", "work"],
        available_layers: ["sor", "fragment"],
        warnings: [],
        updated_at: "2026-03-13T16:00:00Z",
      },
      config: {
        current_value: {
          memory: {
            backend_mode: "memu",
            bridge_url: "https://memory.example.com",
            bridge_api_key_env: "MEMU_API_KEY",
          },
        },
        ui_hints: {
          "memory.bridge_url": {
            label: "MemU Bridge 地址",
          },
          "memory.bridge_api_key_env": {
            label: "MemU API Key 环境变量",
          },
        },
      },
      diagnostics: {
        recovery_summary: {
          latest_backup: null,
          latest_recovery_drill: null,
          ready_for_restore: false,
        },
      },
      sessions: {
        focused_session_id: "session-1",
        focused_thread_id: "thread-1",
        sessions: [
          {
            session_id: "session-1",
            thread_id: "thread-1",
            title: "Memory Thread",
          },
        ],
        operator_summary: {
          total_pending: 0,
          approvals: 0,
          pairing_requests: 0,
        },
        operator_items: [],
      },
    },
  };
}

describe("MemoryPage", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("按当前筛选条件提交 memory.query", async () => {
    const submitAction = vi.fn().mockResolvedValue(null);
    mockWorkbench = {
      snapshot: buildMemorySnapshot(),
      submitAction,
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <MemoryPage />
      </MemoryRouter>
    );

    await userEvent.selectOptions(screen.getByLabelText("记忆类型"), "sor");
    await userEvent.selectOptions(screen.getByLabelText("主题分区"), "contact");
    await userEvent.type(screen.getByLabelText("关键词"), "Alice");
    await userEvent.click(screen.getByLabelText("包含历史版本"));
    await userEvent.click(screen.getByRole("button", { name: "重新查看" }));

    await waitFor(() =>
      expect(submitAction).toHaveBeenCalledWith("memory.query", {
        project_id: "project-default",
        workspace_id: "workspace-default",
        query: "Alice",
        layer: "sor",
        partition: "contact",
        include_history: true,
        include_vault_refs: false,
        limit: 50,
      })
    );
  });

  it("支持切换记录并在右侧 inspector 显示详情", async () => {
    mockWorkbench = {
      snapshot: buildMemorySnapshot(),
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <MemoryPage />
      </MemoryRouter>
    );

    const aliceHeading = await screen.findByRole("heading", { name: "Alice" });
    const aliceInspector = aliceHeading.closest("section");
    expect(aliceInspector).not.toBeNull();
    expect(within(aliceInspector!).getByText(/Alice 偏好异步沟通/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "查看 Bob 详情" }));

    const bobHeading = await screen.findByRole("heading", { name: "Bob" });
    const bobInspector = bobHeading.closest("section");
    expect(bobInspector).not.toBeNull();
    expect(within(bobInspector!).getByText("Bob 需要每周汇总。")).toBeInTheDocument();
  });

  it("会过滤内部技术写回记录并展示派生细节", async () => {
    const snapshot = buildMemorySnapshot();
    snapshot.resources.memory.records.push({
      record_id: "record-derived",
      layer: "derived",
      project_id: "project-default",
      workspace_id: "workspace-default",
      scope_id: "scope-contact",
      partition: "contact",
      subject_key: "Alice 协作偏好",
      summary: "",
      status: "derived",
      version: null,
      created_at: "2026-03-09T10:20:00Z",
      updated_at: null,
      evidence_refs: [{ type: "fragment", id: "fragment-1" }],
      derived_refs: ["record-derived"],
      proposal_refs: [],
      metadata: {
        derived_type: "tom",
        confidence: 0.82,
        belief: "更偏好异步",
      },
      requires_vault_authorization: false,
      retrieval_backend: "memu",
    });
    snapshot.resources.memory.available_layers.push("derived");

    mockWorkbench = {
      snapshot,
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <MemoryPage />
      </MemoryRouter>
    );

    expect(await screen.findByRole("heading", { name: "3 条可读记忆" })).toBeInTheDocument();
    expect(screen.queryByText(/worker_tool:bash:artifact-7/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "查看 Alice 协作偏好 详情" }));

    const derivedHeading = await screen.findByRole("heading", { name: "Alice 协作偏好" });
    const derivedInspector = derivedHeading.closest("section");
    expect(derivedInspector).not.toBeNull();
    expect(within(derivedInspector!).getByText("ToM 判断 · 置信度 82%")).toBeInTheDocument();
    expect(within(derivedInspector!).getByText("更偏好异步")).toBeInTheDocument();
  });
});
