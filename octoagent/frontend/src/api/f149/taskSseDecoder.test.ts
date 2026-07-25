import { describe, expect, it } from "vitest";
import { decodeTaskSseFrame } from "./taskSseDecoder";
import type { RawTaskSseObject } from "./taskSseDecoder";

const ORACLE = "F149_TASK_SSE_FRONTEND_DECODER_MISSING";

function frame(overrides: RawTaskSseObject = {}): RawTaskSseObject {
  return {
    event_id: "event-7",
    task_id: "task-1",
    task_seq: 7,
    ts: "2026-07-25T12:00:00Z",
    type: "STATE_TRANSITION",
    actor: "system",
    payload: {
      kind: "state_transition",
      to_status: "WAITING_APPROVAL",
    },
    final: false,
    ...overrides,
  };
}

describe("F149 Task SSE frontend decoder", () => {
  it("只把完整的 generated state frame 映射为业务状态投影", () => {
    expect(decodeTaskSseFrame(frame()), ORACLE).toEqual({
      ok: true,
      event: {
        kind: "state-transition",
        eventId: "event-7",
        taskId: "task-1",
        taskSeq: 7,
        timestamp: "2026-07-25T12:00:00Z",
        sourceType: "STATE_TRANSITION",
        actor: "system",
        final: false,
        toStatus: "WAITING_APPROVAL",
      },
    });

    const missingFinal = frame();
    delete missingFinal.final;
    expect(decodeTaskSseFrame(missingFinal).ok, ORACLE).toBe(false);
    expect(
      decodeTaskSseFrame(
        frame({
          payload: {
            kind: "state_transition",
            to_status: "MADE_UP",
          },
        }),
      ).ok,
      ORACLE,
    ).toBe(false);
  });

  it("artifact frame 只产生刷新信号，不把原始 payload 带入投影", () => {
    const result = decodeTaskSseFrame(
      frame({
        event_id: "event-artifact",
        type: "ARTIFACT_CREATED",
        payload: {
          kind: "artifact_refresh",
          refresh_artifacts: true,
        },
      }),
    );

    expect(result, ORACLE).toMatchObject({
      ok: true,
      event: {
        kind: "artifact-refresh",
        eventId: "event-artifact",
      },
    });
    expect(JSON.stringify(result), ORACLE).not.toContain("raw_payload");

    expect(
      decodeTaskSseFrame(
        frame({
          type: "ARTIFACT_CREATED",
          payload: {
            kind: "artifact_refresh",
            refresh_artifacts: true,
            access_token: "F149_SECRET_SENTINEL_DO_NOT_RENDER",
          },
        }),
      ).ok,
      ORACLE,
    ).toBe(false);
  });

  it("unknown/history 事件只进入有界且已净化的诊断投影", () => {
    const result = decodeTaskSseFrame(
      frame({
        event_id: "event-history",
        type: "MODEL_CALL_COMPLETED",
        payload: {
          kind: "diagnostic",
          source_type: "MODEL_CALL_COMPLETED",
          diagnostic: {
            summary: "历史记录已净化",
            password: "[REDACTED]",
          },
          truncated: true,
        },
      }),
    );

    expect(result, ORACLE).toEqual({
      ok: true,
      event: {
        kind: "diagnostic",
        eventId: "event-history",
        taskId: "task-1",
        taskSeq: 7,
        timestamp: "2026-07-25T12:00:00Z",
        sourceType: "MODEL_CALL_COMPLETED",
        actor: "system",
        final: false,
        diagnostic: {
          summary: "历史记录已净化",
          password: "[REDACTED]",
        },
        truncated: true,
      },
    });

    const unsafe = decodeTaskSseFrame(
      frame({
        type: "MODEL_CALL_COMPLETED",
        payload: {
          kind: "diagnostic",
          source_type: "MODEL_CALL_COMPLETED",
          diagnostic: {
            password: "F149_SECRET_SENTINEL_DO_NOT_RENDER",
          },
          truncated: false,
        },
      }),
    );
    expect(unsafe.ok, ORACLE).toBe(false);
    expect(JSON.stringify(unsafe), ORACLE).not.toContain(
      "F149_SECRET_SENTINEL_DO_NOT_RENDER",
    );
  });

  it("拒绝业务 type 与 payload kind 不一致及额外 raw 字段", () => {
    expect(
      decodeTaskSseFrame(
        frame({
          type: "USER_MESSAGE",
          payload: {
            kind: "state_transition",
            to_status: "RUNNING",
          },
        }),
      ).ok,
      ORACLE,
    ).toBe(false);

    expect(
      decodeTaskSseFrame({
        ...frame(),
        raw_payload: {
          token: "F149_SECRET_SENTINEL_DO_NOT_RENDER",
        },
      }).ok,
      ORACLE,
    ).toBe(false);
  });
});
