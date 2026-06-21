import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkspaceGitView from "./WorkspaceGitView";

vi.mock("../api/client", () => ({
  fetchWorkspaceHistory: vi.fn(),
  fetchWorkspaceCommitFiles: vi.fn(),
  fetchWorkspaceBlame: vi.fn(),
  fetchWorkspaceDiff: vi.fn(),
  proposeWorkspaceRollback: vi.fn(),
  approveWorkspaceRollback: vi.fn(),
}));

import {
  approveWorkspaceRollback,
  fetchWorkspaceCommitFiles,
  fetchWorkspaceDiff,
  fetchWorkspaceHistory,
  proposeWorkspaceRollback,
} from "../api/client";

const historyMock = vi.mocked(fetchWorkspaceHistory);
const filesMock = vi.mocked(fetchWorkspaceCommitFiles);
const diffMock = vi.mocked(fetchWorkspaceDiff);
const proposeMock = vi.mocked(proposeWorkspaceRollback);
const approveMock = vi.mocked(approveWorkspaceRollback);

beforeEach(() => {
  vi.clearAllMocks();
  historyMock.mockResolvedValue({
    available: true,
    commits: [
      {
        commit: "c2hash",
        short: "c2hash",
        ts: "2026-06-22T10:00:00Z",
        summary: "before write 2",
        files_changed: 1,
        insertions: 0,
        deletions: 0,
      },
      {
        commit: "c1hash",
        short: "c1hash",
        ts: "2026-06-21T10:00:00Z",
        summary: "before write 1",
        files_changed: 1,
        insertions: 0,
        deletions: 0,
      },
    ],
  });
  filesMock.mockResolvedValue({
    files: [{ path: "workspace/main.py", status: "modified" }],
  });
  diffMock.mockResolvedValue({
    current: { content: "v2\n", availability: "available", oversize: false },
    previous: { content: "v1\n", availability: "available", oversize: false },
    binary: false,
    oversize: false,
  });
});

describe("WorkspaceGitView", () => {
  it("加载历史 + 选提交看文件 + 选文件看 diff", async () => {
    const user = userEvent.setup();
    render(<WorkspaceGitView projectSlug="demo" />);
    await waitFor(() =>
      expect(screen.getByText("before write 2")).toBeInTheDocument(),
    );
    await user.click(screen.getByText("before write 2"));
    await waitFor(() =>
      expect(screen.getByText(/workspace\/main\.py · modified/)).toBeInTheDocument(),
    );
    await user.click(screen.getByText(/workspace\/main\.py · modified/));
    await waitFor(() => expect(diffMock).toHaveBeenCalled());
  });

  it("git 不可用 → 友好占位（#6 降级）", async () => {
    historyMock.mockResolvedValue({ available: false, commits: [] });
    render(<WorkspaceGitView projectSlug="demo" />);
    await waitFor(() =>
      expect(screen.getByText("工作区版本历史暂不可用")).toBeInTheDocument(),
    );
  });

  it("回滚 Two-Phase：恢复→确认→propose+approve", async () => {
    proposeMock.mockResolvedValue({
      request_id: "req-1",
      status: "pending",
      files_count: 0,
    });
    approveMock.mockResolvedValue({
      request_id: "req-1",
      status: "executed",
      detail: "",
    });
    const user = userEvent.setup();
    render(<WorkspaceGitView projectSlug="demo" />);
    await waitFor(() =>
      expect(screen.getByText("before write 2")).toBeInTheDocument(),
    );
    await user.click(screen.getAllByText("恢复到此版本")[0]);
    expect(screen.getByText(/确认吗/)).toBeInTheDocument();
    await user.click(screen.getByText("确认恢复"));
    await waitFor(() => {
      expect(proposeMock).toHaveBeenCalled();
      expect(approveMock).toHaveBeenCalledWith("req-1");
    });
  });
});
