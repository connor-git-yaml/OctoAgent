export type MessageRole = "user" | "agent";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  isStreaming: boolean;
  hasApproval?: boolean;
}

export interface ChatRestoreTarget {
  taskIds: string[];
}

export interface ChatSendOptions {
  agentProfileId?: string | null;
  sessionId?: string | null;
  threadId?: string | null;
}
