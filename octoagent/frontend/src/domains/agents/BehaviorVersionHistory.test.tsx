import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import BehaviorVersionHistory from "./BehaviorVersionHistory";

vi.mock("../../api/client", () => ({
  fetchBehaviorVersions: vi.fn(),
  fetchBehaviorVersionDiff: vi.fn(),
}));
vi.mock("../../platform/actions/controlPlaneActions", () => ({
  executeWorkbenchAction: vi.fn(),
}));

import {
  fetchBehaviorVersionDiff,
  fetchBehaviorVersions,
} from "../../api/client";
import { executeWorkbenchAction } from "../../platform/actions/controlPlaneActions";

const versionsMock = vi.mocked(fetchBehaviorVersions);
const diffMock = vi.mocked(fetchBehaviorVersionDiff);
const actionMock = vi.mocked(executeWorkbenchAction);

beforeEach(() => {
  vi.clearAllMocks();
  versionsMock.mockResolvedValue({
    versions: [
      { version_no: 3, ts: "2026-06-21T10:00:00Z", size: 10, hash: "h3" },
      { version_no: 2, ts: "2026-06-20T10:00:00Z", size: 8, hash: "h2" },
      { version_no: 1, ts: "2026-06-19T10:00:00Z", size: 6, hash: "h1" },
    ],
  });
  diffMock.mockResolvedValue({
    current: { content: "用户偏好 v3", availability: "available", oversize: false },
    previous: { content: "用户偏好 v2", availability: "available", oversize: false },
    binary: false,
    oversize: false,
  });
});

describe("BehaviorVersionHistory", () => {
  it("加载版本时间线 + 默认最新两版 diff", async () => {
    render(<BehaviorVersionHistory fileId="USER.md" onClose={() => {}} />);
    await waitFor(() =>
      expect(screen.getByText("USER.md")).toBeInTheDocument(),
    );
    // 时间线 3 个版本（含恢复按钮）
    expect(screen.getAllByText("恢复到此版本")).toHaveLength(3);
    // 默认拉最新两版 diff（version_a=3, version_b=2）
    await waitFor(() =>
      expect(diffMock).toHaveBeenCalledWith(
        expect.objectContaining({ version_a: 3, version_b: 2 }),
      ),
    );
  });

  it("恢复 Two-Phase：点恢复 → 出现确认 → 确认调用 action(confirmed=true)", async () => {
    actionMock.mockResolvedValue({
      code: "BEHAVIOR_RESTORED",
      message: "已恢复 USER.md 到版本 1（记为新版本）",
    } as never);
    const user = userEvent.setup();
    render(<BehaviorVersionHistory fileId="USER.md" onClose={() => {}} />);
    await waitFor(() =>
      expect(screen.getByText("USER.md")).toBeInTheDocument(),
    );
    // 点第一个版本（v3）的"恢复到此版本"
    await user.click(screen.getAllByText("恢复到此版本")[0]);
    // 出现 Two-Phase 确认（proposal）
    expect(screen.getByText(/确认吗/)).toBeInTheDocument();
    await user.click(screen.getByText("确认恢复"));
    await waitFor(() =>
      expect(actionMock).toHaveBeenCalledWith(
        undefined,
        "behavior.restore_version",
        expect.objectContaining({ target_version: 3, confirmed: true }),
      ),
    );
  });

  it("空状态：无版本历史", async () => {
    versionsMock.mockResolvedValue({ versions: [] });
    render(<BehaviorVersionHistory fileId="USER.md" onClose={() => {}} />);
    await waitFor(() =>
      expect(screen.getByText("暂无版本历史")).toBeInTheDocument(),
    );
  });

  it("取消恢复不调用 action", async () => {
    const user = userEvent.setup();
    render(<BehaviorVersionHistory fileId="USER.md" onClose={() => {}} />);
    await waitFor(() =>
      expect(screen.getByText("USER.md")).toBeInTheDocument(),
    );
    await user.click(screen.getAllByText("恢复到此版本")[1]);
    await user.click(screen.getByText("取消"));
    expect(actionMock).not.toHaveBeenCalled();
  });
});
