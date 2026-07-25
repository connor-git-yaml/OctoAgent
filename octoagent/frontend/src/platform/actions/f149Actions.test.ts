import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ActionResultEnvelope } from "../../types";
import {
  decodeF149ActionCommand,
  executeF149Action,
  executeF149ActionWithRefresh,
  F149_ACTION_IDS,
  type F149ActionCommand,
  type F149ActionId,
} from "./f149Actions";

const { executeControlActionMock } = vi.hoisted(() => ({
  executeControlActionMock: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  executeControlAction: executeControlActionMock,
}));

const LIMITS = {
  max_budget_usd: null,
  max_duration_seconds: 300,
  max_request_tokens: 1000,
  max_response_tokens: 500,
  max_steps: 20,
  max_tool_calls: 10,
  repeat_signature_threshold: 3,
};

const CASES = [
  {
    command: {
      actionId: "agent_profile.update_resource_limits",
      params: {
        profile_id: "agent-default",
        resource_limits: LIMITS,
        target_type: "agent_profile",
      },
    },
    data: {
      profile_id: "agent-default",
      resource_limits: LIMITS,
      target_type: "agent_profile",
    },
  },
  {
    command: {
      actionId: "behavior.read_file",
      params: { file_path: "AGENTS.md" },
    },
    data: {
      budget_chars: 4096,
      content: "保持简洁。",
      exists: true,
      file_path: "AGENTS.md",
    },
  },
  {
    command: {
      actionId: "behavior.write_file",
      params: {
        agent_slug: "main",
        content: "保持简洁。",
        file_id: "AGENTS.md",
        project_slug: "default",
      },
    },
    data: {
      file_id: "AGENTS.md",
      resolved_path: ".octoagent/behavior/AGENTS.md",
    },
  },
  {
    command: {
      actionId: "behavior.restore_version",
      params: {
        agent_slug: "main",
        confirmed: true,
        file_id: "AGENTS.md",
        project_slug: "default",
        target_version: 2,
      },
    },
    data: {
      file_id: "AGENTS.md",
      preview: null,
      proposal: false,
      restored_from_version: 3,
      target_version: 2,
    },
  },
  {
    command: {
      actionId: "memory.consolidate",
      params: { project_id: "project-default" },
    },
    data: {
      consolidated_count: 2,
      errors: [],
      message: "已整理 2 条记忆",
      model_alias: null,
      skipped_count: 1,
    },
  },
  {
    command: {
      actionId: "mcp_provider.install",
      params: {
        env: { TOKEN_NAME: "一次性输入" },
        install_source: "npm",
        package_name: "@example/mcp",
      },
    },
    data: {
      server_id: "mcp-example",
      task_id: "install-1",
    },
  },
  {
    command: {
      actionId: "mcp_provider.install_status",
      params: { task_id: "install-1" },
    },
    data: {
      error: null,
      progress_message: "安装完成",
      result: { installed: true },
      status: "completed",
      task_id: "install-1",
    },
  },
] as const satisfies ReadonlyArray<{
  command: F149ActionCommand;
  data: Record<string, unknown>;
}>;

function makeEnvelope(
  actionId: string,
  data: Record<string, unknown>,
  status: ActionResultEnvelope["status"] = "completed",
): ActionResultEnvelope {
  return {
    action_id: actionId,
    code: status === "completed" ? "OK" : "ACTION_FAILED",
    contract_version: "1.0.0",
    correlation_id: "corr-1",
    data,
    handled_at: "2026-07-25T00:00:00Z",
    message: status === "completed" ? "完成" : "失败",
    request_id: "req-1",
    resource_refs: [],
    status,
    target_refs: [],
  };
}

