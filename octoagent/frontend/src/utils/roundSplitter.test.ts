/**
 * utils/roundSplitter L4 直测 —— F143 件 3
 */
import { describe, expect, it } from "vitest";
import type { Artifact, TaskEvent } from "../types";
import {
  MIN_NODE_WIDTH,
  computeTimelineLayout,
  groupByAgent,
  splitIntoRounds,
  type FlowNode,
} from "./roundSplitter";

let seq = 0;
function makeEvent(overrides: Partial<TaskEvent> & { type: string }): TaskEvent {
  seq += 1;
  return {
    event_id: `evt-${seq}`,
    task_id: "task-1",
    task_seq: seq,
    ts: `2026-07-13T10:00:${String(seq).padStart(2, "0")}Z`,
    actor: "kernel",
    payload: {},
    ...overrides,
  } as TaskEvent;
}

function makeNode(overrides: Partial<FlowNode> = {}): FlowNode {
  return {
    id: `node-${Math.random().toString(16).slice(2, 8)}`,
    kind: "tool",
    label: "t",
    status: "success",
    events: [],
    artifacts: [],
    ts: "2026-07-13T10:00:00Z",
    agent: "Orchestrator",
    durationMs: 0,
    ...overrides,
  };
}

describe("splitIntoRounds", () => {
  it("空事件返回空轮次", () => {
    expect(splitIntoRounds([], [])).toEqual([]);
  });

  it("按 USER_MESSAGE 切轮，倒序返回（最新在前）", () => {
    const events = [
      makeEvent({ type: "USER_MESSAGE", payload: { text: "第一问" } }),
      makeEvent({ type: "TOOL_CALL_STARTED", payload: { tool_name: "web.search" } }),
      makeEvent({ type: "TOOL_CALL_COMPLETED", payload: { tool_name: "web.search" } }),
      makeEvent({ type: "USER_MESSAGE", payload: { text: "第二问" } }),
      makeEvent({ type: "MODEL_CALL_STARTED", actor: "worker" }),
    ];
    const rounds = splitIntoRounds(events, []);
    expect(rounds).toHaveLength(2);
    expect(rounds[0]?.triggerMessage).toBe("第二问");
    expect(rounds[1]?.triggerMessage).toBe("第一问");
    expect(rounds[0]?.index).toBe(2);
  });

  it("STARTED/COMPLETED 配对为一个成功节点，FAILED 配对标 error", () => {
    const events = [
      makeEvent({ type: "USER_MESSAGE", payload: { text: "查" } }),
      makeEvent({ type: "TOOL_CALL_STARTED", payload: { tool_name: "web.search" } }),
      makeEvent({ type: "TOOL_CALL_COMPLETED", payload: { tool_name: "web.search" } }),
      makeEvent({ type: "SKILL_STARTED", payload: { skill_id: "s.custom" } }),
      makeEvent({ type: "SKILL_FAILED", payload: { skill_id: "s.custom" } }),
    ];
    const [round] = splitIntoRounds(events, []);
    const tool = round!.nodes.find((n) => n.kind === "tool");
    const skill = round!.nodes.find((n) => n.kind === "skill");
    expect(tool?.status).toBe("success");
    expect(tool?.events).toHaveLength(2);
    expect(skill?.status).toBe("error");
  });

  it("无 completion 的 STARTED 保持 running 节点", () => {
    const events = [
      makeEvent({ type: "USER_MESSAGE", payload: { text: "跑" } }),
      makeEvent({ type: "TOOL_CALL_STARTED", payload: { tool_name: "fs.read" } }),
    ];
    const [round] = splitIntoRounds(events, []);
    expect(round!.nodes.find((n) => n.kind === "tool")?.status).toBe("running");
  });

  it("STATE_TRANSITION 仅终态成节点，中间态隐藏", () => {
    const events = [
      makeEvent({ type: "USER_MESSAGE", payload: { text: "问" } }),
      makeEvent({ type: "STATE_TRANSITION", payload: { to_status: "RUNNING" } }),
      makeEvent({ type: "STATE_TRANSITION", payload: { to_status: "SUCCEEDED" } }),
    ];
    const [round] = splitIntoRounds(events, []);
    const completions = round!.nodes.filter((n) => n.kind === "completion");
    expect(completions).toHaveLength(1);
  });

  it("ARTIFACT_CREATED 折叠到前一节点而非独立节点", () => {
    const artifact = { artifact_id: "art-1", name: "llm-response", parts: [] } as unknown as Artifact;
    const events = [
      makeEvent({ type: "USER_MESSAGE", payload: { text: "写" } }),
      makeEvent({ type: "TOOL_CALL_STARTED", payload: { tool_name: "fs.write" } }),
      makeEvent({ type: "TOOL_CALL_COMPLETED", payload: { tool_name: "fs.write" } }),
      makeEvent({ type: "ARTIFACT_CREATED", payload: { artifact_id: "art-1" } }),
    ];
    const [round] = splitIntoRounds(events, [artifact]);
    expect(round!.nodes.some((n) => n.kind === "artifact")).toBe(false);
    const tool = round!.nodes.find((n) => n.kind === "tool");
    expect(tool?.artifacts.map((a) => a.artifact_id)).toEqual(["art-1"]);
  });

  it("不可见事件类型被隐藏", () => {
    const events = [
      makeEvent({ type: "USER_MESSAGE", payload: { text: "问" } }),
      makeEvent({ type: "TASK_HEARTBEAT" }),
      makeEvent({ type: "POLICY_DECISION" }),
    ];
    const [round] = splitIntoRounds(events, []);
    expect(round!.nodes).toHaveLength(1); // 只剩 USER_MESSAGE
  });

  it("direct 模式（无 WORKER_DISPATCHED）所有节点归 Orchestrator 单泳道", () => {
    const events = [
      makeEvent({ type: "USER_MESSAGE", payload: { text: "问" }, actor: "user" }),
      makeEvent({ type: "TOOL_CALL_STARTED", actor: "tool", payload: { tool_name: "x" } }),
      makeEvent({ type: "TOOL_CALL_COMPLETED", actor: "tool", payload: { tool_name: "x" } }),
    ];
    const [round] = splitIntoRounds(events, []);
    expect(new Set(round!.nodes.map((n) => n.agent))).toEqual(new Set(["Orchestrator"]));
  });

  it("worker 模式：dispatch 后 worker/tool actor 事件归入命名 Worker 泳道", () => {
    const events = [
      makeEvent({ type: "USER_MESSAGE", payload: { text: "派" }, actor: "user" }),
      makeEvent({
        type: "WORKER_DISPATCHED",
        actor: "kernel",
        payload: { agent_name: "Research" },
      }),
      makeEvent({ type: "MODEL_CALL_STARTED", actor: "worker" }),
      makeEvent({ type: "MODEL_CALL_COMPLETED", actor: "worker" }),
      makeEvent({ type: "WORKER_RETURNED", actor: "kernel" }),
    ];
    const [round] = splitIntoRounds(events, []);
    const agents = new Set(round!.nodes.map((n) => n.agent));
    expect(agents.has("Orchestrator")).toBe(true);
    expect(agents.has("Research")).toBe(true);
  });
});

