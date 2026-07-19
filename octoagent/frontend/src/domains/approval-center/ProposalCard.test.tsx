/**
 * ProposalCard 单测 — F145 AC-3 细粒度（busy 态 / 折叠 / 回调触发）
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import ProposalCard from "./ProposalCard";

function renderCard(overrides?: Partial<Parameters<typeof ProposalCard>[0]>) {
  const onAccept = vi.fn().mockResolvedValue(undefined);
  const onReject = vi.fn().mockResolvedValue(undefined);
  render(
    <ProposalCard
      typeLabel="记忆合并"
      summary="建议把 2 条相似记忆合并为一条"
      createdAt={new Date().toISOString()}
      onAccept={onAccept}
      onReject={onReject}
      {...overrides}
    />
  );
  return { onAccept, onReject };
}

describe("ProposalCard", () => {
  it("渲染类型标签、摘要与操作按钮", () => {
    renderCard();
    expect(screen.getByText("记忆合并")).toBeInTheDocument();
    expect(screen.getByText("建议把 2 条相似记忆合并为一条")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "接受" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeInTheDocument();
  });

  it("点击接受触发 onAccept 且执行中双按钮禁用", async () => {
    let resolveAction: () => void = () => {};
    const pending = new Promise<void>((resolve) => {
      resolveAction = resolve;
    });
    const onAccept = vi.fn().mockReturnValue(pending);
    renderCard({ onAccept });

    await userEvent.click(screen.getByRole("button", { name: "接受" }));
    expect(onAccept).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "处理中…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeDisabled();

    resolveAction();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "接受" })).toBeEnabled();
    });
  });

  it("onAccept 抛错时恢复可点（失败呈现由页面层负责）", async () => {
    renderCard({ onAccept: vi.fn().mockRejectedValue(new Error("boom")) });

    // run() 内部不吞错——点击回调是 void 包装，断言最终恢复 idle
    await userEvent.click(screen.getByRole("button", { name: "接受" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "接受" })).toBeEnabled();
    });
  });

  it("敏感标记与折叠详情渲染", async () => {
    renderCard({
      sensitive: true,
      details: <p>理由：来源相同</p>,
    });
    expect(screen.getByText("敏感内容")).toBeInTheDocument();
    await userEvent.click(screen.getByText("查看详情"));
    expect(screen.getByText("理由：来源相同")).toBeInTheDocument();
  });

  it("testid 锚点透传（L1 契约）", () => {
    renderCard({
      rootTestId: "approval-compact-card",
      acceptTestId: "approval-compact-accept",
    });
    expect(screen.getByTestId("approval-compact-card")).toBeInTheDocument();
    expect(screen.getByTestId("approval-compact-accept")).toBeInTheDocument();
  });
});
