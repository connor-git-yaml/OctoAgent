import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchAgentApprovalOverrides,
  fetchF149SkillDetail,
  fetchF149Skills,
  installF149Skill,
  revokeAgentApprovalOverride,
  uninstallF149Skill,
} from "./adapters";
import { ApiError } from "../client";

const clientMocks = vi.hoisted(() => ({
  frontDoorRequest: vi.fn(),
}));

vi.mock("../client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../client")>();
  return {
    ...actual,
    frontDoorRequest: clientMocks.frontDoorRequest,
  };
});

function jsonResponse(status: number, body: object): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("F149 Agent/Skills adapters", () => {
  beforeEach(() => {
    clientMocks.frontDoorRequest.mockReset();
  });

  it("审批覆盖列表与撤销只经统一client seam", async () => {
    clientMocks.frontDoorRequest
      .mockResolvedValueOnce(
        jsonResponse(200, {
          overrides: [
            {
              agent_runtime_id: "agent-1",
              tool_name: "docker.run",
              decision: "always",
              created_at: "2026-07-25T00:00:00Z",
            },
          ],
          total: 1,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          success: true,
          message: "revoked",
          error: null,
        }),
      );

    expect(
      await fetchAgentApprovalOverrides(),
      "F149_AGENT_SKILL_ADAPTER_MISSING",
    ).toEqual([
      {
        agentRuntimeId: "agent-1",
        toolName: "docker.run",
        decision: "always",
        createdAt: "2026-07-25T00:00:00Z",
      },
    ]);
    expect(
      await revokeAgentApprovalOverride("agent-1", "docker.run"),
      "F149_AGENT_SKILL_ADAPTER_MISSING",
    ).toBe(true);
    expect(clientMocks.frontDoorRequest.mock.calls).toEqual([
      ["/api/approval-overrides"],
      ["/api/approval-overrides/agent-1/docker.run", { method: "DELETE" }],
    ]);
  });

  it("Skill列表/详情/安装/删除只经统一client seam", async () => {
    clientMocks.frontDoorRequest
      .mockResolvedValueOnce(
        jsonResponse(200, {
          items: [
            {
              name: "web-research",
              description: "检索公开资料",
              version: "1.0.0",
              author: "OctoAgent",
              tags: ["research"],
              source: "builtin",
              source_path: "/installed/SKILL.md",
            },
          ],
          total: 1,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          name: "web-research",
          description: "检索公开资料",
          version: "1.0.0",
          author: "OctoAgent",
          tags: ["research"],
          source: "builtin",
          source_path: "/installed/SKILL.md",
          trigger_patterns: ["research"],
          tools_required: ["browser"],
          content: "# Skill",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(201, {
          name: "custom-skill",
          source: "user",
          source_path: "/installed/custom/SKILL.md",
          message: "installed",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          name: "custom-skill",
          message: "deleted",
        }),
      );

    const list = await fetchF149Skills();
    const detail = await fetchF149SkillDetail("web-research");
    const installed = await installF149Skill("custom-skill", "# Skill");
    const deleted = await uninstallF149Skill("custom-skill");

    expect(list.total, "F149_AGENT_SKILL_ADAPTER_MISSING").toBe(1);
    expect(detail.content, "F149_AGENT_SKILL_ADAPTER_MISSING").toBe("# Skill");
    expect(installed.name, "F149_AGENT_SKILL_ADAPTER_MISSING").toBe(
      "custom-skill",
    );
    expect(deleted.name, "F149_AGENT_SKILL_ADAPTER_MISSING").toBe(
      "custom-skill",
    );
    expect(clientMocks.frontDoorRequest.mock.calls).toEqual([
      ["/api/skills"],
      ["/api/skills/web-research"],
      [
        "/api/skills",
        {
          method: "POST",
          body: JSON.stringify({ name: "custom-skill", content: "# Skill" }),
        },
      ],
      ["/api/skills/custom-skill", { method: "DELETE" }],
    ]);
  });

  it("API错误保留status/code并由上层统一归属", async () => {
    clientMocks.frontDoorRequest.mockResolvedValue(
      jsonResponse(403, {
        detail: {
          code: "ORIGIN_FORBIDDEN",
          message: "资源权限不足",
        },
      }),
    );

    let caught: unknown;
    try {
      await fetchF149Skills();
    } catch (error) {
      caught = error;
    }
    expect(caught, "F149_AGENT_SKILL_ADAPTER_MISSING").toMatchObject({
      name: "ApiError",
      status: 403,
      code: "ORIGIN_FORBIDDEN",
      message: "资源权限不足",
    } satisfies Partial<ApiError>);
  });
});
