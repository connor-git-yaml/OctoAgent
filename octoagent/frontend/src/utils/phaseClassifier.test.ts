/**
 * utils/phaseClassifier L4 直测 —— F143 件 3
 */
import { describe, expect, it } from "vitest";
import type { PhaseId, TaskEvent, TaskStatus } from "../types";
import {
  PHASE_CONFIGS,
  PHASE_MAP,
  TERMINAL_STATUSES,
  classifyEvents,
  classifyStateTransition,
  formatFileSize,
} from "./phaseClassifier";

let seq = 0;
function makeEvent(type: string, payload: Record<string, unknown> = {}): TaskEvent {
  seq += 1;
  return {
    event_id: `evt-${seq}`,
    task_id: "task-1",
    task_seq: seq,
    ts: "2026-07-13T10:00:00Z",
    type,
    actor: "system",
    payload,
  } as TaskEvent;
}

function phaseStatus(result: ReturnType<typeof classifyEvents>, id: PhaseId) {
  return result.phases.find((p) => p.config.id === id)?.status;
}

describe("classifyStateTransition / PHASE_MAP", () => {
  it("终态迁移归 completed，中间态归 system", () => {
    expect(classifyStateTransition(makeEvent("STATE_TRANSITION", { to_status: "SUCCEEDED" }))).toBe(
      "completed"
    );
    expect(classifyStateTransition(makeEvent("STATE_TRANSITION", { to_status: "RUNNING" }))).toBe(
      "system"
    );
    expect(classifyStateTransition(makeEvent("STATE_TRANSITION", {}))).toBe("system");
  });

  it("代表性事件映射：接收/思考/执行/系统", () => {
    expect(PHASE_MAP.USER_MESSAGE).toBe("received");
    expect(PHASE_MAP.MODEL_CALL_STARTED).toBe("thinking");
    expect(PHASE_MAP.TOOL_CALL_COMPLETED).toBe("executing");
    expect(PHASE_MAP.APPROVAL_REQUESTED).toBe("system");
    expect(PHASE_MAP.ARTIFACT_CREATED).toBe("completed");
  });

  it("TERMINAL_STATUSES 四终态", () => {
    expect([...TERMINAL_STATUSES].sort()).toEqual(["CANCELLED", "FAILED", "REJECTED", "SUCCEEDED"]);
  });
});

describe("classifyEvents", () => {
  it("未知事件类型归 system 阶段", () => {
    const result = classifyEvents([makeEvent("SOME_FUTURE_EVENT")], "RUNNING" as TaskStatus);
    const system = result.phases.find((p) => p.config.id === "system");
    expect(system?.events).toHaveLength(1);
  });

  it("运行中任务：最后活跃可见阶段 active，其余有事件阶段 done", () => {
    const result = classifyEvents(
      [makeEvent("USER_MESSAGE"), makeEvent("MODEL_CALL_STARTED"), makeEvent("TOOL_CALL_STARTED")],
      "RUNNING" as TaskStatus
    );
    expect(phaseStatus(result, "received")).toBe("done");
    expect(phaseStatus(result, "thinking")).toBe("done");
    expect(phaseStatus(result, "executing")).toBe("active");
    expect(phaseStatus(result, "completed")).toBe("pending");
  });

  it("成功终态：全部有事件阶段 done", () => {
    const result = classifyEvents(
      [
        makeEvent("USER_MESSAGE"),
        makeEvent("MODEL_CALL_COMPLETED"),
        makeEvent("STATE_TRANSITION", { to_status: "SUCCEEDED" }),
      ],
      "SUCCEEDED" as TaskStatus
    );
    expect(phaseStatus(result, "received")).toBe("done");
    expect(phaseStatus(result, "thinking")).toBe("done");
    expect(phaseStatus(result, "completed")).toBe("done");
  });

  it("失败终态：最后活跃阶段与含失败事件阶段标 error", () => {
    const result = classifyEvents(
      [makeEvent("USER_MESSAGE"), makeEvent("MODEL_CALL_FAILED")],
      "FAILED" as TaskStatus
    );
    expect(phaseStatus(result, "thinking")).toBe("error");
    expect(phaseStatus(result, "received")).toBe("done");
  });

  it("运行中出现失败事件的中间阶段仍标 done（可能在重试）", () => {
    const result = classifyEvents(
      [makeEvent("MODEL_CALL_FAILED"), makeEvent("TOOL_CALL_STARTED")],
      "RUNNING" as TaskStatus
    );
    expect(phaseStatus(result, "thinking")).toBe("done");
    expect(phaseStatus(result, "executing")).toBe("active");
  });

  it("system 阶段恒 pending 且不参与进度", () => {
    const result = classifyEvents([makeEvent("ERROR")], "FAILED" as TaskStatus);
    expect(phaseStatus(result, "system")).toBe("pending");
  });

  it("phases 顺序与 PHASE_CONFIGS 一致", () => {
    const result = classifyEvents([], "QUEUED" as TaskStatus);
    expect(result.phases.map((p) => p.config.id)).toEqual(PHASE_CONFIGS.map((c) => c.id));
  });
});

describe("formatFileSize", () => {
  it("B/KB/MB/GB 分级与负值钳制", () => {
    expect(formatFileSize(-1)).toBe("0 B");
    expect(formatFileSize(512)).toBe("512 B");
    expect(formatFileSize(2048)).toBe("2.0 KB");
    expect(formatFileSize(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatFileSize(3 * 1024 * 1024 * 1024)).toBe("3.0 GB");
  });
});
