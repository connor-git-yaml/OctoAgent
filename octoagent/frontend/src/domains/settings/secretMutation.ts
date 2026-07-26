export type SecretMutation =
  | { mode: "keep" }
  | { mode: "replace"; value: string }
  | { mode: "remove" };

export type SecretDraftMode = SecretMutation["mode"];
export type SecretClearOutcome = "success" | "failure" | "close";

export interface SecretDraftState {
  configured: boolean;
  mode: SecretDraftMode;
  value: string;
}

export type SecretDrafts = Record<string, SecretDraftState>;

export type SecretDraftCommand =
  | { type: "keep" }
  | { type: "replace"; value: string }
  | { type: "remove" };

const SECRET_PLACEHOLDERS = new Set([
  "**********",
  "••••••••••",
  "[REDACTED]",
  "<REDACTED>",
  "REDACTED",
]);

export class SecretMutationError extends Error {
  constructor(
    readonly envName: string,
    message: string,
  ) {
    super(message);
    this.name = "SecretMutationError";
  }
}

export function readSecretDraft(
  drafts: SecretDrafts,
  envName: string,
  configured: boolean,
): SecretDraftState {
  const existing = drafts[envName];
  if (existing) {
    return {
      ...existing,
      configured,
    };
  }
  return {
    configured,
    mode: configured ? "keep" : "replace",
    value: "",
  };
}

export function updateSecretDrafts(
  drafts: SecretDrafts,
  envName: string,
  configured: boolean,
  command: SecretDraftCommand,
): SecretDrafts {
  const name = envName.trim();
  if (!name) {
    throw new SecretMutationError(envName, "访问密钥字段名不能为空");
  }
  const next: SecretDraftState =
    command.type === "replace"
      ? {
          configured,
          mode: "replace",
          value: command.value,
        }
      : {
          configured,
          mode: command.type,
          value: "",
        };
  return {
    ...drafts,
    [name]: next,
  };
}

export function buildSecretMutations(
  envNames: string[],
  configuredEnvNames: Set<string>,
  drafts: SecretDrafts,
): Record<string, SecretMutation> {
  const mutations: Record<string, SecretMutation> = {};
  for (const envName of new Set(envNames.map((name) => name.trim()).filter(Boolean))) {
    const configured = configuredEnvNames.has(envName);
    const explicitDraft = drafts[envName];
    if (!explicitDraft) {
      if (configured) {
        mutations[envName] = { mode: "keep" };
      }
      continue;
    }
    const draft = readSecretDraft(drafts, envName, configured);
    if (draft.mode === "keep") {
      if (configured) {
        mutations[envName] = { mode: "keep" };
      }
      continue;
    }
    if (draft.mode === "remove") {
      if (configured) {
        mutations[envName] = { mode: "remove" };
      }
      continue;
    }
    const normalized = draft.value.trim();
    if (!normalized) {
      throw new SecretMutationError(envName, "请输入新的访问密钥");
    }
    if (SECRET_PLACEHOLDERS.has(normalized)) {
      throw new SecretMutationError(envName, "访问密钥不能使用遮罩占位，请重新输入");
    }
    mutations[envName] = {
      mode: "replace",
      value: draft.value,
    };
  }
  return mutations;
}

export function hasPendingSecretChanges(drafts: SecretDrafts): boolean {
  return Object.values(drafts).some(
    (draft) =>
      (draft.mode === "replace" && Boolean(draft.value.trim())) ||
      (draft.mode === "remove" && draft.configured),
  );
}

export function clearSecretDrafts(
  _drafts: SecretDrafts,
  _outcome: SecretClearOutcome,
): SecretDrafts {
  return {};
}

export function secretReplacementValues(
  drafts: SecretDrafts,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(drafts)
      .filter(([, draft]) => draft.mode === "replace" && draft.value.trim())
      .map(([envName, draft]) => [envName, draft.value]),
  );
}
