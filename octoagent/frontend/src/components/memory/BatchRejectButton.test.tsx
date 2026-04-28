/**
 * BatchRejectButton 单元测试 — Feature 084 T055（含 F31 修复）
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import BatchRejectButton from "./BatchRejectButton";

vi.mock("../../api/memory-candidates", () => ({
  bulkDiscardCandidates: vi.fn(),
}));

import { bulkDiscardCandidates } from "../../api/memory-candidates";

describe("BatchRejectButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("候选为空时按钮禁用", () => {
    render(
      <BatchRejectButton
        candidateIds={[]}
        onBulkDiscarded={vi.fn()}
        onToast={vi.fn()}
      />
    );

    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("有候选时显示数量", () => {
    render(
      <BatchRejectButton
        candidateIds={["a", "b", "c"]}
        onBulkDiscarded={vi.fn()}
        onToast={vi.fn()}
      />
    );

    expect(screen.getByRole("button")).toHaveTextContent("全部忽略（3 条）");
    expect(screen.getByRole("button")).not.toBeDisabled();
  });

  it("成功时 onBulkDiscarded 收到完整 discardedIds（无 skipped）", async () => {
    vi.mocked(bulkDiscardCandidates).mockResolvedValue({
      discarded_count: 3,
      skipped_ids: [],
    });
    const onBulkDiscarded = vi.fn();
    const onToast = vi.fn();

    render(
      <BatchRejectButton
        candidateIds={["a", "b", "c"]}
        onBulkDiscarded={onBulkDiscarded}
        onToast={onToast}
      />
    );

    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(bulkDiscardCandidates).toHaveBeenCalledWith(["a", "b", "c"]);
      // F31: 全部成功时 discardedIds = candidateIds，skippedIds = []
      expect(onBulkDiscarded).toHaveBeenCalledWith({
        discardedIds: ["a", "b", "c"],
        skippedIds: [],
      });
      expect(onToast).toHaveBeenCalledWith("已忽略全部 3 条候选", false);
    });
  });

  it("F31: 有 skipped_ids 时只把 discarded 部分传回，skipped 保留", async () => {
    vi.mocked(bulkDiscardCandidates).mockResolvedValue({
      discarded_count: 2,
      skipped_ids: ["b"],
    });
    const onBulkDiscarded = vi.fn();
    const onToast = vi.fn();

    render(
      <BatchRejectButton
        candidateIds={["a", "b", "c"]}
        onBulkDiscarded={onBulkDiscarded}
        onToast={onToast}
      />
    );

    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      // F31: discardedIds = candidateIds - skippedIds
      expect(onBulkDiscarded).toHaveBeenCalledWith({
        discardedIds: ["a", "c"],
        skippedIds: ["b"],
      });
      expect(onToast).toHaveBeenCalledWith(
        "已忽略 2 条，1 条跳过（保留在列表中）",
        false,
      );
    });
  });

  it("API 失败时 toast 提示错误，onBulkDiscarded 不被调用", async () => {
    vi.mocked(bulkDiscardCandidates).mockRejectedValue(new Error("网络错误"));
    const onBulkDiscarded = vi.fn();
    const onToast = vi.fn();

    render(
      <BatchRejectButton
        candidateIds={["a", "b"]}
        onBulkDiscarded={onBulkDiscarded}
        onToast={onToast}
      />
    );

    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(onToast).toHaveBeenCalledWith("网络错误", true);
      expect(onBulkDiscarded).not.toHaveBeenCalled();
    });
    expect(screen.getByRole("button")).not.toBeDisabled();
  });

  it("外部 disabled prop 禁用按钮", () => {
    render(
      <BatchRejectButton
        candidateIds={["a"]}
        onBulkDiscarded={vi.fn()}
        onToast={vi.fn()}
        disabled
      />
    );

    expect(screen.getByRole("button")).toBeDisabled();
  });
});
