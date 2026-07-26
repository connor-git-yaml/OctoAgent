import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkspaceGitView from "./WorkspaceGitView";

vi.mock("../api/client", () => ({
  fetchWorkspaceProjects: vi.fn(),
  fetchWorkspaceHistory: vi.fn(),
  fetchWorkspaceCommitFiles: vi.fn(),
  fetchWorkspaceBlame: vi.fn(),
  fetchWorkspaceDiff: vi.fn(),
  proposeWorkspaceRollback: vi.fn(),
  approveWorkspaceRollback: vi.fn(),
  rejectWorkspaceRollback: vi.fn(),
}));

import {
  approveWorkspaceRollback,
  fetchWorkspaceBlame,
  fetchWorkspaceCommitFiles,
  fetchWorkspaceDiff,
  fetchWorkspaceHistory,
  fetchWorkspaceProjects,
  proposeWorkspaceRollback,
  rejectWorkspaceRollback,
} from "../api/client";

const projectsMock = vi.mocked(fetchWorkspaceProjects);
const historyMock = vi.mocked(fetchWorkspaceHistory);
const filesMock = vi.mocked(fetchWorkspaceCommitFiles);
const blameMock = vi.mocked(fetchWorkspaceBlame);
const diffMock = vi.mocked(fetchWorkspaceDiff);
const proposeMock = vi.mocked(proposeWorkspaceRollback);
const approveMock = vi.mocked(approveWorkspaceRollback);
const rejectMock = vi.mocked(rejectWorkspaceRollback);

