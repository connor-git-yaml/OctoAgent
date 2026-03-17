/**
 * 共享时间格式化工具
 */

/** 格式化时间为 HH:MM:SS（用于事件级展示） */
export function formatTime(isoString: string): string {
  const d = new Date(isoString);
  return d.toLocaleString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** 格式化日期时间为 MM/DD HH:MM:SS（用于任务级展示） */
export function formatDateTime(isoString: string): string {
  return new Date(isoString).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** 格式化日期时间，null 安全（可选 fallback） */
export function formatDateTimeSafe(
  value: string | null | undefined,
  fallback = "-",
): string {
  if (!value) return fallback;
  return formatDateTime(value);
}
