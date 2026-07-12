/**
 * domains/chat/session L4 直测 —— F143 件 2/件 3
 */
import { describe, expect, it } from "vitest";
import type { SessionProjectionDocument, SessionProjectionItem, WorkProjectionItem } from "../../types";
import {
  deriveActiveWorkContext,
  ensureArray,
  isAgentDirectExecution,
  resolveRestorableTaskIds,
  resolveSessionOwnerProfileId,
  resolveWorkActor,
  resolveWorkStatus,
  sortWorksByUpdate,
} from "./session";

function makeSession(overrides: Partial<SessionProjectionItem> = {}): SessionProjectionItem {
  return {
    session_id: "sess-1",
    task_id: "task-1",
    channel: "web",
    ...overrides,
  } as SessionProjectionItem;
}

function makeWork(overrides: Partial<WorkProjectionItem> = {}): WorkProjectionItem {
  return {
    work_id: "work-1",
    task_id: "task-1",
    status: "RUNNING",
    runtime_summary: {},
    updated_at: "2026-07-13T10:00:00Z",
    ...overrides,
  } as WorkProjectionItem;
}

function baseContextOptions() {
  return {
    sessions: [] as SessionProjectionItem[],
    webSessions: [] as SessionProjectionItem[],
    routeSessionId: undefined as string | undefined,
    routeSession: null as SessionProjectionItem | null,
    restoreChoice: "continue",
    taskId: null as string | null,
    delegationWorks: [] as WorkProjectionItem[],
    contextFrames: [],
    a2aConversations: [],
    taskDetailStatus: undefined as unknown,
  };
}

describe("deriveActiveWorkContext", () => {
  it("空输入：无活跃会话/work，状态未加载", () => {
    const ctx = deriveActiveWorkContext(baseContextOptions());
    expect(ctx.activeSession).toBeNull();
    expect(ctx.activeWork).toBeNull();
    expect(ctx.hasInternalCollaboration).toBe(false);
    expect(ctx.normalizedTaskStatus).toBe("");
    expect(ctx.hasLoadedTaskStatus).toBe(false);
  });

  it("taskId 命中会话：activeSession=currentSession，execution_summary.work_id 选中 work", () => {
    const session = makeSession({
      execution_summary: { work_id: "work-9" } as never,
      status: "running",
    });
    const work = makeWork({ work_id: "work-9" });
    const otherWork = makeWork({ work_id: "work-x", updated_at: "2026-07-14T10:00:00Z" });
    const ctx = deriveActiveWorkContext({
      ...baseContextOptions(),
      sessions: [session],
      webSessions: [session],
      taskId: "task-1",
      delegationWorks: [otherWork, work],
    });
    expect(ctx.activeSession).toBe(session);
    expect(ctx.currentSession).toBe(session);
    expect(ctx.activeWork?.work_id).toBe("work-9");
    expect(ctx.activeSessionWorkId).toBe("work-9");
    expect(ctx.normalizedTaskStatus).toBe("RUNNING");
    expect(ctx.hasLoadedTaskStatus).toBe(true);
  });

  it("execution_summary 无 work_id 时回退到该 task 最新 work", () => {
    const session = makeSession();
    const older = makeWork({ work_id: "w-old", updated_at: "2026-07-10T10:00:00Z" });
    const newer = makeWork({ work_id: "w-new", updated_at: "2026-07-12T10:00:00Z" });
    const ctx = deriveActiveWorkContext({
      ...baseContextOptions(),
      sessions: [session],
      webSessions: [session],
      taskId: "task-1",
      delegationWorks: [older, newer],
    });
    expect(ctx.activeWork?.work_id).toBe("w-new");
  });

  it("taskDetail status 优先于 session status 且归一化大写", () => {
    const session = makeSession({ status: "queued" });
    const ctx = deriveActiveWorkContext({
      ...baseContextOptions(),
      sessions: [session],
      webSessions: [session],
      taskId: "task-1",
      taskDetailStatus: " succeeded ",
    });
    expect(ctx.normalizedTaskStatus).toBe("SUCCEEDED");
  });

  it("route 模式：无 taskId 时 currentSession 取 routeSession", () => {
    const routeSession = makeSession({ session_id: "sess-route", task_id: "task-route" });
    const ctx = deriveActiveWorkContext({
      ...baseContextOptions(),
      routeSessionId: "sess-route",
      routeSession,
    });
    expect(ctx.currentSession).toBe(routeSession);
  });

  it("A2A 协作：conversation 三级匹配 + hasInternalCollaboration", () => {
    const work = makeWork({
      runtime_summary: { research_a2a_conversation_id: "conv-1" } as never,
      target_kind: "worker" as never,
    });
    const conversation = {
      a2a_conversation_id: "conv-1",
      latest_message_type: "TASK",
      target_agent_session_id: "worker-sess-1",
    } as never;
    const ctx = deriveActiveWorkContext({
      ...baseContextOptions(),
      taskId: "task-1",
      delegationWorks: [work],
      a2aConversations: [conversation],
    });
    expect(ctx.activeConversationId).toBe("conv-1");
    expect(ctx.activeA2AConversationRecord).toBe(conversation);
    expect(ctx.hasInternalCollaboration).toBe(true);
    expect(ctx.activeConversationLatestType).toBe("TASK");
    expect(ctx.activeConversationWorkerSessionId).toBe("worker-sess-1");
  });

  it("direct execution（fallback 单 worker）不算内部协作", () => {
    const work = makeWork({
      runtime_id: "worker.llm.default" as never,
      selected_worker_type: "general" as never,
      target_kind: "fallback" as never,
      runtime_summary: { research_worker_id: "w-1" } as never,
    });
    const ctx = deriveActiveWorkContext({
      ...baseContextOptions(),
      taskId: "task-1",
      delegationWorks: [work],
    });
    expect(ctx.isDirectExecution).toBe(true);
    expect(ctx.hasInternalCollaboration).toBe(false);
  });

  it("legacy 会话：compatibility flag 触发默认提示文案，自带 message 优先", () => {
    const flagged = makeSession({
      compatibility_flags: ["legacy_context_polluted"] as never,
      reset_recommended: true as never,
    });
    const ctx = deriveActiveWorkContext({
      ...baseContextOptions(),
      sessions: [flagged],
      webSessions: [flagged],
      taskId: "task-1",
    });
    expect(ctx.legacyResetRecommended).toBe(true);
    expect(ctx.compatibilityMessage).toContain("建议先重置 continuity");

    const withMessage = makeSession({
      compatibility_flags: ["legacy_context_polluted"] as never,
      compatibility_message: "自定义提示" as never,
    });
    const ctx2 = deriveActiveWorkContext({
      ...baseContextOptions(),
      sessions: [withMessage],
      webSessions: [withMessage],
      taskId: "task-1",
    });
    expect(ctx2.compatibilityMessage).toBe("自定义提示");
  });
});

