/**
 * MemoryCandidatesPage 集成测试 — Feature 084 T053
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";
import MemoryCandidatesPage from "./MemoryCandidatesPage";
import type { MemoryCandidatesResponse } from "../../api/memory-candidates";

vi.mock("../../api/memory-candidates", () => ({
  fetchMemoryCandidates: vi.fn(),
  promoteCandidate: vi.fn(),
  discardCandidate: vi.fn(),
  bulkDiscardCandidates: vi.fn(),
}));

import {
  fetchMemoryCandidates,
  promoteCandidate,
  discardCandidate,
  bulkDiscardCandidates,
} from "../../api/memory-candidates";

function buildResponse(overrides?: Partial<MemoryCandidatesResponse>): MemoryCandidatesResponse {
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

function renderPage() {
  return render(
    <MemoryRouter>
      <MemoryCandidatesPage />
    </MemoryRouter>
  );
}

describe("MemoryCandidatesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loading 状态下展示加载提示", () => {
    vi.mocked(fetchMemoryCandidates).mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByLabelText("加载中")).toBeInTheDocument();
  });

  it("加载成功后展示候选列表", async () => {
    vi.mocked(fetchMemoryCandidates).mockResolvedValue(buildResponse());
    renderPage();

    expect(await screen.findByText("用户喜欢异步沟通")).toBeInTheDocument();
    expect(screen.getByText("用户是全栈工程师")).toBeInTheDocument();
    expect(screen.getByText("90%")).toBeInTheDocument();
  });

  it("加载失败时展示 error 状态和重试按钮", async () => {
    vi.mocked(fetchMemoryCandidates).mockRejectedValue(new Error("网络不可用"));
    renderPage();

    expect(await screen.findByText("加载失败")).toBeInTheDocument();
    expect(screen.getByText("网络不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载" })).toBeInTheDocument();
  });

  it("空列表时展示 empty state", async () => {
    vi.mocked(fetchMemoryCandidates).mockResolvedValue(
      buildResponse({ candidates: [], total: 0, pending_count: 0 })
    );
    renderPage();

    expect(await screen.findByText("暂无待确认的记忆")).toBeInTheDocument();
  });

  it("accept 操作后候选从列表移除", async () => {
    vi.mocked(fetchMemoryCandidates).mockResolvedValue(buildResponse());
    vi.mocked(promoteCandidate).mockResolvedValue(undefined);
    renderPage();

    // 等待候选加载完成
    await screen.findByText("用户喜欢异步沟通");

    // 点击第一张卡片的"接受"
    const acceptButtons = screen.getAllByRole("button", { name: "接受" });
    await userEvent.click(acceptButtons[0]);

    await waitFor(() => {
      expect(screen.queryByText("用户喜欢异步沟通")).not.toBeInTheDocument();
      // 第二条还在
      expect(screen.getByText("用户是全栈工程师")).toBeInTheDocument();
    });
  });

  it("reject 操作后候选从列表移除", async () => {
    vi.mocked(fetchMemoryCandidates).mockResolvedValue(buildResponse());
    vi.mocked(discardCandidate).mockResolvedValue(undefined);
    renderPage();

    await screen.findByText("用户喜欢异步沟通");

    const rejectButtons = screen.getAllByRole("button", { name: "忽略" });
    await userEvent.click(rejectButtons[0]);

    await waitFor(() => {
      expect(screen.queryByText("用户喜欢异步沟通")).not.toBeInTheDocument();
    });
  });

  it("批量忽略后列表清空", async () => {
    vi.mocked(fetchMemoryCandidates).mockResolvedValue(buildResponse());
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
      expect(screen.getByText("暂无待确认的记忆")).toBeInTheDocument();
    });
  });
});
