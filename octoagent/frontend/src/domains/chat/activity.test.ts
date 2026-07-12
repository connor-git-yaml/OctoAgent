/**
 * domains/chat/activity L4 直测 —— F143 件 2/件 3
 */
import { describe, expect, it } from "vitest";
import type { TaskEvent, WorkProjectionItem } from "../../types";
import {
  buildAgentActivity,
  buildChatActivityItems,
  buildFreshTurnActivityItems,
  buildToolTimelineRecords,
  buildWorkerActivity,
  deriveChatActivityView,
  formatActivityStateLabel,
  formatActivityTone,
  formatActorSummary,
  summarizeText,
  summarizeToolList,
} from "./activity";

function makeWork(overrides: Partial<WorkProjectionItem> = {}): WorkProjectionItem {
  return {
    work_id: "work-1",
    task_id: "task-1",
    title: "查天气",
    status: "RUNNING",
    runtime_summary: {},
    updated_at: "2026-07-13T10:00:00Z",
    ...overrides,
  } as WorkProjectionItem;
}

function makeEvent(overrides: Partial<TaskEvent> & { type: string }): TaskEvent {
  return {
    event_id: `evt-${Math.random().toString(16).slice(2, 8)}`,
    task_id: "task-1",
    task_seq: 1,
    ts: "2026-07-13T10:00:00Z",
    actor: "system",
    payload: {},
    ...overrides,
  } as TaskEvent;
}

describe("文本与状态格式化", () => {
  it("summarizeText：空白归并 + 超长截断带省略号", () => {
    expect(summarizeText("  a \n b  ")).toBe("a b");
    expect(summarizeText("")).toBe("");
    const long = "x".repeat(200);
    const out = summarizeText(long, 20);
    expect(out).toHaveLength(20);
    expect(out.endsWith("…")).toBe(true);
  });

  it("summarizeToolList：空/截断/溢出计数", () => {
    expect(summarizeToolList([])).toBe("这轮没有挂出额外工具。");
    expect(summarizeToolList(["a", "b"])).toBe("a、b");
    expect(summarizeToolList(["a", "b", "c", "d", "e", "f"])).toBe(
      "a、b、c、d，另外还有 2 个工具"
    );
  });

  it("formatActivityStateLabel：状态优先，无状态回退消息类型", () => {
    expect(formatActivityStateLabel("RUNNING", "")).toBe("进行中");
    expect(formatActivityStateLabel("waiting_input", "")).toBe("等你补充");
    expect(formatActivityStateLabel("", "RESULT")).toBe("已完成");
    expect(formatActivityStateLabel("", "TASK")).toBe("已接手");
    expect(formatActivityStateLabel("", "")).toBe("处理中");
  });

  it("formatActivityTone：warning/danger/success/draft/running 全分支", () => {
    expect(formatActivityTone("WAITING_APPROVAL", "")).toBe("warning");
    expect(formatActivityTone("FAILED", "")).toBe("danger");
    expect(formatActivityTone("", "RESULT")).toBe("success");
    expect(formatActivityTone("CANCELLED", "")).toBe("draft");
    expect(formatActivityTone("RUNNING", "")).toBe("running");
  });

  it("formatActorSummary：状态文案优先于角色文案", () => {
    expect(formatActorSummary("agent://research", "标题", "WAITING_APPROVAL", "")).toBe(
      "这一步需要你点一次确认，系统才会继续。"
    );
    expect(formatActorSummary("agent://research", "查资料", "RUNNING", "")).toBe(
      "正在查资料：查资料"
    );
    expect(formatActorSummary("agent://someone", "", "RUNNING", "")).toBe(
      "正在理解问题、安排下一步，并整理最终回复。"
    );
  });
});

describe("buildToolTimelineRecords", () => {
  it("started/completed 配对为单条记录，failed 标红", () => {
    const records = buildToolTimelineRecords([
      makeEvent({
        type: "TOOL_CALL_STARTED",
        task_seq: 1,
        payload: { tool_name: "web.search", args_summary: "q=天气" },
      }),
      makeEvent({
        type: "TOOL_CALL_COMPLETED",
        task_seq: 2,
        payload: { tool_name: "web.search", output_summary: "20 度" },
      }),
      makeEvent({
        type: "TOOL_CALL_STARTED",
        task_seq: 3,
        payload: { tool_name: "fs.write" },
      }),
      makeEvent({
        type: "TOOL_CALL_FAILED",
        task_seq: 4,
        payload: { tool_name: "fs.write", error: "denied" },
      }),
    ]);
    expect(records).toHaveLength(2);
    expect(records[0]).toMatchObject({ toolName: "web.search", tone: "success" });
    expect(records[1]).toMatchObject({ toolName: "fs.write", tone: "danger" });
  });

  it("无工具事件返回空", () => {
    expect(buildToolTimelineRecords([makeEvent({ type: "USER_MESSAGE" })])).toEqual([]);
  });
});

