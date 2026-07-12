/**
 * domains/operator/userFacing L4 直测 —— F143 件 3
 */
import { describe, expect, it } from "vitest";
import type { OperatorInboxItem } from "../../types";
import {
  describeOperatorItemForUser,
  formatOperatorKind,
  mapOperatorQuickAction,
} from "./userFacing";

function makeItem(overrides: Partial<OperatorInboxItem> = {}): OperatorInboxItem {
  return {
    item_id: "alert:item-1",
    kind: "alert",
    state: "pending",
    title: "提醒标题",
    summary: "普通摘要",
    task_id: "task-1",
    thread_id: "",
    source_ref: "ref-1",
    created_at: "2026-07-13T10:00:00Z",
    expires_at: null,
    pending_age_seconds: null,
    suggested_actions: [],
    quick_actions: [],
    recent_action_result: null,
    metadata: {},
    ...overrides,
  } as OperatorInboxItem;
}

describe("formatOperatorKind", () => {
  it("已知 kind 映射中文标签，未知回退待处理事项", () => {
    expect(formatOperatorKind("approval")).toBe("待确认");
    expect(formatOperatorKind("alert")).toBe("提醒");
    expect(formatOperatorKind("retryable_failure")).toBe("可重试失败");
    expect(formatOperatorKind("pairing_request")).toBe("协作请求");
    expect(formatOperatorKind("whatever")).toBe("待处理事项");
  });
});

describe("mapOperatorQuickAction（与 domains/chat/approval 同语义）", () => {
  it("审批动作从 item_id 提取 approval_id", () => {
    const item = makeItem({ item_id: "approval:appr-7" });
    expect(mapOperatorQuickAction(item, "approve_once")).toEqual({
      actionId: "operator.approval.resolve",
      params: { approval_id: "appr-7", mode: "once" },
    });
    expect(mapOperatorQuickAction(item, "deny")?.params.mode).toBe("deny");
  });

  it("任务/告警动作带 item_id；未知 kind 返回 null", () => {
    const item = makeItem();
    expect(mapOperatorQuickAction(item, "cancel_task")?.params).toEqual({
      item_id: "alert:item-1",
    });
    expect(mapOperatorQuickAction(item, "unknown" as never)).toBeNull();
  });
});

describe("describeOperatorItemForUser", () => {
  it("approval：可直接操作时提示就地允许/拒绝", () => {
    const view = describeOperatorItemForUser(
      makeItem({
        kind: "approval",
        title: "terminal.exec 需要审批",
        quick_actions: [
          { kind: "approve_once", label: "批准一次", style: "primary", enabled: true },
        ],
      })
    );
    expect(view.kindLabel).toBe("待确认");
    expect(view.title).toBe("terminal.exec 需要审批");
    expect(view.nextStep).toContain("直接在这里允许、拒绝或取消");
    expect(view.taskLinkTo).toBe("/tasks/task-1");
  });

  it("retryable_failure 超时形态：改写为非技术文案并强调重试是重新发起", () => {
    const view = describeOperatorItemForUser(
      makeItem({
        kind: "retryable_failure",
        title: "task worker_runtime_timeout max_exec",
        summary: "worker_runtime_timeout stalled=120s",
        quick_actions: [{ kind: "retry_task", label: "重试", style: "primary", enabled: true }],
      })
    );
    expect(view.title).toBe("这条任务这次没有在时限内完成");
    expect(view.summary).not.toMatch(/timeout|stalled/i);
    expect(view.nextStep).toContain("重新发起一次");
  });

  it("技术味标题不复用：改用通用任务称谓", () => {
    const view = describeOperatorItemForUser(
      makeItem({ kind: "approval", title: "approval task_id=abc runtime.x json" })
    );
    expect(view.title).toBe("这条任务需要你确认");
  });

  it("alert 停滞形态：提取 stalled 秒数并给出人话时长", () => {
    const view = describeOperatorItemForUser(
      makeItem({
        kind: "alert",
        title: "drift=no_progress stalled=90s",
        summary: "state_machine_stall stalled=90s",
      })
    );
    expect(view.title).toBe("这条任务停住了");
    expect(view.summary).toContain("1 分 30 秒");
  });

  it("pairing_request：默认提示确认外部入口", () => {
    const view = describeOperatorItemForUser(
      makeItem({ kind: "pairing_request", title: "pairing json task_id=1", summary: "" })
    );
    expect(view.title).toBe("有一个外部入口想和你建立连接");
    expect(view.nextStep).toContain("不认识就直接拒绝");
  });

  it("无 task_id 时不给任务链接", () => {
    const view = describeOperatorItemForUser(makeItem({ task_id: "" }));
    expect(view.taskLinkTo).toBeNull();
  });
});