beforeEach(() => {
  vi.clearAllMocks();
  projectsMock.mockResolvedValue({
    available: true,
    projects: [
      {
        slug: "my-research",
        name: "my-research",
        last_commit_ts: "2026-06-22T10:00:00Z",
      },
      {
        slug: "default",
        name: "default",
        last_commit_ts: "2026-06-20T10:00:00Z",
      },
    ],
  });
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
  blameMock.mockResolvedValue({
    lines: [
      {
        line_no: 1,
        content: "print('hello')",
        commit: "c2hash",
        short: "c2hash",
        ts: "2026-06-22T10:00:00Z",
        summary: "更新脚本",
      },
    ],
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
      expect(
        screen.getByRole("button", {
          name: /workspace\/main\.py.*已修改/u,
        }),
      ).toBeInTheDocument(),
    );
    await user.click(
      screen.getByRole("button", {
        name: /workspace\/main\.py.*已修改/u,
      }),
    );
    await waitFor(() => expect(diffMock).toHaveBeenCalled());
  });

  it("无 prop → 解析项目列表 + 默认选最近项目（Opus H1：不写死 default）", async () => {
    render(<WorkspaceGitView />);
    // 默认应查询最近提交的项目 my-research，而非写死的 "default"
    await waitFor(() => expect(projectsMock).toHaveBeenCalled());
    await waitFor(() =>
      expect(historyMock).toHaveBeenCalledWith("my-research"),
    );
    await waitFor(() =>
      expect(screen.getByText("before write 2")).toBeInTheDocument(),
    );
  });

  it("git 不可用 → 友好占位（#6 降级）", async () => {
    historyMock.mockResolvedValue({ available: false, commits: [] });
    render(<WorkspaceGitView projectSlug="demo" />);
    await waitFor(() =>
      expect(screen.getByText("工作区版本历史暂不可用")).toBeInTheDocument(),
    );
  });

  it("回滚 Two-Phase：先提交申请，再由独立批准或拒绝动作完成", async () => {
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
    expect(
      screen.getByRole("dialog", { name: "提交回滚申请" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "提交申请" }));
    await waitFor(() => {
      expect(proposeMock).toHaveBeenCalled();
      expect(approveMock).not.toHaveBeenCalled();
      expect(screen.getByText("回滚申请 · 待批准")).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "批准" }));
    await waitFor(() => {
      expect(approveMock).toHaveBeenCalledWith("req-1");
    });

    proposeMock.mockResolvedValue({
      request_id: "req-2",
      status: "pending",
      files_count: 0,
    });
    rejectMock.mockResolvedValue({
      request_id: "req-2",
      status: "rejected",
    });
    await user.click(screen.getAllByText("恢复到此版本")[0]);
    await user.click(screen.getByRole("button", { name: "提交申请" }));
    await user.click(screen.getByRole("button", { name: "拒绝" }));
    await waitFor(() => {
      expect(rejectMock).toHaveBeenCalledWith("req-2");
    });
  });

  it("回滚目标冲突使用普通语言并要求重新发起", async () => {
    proposeMock.mockResolvedValue({
      request_id: "req-conflict",
      status: "pending",
      files_count: 1,
    });
    approveMock.mockRejectedValue(
      Object.assign(new Error("workspace head mismatch: abc123"), {
        status: 409,
      }),
    );
    const user = userEvent.setup();
    render(<WorkspaceGitView projectSlug="demo" />);

    await waitFor(() =>
      expect(screen.getByText("before write 2")).toBeInTheDocument(),
    );
    await user.click(screen.getAllByText("恢复到此版本")[0]);
    await user.click(screen.getByRole("button", { name: "提交申请" }));
    await user.click(screen.getByRole("button", { name: "批准" }));

    await waitFor(() => {
      expect(
        screen.getByText("回滚目标已变化，请重新发起"),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByText(/workspace head mismatch|abc123/u),
    ).not.toBeInTheDocument();
  });

  it("普通区只显示截断后的工作区相对路径，commit 与 blame 只在 Advanced", async () => {
    const longPath =
      "workspace/research/long-running-project/reports/weekly/main.py";
    filesMock.mockResolvedValue({
      files: [{ path: longPath, status: "modified" }],
    });
    const user = userEvent.setup();
    const clipboardWrite = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboardWrite },
    });
    render(<WorkspaceGitView projectSlug="demo" />);

    await waitFor(() =>
      expect(screen.getByText("before write 2")).toBeInTheDocument(),
    );
    expect(screen.queryByText("c2hash")).not.toBeInTheDocument();
    await user.click(screen.getByText("before write 2"));
    const fileButton = await screen.findByRole("button", {
      name: /workspace\/research.*….*main\.py.*已修改/u,
    });
    await waitFor(() => {
      expect(fileButton).toBeInTheDocument();
    });
    expect(screen.queryByText(longPath)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "复制路径" }),
    ).not.toBeInTheDocument();

    await user.click(fileButton);
    await waitFor(() => expect(diffMock).toHaveBeenCalled());
    expect(
      screen.queryByRole("button", { name: "谁改的" }),
    ).not.toBeInTheDocument();

    const trigger = screen.getByRole("button", {
      name: "高级 · 版本与文件信息",
    });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "版本与文件信息" });
    expect(within(dialog).getByText("c2hash")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/workspace\/research.*….*main\.py/u),
    ).toBeInTheDocument();
    const blameButton = within(dialog).getByRole("button", {
      name: "查看逐行修改记录",
    });
    await user.click(blameButton);
    await waitFor(() => {
      expect(within(dialog).getByText("print('hello')")).toBeInTheDocument();
    });
    blameMock.mockRejectedValueOnce(new Error("raw blame failure"));
    await user.click(blameButton);
    await waitFor(() => {
      expect(
        within(dialog).getByText("逐行修改记录暂时无法加载，请重试。"),
      ).toBeInTheDocument();
    });
    expect(
      within(dialog).queryByText("raw blame failure"),
    ).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "复制路径" }));
    expect(clipboardWrite).toHaveBeenCalledWith(longPath);

    await user.keyboard("{Escape}");
    expect(
      screen.queryByRole("dialog", { name: "版本与文件信息" }),
    ).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
