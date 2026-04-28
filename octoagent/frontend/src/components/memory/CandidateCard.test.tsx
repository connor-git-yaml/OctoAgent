/**
 * CandidateCard 单元测试 — Feature 084 T054
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import CandidateCard from "./CandidateCard";
import type { MemoryCandidate } from "../../api/memory-candidates";

// mock API 调用
vi.mock("../../api/memory-candidates", () => ({
  promoteCandidate: vi.fn(),
  discardCandidate: vi.fn(),
}));

import { promoteCandidate, discardCandidate } from "../../api/memory-candidates";

function buildCandidate(overrides?: Partial<MemoryCandidate>): MemoryCandidate {
  return {
    id: "candidate-001",
    fact_content: "用户喜欢异步沟通",
    category: "preference",
    confidence: 0.85,
    created_at: new Date(Date.now() - 3600_000).toISOString(),
    expires_at: null,
    source_turn_id: "turn-001",
    ...overrides,
  };
}

describe("CandidateCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("展示候选内容、分类和置信度", () => {
    const onRemove = vi.fn();
    const onToast = vi.fn();
    render(
      <CandidateCard
        candidate={buildCandidate()}
        onRemove={onRemove}
        onToast={onToast}
      />
    );

    expect(screen.getByText("用户喜欢异步沟通")).toBeInTheDocument();
    expect(screen.getByText("偏好")).toBeInTheDocument();
    expect(screen.getByText("85%")).toBeInTheDocument();
  });

  it("点击接受调用 promoteCandidate，成功后调用 onRemove", async () => {
    vi.mocked(promoteCandidate).mockResolvedValue(undefined);
    const onRemove = vi.fn();
    const onToast = vi.fn();
    render(
      <CandidateCard
        candidate={buildCandidate()}
        onRemove={onRemove}
        onToast={onToast}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "接受" }));

    await waitFor(() => {
      expect(promoteCandidate).toHaveBeenCalledWith("candidate-001");
      expect(onRemove).toHaveBeenCalledWith("candidate-001");
    });
  });

  it("点击接受失败时 toast 提示并恢复状态", async () => {
    vi.mocked(promoteCandidate).mockRejectedValue(new Error("服务器错误"));
    const onRemove = vi.fn();
    const onToast = vi.fn();
    render(
      <CandidateCard
        candidate={buildCandidate()}
        onRemove={onRemove}
        onToast={onToast}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "接受" }));

    await waitFor(() => {
      expect(onToast).toHaveBeenCalledWith("服务器错误", true);
      expect(onRemove).not.toHaveBeenCalled();
    });
    // 按钮应恢复可用
    expect(screen.getByRole("button", { name: "接受" })).not.toBeDisabled();
  });

  it("点击忽略调用 discardCandidate，成功后调用 onRemove", async () => {
    vi.mocked(discardCandidate).mockResolvedValue(undefined);
    const onRemove = vi.fn();
    const onToast = vi.fn();
    render(
      <CandidateCard
        candidate={buildCandidate()}
        onRemove={onRemove}
        onToast={onToast}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "忽略" }));

    await waitFor(() => {
      expect(discardCandidate).toHaveBeenCalledWith("candidate-001");
      expect(onRemove).toHaveBeenCalledWith("candidate-001");
    });
  });

  it("点击编辑后接受 进入编辑模式，展示 textarea", async () => {
    const onRemove = vi.fn();
    const onToast = vi.fn();
    render(
      <CandidateCard
        candidate={buildCandidate()}
        onRemove={onRemove}
        onToast={onToast}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "编辑后接受" }));

    expect(screen.getByLabelText("编辑内容")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存并接受" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toBeInTheDocument();
  });

  it("编辑后保存调用 promoteCandidate 携带修改内容", async () => {
    vi.mocked(promoteCandidate).mockResolvedValue(undefined);
    const onRemove = vi.fn();
    const onToast = vi.fn();
    render(
      <CandidateCard
        candidate={buildCandidate()}
        onRemove={onRemove}
        onToast={onToast}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "编辑后接受" }));

    const textarea = screen.getByLabelText("编辑内容");
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "用户偏好异步文字沟通");

    await userEvent.click(screen.getByRole("button", { name: "保存并接受" }));

    await waitFor(() => {
      expect(promoteCandidate).toHaveBeenCalledWith("candidate-001", "用户偏好异步文字沟通");
      expect(onRemove).toHaveBeenCalledWith("candidate-001");
    });
  });

  it("取消编辑恢复 idle 状态", async () => {
    const onRemove = vi.fn();
    const onToast = vi.fn();
    render(
      <CandidateCard
        candidate={buildCandidate()}
        onRemove={onRemove}
        onToast={onToast}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "编辑后接受" }));
    expect(screen.getByLabelText("编辑内容")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(screen.queryByLabelText("编辑内容")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "接受" })).toBeInTheDocument();
  });

  it("unknown category 显示原始值", () => {
    const onRemove = vi.fn();
    const onToast = vi.fn();
    render(
      <CandidateCard
        candidate={buildCandidate({ category: "custom_type" })}
        onRemove={onRemove}
        onToast={onToast}
      />
    );

    expect(screen.getByText("custom_type")).toBeInTheDocument();
  });
});
