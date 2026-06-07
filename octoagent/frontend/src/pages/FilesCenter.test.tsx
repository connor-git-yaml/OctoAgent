import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FilesCenter from "./FilesCenter";
import type { DiffResponse, LogicalFileItem } from "../types";

vi.mock("../api/client", () => ({
  fetchFileTasks: vi.fn(),
  fetchLogicalFiles: vi.fn(),
  fetchLogicalFileDiff: vi.fn(),
}));

import {
  fetchFileTasks,
  fetchLogicalFileDiff,
  fetchLogicalFiles,
} from "../api/client";

const fetchFileTasksMock = vi.mocked(fetchFileTasks);
const fetchLogicalFilesMock = vi.mocked(fetchLogicalFiles);
const fetchLogicalFileDiffMock = vi.mocked(fetchLogicalFileDiff);

function renderPage() {
  return render(
    <MemoryRouter>
      <FilesCenter />
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("FilesCenter 两级导航", () => {
  it("一级展示任务，点击进入二级文件，点击文件展示 diff", async () => {
    fetchFileTasksMock.mockResolvedValue({
      tasks: [{ task_id: "task-1", title: "周报生成" }],
    });
    fetchLogicalFilesMock.mockResolvedValue({
      files: [
        {
          logical_file_id: "task-1/report.md",
          display_name: "report.md",
          version_count: 3,
        },
      ],
    });
    fetchLogicalFileDiffMock.mockResolvedValue({
      current: {
        version_no: 3,
        content: "新版内容",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      previous: {
        version_no: 2,
        content: "旧版内容",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      binary: false,
      oversize: false,
    });

    const user = userEvent.setup();
    renderPage();

    // 一级：任务列表
    await waitFor(() => {
      expect(screen.getByText("周报生成")).toBeInTheDocument();
    });

    // 进入二级
    await user.click(screen.getByText("周报生成"));
    await waitFor(() => {
      expect(fetchLogicalFilesMock).toHaveBeenCalledWith("task-1");
      expect(screen.getByText("report.md")).toBeInTheDocument();
    });
    // 展示友好名称 + 版本数，不暴露原始 logical_file_id
    expect(screen.getByText("3 个版本")).toBeInTheDocument();
    expect(screen.queryByText("task-1/report.md")).not.toBeInTheDocument();

    // 进入 diff 详情
    await user.click(screen.getByText("report.md"));
    await waitFor(() => {
      expect(fetchLogicalFileDiffMock).toHaveBeenCalledWith(
        "task-1",
        "task-1/report.md"
      );
      expect(screen.getByText("新版内容")).toBeInTheDocument();
      expect(screen.getByText("旧版内容")).toBeInTheDocument();
    });
    // 主视图不暴露内部版本号（SC-004 / CL-1）：仅纯文案「上一版」「当前版」
    expect(screen.getByText("当前版")).toBeInTheDocument();
    expect(screen.getByText("上一版")).toBeInTheDocument();
    expect(screen.queryByText(/v3/)).not.toBeInTheDocument();
    expect(screen.queryByText(/v2/)).not.toBeInTheDocument();
  });

  it("首版（previous=null）展示首版无对比", async () => {
    fetchFileTasksMock.mockResolvedValue({
      tasks: [{ task_id: "task-1", title: "草稿" }],
    });
    fetchLogicalFilesMock.mockResolvedValue({
      files: [
        {
          logical_file_id: "task-1/note.txt",
          display_name: "note.txt",
          version_count: 2,
        },
      ],
    });
    fetchLogicalFileDiffMock.mockResolvedValue({
      current: {
        version_no: 1,
        content: "唯一内容",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      previous: null,
      binary: false,
      oversize: false,
    });

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByText("草稿")).toBeInTheDocument());
    await user.click(screen.getByText("草稿"));
    await waitFor(() => expect(screen.getByText("note.txt")).toBeInTheDocument());
    await user.click(screen.getByText("note.txt"));

    await waitFor(() => {
      expect(screen.getByText("首版无对比")).toBeInTheDocument();
      expect(screen.getByText("唯一内容")).toBeInTheDocument();
    });
  });

  it("可从二级回退到一级任务列表", async () => {
    fetchFileTasksMock.mockResolvedValue({
      tasks: [{ task_id: "task-1", title: "周报生成" }],
    });
    fetchLogicalFilesMock.mockResolvedValue({
      files: [
        {
          logical_file_id: "task-1/report.md",
          display_name: "report.md",
          version_count: 3,
        },
      ],
    });

    const user = userEvent.setup();
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("周报生成")).toBeInTheDocument()
    );
    await user.click(screen.getByText("周报生成"));
    await waitFor(() =>
      expect(screen.getByText("report.md")).toBeInTheDocument()
    );

    // 面包屑「任务」按钮回退
    await user.click(screen.getByRole("button", { name: "任务" }));
    await waitFor(() => {
      expect(screen.queryByText("report.md")).not.toBeInTheDocument();
      expect(screen.getByText("周报生成")).toBeInTheDocument();
    });
  });

  it("一级加载中显示 loading", () => {
    fetchFileTasksMock.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByText("正在加载任务列表…")).toBeInTheDocument();
  });

  it("一级加载失败显示错误", async () => {
    fetchFileTasksMock.mockRejectedValue(new Error("网络错误"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("加载失败：网络错误")).toBeInTheDocument();
    });
  });

  it("一级空列表显示空态", async () => {
    fetchFileTasksMock.mockResolvedValue({ tasks: [] });
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("还没有可对比的文件")).toBeInTheDocument();
    });
  });

  it("快速切换任务时旧响应被丢弃（files 层乱序返回）", async () => {
    fetchFileTasksMock.mockResolvedValue({
      tasks: [
        { task_id: "task-A", title: "任务A" },
        { task_id: "task-B", title: "任务B" },
      ],
    });

    // 两个 deferred promise，手动控制 resolve 顺序模拟乱序。
    // 真实交互：点 task-A 进入文件列表（pending）-> 回退到任务列表 -> 点 task-B（pending）。
    // 回退使 task-A 的在途请求过期；随后先 resolve A（旧）再 resolve B（新），
    // 断言最终只显示 B 的文件，A 响应被丢弃。
    let resolveA!: (v: { files: LogicalFileItem[] }) => void;
    let resolveB!: (v: { files: LogicalFileItem[] }) => void;
    const promiseA = new Promise<{ files: LogicalFileItem[] }>((r) => {
      resolveA = r;
    });
    const promiseB = new Promise<{ files: LogicalFileItem[] }>((r) => {
      resolveB = r;
    });
    fetchLogicalFilesMock.mockImplementation((taskId: string) =>
      taskId === "task-A" ? promiseA : promiseB
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByText("任务A")).toBeInTheDocument());

    // 点 task A（pending，进入 files 视图）
    await user.click(screen.getByText("任务A"));
    // 回退到任务列表（自增 seq 使 A 在途请求过期）
    await user.click(screen.getByRole("button", { name: "任务" }));
    await waitFor(() => expect(screen.getByText("任务B")).toBeInTheDocument());
    // 点 task B（pending）
    await user.click(screen.getByText("任务B"));

    // 先 resolve A（旧请求），再 resolve B（新请求）
    resolveA({
      files: [
        { logical_file_id: "task-A/a.md", display_name: "a.md", version_count: 2 },
      ],
    });
    resolveB({
      files: [
        { logical_file_id: "task-B/b.md", display_name: "b.md", version_count: 2 },
      ],
    });

    // 最终显示 B 的文件，A 响应被丢弃
    await waitFor(() => expect(screen.getByText("b.md")).toBeInTheDocument());
    expect(screen.queryByText("a.md")).not.toBeInTheDocument();
  });

  it("快速切换文件时旧响应被丢弃（diff 层乱序返回）", async () => {
    fetchFileTasksMock.mockResolvedValue({
      tasks: [{ task_id: "task-1", title: "任务1" }],
    });
    fetchLogicalFilesMock.mockResolvedValue({
      files: [
        {
          logical_file_id: "task-1/file-A.md",
          display_name: "file-A.md",
          version_count: 2,
        },
        {
          logical_file_id: "task-1/file-B.md",
          display_name: "file-B.md",
          version_count: 2,
        },
      ],
    });

    // 真实交互：点 file-A 进入 diff（pending）-> 回退到文件列表 -> 点 file-B（pending）。
    // 回退使 file-A 的在途 diff 请求过期；先 resolve A（旧）再 resolve B（新），
    // 断言最终只显示 B 的 diff 内容。
    let resolveA!: (v: DiffResponse) => void;
    let resolveB!: (v: DiffResponse) => void;
    const diffA = new Promise<DiffResponse>((r) => {
      resolveA = r;
    });
    const diffB = new Promise<DiffResponse>((r) => {
      resolveB = r;
    });
    fetchLogicalFileDiffMock.mockImplementation(
      (_taskId: string, logicalFileId: string) =>
        logicalFileId === "task-1/file-A.md" ? diffA : diffB
    );

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByText("任务1")).toBeInTheDocument());
    await user.click(screen.getByText("任务1"));
    await waitFor(() =>
      expect(screen.getByText("file-A.md")).toBeInTheDocument()
    );

    // 点 file-A（pending，进入 diff 视图）
    await user.click(screen.getByText("file-A.md"));
    // 回退到文件列表（自增 seq 使 file-A 在途 diff 请求过期）
    await user.click(screen.getByRole("button", { name: "任务1" }));
    await waitFor(() =>
      expect(screen.getByText("file-B.md")).toBeInTheDocument()
    );
    // 点 file-B（pending）
    await user.click(screen.getByText("file-B.md"));

    // 先 resolve A（旧请求），再 resolve B（新请求）
    resolveA({
      current: {
        version_no: 2,
        content: "A 当前内容",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      previous: {
        version_no: 1,
        content: "A 旧内容",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      binary: false,
      oversize: false,
    });
    resolveB({
      current: {
        version_no: 2,
        content: "B 当前内容",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      previous: {
        version_no: 1,
        content: "B 旧内容",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      binary: false,
      oversize: false,
    });

    // 最终显示 B 的 diff 内容，A 响应被丢弃
    await waitFor(() =>
      expect(screen.getByText("B 当前内容")).toBeInTheDocument()
    );
    expect(screen.getByText("B 旧内容")).toBeInTheDocument();
    expect(screen.queryByText("A 当前内容")).not.toBeInTheDocument();
    expect(screen.queryByText("A 旧内容")).not.toBeInTheDocument();
  });
});
