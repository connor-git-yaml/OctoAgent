/**
 * hooks/chatStreamHelpers L4 直测 —— F143 件 3
 *
 * 此前仅经 useChatStream hook 间接覆盖；此处直测行为主干。
 */
import { afterEach, describe, expect, it } from "vitest";
import type { TaskDetailResponse } from "../types";
import {
  ACTIVE_CHAT_TASK_STORAGE_KEY,
  AGENT_STREAM_PLACEHOLDER,
  buildMessagesFromTaskDetail,
  buildPendingConversationScope,
  buildRestoreCandidateTaskIds,
  extractFailureMessage,
  fillPendingAgentMessage,
  findLastAgentContentInCurrentTurn,
  isUserVisibleModelEvent,
  normalizeTaskId,
  persistTaskId,
  readStoredTaskId,
  sanitizeAgentVisibleText,
} from "./chatStreamHelpers";
import type { ChatMessage } from "./chatStreamTypes";

let seq = 0;
function makeEvent(type: string, payload: Record<string, unknown>) {
  seq += 1;
  return {
    event_id: `evt-${seq}`,
    task_id: "task-1",
    task_seq: seq,
    ts: "2026-07-13T10:00:00Z",
    type,
    actor: "system",
    payload,
  };
}

function makeDetail(events: unknown[], artifacts: unknown[] = []): TaskDetailResponse {
  return {
    task: { task_id: "task-1", title: "t", status: "SUCCEEDED" },
    events,
    artifacts,
  } as unknown as TaskDetailResponse;
}

afterEach(() => {
  window.sessionStorage.clear();
});

describe("storage 与 id 归一化", () => {
  it("persistTaskId/readStoredTaskId 往返，空值即清除", () => {
    persistTaskId("task-1");
    expect(window.sessionStorage.getItem(ACTIVE_CHAT_TASK_STORAGE_KEY)).toBe("task-1");
    expect(readStoredTaskId()).toBe("task-1");
    persistTaskId(null);
    expect(readStoredTaskId()).toBeNull();
    persistTaskId("   ");
    expect(readStoredTaskId()).toBeNull();
  });

  it("normalizeTaskId：trim 后空即 null", () => {
    expect(normalizeTaskId(" task-1 ")).toBe("task-1");
    expect(normalizeTaskId("  ")).toBeNull();
    expect(normalizeTaskId(null)).toBeNull();
  });

  it("buildRestoreCandidateTaskIds：当前 taskId 优先 + 去重 + 归一化", () => {
    expect(
      buildRestoreCandidateTaskIds("task-live", { taskIds: [" task-live ", "task-old", ""] })
    ).toEqual(["task-live", "task-old"]);
    expect(buildRestoreCandidateTaskIds(null, null)).toEqual([]);
  });

  it("buildPendingConversationScope：无 token 即 null，字段全 trim", () => {
    expect(buildPendingConversationScope({ newConversationToken: " " })).toBeNull();
    expect(
      buildPendingConversationScope({
        newConversationToken: " tok ",
        newConversationProjectId: " proj ",
        newConversationAgentProfileId: " agent ",
      })
    ).toEqual({ token: "tok", projectId: "proj", agentProfileId: "agent" });
  });
});

describe("sanitizeAgentVisibleText（tool transcript 剥离）", () => {
  it("无 transcript 标记时原样返回（仅换行归一化）", () => {
    expect(sanitizeAgentVisibleText("你好\r\n世界")).toBe("你好\n世界");
  });

  it("剥离 to=tool 行与其后的裸 JSON，保留前后正文", () => {
    const dirty =
      "结论先行。 to=memory.search\n" +
      '{"query":"x","matches":[]}\n' +
      "最终答复。";
    const clean = sanitizeAgentVisibleText(dirty);
    expect(clean).toContain("结论先行。");
    expect(clean).toContain("最终答复。");
    expect(clean).not.toContain("to=memory.search");
    expect(clean).not.toContain('"query"');
  });

  it("剥离 transcript 后跟随的 fenced 代码块", () => {
    const dirty = "先说结论 to=web.search\n```json\n{\"result\":1}\n```\n收尾。";
    const clean = sanitizeAgentVisibleText(dirty);
    expect(clean).not.toContain('"result"');
    expect(clean).toContain("收尾。");
  });

  it("含 transcript 标记但清洗后为空时回退原文", () => {
    const dirty = 'to=memory.search\n{"matches":[]}';
    expect(sanitizeAgentVisibleText(dirty)).toBe(dirty.trim());
  });
});

