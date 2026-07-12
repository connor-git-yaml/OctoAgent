/**
 * domains/chat/presentation L4 直测 —— F143 件 2/件 3
 */
import { describe, expect, it } from "vitest";
import type { SessionProjectionItem, WorkerProfileItem } from "../../types";
import {
  deriveChatHeaderPresentation,
  formatAgentRoleLabel,
  formatCollaborationDirectionLabel,
  formatTaskStatusLabel,
  formatTaskStatusTone,
  formatToolBoundaryLabel,
} from "./presentation";

function makeSession(overrides: Partial<SessionProjectionItem> = {}): SessionProjectionItem {
  return {
    session_id: "sess-1",
    task_id: "task-1",
    channel: "web",
    ...overrides,
  } as SessionProjectionItem;
}

function baseHeaderOptions() {
  return {
    taskId: null as string | null,
    routeSessionId: undefined as string | undefined,
    taskDetailTitle: undefined as string | undefined,
    activeSession: null as SessionProjectionItem | null,
    currentSession: null as SessionProjectionItem | null,
    routeSessionThreadId: undefined as string | undefined,
    activeWork: null,
    activeConversationId: "",
    activeConversationWorkerSessionId: "",
    activeContextFrameId: undefined as string | undefined,
    activeSessionOwnerProfileId: "",
    workerProfiles: [] as WorkerProfileItem[],
    workerProfilesSummary: {} as Record<string, unknown>,
    availableProjects: [],
    currentProjectId: "",
    pendingConversationProjectId: "",
    pendingConversationAgentProfileId: "",
  };
}

describe("状态与角色格式化", () => {
  it("formatTaskStatusLabel/Tone 常见状态映射", () => {
    expect(formatTaskStatusLabel("RUNNING")).toBe("进行中");
    expect(formatTaskStatusLabel("waiting_approval")).toBe("等你确认");
    expect(formatTaskStatusTone("FAILED")).toBe("danger");
    expect(formatTaskStatusTone("SUCCEEDED")).toBe("success");
  });

  it("formatAgentRoleLabel：空值/主助手/角色关键词/前缀剥离", () => {
    expect(formatAgentRoleLabel("")).toBe("未分配");
    expect(formatAgentRoleLabel("agent://x", { isMainAgent: true })).toBe("主助手");
    expect(formatAgentRoleLabel("agent://research-1")).toBe("Research Worker");
    expect(formatAgentRoleLabel("agent://ops-guard")).toBe("Ops Worker");
    expect(formatAgentRoleLabel("agent://custom-bot")).toBe("custom-bot");
  });

  it("工具边界与协作方向文案", () => {
    expect(formatToolBoundaryLabel("core_only")).toBe("只使用平台默认工具范围");
    expect(formatToolBoundaryLabel("")).toBe("当前没有记录工具范围");
    expect(formatCollaborationDirectionLabel("inbound")).toBe("专门角色 -> 主助手");
    expect(formatCollaborationDirectionLabel("outbound")).toBe("主助手 -> 专门角色");
  });
});

