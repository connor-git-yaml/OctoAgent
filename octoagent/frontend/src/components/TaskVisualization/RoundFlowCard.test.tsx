import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Round } from "../../utils/roundSplitter";
import RoundFlowCard, { buildRoundDiagnosticSummary } from "./RoundFlowCard";

const ROUND: Round = {
  id: "round-1",
  index: 3,
  triggerMessage: "不得进入剪贴板的用户消息",
  triggerEventId: "event-private",
  taskId: "task-private",
  startTime: "2026-07-26T08:00:00Z",
  nodes: [
    {
      id: "node-1",
      kind: "tool",
      label: "读取状态",
      status: "success",
      events: [],
      artifacts: [],
      ts: "2026-07-26T08:00:00Z",
      agent: "Orchestrator",
      durationMs: 12,
    },
  ],
};

describe("RoundFlowCard诊断复制", () => {
  it("只复制轮次摘要，不复制内部标识或用户消息", () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(<RoundFlowCard round={ROUND} onNodeClick={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "复制诊断信息" }));

    const summary = buildRoundDiagnosticSummary(ROUND);
    expect(writeText).toHaveBeenCalledWith(summary);
    expect(summary).toContain("轮次: #3");
    expect(summary).toContain("步骤数: 1");
    expect(summary).toContain("Agent 数: 1");
    expect(summary).not.toContain(ROUND.taskId);
    expect(summary).not.toContain(ROUND.triggerEventId);
    expect(summary).not.toContain(ROUND.triggerMessage);
  });
});
