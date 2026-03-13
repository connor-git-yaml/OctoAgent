import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatWorkbench from "./ChatWorkbench";

const useWorkbenchMock = vi.fn();
const useChatStreamMock = vi.fn();
const fetchTaskDetailMock = vi.fn();

vi.mock("../components/shell/WorkbenchLayout", () => ({
  useWorkbench: () => useWorkbenchMock(),
}));

vi.mock("../hooks/useChatStream", () => ({
  useChatStream: (...args: unknown[]) => useChatStreamMock(...args),
}));

vi.mock("../api/client", () => ({
  fetchTaskDetail: (...args: unknown[]) => fetchTaskDetailMock(...args),
}));

function buildSnapshot() {
  return {
    resources: {
      sessions: {
        resource_type: "sessions",
        resource_id: "sessions:overview",
        schema_version: 1,
        sessions: [],
        focused_session_id: "",
        focused_thread_id: "",
      },
      worker_profiles: {
        summary: {
          default_profile_id: "project-default:nas-guardian",
        },
        profiles: [
          {
            profile_id: "project-default:nas-guardian",
            name: "NAS 管家",
            summary: "默认 Worker 模板。",
            static_config: {
              tool_profile: "standard",
            },
            dynamic_context: {
              current_tool_resolution_mode: "profile_first_core",
              current_mounted_tools: [],
              current_blocked_tools: [],
              current_discovery_entrypoints: ["workers.review"],
            },
          },
        ],
      },
      delegation: {
        resource_type: "delegation_plane",
        resource_id: "delegation:overview",
        schema_version: 1,
        works: [],
      },
      context_continuity: {
        resource_type: "context_continuity",
        resource_id: "context:overview",
        schema_version: 1,
        frames: [],
        degraded: {
          is_degraded: false,
        },
      },
      memory: {
        summary: {
          sor_current_count: 0,
        },
      },
    },
  };
}

describe("ChatWorkbench", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("发送消息时会带上当前默认 Worker 模板的 profile_id", async () => {
    const sendMessage = vi.fn().mockResolvedValue(undefined);
    useWorkbenchMock.mockReturnValue({
      snapshot: buildSnapshot(),
      refreshResources: vi.fn().mockResolvedValue(undefined),
    });
    useChatStreamMock.mockReturnValue({
      messages: [],
      sendMessage,
      streaming: false,
      restoring: false,
      error: null,
      taskId: null,
    });
    fetchTaskDetailMock.mockResolvedValue(null);

    render(
      <MemoryRouter>
        <ChatWorkbench />
      </MemoryRouter>
    );

    await userEvent.type(
      screen.getByPlaceholderText("告诉 OctoAgent 你现在要做什么"),
      "检查今天的备份情况"
    );
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(sendMessage).toHaveBeenCalledWith("检查今天的备份情况", {
        agentProfileId: "project-default:nas-guardian",
      });
    });
  });
});
