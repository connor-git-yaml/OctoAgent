/**
 * 共享 FakeEventSource 测试替身 —— F143 件 5
 *
 * 收敛 useChatStream.test 与 TaskDetail.test 的两份 ~50 行重复实现。
 * 语义取两者超集（以更贴近真实 EventSource 的 TaskDetail 版为基）：
 * - 构造函数捕获 url（可断言连接目标 / 连接次数）；
 * - `emit(type)` 派发对应 addEventListener 监听器；type === "message" 时
 *   同步派发 onmessage（真实 EventSource 对无 event: 字段的默认事件行为）；
 * - `close()` 置 readyState = CLOSED；
 * - `instances` 每次 install 重置，避免跨用例泄漏。
 *
 * 注意：Playwright 侧 e2e/chat-scripted-loop.spec.ts 的浏览器注入版属 L1
 * 设施（无 vitest 依赖、经 addInitScript 注入），语境不同不在收敛范围。
 */
import { vi } from "vitest";

export interface InstallFakeEventSourceOptions {
  /** 初始 readyState（默认 1=OPEN；传 2 可模拟"连接已关闭"场景） */
  initialReadyState?: number;
}

export function installFakeEventSource(options: InstallFakeEventSourceOptions = {}) {
  const initialReadyState = options.initialReadyState ?? 1;

  class FakeEventSource {
    static CLOSED = 2;
    static instances: FakeEventSource[] = [];
    readyState = initialReadyState;
    onopen: ((this: EventSource, ev: Event) => void) | null = null;
    onerror: ((this: EventSource, ev: Event) => void) | null = null;
    onmessage: ((this: EventSource, ev: MessageEvent) => void) | null = null;
    listeners = new Map<string, Array<(ev: MessageEvent) => void>>();

    constructor(public readonly url: string) {
      FakeEventSource.instances.push(this);
    }

    addEventListener(type: string, listener: (ev: MessageEvent) => void): void {
      const current = this.listeners.get(type) ?? [];
      current.push(listener);
      this.listeners.set(type, current);
    }

    removeEventListener(type: string, listener: (ev: MessageEvent) => void): void {
      const current = this.listeners.get(type) ?? [];
      this.listeners.set(
        type,
        current.filter((item) => item !== listener)
      );
    }

    emit(type: string, payload: unknown): void {
      const event = {
        data: JSON.stringify(payload),
      } as MessageEvent;
      for (const listener of this.listeners.get(type) ?? []) {
        listener(event);
      }
      if (type === "message") {
        this.onmessage?.call(this as unknown as EventSource, event);
      }
    }

    close(): void {
      this.readyState = FakeEventSource.CLOSED;
    }
  }

  vi.stubGlobal("EventSource", FakeEventSource);
  return FakeEventSource;
}
