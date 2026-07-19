/**
 * ApprovalCenterPage 集成测试 — F145 AC-1/2/3/4/8
 *
 * 含从 MemoryCandidatesPage.test.tsx 迁移的等价用例（memory 源吸收不回归，AC-2）。
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";
import ApprovalCenterPage from "./ApprovalCenterPage";
import { ApprovalActionError } from "../../api/approval-center";
import type {
  CompactCandidatesResponse,
  ConsolidationCandidatesResponse,
} from "../../api/approval-center";
import type { MemoryCandidatesResponse } from "../../api/memory-candidates";

vi.mock("../../api/memory-candidates", () => ({
  fetchMemoryCandidates: vi.fn(),
  promoteCandidate: vi.fn(),
  discardCandidate: vi.fn(),
  bulkDiscardCandidates: vi.fn(),
}));

vi.mock("../../api/approval-center", async (importOriginal) => {
  // ApprovalActionError 保留真实实现（mapApprovalFailure instanceof 判定需要）
  const actual = await importOriginal<typeof import("../../api/approval-center")>();
  return {
    ...actual,
    fetchConsolidationCandidates: vi.fn(),
    acceptConsolidationCandidate: vi.fn(),
    rejectConsolidationCandidate: vi.fn(),
    bulkRejectConsolidation: vi.fn(),
    fetchCompactCandidates: vi.fn(),
    acceptCompactCandidate: vi.fn(),
    rejectCompactCandidate: vi.fn(),
    fetchApprovalSummary: vi.fn(),
  };
});

import {
  fetchMemoryCandidates,
  promoteCandidate,
  discardCandidate,
  bulkDiscardCandidates,
} from "../../api/memory-candidates";
import {
  acceptCompactCandidate,
  acceptConsolidationCandidate,
  bulkRejectConsolidation,
  fetchCompactCandidates,
  fetchConsolidationCandidates,
} from "../../api/approval-center";

function memoryResponse(
  overrides?: Partial<MemoryCandidatesResponse>
): MemoryCandidatesResponse {
  return {
    candidates: [
      {
        id: "c1",
        fact_content: "用户喜欢异步沟通",
        category: "preference",
        confidence: 0.9,
        created_at: new Date(Date.now() - 7200_000).toISOString(),
        expires_at: null,
        source_turn_id: "turn-1",
      },
      {
        id: "c2",
        fact_content: "用户是全栈工程师",
        category: "identity",
        confidence: 0.75,
        created_at: new Date(Date.now() - 1800_000).toISOString(),
        expires_at: null,
        source_turn_id: "turn-2",
      },
    ],
    total: 2,
    pending_count: 2,
    ...overrides,
  };
}

function consolidationResponse(
  overrides?: Partial<ConsolidationCandidatesResponse>
): ConsolidationCandidatesResponse {
  return {
    candidates: [
      {
        candidate_id: "consol-1",
        run_id: "run-1",
        partition: "profile",
        subject_key: "timezone",
        source_count: 2,
        merged_content: "用户时区 Asia/Shanghai（权威）",
        rationale: "两条同指一个时区",
        confidence: 0.9,
        is_sensitive: false,
        status: "pending",
        created_at: new Date(Date.now() - 3600_000).toISOString(),
        source_previews: ["时区 上海", "时区 Asia/Shanghai"],
      },
    ],
    pending_count: 1,
    ...overrides,
  };
}

function compactResponse(
  overrides?: Partial<CompactCandidatesResponse>
): CompactCandidatesResponse {
  return {
    candidates: [
      {
        candidate_id: "bcpt-1",
        run_id: "run-b1",
        file_id: "AGENTS.md",
        agent_slug: "main",
        project_slug: "default",
        rationale: "合并了三条重复的简洁性规则",
        size_before: 342,
        size_after: 242,
        status: "pending",
        created_at: new Date(Date.now() - 600_000).toISOString(),
        diff: "@@ -1,2 +1,1 @@\n-旧规则甲\n-旧规则乙\n+精简后的规则\n",
      },
    ],
    pending_count: 1,
    ...overrides,
  };
}

function emptyAll() {
  vi.mocked(fetchMemoryCandidates).mockResolvedValue(
    memoryResponse({ candidates: [], total: 0, pending_count: 0 })
  );
  vi.mocked(fetchConsolidationCandidates).mockResolvedValue(
    consolidationResponse({ candidates: [], pending_count: 0 })
  );
  vi.mocked(fetchCompactCandidates).mockResolvedValue(
    compactResponse({ candidates: [], pending_count: 0 })
  );
}

function fullAll() {
  vi.mocked(fetchMemoryCandidates).mockResolvedValue(memoryResponse());
  vi.mocked(fetchConsolidationCandidates).mockResolvedValue(
    consolidationResponse()
  );
  vi.mocked(fetchCompactCandidates).mockResolvedValue(compactResponse());
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ApprovalCenterPage />
    </MemoryRouter>
  );
}

describe("ApprovalCenterPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ---- AC-1：三源分组渲染 ----

  it("三源都有数据时渲染三个分组与人话摘要", async () => {
    fullAll();
    renderPage();

    expect(await screen.findByText("新记忆（2）")).toBeInTheDocument();
    expect(screen.getByText("记忆合并建议（1）")).toBeInTheDocument();
    expect(screen.getByText("规则精简建议（1）")).toBeInTheDocument();
    // 人话摘要
    expect(screen.getByText("建议把 2 条相似记忆合并为一条")).toBeInTheDocument();
    expect(
      screen.getByText("建议精简「AGENTS.md」：约 342 字 → 约 242 字")
    ).toBeInTheDocument();
    // 技术字段不上卡面（AC-1）
    expect(screen.queryByText(/consol-1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/run-b1/)).not.toBeInTheDocument();
  });

  it("三源全空时展示统一 empty state", async () => {
    emptyAll();
    renderPage();

    expect(await screen.findByText("暂无待处理的提议")).toBeInTheDocument();
    expect(screen.queryByText(/新记忆（/)).not.toBeInTheDocument();
  });

  it("一源加载失败只影响该分组，其余源可操作（按源降级）", async () => {
    vi.mocked(fetchMemoryCandidates).mockRejectedValue(new Error("网络不可用"));
    vi.mocked(fetchConsolidationCandidates).mockResolvedValue(
      consolidationResponse()
    );
    vi.mocked(fetchCompactCandidates).mockResolvedValue(
      compactResponse({ candidates: [], pending_count: 0 })
    );
    renderPage();

    expect(await screen.findByText("这部分暂时加载失败")).toBeInTheDocument();
    expect(screen.getByText("网络不可用")).toBeInTheDocument();
    // 其余源正常渲染
    expect(screen.getByText("记忆合并建议（1）")).toBeInTheDocument();
  });

  // ---- AC-2：memory 源迁移等价用例（吸收不回归） ----

  it("memory accept 后候选从列表移除", async () => {
    fullAll();
    vi.mocked(promoteCandidate).mockResolvedValue(undefined);
    renderPage();

    await screen.findByText("用户喜欢异步沟通");
    const acceptButtons = screen.getAllByRole("button", { name: "接受" });
    // memory 卡的接受按钮在最前（新记忆 section 在页面靠前）
    await userEvent.click(acceptButtons[0]);

    await waitFor(() => {
      expect(screen.queryByText("用户喜欢异步沟通")).not.toBeInTheDocument();
      expect(screen.getByText("用户是全栈工程师")).toBeInTheDocument();
    });
  });

  it("memory reject 后候选从列表移除", async () => {
    fullAll();
    vi.mocked(discardCandidate).mockResolvedValue(undefined);
    renderPage();

    await screen.findByText("用户喜欢异步沟通");
    const rejectButtons = screen.getAllByRole("button", { name: "忽略" });
    await userEvent.click(rejectButtons[0]);

    await waitFor(() => {
      expect(screen.queryByText("用户喜欢异步沟通")).not.toBeInTheDocument();
    });
  });

  it("memory 批量忽略后该分组清空", async () => {
    vi.mocked(fetchMemoryCandidates).mockResolvedValue(memoryResponse());
    vi.mocked(fetchConsolidationCandidates).mockResolvedValue(
      consolidationResponse({ candidates: [], pending_count: 0 })
    );
    vi.mocked(fetchCompactCandidates).mockResolvedValue(
      compactResponse({ candidates: [], pending_count: 0 })
    );
    vi.mocked(bulkDiscardCandidates).mockResolvedValue({
      discarded_count: 2,
      skipped_ids: [],
    });
    renderPage();

    await screen.findByText("用户喜欢异步沟通");
    await userEvent.click(screen.getByRole("button", { name: /全部忽略/ }));

    await waitFor(() => {
      expect(screen.queryByText("用户喜欢异步沟通")).not.toBeInTheDocument();
      expect(screen.queryByText("用户是全栈工程师")).not.toBeInTheDocument();
      expect(screen.getByText("暂无待处理的提议")).toBeInTheDocument();
    });
  });

  // ---- AC-3：F127 accept 与 conflict 终态分流 ----

  it("consolidation accept 成功后卡片移除 + 成功 toast", async () => {
    fullAll();
    vi.mocked(acceptConsolidationCandidate).mockResolvedValue(undefined);
    renderPage();

    await screen.findByText("建议把 2 条相似记忆合并为一条");
    // ProposalCard 的接受按钮：定位到记忆合并卡内
    const acceptButtons = screen.getAllByRole("button", { name: "接受" });
    await userEvent.click(acceptButtons[acceptButtons.length - 2]);

    await waitFor(() => {
      expect(acceptConsolidationCandidate).toHaveBeenCalledWith("consol-1");
      expect(
        screen.queryByText("建议把 2 条相似记忆合并为一条")
      ).not.toBeInTheDocument();
      expect(screen.getByText("已合并为一条记忆。")).toBeInTheDocument();
    });
  });

  it("conflict 终态：卡片移除 + 已失效 toast（不诱导重试）", async () => {
    vi.mocked(fetchMemoryCandidates).mockResolvedValue(
      memoryResponse({ candidates: [], total: 0, pending_count: 0 })
    );
    vi.mocked(fetchConsolidationCandidates).mockResolvedValue(
      consolidationResponse()
    );
    vi.mocked(fetchCompactCandidates).mockResolvedValue(
      compactResponse({ candidates: [], pending_count: 0 })
    );
    vi.mocked(acceptConsolidationCandidate).mockRejectedValue(
      new ApprovalActionError({
        httpStatus: 409,
        resultStatus: "conflict",
        detail: "源已变更",
      })
    );
    renderPage();

    await screen.findByText("建议把 2 条相似记忆合并为一条");
    await userEvent.click(screen.getByRole("button", { name: "接受" }));

    await waitFor(() => {
      expect(
        screen.queryByText("建议把 2 条相似记忆合并为一条")
      ).not.toBeInTheDocument();
      expect(
        screen.getByText("这条提议在等待期间已失效，已自动关闭。")
      ).toBeInTheDocument();
    });
  });

  it("pending 回滚：卡片保留 + 可重试 toast", async () => {
    vi.mocked(fetchMemoryCandidates).mockResolvedValue(
      memoryResponse({ candidates: [], total: 0, pending_count: 0 })
    );
    vi.mocked(fetchConsolidationCandidates).mockResolvedValue(
      consolidationResponse()
    );
    vi.mocked(fetchCompactCandidates).mockResolvedValue(
      compactResponse({ candidates: [], pending_count: 0 })
    );
    vi.mocked(acceptConsolidationCandidate).mockRejectedValue(
      new ApprovalActionError({
        httpStatus: 409,
        resultStatus: "pending",
        detail: "内部回滚",
      })
    );
    renderPage();

    await screen.findByText("建议把 2 条相似记忆合并为一条");
    await userEvent.click(screen.getByRole("button", { name: "接受" }));

    await waitFor(() => {
      expect(
        screen.getByText("建议把 2 条相似记忆合并为一条")
      ).toBeInTheDocument();
      expect(screen.getByText("处理没有成功，请稍后重试。")).toBeInTheDocument();
    });
  });

  it("consolidation 全部拒绝按 rejected 结果移除", async () => {
    vi.mocked(fetchMemoryCandidates).mockResolvedValue(
      memoryResponse({ candidates: [], total: 0, pending_count: 0 })
    );
    vi.mocked(fetchConsolidationCandidates).mockResolvedValue(
      consolidationResponse()
    );
    vi.mocked(fetchCompactCandidates).mockResolvedValue(
      compactResponse({ candidates: [], pending_count: 0 })
    );
    vi.mocked(bulkRejectConsolidation).mockResolvedValue({
      rejected: ["consol-1"],
      skipped: [],
    });
    renderPage();

    await screen.findByText("建议把 2 条相似记忆合并为一条");
    await userEvent.click(screen.getByRole("button", { name: /全部拒绝/ }));

    await waitFor(() => {
      expect(
        screen.queryByText("建议把 2 条相似记忆合并为一条")
      ).not.toBeInTheDocument();
      expect(screen.getByText("暂无待处理的提议")).toBeInTheDocument();
    });
  });

  // ---- AC-4：F111 accept + diff 折叠渲染 ----

  it("compact accept 调对应 API 并移除卡片", async () => {
    vi.mocked(fetchMemoryCandidates).mockResolvedValue(
      memoryResponse({ candidates: [], total: 0, pending_count: 0 })
    );
    vi.mocked(fetchConsolidationCandidates).mockResolvedValue(
      consolidationResponse({ candidates: [], pending_count: 0 })
    );
    vi.mocked(fetchCompactCandidates).mockResolvedValue(compactResponse());
    vi.mocked(acceptCompactCandidate).mockResolvedValue(undefined);
    renderPage();

    await screen.findByText("建议精简「AGENTS.md」：约 342 字 → 约 242 字");
    await userEvent.click(screen.getByTestId("approval-compact-accept"));

    await waitFor(() => {
      expect(acceptCompactCandidate).toHaveBeenCalledWith("bcpt-1");
      expect(
        screen.queryByText("建议精简「AGENTS.md」：约 342 字 → 约 242 字")
      ).not.toBeInTheDocument();
      expect(screen.getByText("已接受，规则文件已更新。")).toBeInTheDocument();
    });
  });

  it("compact 折叠区渲染 diff 行（增删着色模型）与理由", async () => {
    vi.mocked(fetchMemoryCandidates).mockResolvedValue(
      memoryResponse({ candidates: [], total: 0, pending_count: 0 })
    );
    vi.mocked(fetchConsolidationCandidates).mockResolvedValue(
      consolidationResponse({ candidates: [], pending_count: 0 })
    );
    vi.mocked(fetchCompactCandidates).mockResolvedValue(compactResponse());
    renderPage();

    await screen.findByText("建议精简「AGENTS.md」：约 342 字 → 约 242 字");
    await userEvent.click(screen.getByText("查看详情"));

    expect(screen.getByText("理由：合并了三条重复的简洁性规则")).toBeInTheDocument();
    const removed = screen.getByText("旧规则甲");
    expect(removed.closest('[data-diff-kind="removed"]')).not.toBeNull();
    const added = screen.getByText("精简后的规则");
    expect(added.closest('[data-diff-kind="added"]')).not.toBeNull();
  });

  it("consolidation 折叠区展示来源记忆预览", async () => {
    vi.mocked(fetchMemoryCandidates).mockResolvedValue(
      memoryResponse({ candidates: [], total: 0, pending_count: 0 })
    );
    vi.mocked(fetchConsolidationCandidates).mockResolvedValue(
      consolidationResponse()
    );
    vi.mocked(fetchCompactCandidates).mockResolvedValue(
      compactResponse({ candidates: [], pending_count: 0 })
    );
    renderPage();

    await screen.findByText("建议把 2 条相似记忆合并为一条");
    await userEvent.click(screen.getByText("查看详情"));

    expect(screen.getByText("将被合并的记忆：")).toBeInTheDocument();
    expect(screen.getByText("时区 上海")).toBeInTheDocument();
    expect(screen.getByText("时区 Asia/Shanghai")).toBeInTheDocument();
  });
});
