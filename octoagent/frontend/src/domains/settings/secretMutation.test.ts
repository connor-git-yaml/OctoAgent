import { describe, expect, it } from "vitest";
import {
  buildSecretMutations,
  clearSecretDrafts,
  hasPendingSecretChanges,
  readSecretDraft,
  secretReplacementValues,
  updateSecretDrafts,
  type SecretClearOutcome,
  type SecretDrafts,
} from "./secretMutation";

const ENV_NAME = "OPENAI_API_KEY";
const SECRET_VALUE = "sk-f149-write-only-secret";

describe("secretMutation", () => {
  it("按后端合同生成 keep、replace、remove 三态并拒绝遮罩占位", () => {
    const configured = new Set([ENV_NAME]);

    expect(buildSecretMutations([ENV_NAME], configured, {})).toEqual({
      [ENV_NAME]: { mode: "keep" },
    });

    const replaced = updateSecretDrafts({}, ENV_NAME, true, {
      type: "replace",
      value: SECRET_VALUE,
    });
    expect(buildSecretMutations([ENV_NAME], configured, replaced)).toEqual({
      [ENV_NAME]: { mode: "replace", value: SECRET_VALUE },
    });

    const removed = updateSecretDrafts(replaced, ENV_NAME, true, {
      type: "remove",
    });
    expect(buildSecretMutations([ENV_NAME], configured, removed)).toEqual({
      [ENV_NAME]: { mode: "remove" },
    });

    for (const placeholder of [
      "**********",
      "••••••••••",
      "[REDACTED]",
      "<REDACTED>",
      "REDACTED",
    ]) {
      const placeholderDraft = updateSecretDrafts({}, ENV_NAME, true, {
        type: "replace",
        value: placeholder,
      });
      expect(() =>
        buildSecretMutations([ENV_NAME], configured, placeholderDraft),
      ).toThrow(/遮罩占位/u);
    }
  });

  it("只保留本次输入，并在成功、失败或关闭时清空", () => {
    const draft: SecretDrafts = {
      [ENV_NAME]: {
        configured: true,
        mode: "replace",
        value: SECRET_VALUE,
      },
    };

    expect(hasPendingSecretChanges(draft)).toBe(true);
    expect(secretReplacementValues(draft)).toEqual({
      [ENV_NAME]: SECRET_VALUE,
    });

    for (const outcome of [
      "success",
      "failure",
      "close",
    ] satisfies SecretClearOutcome[]) {
      const cleared = clearSecretDrafts(draft, outcome);
      expect(cleared).toEqual({});
      expect(readSecretDraft(cleared, ENV_NAME, true)).toEqual({
        configured: true,
        mode: "keep",
        value: "",
      });
      expect(JSON.stringify(cleared)).not.toContain(SECRET_VALUE);
    }
  });

  it("未配置且未输入的字段不会伪造 keep 或 replace", () => {
    expect(buildSecretMutations([ENV_NAME], new Set(), {})).toEqual({});
    expect(readSecretDraft({}, ENV_NAME, false)).toEqual({
      configured: false,
      mode: "replace",
      value: "",
    });
  });
});