describe("isUserVisibleModelEvent / extractFailureMessage", () => {
  it("skill_id 无 artifact_ref 的内部调用不可见；带 artifact_ref 可见", () => {
    expect(isUserVisibleModelEvent({ type: "X", payload: { skill_id: "s" } })).toBe(false);
    expect(
      isUserVisibleModelEvent({ type: "X", payload: { skill_id: "s", artifact_ref: "a-1" } })
    ).toBe(true);
    expect(isUserVisibleModelEvent({ type: "X", payload: {} })).toBe(true);
  });

  it("整段 JSON 的 response_summary 视为内部响应", () => {
    expect(
      isUserVisibleModelEvent({
        type: "X",
        payload: { response_summary: '{"plan":"recall"}' },
      })
    ).toBe(false);
  });

  it("失败文案归一化：缺信息/环境/降级三类 + 原样透传 + 空 payload 兜底", () => {
    expect(extractFailureMessage({ type: "X", payload: { error: "missing location" } })).toContain(
      "请补充地点"
    );
    expect(extractFailureMessage({ type: "X", payload: { error: "backend timeout" } })).toContain(
      "稍后重试"
    );
    expect(extractFailureMessage({ type: "X", payload: { error: "已降级 fallback" } })).toContain(
      "降级运行"
    );
    expect(extractFailureMessage({ type: "X", payload: { error: "boom" } })).toBe("boom");
    expect(extractFailureMessage({ type: "X", payload: {} })).toBe(
      "本次回复没有成功完成，请稍后重试。"
    );
  });
});

describe("buildMessagesFromTaskDetail", () => {
  it("USER_MESSAGE + 可见 COMPLETED 按 task_seq 排序重建", () => {
    const messages = buildMessagesFromTaskDetail(
      makeDetail([
        makeEvent("MODEL_CALL_COMPLETED", { response_summary: "答" }),
        makeEvent("USER_MESSAGE", { text: "问" }),
      ])
    );
    // seq 递增：COMPLETED 先于 USER_MESSAGE 产生 → 排序后 COMPLETED 在前
    expect(messages.map((m) => m.role)).toEqual(["agent", "user"]);
  });

  it("正文为空时回退 artifact 文本；非 llm-response artifact_ref 跳过", () => {
    const artifact = {
      artifact_id: "art-1",
      name: "llm-response",
      parts: [{ content: "artifact 正文" }],
    };
    const messages = buildMessagesFromTaskDetail(
      makeDetail(
        [
          makeEvent("USER_MESSAGE", { text: "问" }),
          makeEvent("MODEL_CALL_COMPLETED", { artifact_ref: "art-1" }),
          makeEvent("MODEL_CALL_COMPLETED", { artifact_ref: "art-other" }),
        ],
        [artifact]
      )
    );
    expect(messages).toHaveLength(2);
    expect(messages[1]?.content).toBe("artifact 正文");
  });

  it("可见 MODEL_CALL_FAILED 生成失败占位消息", () => {
    const messages = buildMessagesFromTaskDetail(
      makeDetail([makeEvent("MODEL_CALL_FAILED", { error: "x" })])
    );
    expect(messages[0]?.content).toBe("本次回复失败，请重试。");
  });

  it("内部事件（skill_id 无 artifact）不进消息列表", () => {
    const messages = buildMessagesFromTaskDetail(
      makeDetail([makeEvent("MODEL_CALL_COMPLETED", { skill_id: "s", response_summary: "内部" })])
    );
    expect(messages).toEqual([]);
  });
});

describe("轮边界与兜底填充", () => {
  it("findLastAgentContentInCurrentTurn：只取最后一条 user 之后的 agent 输出", () => {
    const messages: ChatMessage[] = [
      { id: "u1", role: "user", content: "问1", isStreaming: false },
      { id: "a1", role: "agent", content: "答1", isStreaming: false },
      { id: "u2", role: "user", content: "问2", isStreaming: false },
    ];
    expect(findLastAgentContentInCurrentTurn(messages)).toBe("");
    const withAnswer = [
      ...messages,
      { id: "a2", role: "agent", content: "答2", isStreaming: false } as ChatMessage,
    ];
    expect(findLastAgentContentInCurrentTurn(withAnswer)).toBe("答2");
  });

  it("fillPendingAgentMessage：占位/流式态被填充，实内容保留", () => {
    const messages: ChatMessage[] = [
      { id: "m1", role: "agent", content: AGENT_STREAM_PLACEHOLDER, isStreaming: false },
      { id: "m2", role: "agent", content: "已有内容", isStreaming: false },
    ];
    const filled = fillPendingAgentMessage(messages, "m1", "兜底内容");
    expect(filled[0]).toMatchObject({ content: "兜底内容", isStreaming: false });
    expect(fillPendingAgentMessage(messages, "m2", "兜底内容")[1]?.content).toBe("已有内容");
  });
});
