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

/** 聊天输入框斜杠命令表（F143 件 2 块 E 下沉） */
export const CHAT_SLASH_COMMANDS = [
  {
    value: "/approve",
    description: "批准一次当前审批",
    action: "approve_once",
  },
  {
    value: "/approve always",
    description: "总是批准当前审批",
    action: "approve_always",
  },
  {
    value: "/deny",
    description: "拒绝当前审批",
    action: "deny",
  },
] as const;

export type ChatSlashCommand = (typeof CHAT_SLASH_COMMANDS)[number];

/** 输入前缀匹配斜杠命令（非 "/" 开头返回空） */
export function matchSlashCommands(input: string): ChatSlashCommand[] {
  const normalized = input.trim().toLowerCase();
  if (!normalized.startsWith("/")) {
    return [];
  }
  return CHAT_SLASH_COMMANDS.filter((item) => item.value.startsWith(normalized));
}
