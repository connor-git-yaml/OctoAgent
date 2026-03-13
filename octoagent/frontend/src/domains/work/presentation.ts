export function formatWorkStatusTone(status: string): string {
  switch (status.trim().toLowerCase()) {
    case "succeeded":
    case "merged":
      return "success";
    case "running":
    case "created":
    case "assigned":
      return "running";
    case "waiting_approval":
    case "waiting_input":
    case "paused":
    case "escalated":
      return "warning";
    case "failed":
    case "timed_out":
    case "cancelled":
      return "danger";
    default:
      return "draft";
  }
}
