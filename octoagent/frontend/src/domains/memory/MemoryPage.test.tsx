import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import MemoryPage from "./MemoryPage";

let mockWorkbench: {
  snapshot: unknown;
  submitAction: ReturnType<typeof vi.fn>;
  busyActionId: string | null;
  refreshSnapshot?: ReturnType<typeof vi.fn>;
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
        backend_state: "healthy",
        backend_id: "memory-local",
        retrieval_profile: {
          engine_mode: "memu_compat",
          engine_label: "MemU 兼容链路",
          transport: "http",
          transport_label: "HTTP Bridge",
          active_backend: "memu",
          active_backend_label: "增强检索",
          backend_state: "healthy",
          backend_summary: "当前已经通过 HTTP Bridge 接上增强记忆链路。",
          uses_compat_bridge: true,
          warnings: [],
          bindings: [
            {
              binding_key: "reasoning",
              label: "记忆加工",
              configured_alias: "main",
              effective_target: "main",
              effective_label: "main",
              fallback_target: "main",
              fallback_label: "main（默认）",
              status: "configured",
              summary: "当前优先用 main 做记忆加工、总结和候选整理。",
              warnings: [],
            },
            {
              binding_key: "expand",
              label: "查询扩写",
              configured_alias: "",
              effective_target: "main",
              effective_label: "main（默认）",
              fallback_target: "main",
              fallback_label: "main（默认）",
              status: "fallback",
              summary: "未绑定查询扩写模型时，当前沿用 main 做 recall 扩写。",
              warnings: [],
            },
            {
              binding_key: "embedding",
              label: "语义检索",
              configured_alias: "",
              effective_target: "engine-default",
              effective_label: "Qwen3-Embedding-0.6B（默认）",
              fallback_target: "engine-default",
              fallback_label: "Qwen3-Embedding-0.6B（默认）",
              status: "fallback",
              summary:
                "未绑定 embedding 模型时，当前优先由内建 Qwen3-Embedding-0.6B 接管；若本机运行时暂不可用，会自动回退到双语 hash embedding。",
              warnings: [],
            },
            {
              binding_key: "rerank",
              label: "结果重排",
              configured_alias: "",
              effective_target: "heuristic",
              effective_label: "heuristic（默认）",
              fallback_target: "heuristic",
              fallback_label: "heuristic（默认）",
              status: "fallback",
              summary: "未绑定 rerank 模型时，当前继续使用 heuristic 重排。",
              warnings: [],
            },
          ],
        },
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

  it("资源缺失时给出可恢复降级态，而不是直接崩溃", async () => {
    const refreshSnapshot = vi.fn().mockResolvedValue(undefined);
    mockWorkbench = {
      snapshot: {
        resources: {
          memory: null,
          config: null,
        },
      },
      submitAction: vi.fn(),
      busyActionId: null,
      refreshSnapshot,
    };

    render(
      <MemoryRouter>
        <MemoryPage />
      </MemoryRouter>
    );

    expect(screen.getByText("这页暂时还没拿到完整的 Memory 快照")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "回到 Chat" })).toHaveAttribute("href", "/chat");

    await userEvent.click(screen.getByRole("button", { name: "重新加载 Memory" }));

    expect(refreshSnapshot).toHaveBeenCalledTimes(1);
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

  it("只保留真正需要处理的 Memory 提示，并移除无关的全局待办区", async () => {
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

    expect(await screen.findByRole("heading", { name: "还有新的内容待整理" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "整理最新记忆" })).toBeInTheDocument();
    expect(screen.queryByText("现在就能使用")).not.toBeInTheDocument();
    expect(screen.queryByText("继续积累内容")).not.toBeInTheDocument();
    expect(screen.queryByText("待确认事项")).not.toBeInTheDocument();
    expect(screen.queryByText("备份与恢复")).not.toBeInTheDocument();
  });

  it("会展示当前实际生效的检索画像，而不是只靠模式猜状态", async () => {
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

    expect(await screen.findByText("引擎 MemU 兼容链路")).toBeInTheDocument();
    expect(screen.getByText("接入 HTTP Bridge")).toBeInTheDocument();
    expect(screen.getByText("记忆加工")).toBeInTheDocument();
    expect(screen.getByText("当前优先用 main 做记忆加工、总结和候选整理。")).toBeInTheDocument();
    expect(screen.getByText("语义检索")).toBeInTheDocument();
    expect(screen.getByText("Qwen3-Embedding-0.6B（默认）")).toBeInTheDocument();
  });

  it("会在 Memory 页面展示 embedding 迁移进度，并允许切换到新索引", async () => {
    const snapshot = buildMemorySnapshot();
    snapshot.resources.retrieval_platform = {
      resource_type: "retrieval_platform",
      active_project_id: "project-default",
      active_workspace_id: "workspace-default",
      profiles: [],
      corpora: [
        {
          corpus_kind: "memory",
          label: "Memory",
          active_generation_id: "gen-memory-active",
          pending_generation_id: "gen-memory-next",
          active_profile_id: "builtin:engine-default",
          active_profile_target: "engine-default",
          desired_profile_id: "alias:knowledge-embed",
          desired_profile_target: "knowledge-embed",
          state: "migration_pending",
          summary: "新的 embedding 已准备好切换，但当前查询仍继续使用旧索引。",
          warnings: ["embedding 迁移尚未 cutover；当前仍使用 engine-default。"],
          last_cutover_at: "2026-03-14T09:00:00Z",
        },
      ],
      generations: [
        {
          generation_id: "gen-memory-active",
          corpus_kind: "memory",
          profile_id: "builtin:engine-default",
          profile_target: "engine-default",
          label: "Qwen3-Embedding-0.6B（默认）",
          status: "active",
          is_active: true,
          build_job_id: "",
          previous_generation_id: "",
          created_at: "2026-03-14T09:00:00Z",
          updated_at: "2026-03-14T09:00:00Z",
          activated_at: "2026-03-14T09:00:00Z",
          completed_at: "2026-03-14T09:00:00Z",
          rollback_deadline: null,
          warnings: [],
          metadata: {},
        },
        {
          generation_id: "gen-memory-next",
          corpus_kind: "memory",
          profile_id: "alias:knowledge-embed",
          profile_target: "knowledge-embed",
          label: "knowledge-embed",
          status: "ready_to_cutover",
          is_active: false,
          build_job_id: "job-memory-next",
          previous_generation_id: "gen-memory-active",
          created_at: "2026-03-15T10:00:00Z",
          updated_at: "2026-03-15T10:05:00Z",
          activated_at: null,
          completed_at: "2026-03-15T10:05:00Z",
          rollback_deadline: null,
          warnings: ["配置已更新；切换前仍继续使用旧索引。"],
          metadata: {},
        },
      ],
      build_jobs: [
        {
          job_id: "job-memory-next",
          corpus_kind: "memory",
          generation_id: "gen-memory-next",
          stage: "ready_to_cutover",
          summary: "新索引已经准备好，等待切换。",
          total_items: 120,
          processed_items: 120,
          percent_complete: 100,
          can_cancel: true,
          created_at: "2026-03-15T10:00:00Z",
          updated_at: "2026-03-15T10:05:00Z",
          completed_at: "2026-03-15T10:05:00Z",
          latest_error: "",
          latest_maintenance_run_id: "run-memory-next",
          metadata: {},
        },
      ],
      warnings: [],
      summary: {
        active_generation_count: 1,
        pending_generation_count: 1,
        profile_count: 2,
      },
      updated_at: "2026-03-15T10:05:00Z",
    };
    const submitAction = vi.fn().mockResolvedValue(null);
    mockWorkbench = {
      snapshot,
      submitAction,
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <MemoryPage />
      </MemoryRouter>
    );

    expect(await screen.findByText("Embedding 迁移")).toBeInTheDocument();
    expect(screen.getByText("当前查询继续使用旧索引，直到新索引切换完成")).toBeInTheDocument();
    expect(
      screen.getByText(/Memory 和未来知识库会共用这条 embedding 轨道/)
    ).toBeInTheDocument();
    expect(screen.getAllByText("待切换")).toHaveLength(2);
    expect(screen.getByText("100%")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "切换到新索引" }));

    expect(submitAction).toHaveBeenCalledWith("retrieval.index.cutover", {
      generation_id: "gen-memory-next",
      project_id: "project-default",
      workspace_id: "workspace-default",
    });
  });

  it("没有积压时会隐藏下一步区，避免继续提醒已完成事项", async () => {
    const snapshot = buildMemorySnapshot();
    snapshot.resources.memory.summary.fragment_count = 0;
    snapshot.resources.memory.summary.pending_replay_count = 0;

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

    expect(await screen.findByRole("heading", { name: "Memory 当前记住了 2 条现行结论" })).toBeInTheDocument();
    expect(screen.queryByText("下一步")).not.toBeInTheDocument();
    expect(screen.queryByText("整理最新记忆")).not.toBeInTheDocument();
  });

  it("sessions 缺少会话列表时不会因为 focused session 查找而崩溃", async () => {
    const snapshot = buildMemorySnapshot();
    delete snapshot.resources.sessions.sessions;

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

    expect(await screen.findByRole("heading", { name: "2 条可读记忆" })).toBeInTheDocument();
    expect(screen.queryByText("待确认事项")).not.toBeInTheDocument();
  });
});
