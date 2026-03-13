import { executeControlAction } from "../../api/client";
import type {
  ActionRequestEnvelope,
  ActionResultEnvelope,
  ControlPlaneResourceRef,
} from "../../types";

type ActionInvalidationMode = "full-snapshot" | "resource-refs";

interface ActionInvalidationRule {
  mode: ActionInvalidationMode;
  reason: string;
}

const DEFAULT_ACTION_INVALIDATION_RULE: ActionInvalidationRule = {
  mode: "resource-refs",
  reason: "按 action 返回的 resource refs 局部刷新",
};

export const CONTROL_ACTION_INVALIDATION_RULES: Record<
  string,
  ActionInvalidationRule
> = {
  "project.select": {
    mode: "full-snapshot",
    reason: "项目切换会影响当前工作台上几乎所有 canonical resources。",
  },
};

export function makeRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function resolveActionInvalidationRule(
  actionId: string
): ActionInvalidationRule {
  return CONTROL_ACTION_INVALIDATION_RULES[actionId] ?? DEFAULT_ACTION_INVALIDATION_RULE;
}

export function shouldRefreshFullSnapshot(actionId: string): boolean {
  return resolveActionInvalidationRule(actionId).mode === "full-snapshot";
}

interface ActionRefreshHandlers {
  refreshSnapshot: () => Promise<void>;
  refreshResources: (refs: ControlPlaneResourceRef[]) => Promise<void>;
}

export function buildControlActionRequest(
  contractVersion: string | undefined,
  actionId: string,
  params: Record<string, unknown>
): ActionRequestEnvelope {
  return {
    contract_version: contractVersion,
    request_id: makeRequestId(),
    action_id: actionId,
    surface: "web",
    actor: {
      actor_id: "user:web",
      actor_label: "Owner",
    },
    params,
  };
}

export async function executeWorkbenchAction(
  contractVersion: string | undefined,
  actionId: string,
  params: Record<string, unknown>
): Promise<ActionResultEnvelope> {
  return executeControlAction(
    buildControlActionRequest(contractVersion, actionId, params)
  );
}

export async function executeWorkbenchActionWithRefresh(
  contractVersion: string | undefined,
  actionId: string,
  params: Record<string, unknown>,
  handlers: ActionRefreshHandlers
): Promise<ActionResultEnvelope> {
  const result = await executeWorkbenchAction(contractVersion, actionId, params);
  if (shouldRefreshFullSnapshot(actionId)) {
    await handlers.refreshSnapshot();
  } else {
    await handlers.refreshResources(result.resource_refs);
  }
  return result;
}
