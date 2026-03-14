export function formatTaskStatusLabel(status: string): string {
  switch (status.trim().toUpperCase()) {
    case "QUEUED":
      return "排队中";
    case "RUNNING":
      return "进行中";
    case "WAITING_INPUT":
      return "等你补充";
    case "WAITING_APPROVAL":
      return "等你确认";
    case "PAUSED":
      return "已暂停";
    case "SUCCEEDED":
      return "已完成";
    case "FAILED":
      return "失败";
    case "CANCELLED":
      return "已取消";
    case "REJECTED":
      return "已拒绝";
    default:
      return status || "尚未创建";
  }
}

export function formatTaskStatusTone(status: string): string {
  switch (status.trim().toUpperCase()) {
    case "SUCCEEDED":
      return "success";
    case "RUNNING":
      return "running";
    case "QUEUED":
      return "draft";
    case "WAITING_INPUT":
    case "WAITING_APPROVAL":
      return "warning";
    case "FAILED":
    case "REJECTED":
      return "danger";
    case "CANCELLED":
    case "PAUSED":
      return "draft";
    default:
      return "draft";
  }
}

export function formatAgentRoleLabel(agent: string): string {
  const normalized = agent.trim().toLowerCase();
  if (!normalized) {
    return "未分配";
  }
  if (normalized.includes("butler")) {
    return "主助手";
  }
  if (normalized.includes("research")) {
    return "Research Worker";
  }
  if (normalized.includes("ops")) {
    return "Ops Worker";
  }
  if (normalized.includes("dev")) {
    return "Dev Worker";
  }
  return agent.replace(/^agent:\/\//, "");
}

export function formatToolBoundaryLabel(mode: string): string {
  switch (mode.trim().toLowerCase()) {
    case "profile_first_core":
      return "优先沿用当前模板的工具范围";
    case "runtime_first":
      return "优先沿用这轮任务临时挂载的工具";
    case "core_only":
      return "只使用平台默认工具范围";
    default:
      return mode || "当前没有记录工具范围";
  }
}

export function formatDiscoveryEntrypointLabel(entrypoint: string): string {
  switch (entrypoint.trim().toLowerCase()) {
    case "workers.review":
      return "让系统重新评估是否需要额外角色";
    case "memory.search":
      return "补查已有背景记录";
    case "web.search":
      return "补查外部资料";
    default:
      return entrypoint || "未记录";
  }
}

export function formatCollaborationDirectionLabel(direction: string): string {
  return direction === "inbound" ? "专门角色 -> 主助手" : "主助手 -> 专门角色";
}
