/**
 * useSSE Hook -- 封装原生 EventSource，支持自动连接/断连/重连
 *
 * 功能：
 * 1. 连接 GET /api/stream/task/{taskId}
 * 2. 解析 SSE 事件并回调
 * 3. 终态时自动关闭连接（final: true）
 * 4. 组件卸载时自动断连
 */

import { useEffect, useRef, useState, useCallback } from "react";
import type { SSEEventData } from "../types";

export type SSEStatus = "connecting" | "connected" | "disconnected" | "closed";

interface UseSSEOptions {
  /** 任务 ID */
  taskId: string;
  /** 是否启用（任务非终态时启用） */
  enabled: boolean;
  /** 收到新事件的回调 */
  onEvent: (event: SSEEventData) => void;
}

interface UseSSEReturn {
  /** 连接状态 */
  status: SSEStatus;
}

export function useSSE({ taskId, enabled, onEvent }: UseSSEOptions): UseSSEReturn {
  const [status, setStatus] = useState<SSEStatus>("disconnected");
  const eventSourceRef = useRef<EventSource | null>(null);
  const onEventRef = useRef(onEvent);

  // 保持 onEvent 回调最新
  onEventRef.current = onEvent;

  const disconnect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!enabled || !taskId) {
      disconnect();
      setStatus("disconnected");
      return;
    }

    setStatus("connecting");

    const url = `/api/stream/task/${taskId}`;
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onopen = () => {
      setStatus("connected");
    };

    es.onerror = () => {
      // EventSource 会自动重连，这里只更新状态
      if (es.readyState === EventSource.CLOSED) {
        setStatus("closed");
      } else {
        setStatus("disconnected");
      }
    };

    // 监听所有事件类型
    const eventTypes = [
      "TASK_CREATED",
      "USER_MESSAGE",
      "MODEL_CALL_STARTED",
      "MODEL_CALL_COMPLETED",
      "MODEL_CALL_FAILED",
      "STATE_TRANSITION",
      "ARTIFACT_CREATED",
      "ERROR",
    ];

    const handler = (e: MessageEvent) => {
      try {
        const data: SSEEventData = JSON.parse(e.data);

        onEventRef.current(data);

        // 终态检测：final: true 时关闭连接
        if (data.final) {
          es.close();
          setStatus("closed");
        }
      } catch {
        // 忽略解析失败的事件（如心跳）
      }
    };

    for (const type of eventTypes) {
      es.addEventListener(type, handler);
    }

    // 同时监听通用 message 事件（兜底）
    es.onmessage = handler;

    return () => {
      for (const type of eventTypes) {
        es.removeEventListener(type, handler);
      }
      es.close();
      eventSourceRef.current = null;
    };
  }, [taskId, enabled, disconnect]);

  return { status };
}