describe("既有纯函数（件 3 补测）", () => {
  it("ensureArray：数组透传、非数组归空", () => {
    expect(ensureArray([1, 2])).toEqual([1, 2]);
    expect(ensureArray(null)).toEqual([]);
    expect(ensureArray(undefined)).toEqual([]);
  });

  it("resolveRestorableTaskIds：focused 优先 + 去重 + new_conversation_token 抑制", () => {
    const doc = {
      focused_session_id: "s2",
      focused_thread_id: "",
      new_conversation_token: "",
      sessions: [
        { session_id: "s1", thread_id: "t1", task_id: "task-a", channel: "web" },
        { session_id: "s2", thread_id: "t2", task_id: "task-b", channel: "web" },
        { session_id: "s3", thread_id: "t3", task_id: "task-a", channel: "telegram" },
      ],
    } as unknown as SessionProjectionDocument;
    expect(resolveRestorableTaskIds(doc)).toEqual(["task-b", "task-a"]);
    const suppressed = { ...doc, new_conversation_token: "tok" } as SessionProjectionDocument;
    expect(resolveRestorableTaskIds(suppressed)).toEqual([]);
  });

  it("resolveRestorableTaskIds：跳过 reset_recommended 会话", () => {
    const doc = {
      sessions: [
        { session_id: "s1", task_id: "task-legacy", channel: "web", reset_recommended: true },
        { session_id: "s2", task_id: "task-live", channel: "web" },
      ],
    } as unknown as SessionProjectionDocument;
    expect(resolveRestorableTaskIds(doc)).toEqual(["task-live"]);
  });

  it("resolveSessionOwnerProfileId：session_owner 优先，agent_profile 兜底", () => {
    expect(
      resolveSessionOwnerProfileId(
        makeSession({ session_owner_profile_id: " owner-1 " as never, agent_profile_id: "a-1" as never })
      )
    ).toBe("owner-1");
    expect(
      resolveSessionOwnerProfileId(makeSession({ agent_profile_id: "a-1" as never }))
    ).toBe("a-1");
    expect(resolveSessionOwnerProfileId(null)).toBe("");
  });

  it("sortWorksByUpdate 按 updated_at 倒序且不改原数组", () => {
    const works = [
      makeWork({ work_id: "w1", updated_at: "2026-07-10T00:00:00Z" }),
      makeWork({ work_id: "w2", updated_at: "2026-07-12T00:00:00Z" }),
    ];
    const sorted = sortWorksByUpdate(works);
    expect(sorted.map((w) => w.work_id)).toEqual(["w2", "w1"]);
    expect(works.map((w) => w.work_id)).toEqual(["w1", "w2"]);
  });

  it("resolveWorkActor/resolveWorkStatus：runtime_summary 优先并归一化", () => {
    const work = makeWork({
      runtime_summary: {
        research_worker_id: "researcher",
        research_worker_status: "running",
      } as never,
      status: "QUEUED",
    });
    expect(resolveWorkActor(work)).toBe("agent://researcher");
    expect(resolveWorkStatus(work)).toBe("RUNNING");
    expect(resolveWorkStatus(makeWork({ status: "done" as never }))).toBe("DONE");
  });

  it("isAgentDirectExecution：route_reason single_worker 亦判直连", () => {
    expect(
      isAgentDirectExecution(
        makeWork({
          runtime_id: "worker.llm.default" as never,
          selected_worker_type: "general" as never,
          route_reason: "policy:single_worker" as never,
        })
      )
    ).toBe(true);
    expect(isAgentDirectExecution(makeWork())).toBe(false);
    expect(isAgentDirectExecution(null)).toBe(false);
  });
});
