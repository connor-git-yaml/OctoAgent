/**
 * ApprovalPanel -- T045
 *
 * Approvals 面板主组件。
 * 组合 useApprovals + ApprovalCard 列表渲染 + 空状态提示 + 加载状态。
 * 对齐 FR-020, FR-021
 */

import { useApprovals } from "../../hooks/useApprovals";
import { ApprovalCard } from "./ApprovalCard";

export function ApprovalPanel() {
  const { approvals, total, loading, error, resolve, refresh } = useApprovals();

  return (
    <div
      style={{
        padding: "16px",
        maxWidth: "600px",
      }}
    >
      {/* 标题栏 */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "16px",
        }}
      >
        <h2 style={{ margin: 0, fontSize: "18px" }}>
          待审批
          {total > 0 && (
            <span
              style={{
                backgroundColor: "#f44336",
                color: "white",
                borderRadius: "12px",
                padding: "2px 8px",
                fontSize: "12px",
                marginLeft: "8px",
                verticalAlign: "middle",
              }}
            >
              {total}
            </span>
          )}
        </h2>
        <button
          onClick={refresh}
          disabled={loading}
          style={{
            padding: "6px 12px",
            backgroundColor: "#e0e0e0",
            border: "none",
            borderRadius: "4px",
            cursor: loading ? "not-allowed" : "pointer",
            fontSize: "13px",
          }}
        >
          {loading ? "加载中…" : "刷新"}
        </button>
      </div>

      {/* 错误提示 */}
      {error && (
        <div
          style={{
            backgroundColor: "#ffebee",
            color: "#c62828",
            padding: "12px",
            borderRadius: "4px",
            marginBottom: "12px",
            fontSize: "13px",
          }}
        >
          {error}
        </div>
      )}

      {/* 审批列表 */}
      {approvals.length > 0 ? (
        approvals.map((approval) => (
          <ApprovalCard
            key={approval.approval_id}
            approval={approval}
            onResolve={resolve}
          />
        ))
      ) : loading ? (
        <div
          style={{
            textAlign: "center",
            color: "#999",
            padding: "40px 0",
          }}
        >
          加载审批列表…
        </div>
      ) : (
        <div
          style={{
            textAlign: "center",
            color: "#999",
            padding: "40px 0",
            fontSize: "14px",
          }}
        >
          暂无待审批项
        </div>
      )}
    </div>
  );
}
