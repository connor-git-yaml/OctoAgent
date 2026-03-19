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
        retrieval_backend: "sqlite-metadata",
        backend_state: "healthy",
        backend_id: "memory-local",
        retrieval_profile: {
          engine_mode: "builtin",
          engine_label: "内建记忆引擎",
          transport: "builtin",
          transport_label: "内建",
          active_backend: "sqlite-metadata",
          active_backend_label: "本地元数据回退",
          backend_state: "healthy",
          backend_summary: "当前 Memory 以本地 canonical store 为主。",
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
          scope_id: "",
          layer: "",
          partition: "",
          include_history: false,
          include_vault_refs: false,
          limit: 50,
        },
        summary: {
          sor_current_count: 2,
          fragment_count: 1,
          pending_consolidation_count: 1,
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
            retrieval_backend: "sqlite-metadata",
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
            retrieval_backend: "sqlite-metadata",
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
            retrieval_backend: "sqlite-metadata",
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
          memory: {},
        },
        ui_hints: {},
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
        scope_id: "",
        query: "Alice",
        layer: "sor",
        partition: "contact",
        include_history: true,
        include_vault_refs: false,
        limit: 50,
      })
    );
  });

  it("scope 选择器在多 scope 时渲染，切换后提交正确 scope_id", async () => {
    const snapshot = buildMemorySnapshot();
    // 提供多个 scope 以触发选择器渲染
    snapshot.resources.memory.available_scopes = [
      "memory/shared/butler-main",
      "memory/private/worker-ops/runtime:abc",
    ];
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

    // scope 选择器应该出现
    const scopeSelect = screen.getByLabelText("作用域");
    expect(scopeSelect).toBeInTheDocument();

    // 切换到 Worker 私有 scope
    await userEvent.selectOptions(scopeSelect, "memory/private/worker-ops/runtime:abc");
    await userEvent.click(screen.getByRole("button", { name: "重新查看" }));

    await waitFor(() =>
      expect(submitAction).toHaveBeenCalledWith("memory.query", {
        project_id: "project-default",
        workspace_id: "workspace-default",
        scope_id: "memory/private/worker-ops/runtime:abc",
        query: "",
        layer: "",
        partition: "",
        include_history: false,
        include_vault_refs: false,
        limit: 50,
      })
    );
  });

  it("scope 选择器在仅 1 个 scope 时仍渲染，包含「全部作用域」选项", async () => {
    const snapshot = buildMemorySnapshot();
    snapshot.resources.memory.available_scopes = ["scope-contact"];
    mockWorkbench = {
      snapshot: snapshot,
      submitAction: vi.fn(),
      busyActionId: null,
    };

    render(
      <MemoryRouter>
        <MemoryPage />
      </MemoryRouter>
    );

    // 1 个 scope + ""（全部作用域）= 2 个选项 -> 选择器渲染
    const scopeSelect = screen.getByLabelText("作用域");
    expect(scopeSelect).toBeInTheDocument();

    // 第一个选项应该是「全部作用域」
    const options = within(scopeSelect).getAllByRole("option");
    expect(options[0]).toHaveTextContent("全部作用域");
    expect(options[0]).toHaveValue("");
  });

  it("清空筛选按钮重置 scope_id 为空", async () => {
    const snapshot = buildMemorySnapshot();
    snapshot.resources.memory.available_scopes = [
      "memory/shared/butler-main",
      "memory/private/worker-ops/runtime:abc",
    ];
    snapshot.resources.memory.filters.scope_id = "memory/shared/butler-main";
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

    // 初始 scope 应该被选中
    expect(screen.getByLabelText("作用域")).toHaveValue("memory/shared/butler-main");

    // 点击清空筛选
    await userEvent.click(screen.getByRole("button", { name: "清空筛选" }));

    await waitFor(() =>
      expect(submitAction).toHaveBeenCalledWith("memory.query", {
        project_id: "project-default",
        workspace_id: "workspace-default",
        scope_id: "",
        query: "",
        layer: "",
        partition: "",
        include_history: false,
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

    expect(screen.getByText("Memory 数据暂时不可用")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "回到 Chat" })).toHaveAttribute("href", "/");

    await userEvent.click(screen.getByRole("button", { name: "重新加载" }));

    expect(refreshSnapshot).toHaveBeenCalledTimes(1);
  });

  it("点击记录卡片弹出详情 modal", async () => {
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

    // 点击 Bob 的记录卡片，弹出详情 modal
    const bobCard = (await screen.findByText("Bob")).closest("article") as HTMLElement;
    expect(bobCard).not.toBeNull();
    await userEvent.click(bobCard);

    // modal 中应该展示 Bob 的详情
    const modalHeading = await screen.findByRole("heading", { name: "Bob" });
    const modalBody = modalHeading.closest(".wb-modal-body") as HTMLElement;
    expect(modalBody).not.toBeNull();
    expect(within(modalBody).getByText("Bob 需要每周汇总。")).toBeInTheDocument();

    // 关闭 modal
    await userEvent.click(within(modalBody).getByRole("button", { name: "关闭" }));
    await waitFor(() =>
      expect(screen.queryByText("关闭")).not.toBeInTheDocument()
    );
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

    // 3 条可见记忆（Alice + Bob + 派生，internal 被过滤）
    expect(await screen.findByRole("heading", { name: "3 条记忆" })).toBeInTheDocument();
    expect(screen.queryByText(/worker_tool:bash:artifact-7/)).not.toBeInTheDocument();

    // 点击派生记录卡片弹出 modal
    const derivedCard = screen.getByText("Alice 协作偏好").closest("article") as HTMLElement;
    expect(derivedCard).not.toBeNull();
    await userEvent.click(derivedCard);

    const modalHeading = await screen.findByRole("heading", { name: "Alice 协作偏好" });
    const modalBody = modalHeading.closest(".wb-modal-body") as HTMLElement;
    expect(modalBody).not.toBeNull();
    expect(within(modalBody).getByText("ToM 判断 · 置信度 82%")).toBeInTheDocument();
    expect(within(modalBody).getByText("更偏好异步")).toBeInTheDocument();
  });

  it("简化后的 hero 不再展示下一步引导面板", async () => {
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

    // hero 区存在且展示正常状态
    expect(await screen.findByText("Memory")).toBeInTheDocument();

    // 不再有下一步引导和已删除的面板
    expect(screen.queryByText("下一步")).not.toBeInTheDocument();
    expect(screen.queryByText("为什么这样判断")).not.toBeInTheDocument();
    expect(screen.queryByText("当前视图")).not.toBeInTheDocument();
    expect(screen.queryByText("更多入口")).not.toBeInTheDocument();
  });

  it("hero 展示引擎 chip", async () => {
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

    expect(await screen.findByText("引擎 内建记忆引擎")).toBeInTheDocument();
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

  it("没有积压时 hero 标题显示现行结论数", async () => {
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

    expect(await screen.findByRole("heading", { name: "2 条现行结论" })).toBeInTheDocument();
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

    expect(await screen.findByRole("heading", { name: "2 条记忆" })).toBeInTheDocument();
    expect(screen.queryByText("待确认事项")).not.toBeInTheDocument();
  });
});
