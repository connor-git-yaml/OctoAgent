import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type {
  BackupBundle,
  ExportManifest,
  RecoverySummary,
  UpdateAttemptSummary,
} from "../../types";
import MaintenanceRecoverySection from "./MaintenanceRecoverySection";
import {
  isValidUpdateDryRun,
  resolveMaintenanceSurfaceState,
  type MaintenanceRecoveryApi,
} from "./maintenanceRecoveryState";

const RECOVERY_READY: RecoverySummary = {
  latest_backup: {
    bundle_id: "bundle-1",
    output_path: "artifacts/backups/bundle-1.tar",
    created_at: "2026-07-25T08:00:00Z",
    size_bytes: 1024,
    manifest: {
      manifest_version: 1,
      bundle_id: "bundle-1",
      created_at: "2026-07-25T08:00:00Z",
      source_project_root: ".",
      scopes: ["settings", "memory"],
      files: [],
      warnings: [],
      excluded_paths: [],
      sensitivity_level: "operator_sensitive",
      notes: [],
    },
  },
  latest_recovery_drill: {
    status: "PASSED",
    checked_at: "2026-07-25T08:05:00Z",
    bundle_path: "artifacts/backups/bundle-1.tar",
    summary: "备份结构与校验均可用于恢复。",
    failure_reason: "",
    remediation: [],
  },
  ready_for_restore: true,
};

const UPDATE_IDLE: UpdateAttemptSummary = {
  overall_status: null,
  phases: [],
};

const UPDATE_DRY_RUN: UpdateAttemptSummary = {
  attempt_id: "update-dry-run-1",
  dry_run: true,
  overall_status: "SUCCEEDED",
  current_phase: "verify",
  phases: [],
};

const BACKUP_RESULT = RECOVERY_READY.latest_backup as BackupBundle;
const EXPORT_RESULT: ExportManifest = {
  export_id: "export-1",
  created_at: "2026-07-25T08:10:00Z",
  output_path: "artifacts/exports/chats.json",
  filters: {},
  tasks: [],
  event_count: 2,
  artifact_refs: [],
};

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

function buildApi(
  overrides: Partial<MaintenanceRecoveryApi> = {},
): MaintenanceRecoveryApi {
  return {
    fetchRecoverySummary: vi.fn().mockResolvedValue(RECOVERY_READY),
    fetchUpdateStatus: vi.fn().mockResolvedValue(UPDATE_IDLE),
    triggerBackupCreate: vi.fn().mockResolvedValue(BACKUP_RESULT),
    triggerExportChats: vi.fn().mockResolvedValue(EXPORT_RESULT),
    triggerRestart: vi.fn().mockResolvedValue(UPDATE_IDLE),
    triggerUpdateApply: vi.fn().mockResolvedValue(UPDATE_IDLE),
    triggerUpdateDryRun: vi.fn().mockResolvedValue(UPDATE_DRY_RUN),
    triggerVerify: vi.fn().mockResolvedValue(UPDATE_IDLE),
    ...overrides,
  };
}

