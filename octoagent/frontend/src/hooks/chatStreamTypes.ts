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

/** SSE 推送的审批事件快照（直接从事件 payload 构造，不依赖 REST 轮询） */
export interface SSEApprovalSnapshot {
  approvalId: string;
  taskId: string;
  toolName: string;
  toolArgsSummary: string;
  riskExplanation: string;
  sideEffectLevel: string;
  createdAt: string;
  expiresAt: string;
}

export interface ChatSessionScopeSnapshot {
  activeProjectId?: string | null;
  /** @deprecated workspace 概念已移除，保留字段仅为兼容 */
  activeWorkspaceId?: string | null;
  newConversationToken?: string | null;
  newConversationProjectId?: string | null;
  /** @deprecated workspace 概念已移除，保留字段仅为兼容 */
  newConversationWorkspaceId?: string | null;
  newConversationAgentProfileId?: string | null;
}

export interface PendingConversationScope {
  token: string;
  projectId: string;
  agentProfileId: string;
}