describe("F149 typed action commands/results", () => {
  beforeEach(() => {
    executeControlActionMock.mockReset();
  });

  it("枚举Gateway导出的七个F149 action，包含restore且排除dead actions", () => {
    expect(F149_ACTION_IDS).toEqual(
      CASES.map(({ command }) => command.actionId),
    );
    expect(F149_ACTION_IDS).toContain("behavior.restore_version");
    expect(F149_ACTION_IDS).not.toContain(
      "memory.profile_generate" as F149ActionId,
    );
    expect(F149_ACTION_IDS).not.toContain("memory.query" as F149ActionId);
    expect(F149_ACTION_IDS).not.toContain("automation.pause" as F149ActionId);
  });

  it.each(CASES)(
    "$command.actionId经现有executor返回强类型结果",
    async ({ command, data }) => {
      const executor = vi
        .fn()
        .mockResolvedValue(makeEnvelope(command.actionId, data));

      expect(decodeF149ActionCommand(command)).toEqual(command);
      const outcome = await executeF149Action(command, executor);

      expect(executor).toHaveBeenCalledTimes(1);
      expect(executor).toHaveBeenCalledWith(command.actionId, command.params);
      expect(outcome).toMatchObject({
        ok: true,
        actionId: command.actionId,
        data,
      });
    },
  );

  it("拒绝unknown action、额外字段和非法参数且不调用executor", async () => {
    const executor = vi.fn();
    const invalidCommands = [
      { actionId: "memory.profile_generate", params: { project_id: "p1" } },
      {
        actionId: "behavior.restore_version",
        params: {
          agent_slug: "main",
          confirmed: true,
          file_id: "AGENTS.md",
          project_slug: "default",
          target_version: 0,
        },
      },
      {
        actionId: "memory.consolidate",
        params: { project_id: "p1", raw_payload: "forbidden" },
      },
    ];

    for (const invalid of invalidCommands) {
      expect(decodeF149ActionCommand(invalid)).toBeNull();
      const outcome = await executeF149Action(
        invalid as unknown as F149ActionCommand,
        executor,
      );
      expect(outcome).toEqual({
        ok: false,
        actionId: null,
        error: { kind: "invalid-command" },
      });
    }
    expect(executor).not.toHaveBeenCalled();
  });

  it("把null、失败envelope、action漂移和schema漂移收敛为typed error", async () => {
    const command = CASES[1].command;
    const failures = [
      {
        result: null,
        expected: {
          ok: false,
          actionId: command.actionId,
          error: { kind: "execution-failed" },
        },
      },
      {
        result: makeEnvelope(command.actionId, CASES[1].data, "rejected"),
        expected: {
          ok: false,
          actionId: command.actionId,
          error: { kind: "action-failed", code: "ACTION_FAILED" },
        },
      },
      {
        result: makeEnvelope("behavior.write_file", CASES[1].data),
        expected: {
          ok: false,
          actionId: command.actionId,
          error: { kind: "invalid-result" },
        },
      },
      {
        result: makeEnvelope(command.actionId, {
          ...CASES[1].data,
          exists: "yes",
        }),
        expected: {
          ok: false,
          actionId: command.actionId,
          error: { kind: "invalid-result" },
        },
      },
      {
        result: makeEnvelope(command.actionId, {
          ...CASES[1].data,
          raw_payload: "forbidden",
        }),
        expected: {
          ok: false,
          actionId: command.actionId,
          error: { kind: "invalid-result" },
        },
      },
    ];

    for (const { result, expected } of failures) {
      const outcome = await executeF149Action(
        command,
        vi.fn().mockResolvedValue(result),
      );
      expect(outcome).toEqual(expected);
    }
  });

  it("executor异常只返回稳定错误，不回显一次性env", async () => {
    const command = {
      ...CASES[5].command,
      params: {
        ...CASES[5].command.params,
        env: { ACCESS_TOKEN: "temporary-value-never-returned" },
      },
    } satisfies F149ActionCommand;

    const outcome = await executeF149Action(command, async () => {
      throw new Error(command.params.env.ACCESS_TOKEN);
    });

    expect(outcome).toEqual({
      ok: false,
      actionId: "mcp_provider.install",
      error: { kind: "execution-failed" },
    });
    expect(JSON.stringify(outcome)).not.toContain(
      command.params.env.ACCESS_TOKEN,
    );
  });

  it("直接复用executeWorkbenchActionWithRefresh的resource refresh语义", async () => {
    const command = CASES[4].command;
    const refs = [
      {
        resource_id: "memory:project-default",
        resource_type: "memory",
        schema_version: 1,
      },
    ];
    executeControlActionMock.mockResolvedValue({
      ...makeEnvelope(command.actionId, CASES[4].data),
      resource_refs: refs,
    });
    const refreshSnapshot = vi.fn().mockResolvedValue(undefined);
    const refreshResources = vi.fn().mockResolvedValue(undefined);

    const outcome = await executeF149ActionWithRefresh(command, "1.0.0", {
      refreshSnapshot,
      refreshResources,
    });

    expect(outcome.ok).toBe(true);
    expect(refreshResources).toHaveBeenCalledWith(refs);
    expect(refreshSnapshot).not.toHaveBeenCalled();
  });
});
