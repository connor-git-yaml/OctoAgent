import { useCallback, useEffect, useReducer } from "react";
import {
  fetchRecoverySummary,
  fetchUpdateStatus,
  triggerBackupCreate,
  triggerExportChats,
  triggerRestart,
  triggerUpdateApply,
  triggerUpdateDryRun,
  triggerVerify,
} from "../../api/client";
import { mapF149ErrorOwnership } from "../../api/f149/errorOwnership";
import type {
  BackupBundle,
  ExportManifest,
  RecoverySummary,
  UpdateAttemptSummary,
} from "../../types";

export type MaintenanceSurfaceState =
  | "loading"
  | "ready"
  | "empty"
  | "recoverable-error"
  | "permission-denied"
  | "global-auth";

export interface MaintenanceSurfaceInput {
  connected: boolean;
  error: Error | null;
  loading: boolean;
}

export interface MaintenanceRecoveryApi {
  fetchRecoverySummary: () => Promise<RecoverySummary>;
  fetchUpdateStatus: () => Promise<UpdateAttemptSummary>;
  triggerBackupCreate: (label?: string) => Promise<BackupBundle>;
  triggerExportChats: () => Promise<ExportManifest>;
  triggerRestart: () => Promise<UpdateAttemptSummary>;
  triggerUpdateApply: (wait?: boolean) => Promise<UpdateAttemptSummary>;
  triggerUpdateDryRun: () => Promise<UpdateAttemptSummary>;
  triggerVerify: () => Promise<UpdateAttemptSummary>;
}

export const DEFAULT_MAINTENANCE_RECOVERY_API: MaintenanceRecoveryApi =
  Object.freeze({
    fetchRecoverySummary,
    fetchUpdateStatus,
    triggerBackupCreate,
    triggerExportChats,
    triggerRestart,
    triggerUpdateApply,
    triggerUpdateDryRun,
    triggerVerify,
  });

export function resolveMaintenanceSurfaceState(
  input: MaintenanceSurfaceInput,
): MaintenanceSurfaceState {
  if (!input.connected) {
    return "empty";
  }
  if (input.error) {
    const ownership = mapF149ErrorOwnership(input.error);
    if (ownership.owner === "global-auth") {
      return "global-auth";
    }
    return ownership.state === "forbidden"
      ? "permission-denied"
      : "recoverable-error";
  }
  if (input.loading) {
    return "loading";
  }
  return "ready";
}

export function isValidUpdateDryRun(
  summary: UpdateAttemptSummary | null,
): boolean {
  return Boolean(
    summary?.dry_run === true &&
      summary.overall_status === "SUCCEEDED" &&
      summary.attempt_id?.trim(),
  );
}

export type MaintenanceAction =
  | "backup"
  | "export"
  | "update-dry-run"
  | "update-apply"
  | "restart"
  | "verify";

export type MaintenanceConfirmation = "backup" | "apply" | "restart";

interface MaintenanceRecoveryState {
  busyAction: MaintenanceAction | null;
  confirmation: MaintenanceConfirmation | null;
  confirmationInput: string;
  error: Error | null;
  loading: boolean;
  notice: string | null;
  recovery: RecoverySummary | null;
  update: UpdateAttemptSummary | null;
}

type MaintenanceRecoveryEvent =
  | { type: "load-started" }
  | {
      type: "load-succeeded";
      recovery: RecoverySummary;
      update: UpdateAttemptSummary;
    }
  | { type: "failed"; error: Error }
  | { type: "action-started"; action: MaintenanceAction }
  | {
      type: "action-succeeded";
      action: MaintenanceAction;
      result: BackupBundle | ExportManifest | UpdateAttemptSummary;
    }
  | { type: "confirmation-opened"; confirmation: MaintenanceConfirmation }
  | { type: "confirmation-changed"; value: string }
  | { type: "confirmation-closed" }
  | { type: "disconnected" };

const INITIAL_STATE: MaintenanceRecoveryState = {
  busyAction: null,
  confirmation: null,
  confirmationInput: "",
  error: null,
  loading: true,
  notice: null,
  recovery: null,
  update: null,
};

