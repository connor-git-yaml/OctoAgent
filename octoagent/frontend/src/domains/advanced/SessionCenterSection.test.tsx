import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { SessionProjectionItem } from "../../types";
import SessionCenterSection from "./SessionCenterSection";

function buildSession(sessionId: string): SessionProjectionItem {
  return {
    session_id: sessionId,
    thread_id: `thread-${sessionId}`,
    task_id: `task-${sessionId}`,
    parent_task_id: "",
    parent_work_id: "",
    title: `Task ${sessionId}`,
    status: "RUNNING",
    channel: "web",
    requester_id: "owner",
    project_id: "project-default",
    workspace_id: "workspace-default",
    runtime_kind: "worker",
    latest_message_summary: "正在处理中",
    latest_event_at: "2026-03-09T10:06:00Z",
    execution_summary: {},
    capabilities: [],
    detail_refs: {},
  };
}

describe("SessionCenterSection", () => {
  it("暴露开始新对话与重置 continuity 入口", async () => {
    const session = buildSession("session-alpha");
    const onNewSession = vi.fn();
    const onFocusSession = vi.fn();
    const onUnfocusSession = vi.fn();
    const onResetSession = vi.fn();
    const onExportSession = vi.fn();
    const onInterruptSession = vi.fn();
    const onResumeSession = vi.fn();
    const onSessionLaneChange = vi.fn();

    render(
      <MemoryRouter>
        <SessionCenterSection
          sessionFilter=""
          onSessionFilterChange={() => {}}
          sessionLane="all"
          onSessionLaneChange={onSessionLaneChange}
          sessionSummary={{
            total_sessions: 1,
            running_sessions: 1,
            queued_sessions: 0,
            history_sessions: 0,
            focused_sessions: 1,
          }}
          contextA2AConversations={[]}
          contextA2AMessages={[]}
          contextRecallFrames={[]}
          contextMemoryNamespaceCount={0}
          filteredSessions={[session]}
          focusedSessionId={session.session_id}
          busyActionId={null}
          onNewSession={onNewSession}
          onFocusSession={onFocusSession}
          onUnfocusSession={onUnfocusSession}
          onResetSession={onResetSession}
          onExportSession={onExportSession}
          onInterruptSession={onInterruptSession}
          onResumeSession={onResumeSession}
          projectNameForId={(projectId) =>
            projectId === "project-default" ? "Default Project" : projectId
          }
          workspaceNameForId={(workspaceId) =>
            workspaceId === "workspace-default" ? "Primary Workspace" : workspaceId
          }
          formatDateTime={(value) => value ?? "-"}
          formatA2ADirection={(value) => value}
          formatA2AMessageType={(value) => value}
          formatJson={(value) => JSON.stringify(value)}
          statusTone={() => "neutral"}
        />
      </MemoryRouter>
    );

    expect(screen.getByText("Focused")).toBeInTheDocument();
    expect(screen.getByText("Project: Default Project (project-default)")).toBeInTheDocument();
    expect(screen.getByText("Workspace: Primary Workspace (workspace-default)")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "开始新对话" }));
    expect(onNewSession).toHaveBeenCalledWith(session);

    await userEvent.click(screen.getByRole("button", { name: "取消聚焦" }));
    expect(onUnfocusSession).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole("button", { name: "重置 continuity" }));
    expect(onResetSession).toHaveBeenCalledWith(session);

    await userEvent.click(screen.getByRole("button", { name: "聚焦" }));
    expect(onFocusSession).toHaveBeenCalledWith(session);

    await userEvent.click(screen.getByRole("button", { name: "历史 0" }));
    expect(onSessionLaneChange).toHaveBeenCalledWith("history");
  });
});
