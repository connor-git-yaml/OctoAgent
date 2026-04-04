/** 终态任务状态（共享常量，避免重复定义） */
export const TERMINAL_TASK_STATUSES = new Set([
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
  "REJECTED",
]);

/** 从 Record 中安全读取 string 值 */
export function readRecordString(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value : "";
}