function actionNotice(action: MaintenanceAction): string {
  const notices: Record<MaintenanceAction, string> = {
    backup: "备份已创建。",
    export: "聊天导出已创建。",
    "update-dry-run": "试运行已完成。",
    "update-apply": "升级请求已生效。",
    restart: "重启请求已提交。",
    verify: "运行时校验已完成。",
  };
  return notices[action];
}

function reduceActionResult(
  state: MaintenanceRecoveryState,
  event: Extract<MaintenanceRecoveryEvent, { type: "action-succeeded" }>,
): MaintenanceRecoveryState {
  const next = {
    ...state,
    busyAction: null,
    confirmation: null,
    confirmationInput: "",
    error: null,
    notice: actionNotice(event.action),
  };
  if (event.action === "backup") {
    return {
      ...next,
      recovery: state.recovery
        ? { ...state.recovery, latest_backup: event.result as BackupBundle }
        : state.recovery,
    };
  }
  if (event.action === "export") {
    return next;
  }
  return {
    ...next,
    update: event.result as UpdateAttemptSummary,
  };
}

function maintenanceRecoveryReducer(
  state: MaintenanceRecoveryState,
  event: MaintenanceRecoveryEvent,
): MaintenanceRecoveryState {
  switch (event.type) {
    case "load-started":
      return { ...state, error: null, loading: true, notice: null };
    case "load-succeeded":
      return {
        ...state,
        error: null,
        loading: false,
        recovery: event.recovery,
        update: event.update,
      };
    case "failed":
      return {
        ...state,
        busyAction: null,
        confirmation: null,
        confirmationInput: "",
        error: event.error,
        loading: false,
      };
    case "action-started":
      return { ...state, busyAction: event.action, error: null, notice: null };
    case "action-succeeded":
      return reduceActionResult(state, event);
    case "confirmation-opened":
      return {
        ...state,
        confirmation: event.confirmation,
        confirmationInput: "",
      };
    case "confirmation-changed":
      return { ...state, confirmationInput: event.value };
    case "confirmation-closed":
      return { ...state, confirmation: null, confirmationInput: "" };
    case "disconnected":
      return { ...INITIAL_STATE, loading: false };
  }
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error("维护请求失败");
}

async function executeMaintenanceAction(
  api: MaintenanceRecoveryApi,
  action: MaintenanceAction,
): Promise<BackupBundle | ExportManifest | UpdateAttemptSummary> {
  switch (action) {
    case "backup":
      return api.triggerBackupCreate("manual");
    case "export":
      return api.triggerExportChats();
    case "update-dry-run":
      return api.triggerUpdateDryRun();
    case "update-apply":
      return api.triggerUpdateApply(false);
    case "restart":
      return api.triggerRestart();
    case "verify":
      return api.triggerVerify();
  }
}

export function useMaintenanceRecoveryState(
  api: MaintenanceRecoveryApi,
  connected: boolean,
) {
  const [state, dispatch] = useReducer(
    maintenanceRecoveryReducer,
    INITIAL_STATE,
  );

  const load = useCallback(async () => {
    if (!connected) {
      dispatch({ type: "disconnected" });
      return;
    }
    dispatch({ type: "load-started" });
    try {
      const [recovery, update] = await Promise.all([
        api.fetchRecoverySummary(),
        api.fetchUpdateStatus(),
      ]);
      dispatch({ type: "load-succeeded", recovery, update });
    } catch (error) {
      dispatch({ type: "failed", error: asError(error) });
    }
  }, [api, connected]);

  useEffect(() => {
    void load();
  }, [load]);

  const runAction = useCallback(
    async (action: MaintenanceAction) => {
      dispatch({ type: "action-started", action });
      try {
        const result = await executeMaintenanceAction(api, action);
        dispatch({ type: "action-succeeded", action, result });
      } catch (error) {
        dispatch({ type: "failed", error: asError(error) });
      }
    },
    [api],
  );

  return {
    closeConfirmation: () => dispatch({ type: "confirmation-closed" }),
    load,
    openConfirmation: (confirmation: MaintenanceConfirmation) =>
      dispatch({ type: "confirmation-opened", confirmation }),
    runAction,
    setConfirmationInput: (value: string) =>
      dispatch({ type: "confirmation-changed", value }),
    state,
  };
}
