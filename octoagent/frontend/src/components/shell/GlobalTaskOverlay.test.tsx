import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import GlobalTaskOverlay from "./GlobalTaskOverlay";
import type { WorkProjectionItem } from "../../types";

function makeWork(overrides: Partial<WorkProjectionItem>): WorkProjectionItem {
  return {
    work_id: "work-1",
    task_id: "task-1",
    parent_work_id: "",
    title: "查资料",
    status: "running",
    target_kind: "worker",
    selected_worker_type: "",
    route_reason: "",
    owner_id: "",
    selected_tools: [],
    pipeline_run_id: "",
    runtime_id: "",
    project_id: "project-default",
    requested_agent_profile_id: "",
    requested_agent_profile_version: 0,
    effective_profile_snapshot_id: "",
    child_work_ids: [],
    child_work_count: 0,
    merge_ready: false,
    runtime_summary: {},
    updated_at: null,
    capabilities: [],
    ...overrides,
  };
}

describe("GlobalTaskOverlay（F148 全局任务浮层 1b）", () => {
  it("无活跃任务时不渲染 FAB（交互态①）", () => {
    const { container } = render(
      <MemoryRouter>
        <GlobalTaskOverlay activeWorks={[]} resolveAgentName={() => "主 Agent"} />
      </MemoryRouter>
    );
    expect(container.querySelector(".v2-task-fab")).toBeNull();
  });

  it("有活跃任务时 FAB 显示数量，展开列出任务", async () => {
    render(
      <MemoryRouter>
        <GlobalTaskOverlay
          activeWorks={[
            makeWork({ work_id: "w1", title: "查天气", status: "running" }),
            makeWork({ work_id: "w2", title: "写报告", status: "escalated" }),
          ]}
          resolveAgentName={() => "研究员小 A"}
        />
      </MemoryRouter>
    );
    // FAB 数量
    expect(screen.getByRole("button", { name: "进行中的任务 2 项" })).toBeInTheDocument();
    // 展开前浮层不在
    expect(screen.queryByTestId("global-task-overlay")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "进行中的任务 2 项" }));
    const overlay = screen.getByTestId("global-task-overlay");
    expect(overlay).toBeInTheDocument();
    expect(within(overlay).getByText("查天气")).toBeInTheDocument();
    expect(within(overlay).getByText("写报告")).toBeInTheDocument();
    // 与右栏同一状态词表（交互态④）——作用域限定在浮层内（FAB 也含「进行中」字样）
    expect(within(overlay).getByText("进行中")).toBeInTheDocument();
    expect(within(overlay).getByText("需关注")).toBeInTheDocument();
  });
});
