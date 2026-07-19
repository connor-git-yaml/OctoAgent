import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SessionRunPanel, type SessionRunPanelProps } from "./SessionRunPanel";
import type { Artifact, TaskEvent } from "../../types";

function renderPanel(overrides: Partial<SessionRunPanelProps> = {}) {
  const props: SessionRunPanelProps = {
    taskId: "task-1",
    statusLabel: "运行中",
    statusTone: "running",
    normalizedTaskStatus: "RUNNING",
    currentStep: "正在搜索资料",
    streaming: true,
    events: [],
    artifacts: [],
    techRefs: [],
    ...overrides,
  };
  return render(
    <MemoryRouter>
      <SessionRunPanel {...props} />
    </MemoryRouter>
  );
}

describe("SessionRunPanel（F148 右栏本会话运行状态）", () => {
  it("无活跃任务时显示就绪空态（交互态①）", () => {
    renderPanel({ taskId: null });
    expect(screen.getByTestId("session-run-empty")).toBeInTheDocument();
    expect(screen.getByText("就绪")).toBeInTheDocument();
    // 空态不渲染「打开任务详情」
    expect(screen.queryByText("打开任务详情")).not.toBeInTheDocument();
  });

  it("成功终态且非流式回到就绪空态——不因残留 taskId 显示旧运行（Codex P2）", () => {
    renderPanel({ taskId: "task-old", normalizedTaskStatus: "SUCCEEDED", streaming: false });
    expect(screen.getByTestId("session-run-empty")).toBeInTheDocument();
  });

  it("失败终态仍显示面板 + 失败横幅（不静默消失）", () => {
    renderPanel({ normalizedTaskStatus: "FAILED", statusLabel: "已失败", streaming: false });
    expect(screen.queryByTestId("session-run-empty")).not.toBeInTheDocument();
    expect(screen.getByTestId("session-run-failed")).toBeInTheDocument();
  });

  it("运行态显示状态徽标 + 进度 + 打开任务链接", () => {
    renderPanel({ currentStep: "正在搜索资料" });
    expect(screen.getByText("本会话运行状态")).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByText("正在搜索资料")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开任务详情" })).toHaveAttribute(
      "href",
      "/tasks/task-1"
    );
  });

  it("事件流按类型渲染友好动作文案", () => {
    const events: TaskEvent[] = [
      {
        event_id: "e1",
        task_seq: 1,
        ts: "2026-07-20T01:00:00Z",
        type: "TOOL_CALL_STARTED",
        actor: "agent",
        payload: { tool_name: "web.search" },
      },
      {
        event_id: "e2",
        task_seq: 2,
        ts: "2026-07-20T01:00:05Z",
        type: "ARTIFACT_CREATED",
        actor: "agent",
        payload: {},
      },
    ];
    renderPanel({ events });
    expect(screen.getByText("调用工具 · web.search")).toBeInTheDocument();
    expect(screen.getByText("生成产出文件")).toBeInTheDocument();
  });

  it("有工件时渲染产出文件名", () => {
    const artifacts: Artifact[] = [
      { artifact_id: "a1", name: "report.md", size: 128, parts: [] },
    ];
    renderPanel({ artifacts });
    expect(screen.getByText("report.md")).toBeInTheDocument();
  });
});
