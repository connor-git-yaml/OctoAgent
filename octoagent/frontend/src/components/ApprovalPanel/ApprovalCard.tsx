/**
 * ApprovalCard -- T044
 *
 * 单个审批请求卡片组件。
 * 展示工具名称、参数摘要(脱敏)、风险说明、剩余倒计时 + 三按钮操作。
 * 对齐 FR-020, FR-021, FR-028
 */

import { useEffect, useState } from "react";
import type { ApprovalItem, ApprovalDecision } from "../../hooks/useApprovals";

interface ApprovalCardProps {
  /** 审批项数据 */
  approval: ApprovalItem;
  /** 提交决策回调 */
  onResolve: (approvalId: string, decision: ApprovalDecision) => Promise<boolean>;
}

/** 副作用级别到展示标签的映射 */
const SIDE_EFFECT_LABELS: Record<string, { text: string; color: string }> = {
  none: { text: "只读", color: "#4caf50" },
  reversible: { text: "可逆", color: "#ff9800" },
  irreversible: { text: "不可逆", color: "#f44336" },
};

export function ApprovalCard({ approval, onResolve }: ApprovalCardProps) {
  const [remainingSeconds, setRemainingSeconds] = useState(
    approval.remaining_seconds
  );
  const [resolving, setResolving] = useState(false);

  // 倒计时
  useEffect(() => {
    setRemainingSeconds(approval.remaining_seconds);

    const timer = setInterval(() => {
      setRemainingSeconds((prev) => {
        if (prev <= 0) return 0;
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [approval.remaining_seconds]);

  const handleResolve = async (decision: ApprovalDecision) => {
    setResolving(true);
    try {
      await onResolve(approval.approval_id, decision);
    } finally {
      setResolving(false);
    }
  };

  const sideEffect =
    SIDE_EFFECT_LABELS[approval.side_effect_level] ||
    SIDE_EFFECT_LABELS["irreversible"];

  const formatTime = (seconds: number): string => {
    if (seconds <= 0) return "已过期";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  return (
    <div
      style={{
        border: "1px solid #e0e0e0",
        borderRadius: "8px",
        padding: "16px",
        marginBottom: "12px",
        backgroundColor: remainingSeconds <= 0 ? "#fff3e0" : "#ffffff",
      }}
    >
      {/* 头部: 工具名 + 副作用标签 + 倒计时 */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "8px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <strong style={{ fontSize: "16px" }}>{approval.tool_name}</strong>
          <span
            style={{
              backgroundColor: sideEffect.color,
              color: "white",
              padding: "2px 8px",
              borderRadius: "4px",
              fontSize: "12px",
            }}
          >
            {sideEffect.text}
          </span>
        </div>
        <span
          style={{
            fontSize: "14px",
            color: remainingSeconds <= 30 ? "#f44336" : "#666",
            fontFamily: "monospace",
          }}
        >
          {formatTime(remainingSeconds)}
        </span>
      </div>

      {/* 参数摘要（脱敏） -- FR-028 */}
      <div
        style={{
          backgroundColor: "#f5f5f5",
          padding: "8px 12px",
          borderRadius: "4px",
          marginBottom: "8px",
          fontSize: "13px",
          fontFamily: "monospace",
          wordBreak: "break-all",
        }}
      >
        {approval.tool_args_summary}
      </div>

      {/* 风险说明 */}
      <div
        style={{
          color: "#666",
          fontSize: "13px",
          marginBottom: "12px",
        }}
      >
        {approval.risk_explanation}
      </div>

      {/* 策略标签 */}
      <div
        style={{
          color: "#999",
          fontSize: "11px",
          marginBottom: "12px",
        }}
      >
        策略: {approval.policy_label}
      </div>

      {/* 三按钮操作 -- FR-021 */}
      <div style={{ display: "flex", gap: "8px" }}>
        <button
          onClick={() => handleResolve("allow-once")}
          disabled={resolving || remainingSeconds <= 0}
          style={{
            flex: 1,
            padding: "8px 16px",
            backgroundColor: resolving ? "#ccc" : "#4caf50",
            color: "white",
            border: "none",
            borderRadius: "4px",
            cursor: resolving ? "not-allowed" : "pointer",
            fontSize: "13px",
          }}
        >
          允许一次
        </button>
        <button
          onClick={() => handleResolve("allow-always")}
          disabled={resolving || remainingSeconds <= 0}
          style={{
            flex: 1,
            padding: "8px 16px",
            backgroundColor: resolving ? "#ccc" : "#2196f3",
            color: "white",
            border: "none",
            borderRadius: "4px",
            cursor: resolving ? "not-allowed" : "pointer",
            fontSize: "13px",
          }}
        >
          始终允许
        </button>
        <button
          onClick={() => handleResolve("deny")}
          disabled={resolving || remainingSeconds <= 0}
          style={{
            flex: 1,
            padding: "8px 16px",
            backgroundColor: resolving ? "#ccc" : "#f44336",
            color: "white",
            border: "none",
            borderRadius: "4px",
            cursor: resolving ? "not-allowed" : "pointer",
            fontSize: "13px",
          }}
        >
          拒绝
        </button>
      </div>
    </div>
  );
}
