/**
 * F140 L1 外部断言支撑件（spec D5）——断言全在 UI 外（node 上下文）。
 *
 * 三通道：
 * 1. REST 事件链：GET /api/tasks（列表取最新）→ GET /api/tasks/{id}（events）
 * 2. 文件系统：实例 root 下直读工具真实写盘产物
 * 3. 失败 marker 定性扫描（cc-haha desktop-smoke 技巧）：等待超时前扫 UI
 *    已知失败文案，把裸超时降级成可定性失败
 *
 * 场景契约常量与 Python 侧 `l1_support/scenario_brain.py` 同步（改动须两侧
 * 同一 commit）。
 */
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import type { Page } from "@playwright/test";

const E2E_DIR = dirname(fileURLToPath(import.meta.url));

// --- server 拓扑（config 与测试共享的单一事实源） ---
export type L1Mode = "loopback" | "bearer";
export const L1_LOOPBACK_PORT = Number(process.env.L1_LOOPBACK_PORT || 8151);
export const L1_BEARER_PORT = Number(process.env.L1_BEARER_PORT || 8152);
export const L1_LOOPBACK_ROOT = join(E2E_DIR, ".l1-runtime", "loopback");
export const L1_BEARER_ROOT = join(E2E_DIR, ".l1-runtime", "bearer");
/** bearer 场景测试专用假 token（非真实凭证） */
export const L1_FD_TOKEN_VALUE = "l1-e2e-test-token";

export function l1ServerUrl(mode: L1Mode): string {
  const port = mode === "bearer" ? L1_BEARER_PORT : L1_LOOPBACK_PORT;
  return `http://127.0.0.1:${port}`;
}

export function l1InstanceRoot(mode: L1Mode): string {
  return mode === "bearer" ? L1_BEARER_ROOT : L1_LOOPBACK_ROOT;
}

// --- 场景契约常量（scenario_brain.py 同步字面量） ---
export const L1_WRITE_MARKER = "L1-WRITE";
export const L1_WRITE_FILE_RELPATH = "l1_e2e/note.md";
export const L1_WRITE_FILE_CONTENT = "F140-L1-MARKER：这行内容由脚本决策环真实写盘";
export const L1_WRITE_REPLY = "文件已写好（L1 场景①）";

// --- 已知失败 marker（UI 稳定错误文案；命中即定性失败而非裸超时） ---
export const KNOWN_FAILURE_MARKERS = [
  "刚才没有发送成功",
  "发送失败",
  "front-door 配置无效",
] as const;

/**
 * 等待稳定信号的包装：expectation 超时时先扫已知失败 marker，命中则抛
 * 定性错误（含命中文案），否则原样抛超时。
 */
export async function withFailureMarkerScan<T>(
  page: Page,
  run: () => Promise<T>
): Promise<T> {
  try {
    return await run();
  } catch (err) {
    const body = await page.textContent("body").catch(() => "");
    for (const marker of KNOWN_FAILURE_MARKERS) {
      if (body && body.includes(marker)) {
        throw new Error(
          `L1 定性失败：UI 出现已知失败文案「${marker}」（原始等待错误：${String(err)}）`
        );
      }
    }
    throw err;
  }
}

// --- REST 事件链断言通道（node fetch，走 UI 外） ---

interface TaskEvent {
  type: string;
  payload?: Record<string, unknown>;
}

export interface TaskDetail {
  task: { task_id: string; status: string; title: string };
  events: TaskEvent[];
}

function authHeaders(mode: L1Mode): Record<string, string> {
  return mode === "bearer" ? { Authorization: `Bearer ${L1_FD_TOKEN_VALUE}` } : {};
}

export async function fetchTaskDetail(mode: L1Mode, taskId: string): Promise<TaskDetail> {
  const resp = await fetch(`${l1ServerUrl(mode)}/api/tasks/${taskId}`, {
    headers: authHeaders(mode),
  });
  if (!resp.ok) throw new Error(`GET /api/tasks/${taskId} ${resp.status}`);
  return (await resp.json()) as TaskDetail;
}

/** 轮询 task 到终态 SUCCEEDED（SSE 回复先于终态持久化的窗口兜底）。 */
export async function pollTaskSucceeded(
  mode: L1Mode,
  taskId: string,
  timeoutMs = 15_000
): Promise<TaskDetail> {
  const deadline = Date.now() + timeoutMs;
  let last: TaskDetail | null = null;
  while (Date.now() < deadline) {
    last = await fetchTaskDetail(mode, taskId);
    if (last.task.status === "SUCCEEDED") return last;
    if (["FAILED", "CANCELLED"].includes(last.task.status)) {
      throw new Error(`L1: task 终态异常 ${last.task.status}`);
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`L1: task ${taskId} 未在 ${timeoutMs}ms 内 SUCCEEDED（最后状态 ${last?.task.status}）`);
}

export function eventsOfType(detail: TaskDetail, type: string): TaskEvent[] {
  return detail.events.filter((e) => e.type === type);
}

export function toolCallEvents(
  detail: TaskDetail,
  type: "TOOL_CALL_STARTED" | "TOOL_CALL_COMPLETED" | "TOOL_CALL_FAILED",
  toolName: string
): TaskEvent[] {
  return eventsOfType(detail, type).filter(
    (e) => (e.payload as { tool_name?: string } | undefined)?.tool_name === toolName
  );
}

// --- 文件系统断言通道 ---

export function readInstanceFile(mode: L1Mode, relParts: string[]): string {
  const full = join(l1InstanceRoot(mode), ...relParts);
  if (!existsSync(full)) {
    throw new Error(`L1: 期望的写盘产物不存在：${full}`);
  }
  return readFileSync(full, "utf-8");
}

/**
 * 零真 LLM 防线终局断言（AC-3；Codex re-review P2 闭环）：launcher 的
 * resolve bomb 任何路径命中（含被宽 except 吞掉异常的后台 memory-extraction）
 * 都会先落 sentinel 文件——每场景末尾调用本断言，防线与吞噬层解耦。
 */
export function assertBombNotTripped(mode: L1Mode): void {
  const sentinel = join(l1InstanceRoot(mode), "L1_BOMB_TRIPPED");
  if (existsSync(sentinel)) {
    throw new Error(
      `L1 零真 LLM 防线击穿：resolve bomb 被触发。现场：\n${readFileSync(sentinel, "utf-8")}`
    );
  }
}