describe("groupByAgent", () => {
  it("同 agent 合并单泳道且保持首现顺序", () => {
    const lanes = groupByAgent([
      makeNode({ agent: "Orchestrator" }),
      makeNode({ agent: "Research" }),
      makeNode({ agent: "Orchestrator" }),
    ]);
    expect(lanes.map((l) => l.agent)).toEqual(["Orchestrator", "Research"]);
    expect(lanes[0]?.nodes).toHaveLength(2);
  });

  it("泳道状态：error > running > 末节点状态；耗时求和", () => {
    const errorLane = groupByAgent([
      makeNode({ agent: "A", status: "success", durationMs: 100 }),
      makeNode({ agent: "A", status: "error", durationMs: 50 }),
    ])[0]!;
    expect(errorLane.laneStatus).toBe("error");
    expect(errorLane.totalDurationMs).toBe(150);

    const runningLane = groupByAgent([
      makeNode({ agent: "B", status: "running" }),
      makeNode({ agent: "B", status: "success" }),
    ])[0]!;
    expect(runningLane.laneStatus).toBe("running");

    const tailLane = groupByAgent([
      makeNode({ agent: "C", status: "success" }),
      makeNode({ agent: "C", status: "neutral" }),
    ])[0]!;
    expect(tailLane.laneStatus).toBe("neutral");
  });
});

describe("computeTimelineLayout", () => {
  it("节点 < 2 时降级布局", () => {
    const layout = computeTimelineLayout(
      groupByAgent([makeNode({ agent: "Orchestrator" })]),
      "2026-07-13T10:00:00Z"
    );
    expect(layout.degraded).toBe(true);
  });

  it("Orchestrator 单泳道顺排：位置递增、宽度为最小宽", () => {
    const nodes = [
      makeNode({ id: "n1", agent: "Orchestrator" }),
      makeNode({ id: "n2", agent: "Orchestrator" }),
      makeNode({ id: "n3", agent: "Orchestrator" }),
    ];
    const layout = computeTimelineLayout(groupByAgent(nodes), "2026-07-13T10:00:00Z");
    const l1 = layout.nodeLayouts.get("n1")!;
    const l2 = layout.nodeLayouts.get("n2")!;
    expect(l1.widthPx).toBe(MIN_NODE_WIDTH);
    expect(l2.leftPx).toBeGreaterThan(l1.leftPx);
    expect(layout.totalWidthPx).toBeGreaterThan(0);
  });

  it("worker 胶囊按 Worker 泳道宽度展宽且泳道索引正确", () => {
    const nodes = [
      makeNode({ id: "orch-1", agent: "Orchestrator" }),
      makeNode({
        id: "worker-capsule",
        agent: "Orchestrator",
        kind: "worker",
        events: [makeEvent({ type: "WORKER_DISPATCHED", payload: { agent_name: "Research" } })],
      }),
      makeNode({ id: "w1", agent: "Research" }),
      makeNode({ id: "w2", agent: "Research" }),
      makeNode({ id: "w3", agent: "Research" }),
    ];
    const layout = computeTimelineLayout(groupByAgent(nodes), "2026-07-13T10:00:00Z");
    const capsule = layout.nodeLayouts.get("worker-capsule")!;
    expect(capsule.widthPx).toBeGreaterThan(MIN_NODE_WIDTH);
    expect(layout.nodeLayouts.get("w1")?.laneIndex).toBe(1);
  });
});