describe("MaintenanceRecoverySection", () => {
  it("把适用状态收敛为 loading、empty、recoverable error、403 与 ready", () => {
    expect(
      resolveMaintenanceSurfaceState({
        connected: true,
        error: null,
        loading: true,
      }),
    ).toBe("loading");
    expect(
      resolveMaintenanceSurfaceState({
        connected: false,
        error: null,
        loading: false,
      }),
    ).toBe("empty");
    expect(
      resolveMaintenanceSurfaceState({
        connected: true,
        error: new ApiError("forbidden", { status: 403 }),
        loading: false,
      }),
    ).toBe("permission-denied");
    expect(
      resolveMaintenanceSurfaceState({
        connected: true,
        error: new ApiError("not found", { status: 404 }),
        loading: false,
      }),
    ).toBe("recoverable-error");
    expect(
      resolveMaintenanceSurfaceState({
        connected: true,
        error: new ApiError("conflict", { status: 409 }),
        loading: false,
      }),
    ).toBe("recoverable-error");
    expect(
      resolveMaintenanceSurfaceState({
        connected: true,
        error: null,
        loading: false,
      }),
    ).toBe("ready");
    expect(isValidUpdateDryRun(UPDATE_IDLE)).toBe(false);
    expect(isValidUpdateDryRun(UPDATE_DRY_RUN)).toBe(true);
  });

  it("loading 与未连接引导不渲染危险动作", async () => {
    const pending = deferred<RecoverySummary>();
    const api = buildApi({
      fetchRecoverySummary: vi.fn(() => pending.promise),
    });
    const { rerender } = render(
      <MaintenanceRecoverySection api={api} connected />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("正在读取维护状态");
    expect(
      screen.queryByRole("button", { name: "创建备份" }),
    ).not.toBeInTheDocument();

    rerender(<MaintenanceRecoverySection api={api} connected={false} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "先连接至少一个模型供应商",
    );
    expect(
      screen.queryByRole("button", { name: "创建备份" }),
    ).not.toBeInTheDocument();
  });

  it("可恢复错误提供重试，origin 403 只说明资源权限", async () => {
    const api = buildApi({
      fetchRecoverySummary: vi
        .fn()
        .mockRejectedValueOnce(new Error("temporary"))
        .mockResolvedValueOnce(RECOVERY_READY),
    });
    const { rerender } = render(
      <MaintenanceRecoverySection api={api} connected />,
    );

    expect(
      await screen.findByRole("alert", { name: "维护状态暂时不可用" }),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(
      await screen.findByRole("heading", { name: "高级 · 维护与恢复" }),
    ).toBeInTheDocument();

    rerender(
      <MaintenanceRecoverySection
        api={buildApi({
          fetchRecoverySummary: vi.fn().mockRejectedValue(
            new ApiError("forbidden", {
              status: 403,
              code: "RESOURCE_FORBIDDEN",
            }),
          ),
        })}
        connected
      />,
    );
    expect(
      await screen.findByText("当前账号没有权限修改设置，请联系管理员。"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/重新登录/)).not.toBeInTheDocument();
  });

  it("在 Advanced 窄区展示摘要与七项能力，并只加载一份状态", async () => {
    const api = buildApi();
    render(<MaintenanceRecoverySection api={api} connected />);

    expect(
      await screen.findByRole("heading", { name: "高级 · 维护与恢复" }),
    ).toBeInTheDocument();
    expect(screen.getByText("恢复准备度摘要")).toBeInTheDocument();
    expect(screen.getAllByText("已就绪")).toHaveLength(2);
    expect(screen.getByText("备份结构与校验均可用于恢复。")).toBeInTheDocument();
    expect(
      screen.getByText("全部会话与消息（不含密钥与运行日志）"),
    ).toBeInTheDocument();
    for (const name of [
      "创建备份",
      "导出聊天",
      "试运行",
      "生效",
      "重启运行时",
      "运行校验",
    ]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(api.fetchRecoverySummary).toHaveBeenCalledTimes(1);
    expect(api.fetchUpdateStatus).toHaveBeenCalledTimes(1);
    expect(
      screen.getByTestId("maintenance-recovery-root"),
    ).toHaveAttribute("data-state-source", "maintenance-recovery");
  });

  it("创建备份必须输入 backup 强确认", async () => {
    const api = buildApi();
    render(<MaintenanceRecoverySection api={api} connected />);
    await screen.findByRole("heading", { name: "高级 · 维护与恢复" });

    await userEvent.click(screen.getByRole("button", { name: "创建备份" }));
    const confirm = screen.getByRole("button", { name: "确认创建备份" });
    expect(confirm).toBeDisabled();
    await userEvent.type(
      screen.getByRole("textbox", { name: "输入 backup 继续" }),
      "backup",
    );
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() =>
      expect(api.triggerBackupCreate).toHaveBeenCalledWith("manual"),
    );
  });

  it("只有有效试运行完成后才允许强确认生效", async () => {
    const api = buildApi();
    render(<MaintenanceRecoverySection api={api} connected />);
    await screen.findByRole("heading", { name: "高级 · 维护与恢复" });

    expect(screen.getByRole("button", { name: "生效" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "试运行" }));
    await waitFor(() => expect(api.triggerUpdateDryRun).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/最近一次有效试运行.*update-dry-run-1/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "生效" }));
    const confirm = screen.getByRole("button", { name: "确认生效" });
    expect(confirm).toBeDisabled();
    await userEvent.type(
      screen.getByRole("textbox", { name: "输入 apply 继续" }),
      "apply",
    );
    await userEvent.click(confirm);
    await waitFor(() =>
      expect(api.triggerUpdateApply).toHaveBeenCalledWith(false),
    );
  });

  it("重启只承诺短暂不可用，并要求 restart 强确认", async () => {
    const api = buildApi();
    render(<MaintenanceRecoverySection api={api} connected />);
    await screen.findByRole("heading", { name: "高级 · 维护与恢复" });

    expect(screen.getByText("重启期间会短暂不可用。")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "重启运行时" }),
    );
    const confirm = screen.getByRole("button", { name: "确认重启" });
    expect(confirm).toBeDisabled();
    await userEvent.type(
      screen.getByRole("textbox", { name: "输入 restart 继续" }),
      "restart",
    );
    await userEvent.click(confirm);
    await waitFor(() => expect(api.triggerRestart).toHaveBeenCalledTimes(1));
  });

  it("导出范围固定，校验复用同一 application state", async () => {
    const api = buildApi();
    render(<MaintenanceRecoverySection api={api} connected />);
    await screen.findByRole("heading", { name: "高级 · 维护与恢复" });

    await userEvent.click(screen.getByRole("button", { name: "导出聊天" }));
    await userEvent.click(screen.getByRole("button", { name: "运行校验" }));
    await waitFor(() => expect(api.triggerExportChats).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.triggerVerify).toHaveBeenCalledTimes(1));
    expect(
      screen.getByText("全部会话与消息（不含密钥与运行日志）"),
    ).toBeInTheDocument();
  });
});