describe("buildAgentActivity / buildWorkerActivity", () => {
  it("等待补充/等待确认优先级最高", () => {
    expect(buildAgentActivity("WAITING_INPUT", false, true, "RESULT", false).stateLabel).toBe(
      "等你补充"
    );
    expect(buildAgentActivity("WAITING_APPROVAL", true, false, "", true).tone).toBe("warning");
  });

  it("内部协作：RESULT 显示整理中，否则协调中", () => {
    expect(buildAgentActivity("RUNNING", true, true, "RESULT", false).stateLabel).toBe("整理中");
    expect(buildAgentActivity("RUNNING", true, true, "TASK", false).stateLabel).toBe("协调中");
  });

  it("直连执行与终态兜底", () => {
    expect(buildAgentActivity("RUNNING", false, false, "", true).summary).toBe(
      "主助手正在直接调用工具处理这条消息。"
    );
    expect(buildAgentActivity("SUCCEEDED", false, false, "", false).stateLabel).toBe("准备中");
  });

  it("buildWorkerActivity：直连 work 与主助手 actor 返回 null，其余按状态成活动", () => {
    const direct = makeWork({
      runtime_id: "worker.llm.default" as never,
      selected_worker_type: "general" as never,
      target_kind: "fallback" as never,
    });
    expect(buildWorkerActivity(direct, "")).toBeNull();

    const research = makeWork({
      runtime_summary: { research_worker_id: "research-1" } as never,
      status: "RUNNING",
    });
    const activity = buildWorkerActivity(research, "TASK");
    expect(activity).toMatchObject({
      actor: "Research Worker",
      stateLabel: "进行中",
      tone: "running",
    });
  });
});

describe("deriveChatActivityView（件 2 块 B）", () => {
  it("无 activeWork：全部为空且证据时间为 0", () => {
    const view = deriveChatActivityView({
      delegationWorks: [makeWork()],
      activeWork: null,
      taskDetailEvents: [],
      activeConversationLatestType: "",
      activeA2ATargetAgent: "",
      hasInternalCollaboration: false,
    });
    expect(view.relatedWorks).toEqual([]);
    expect(view.workerActivities).toEqual([]);
    expect(view.fallbackWorkerActivity).toEqual([]);
    expect(view.latestRuntimeEvidenceMs).toBe(0);
  });

  it("related works（自身+子 work）产出 worker 活动并带轨迹标题", () => {
    const parent = makeWork({
      work_id: "w-parent",
      runtime_summary: { research_worker_id: "research-1" } as never,
    });
    const child = makeWork({
      work_id: "w-child",
      parent_work_id: "w-parent" as never,
      runtime_summary: { research_worker_id: "research-2" } as never,
      updated_at: "2026-07-14T10:00:00Z",
    });
    const unrelated = makeWork({ work_id: "w-other" });
    const view = deriveChatActivityView({
      delegationWorks: [parent, child, unrelated],
      activeWork: parent,
      taskDetailEvents: [
        makeEvent({ type: "TOOL_CALL_STARTED", ts: "2026-07-13T12:00:00Z", payload: { work_id: "w-parent" } }),
      ],
      activeConversationLatestType: "TASK",
      activeA2ATargetAgent: "",
      hasInternalCollaboration: true,
    });
    expect(view.relatedWorks.map((w) => w.work_id)).toEqual(["w-child", "w-parent"]);
    expect(view.workerActivities).toHaveLength(2);
    expect(view.workerActivities[0]?.traceTitle).toContain("的处理轨迹");
    expect(view.workEvents).toHaveLength(1);
    expect(view.latestRuntimeEvidenceMs).toBe(Date.parse("2026-07-13T12:00:00Z"));
  });

  it("兜底 worker 活动：有协作但无 worker 活动时按 A2A target 合成一条", () => {
    const view = deriveChatActivityView({
      delegationWorks: [],
      activeWork: makeWork({ status: "RUNNING" }),
      taskDetailEvents: [],
      activeConversationLatestType: "TASK",
      activeA2ATargetAgent: "agent://research-worker",
      hasInternalCollaboration: true,
    });
    expect(view.workerActivities).toEqual([]);
    expect(view.fallbackWorkerActivity).toHaveLength(1);
    expect(view.fallbackWorkerActivity[0]).toMatchObject({
      id: "worker-fallback",
      actor: "Research Worker",
    });
  });

  it("无协作时不合成兜底活动", () => {
    const view = deriveChatActivityView({
      delegationWorks: [],
      activeWork: makeWork(),
      taskDetailEvents: [],
      activeConversationLatestType: "",
      activeA2ATargetAgent: "agent://research-worker",
      hasInternalCollaboration: false,
    });
    expect(view.fallbackWorkerActivity).toEqual([]);
  });
});

describe("buildChatActivityItems / buildFreshTurnActivityItems（件 2 块 B）", () => {
  it("fresh turn 占位活动形状稳定", () => {
    const items = buildFreshTurnActivityItems();
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ id: "agent-fresh-turn", actor: "主助手", tone: "running" });
  });

  it("主助手条目在首位，worker 活动至多 2 条，兜底活动殿后", () => {
    const worker = (id: string) => ({
      id,
      actor: `W-${id}`,
      stateLabel: "进行中",
      tone: "running" as const,
      summary: "",
    });
    const items = buildChatActivityItems({
      normalizedTaskStatus: "RUNNING",
      streaming: true,
      hasInternalCollaboration: true,
      activeConversationLatestType: "TASK",
      isDirectExecution: false,
      activeWork: null,
      workEvents: [],
      workerActivities: [worker("w1"), worker("w2"), worker("w3")],
      fallbackWorkerActivity: [worker("fb")],
    });
    expect(items.map((item) => item.id)).toEqual(["main-agent", "w1", "w2", "fb"]);
    expect(items[0]?.traceTitle).toBe("主助手的委派轨迹");
    expect(items[0]?.traceEntries).toEqual([]);
  });

  it("直连执行使用直连轨迹标题", () => {
    const items = buildChatActivityItems({
      normalizedTaskStatus: "RUNNING",
      streaming: false,
      hasInternalCollaboration: false,
      activeConversationLatestType: "",
      isDirectExecution: true,
      activeWork: null,
      workEvents: [],
      workerActivities: [],
      fallbackWorkerActivity: [],
    });
    expect(items[0]?.traceTitle).toBe("主助手的直连处理轨迹");
  });
});