describe("deriveChatHeaderPresentation（件 2 块 D）", () => {
  it("空会话：默认标题与 OctoAgent 兜底 owner 名", () => {
    const view = deriveChatHeaderPresentation(baseHeaderOptions());
    expect(view.conversationTitle).toBe("开始一段对话");
    expect(view.conversationOwnerName).toBe("OctoAgent");
    expect(view.techRefs).toEqual([]);
    expect(view.canEditSessionAlias).toBe(false);
  });

  it("标题级联：taskDetail > session.title > latest_message_summary > 处理中占位", () => {
    const session = makeSession({
      title: "会话标题" as never,
      latest_message_summary: "最近一条摘要" as never,
    });
    expect(
      deriveChatHeaderPresentation({
        ...baseHeaderOptions(),
        taskId: "task-1",
        taskDetailTitle: "任务标题",
        activeSession: session,
      }).conversationTitle
    ).toBe("任务标题");
    expect(
      deriveChatHeaderPresentation({
        ...baseHeaderOptions(),
        taskId: "task-1",
        activeSession: makeSession({ latest_message_summary: "最近一条摘要" as never }),
      }).conversationTitle
    ).toBe("最近一条摘要");
    expect(
      deriveChatHeaderPresentation({ ...baseHeaderOptions(), taskId: "task-1" }).conversationTitle
    ).toBe("这轮对话正在处理中");
  });

  it("techRefs 只收集非空引用且保序", () => {
    const view = deriveChatHeaderPresentation({
      ...baseHeaderOptions(),
      taskId: "task-1",
      activeSession: makeSession(),
      activeConversationId: "conv-1",
      activeContextFrameId: "frame-1",
    });
    expect(view.techRefs.map((item) => item.label)).toEqual([
      "任务 ID",
      "会话 ID",
      "协作链路 ID",
      "上下文帧 ID",
    ]);
  });

  it("owner 名级联：session_owner_name > 匹配 profile 名 > 默认 profile > summary 名 > OctoAgent", () => {
    const profiles = [
      { profile_id: "p-owner", name: "研究员" },
      { profile_id: "p-default", name: "默认管家" },
    ] as WorkerProfileItem[];
    const summary = { default_profile_id: "p-default", default_profile_name: "后端名" };

    expect(
      deriveChatHeaderPresentation({
        ...baseHeaderOptions(),
        activeSession: makeSession({ session_owner_name: " 直连名 " as never }),
        workerProfiles: profiles,
        workerProfilesSummary: summary,
      }).conversationOwnerName
    ).toBe("直连名");

    expect(
      deriveChatHeaderPresentation({
        ...baseHeaderOptions(),
        activeSessionOwnerProfileId: "p-owner",
        workerProfiles: profiles,
        workerProfilesSummary: summary,
      }).conversationOwnerName
    ).toBe("研究员");

    expect(
      deriveChatHeaderPresentation({
        ...baseHeaderOptions(),
        workerProfiles: profiles,
        workerProfilesSummary: summary,
      }).conversationOwnerName
    ).toBe("默认管家");
  });

  it("owner profile id 级联含 pendingConversationAgentProfileId", () => {
    const view = deriveChatHeaderPresentation({
      ...baseHeaderOptions(),
      pendingConversationAgentProfileId: "p-pending",
    });
    expect(view.currentSessionOwnerProfileId).toBe("p-pending");
  });

  it("会话别名展示与可编辑 id：alias 优先，effectiveProject 兜底标题", () => {
    const session = makeSession({
      session_id: "sess-9",
      thread_id: "thread-9" as never,
      alias: " 我的会话 " as never,
      title: "原始标题" as never,
      project_id: "proj-1" as never,
    });
    const view = deriveChatHeaderPresentation({
      ...baseHeaderOptions(),
      taskId: "task-1",
      activeSession: session,
      currentSession: session,
      availableProjects: [{ project_id: "proj-1", slug: "p", name: "项目甲" }] as never,
    });
    expect(view.currentSessionAlias).toBe("我的会话");
    expect(view.sessionTitleBase).toBe("原始标题");
    expect(view.sessionDisplayName).toContain("我的会话");
    expect(view.editableSessionId).toBe("sess-9");
    expect(view.editableThreadId).toBe("thread-9");
    expect(view.canEditSessionAlias).toBe(true);
    expect(view.effectiveProjectId).toBe("proj-1");
    expect(view.effectiveProjectLabel).toBe("项目甲");
  });

  it("无会话标题时 sessionTitleBase 回退项目名", () => {
    const view = deriveChatHeaderPresentation({
      ...baseHeaderOptions(),
      currentProjectId: "proj-2",
      availableProjects: [{ project_id: "proj-2", slug: "q", name: "项目乙" }] as never,
    });
    expect(view.sessionTitleBase).toBe("项目乙");
  });
});
