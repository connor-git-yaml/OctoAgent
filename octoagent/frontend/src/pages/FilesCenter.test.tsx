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
  fetchLogicalFileVersions: vi.fn(),
}));

import {
  fetchFileTasks,
  fetchLogicalFileDiff,
  fetchLogicalFileVersions,
  fetchLogicalFiles,
} from "../api/client";

const fetchFileTasksMock = vi.mocked(fetchFileTasks);
const fetchLogicalFilesMock = vi.mocked(fetchLogicalFiles);
const fetchLogicalFileDiffMock = vi.mocked(fetchLogicalFileDiff);
const fetchLogicalFileVersionsMock = vi.mocked(fetchLogicalFileVersions);

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
        content: "公共行\n新增内容",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      previous: {
        version_no: 2,
        content: "公共行\n删除内容",
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
      expect(screen.getByText("新增内容")).toBeInTheDocument();
      expect(screen.getByText("删除内容")).toBeInTheDocument();
    });

    // jsdiff 行级高亮：added 行含新增内容（绿）/ removed 行含删除内容（红）/ 公共行未变
    const addedRow = screen.getByText("新增内容").closest("[data-diff-kind]");
    expect(addedRow).toHaveAttribute("data-diff-kind", "added");
    const removedRow = screen.getByText("删除内容").closest("[data-diff-kind]");
    expect(removedRow).toHaveAttribute("data-diff-kind", "removed");
    const unchangedRow = screen.getByText("公共行").closest("[data-diff-kind]");
    expect(unchangedRow).toHaveAttribute("data-diff-kind", "unchanged");

    // 主 diff 视图不暴露任何技术字段（SC-004）：无版本号 vN
    expect(screen.queryByText(/v3/)).not.toBeInTheDocument();
    expect(screen.queryByText(/v2/)).not.toBeInTheDocument();
    // Advanced 折叠默认收起：未点击展开时不应触发版本元信息请求
    expect(fetchLogicalFileVersionsMock).not.toHaveBeenCalled();
  });

  it("展开 Advanced 折叠区懒加载版本元信息（技术字段仅此区）", async () => {
    fetchFileTasksMock.mockResolvedValue({
      tasks: [{ task_id: "task-1", title: "周报生成" }],
    });
    fetchLogicalFilesMock.mockResolvedValue({
      files: [
        {
          logical_file_id: "task-1/report.md",
          display_name: "report.md",
          version_count: 2,
        },
      ],
    });
    fetchLogicalFileDiffMock.mockResolvedValue({
      current: {
        version_no: 2,
        content: "当前内容",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      previous: {
        version_no: 1,
        content: "上一版内容",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      binary: false,
      oversize: false,
    });
    fetchLogicalFileVersionsMock.mockResolvedValue({
      versions: [
        {
          version_no: 2,
          ts: "2026-06-06T10:00:00Z",
          size: 128,
          hash: "abcdef1234567890",
          storage_kind: "inline",
        },
        {
          version_no: 1,
          ts: "2026-06-06T09:00:00Z",
          size: 64,
          hash: "0011223344556677",
          storage_kind: "snapshot",
        },
      ],
    });

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByText("周报生成")).toBeInTheDocument());
    await user.click(screen.getByText("周报生成"));
    await waitFor(() => expect(screen.getByText("report.md")).toBeInTheDocument());
    await user.click(screen.getByText("report.md"));
    await waitFor(() => expect(screen.getByText("当前内容")).toBeInTheDocument());

    // 收起状态下技术字段不出现在 DOM（默认收起 + 懒加载）
    expect(fetchLogicalFileVersionsMock).not.toHaveBeenCalled();

    // 展开 Advanced 折叠区
    await user.click(screen.getByText("高级信息（版本详情）"));

    await waitFor(() => {
      expect(fetchLogicalFileVersionsMock).toHaveBeenCalledWith(
        "task-1",
        "task-1/report.md"
      );
      // 技术字段（版本号 / hash 前 8 位 / size / storage_kind）只在此区出现
      expect(screen.getByText("版本号：v2")).toBeInTheDocument();
      expect(screen.getByText("版本号：v1")).toBeInTheDocument();
      expect(screen.getByText("哈希：abcdef12")).toBeInTheDocument();
      expect(screen.getByText("大小：128 字节")).toBeInTheDocument();
      expect(screen.getByText("存储：inline")).toBeInTheDocument();
    });
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

  it("二进制文件降级文案 + Advanced 区仍可展开（FR-018）", async () => {
    fetchFileTasksMock.mockResolvedValue({
      tasks: [{ task_id: "task-1", title: "导出" }],
    });
    fetchLogicalFilesMock.mockResolvedValue({
      files: [
        {
          logical_file_id: "task-1/data.bin",
          display_name: "data.bin",
          version_count: 2,
        },
      ],
    });
    fetchLogicalFileDiffMock.mockResolvedValue({
      current: {
        version_no: 2,
        content: null,
        availability: "unavailable",
        storage_kind: "snapshot",
        oversize: false,
      },
      previous: {
        version_no: 1,
        content: null,
        availability: "unavailable",
        storage_kind: "snapshot",
        oversize: false,
      },
      binary: true,
      oversize: false,
    });
    fetchLogicalFileVersionsMock.mockResolvedValue({
      versions: [
        {
          version_no: 2,
          ts: "2026-06-06T10:00:00Z",
          size: 2048,
          hash: "deadbeefcafef00d",
          storage_kind: "snapshot",
        },
      ],
    });

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("导出")).toBeInTheDocument());
    await user.click(screen.getByText("导出"));
    await waitFor(() => expect(screen.getByText("data.bin")).toBeInTheDocument());
    await user.click(screen.getByText("data.bin"));

    await waitFor(() =>
      expect(
        screen.getByText("这是二进制文件，暂时无法显示内容对比。")
      ).toBeInTheDocument()
    );

    // 即使内容不可 diff，Advanced 区版本元信息仍可展开
    await user.click(screen.getByText("高级信息（版本详情）"));
    await waitFor(() => {
      expect(screen.getByText("版本号：v2")).toBeInTheDocument();
      expect(screen.getByText("哈希：deadbeef")).toBeInTheDocument();
    });
  });

  it("超大文件降级文案（FR-019 / SC-005）", async () => {
    fetchFileTasksMock.mockResolvedValue({
      tasks: [{ task_id: "task-1", title: "日志" }],
    });
    fetchLogicalFilesMock.mockResolvedValue({
      files: [
        {
          logical_file_id: "task-1/huge.log",
          display_name: "huge.log",
          version_count: 2,
        },
      ],
    });
    fetchLogicalFileDiffMock.mockResolvedValue({
      current: {
        version_no: 2,
        content: null,
        availability: "available",
        storage_kind: "snapshot",
        oversize: true,
      },
      previous: {
        version_no: 1,
        content: null,
        availability: "available",
        storage_kind: "snapshot",
        oversize: true,
      },
      binary: false,
      oversize: true,
    });

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("日志")).toBeInTheDocument());
    await user.click(screen.getByText("日志"));
    await waitFor(() => expect(screen.getByText("huge.log")).toBeInTheDocument());
    await user.click(screen.getByText("huge.log"));

    await waitFor(() =>
      expect(
        screen.getByText("文件内容过大，暂时无法显示内容对比。")
      ).toBeInTheDocument()
    );
  });

  it("上一版内容不可用时退回当前内容纯展示（FR-010）", async () => {
    fetchFileTasksMock.mockResolvedValue({
      tasks: [{ task_id: "task-1", title: "文档" }],
    });
    fetchLogicalFilesMock.mockResolvedValue({
      files: [
        {
          logical_file_id: "task-1/doc.md",
          display_name: "doc.md",
          version_count: 2,
        },
      ],
    });
    fetchLogicalFileDiffMock.mockResolvedValue({
      current: {
        version_no: 2,
        content: "当前可用内容",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      previous: {
        version_no: 1,
        content: null,
        availability: "unavailable",
        storage_kind: "snapshot",
        oversize: false,
      },
      binary: false,
      oversize: false,
    });

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("文档")).toBeInTheDocument());
    await user.click(screen.getByText("文档"));
    await waitFor(() => expect(screen.getByText("doc.md")).toBeInTheDocument());
    await user.click(screen.getByText("doc.md"));

    await waitFor(() => {
      expect(
        screen.getByText("上一版内容暂不可用，仅显示当前内容")
      ).toBeInTheDocument();
      expect(screen.getByText("当前可用内容")).toBeInTheDocument();
    });
  });

  it("两版内容完全相同（非空）显示无差异且不渲染 diff 行（FR-015）", async () => {
    fetchFileTasksMock.mockResolvedValue({
      tasks: [{ task_id: "task-1", title: "稳定文档" }],
    });
    fetchLogicalFilesMock.mockResolvedValue({
      files: [
        {
          logical_file_id: "task-1/same.md",
          display_name: "same.md",
          version_count: 2,
        },
      ],
    });
    fetchLogicalFileDiffMock.mockResolvedValue({
      current: {
        version_no: 2,
        content: "完全相同的内容\n第二行",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      previous: {
        version_no: 1,
        content: "完全相同的内容\n第二行",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      binary: false,
      oversize: false,
    });

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("稳定文档")).toBeInTheDocument());
    await user.click(screen.getByText("稳定文档"));
    await waitFor(() => expect(screen.getByText("same.md")).toBeInTheDocument());
    await user.click(screen.getByText("same.md"));

    await waitFor(() => {
      expect(screen.getByText("无差异")).toBeInTheDocument();
      expect(
        screen.getByText("当前版与上一版内容相同。")
      ).toBeInTheDocument();
    });
    // 不渲染任何逐行 diff（无 data-diff-kind 行）
    expect(document.querySelector("[data-diff-kind]")).toBeNull();
  });

  it("两空文件（''==='')显示无差异（FR-015）", async () => {
    fetchFileTasksMock.mockResolvedValue({
      tasks: [{ task_id: "task-1", title: "空文件" }],
    });
    fetchLogicalFilesMock.mockResolvedValue({
      files: [
        {
          logical_file_id: "task-1/empty.txt",
          display_name: "empty.txt",
          version_count: 2,
        },
      ],
    });
    fetchLogicalFileDiffMock.mockResolvedValue({
      current: {
        version_no: 2,
        content: "",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      previous: {
        version_no: 1,
        content: "",
        availability: "available",
        storage_kind: "inline",
        oversize: false,
      },
      binary: false,
      oversize: false,
    });

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("空文件")).toBeInTheDocument());
    await user.click(screen.getByText("空文件"));
    await waitFor(() => expect(screen.getByText("empty.txt")).toBeInTheDocument());
    await user.click(screen.getByText("empty.txt"));

    await waitFor(() => {
      expect(screen.getByText("无差异")).toBeInTheDocument();
      expect(
        screen.getByText("当前版与上一版内容相同。")
      ).toBeInTheDocument();
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
