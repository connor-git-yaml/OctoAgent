/**
 * 新建对话 Modal — 选择 Agent + 输入名字。
 *
 * 使用 createPortal 渲染到 body，与 AgentCenter 的 Modal 保持一致风格。
 */

import { useState, type FormEvent } from "react";
import { createPortal } from "react-dom";

export interface AgentOption {
  profile_id: string;
  name: string;
}

interface NewSessionModalProps {
  agents: AgentOption[];
  busy: boolean;
  onConfirm: (agentProfileId: string, projectName: string) => void;
  onClose: () => void;
}

export default function NewSessionModal({
  agents,
  busy,
  onConfirm,
  onClose,
}: NewSessionModalProps) {
  const [selectedAgentId, setSelectedAgentId] = useState(
    agents.length === 1 ? agents[0].profile_id : ""
  );
  const [projectName, setProjectName] = useState("");
  const [error, setError] = useState("");

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmedName = projectName.trim();
    if (!trimmedName) {
      setError("请输入对话名字");
      return;
    }
    if (!selectedAgentId) {
      setError("请选择一个 Agent");
      return;
    }
    // 校验文件夹命名规则
    if (/[<>:"/\\|?*]/.test(trimmedName)) {
      setError("名字不能包含特殊字符（< > : \" / \\ | ? *）");
      return;
    }
    setError("");
    onConfirm(selectedAgentId, trimmedName);
  };

  return createPortal(
    <div
      className="wb-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) {
          onClose();
        }
      }}
    >
      <div className="wb-modal-body" style={{ maxWidth: 440 }}>
        <form onSubmit={handleSubmit}>
          <h2 style={{ marginBottom: 16 }}>新建对话</h2>

          <label className="wb-form-label" htmlFor="new-session-agent">
            选择 Agent
          </label>
          <select
            id="new-session-agent"
            className="wb-select"
            value={selectedAgentId}
            onChange={(e) => setSelectedAgentId(e.target.value)}
            disabled={busy}
          >
            <option value="">— 请选择 —</option>
            {agents.map((agent) => (
              <option key={agent.profile_id} value={agent.profile_id}>
                {agent.name}
              </option>
            ))}
          </select>

          <label
            className="wb-form-label"
            htmlFor="new-session-name"
            style={{ marginTop: 12 }}
          >
            对话名字
          </label>
          <input
            id="new-session-name"
            className="wb-input"
            type="text"
            placeholder="例如：日常问答、项目调研…"
            value={projectName}
            onChange={(e) => {
              setProjectName(e.target.value);
              if (error) setError("");
            }}
            disabled={busy}
            autoFocus
          />

          {error && (
            <p className="wb-form-error" style={{ marginTop: 8 }}>
              {error}
            </p>
          )}

          <div
            style={{
              display: "flex",
              gap: 8,
              justifyContent: "flex-end",
              marginTop: 20,
            }}
          >
            <button
              type="button"
              className="wb-button wb-button-secondary"
              onClick={onClose}
              disabled={busy}
            >
              取消
            </button>
            <button
              type="submit"
              className="wb-button wb-button-primary"
              disabled={busy || !selectedAgentId || !projectName.trim()}
            >
              {busy ? "创建中…" : "创建"}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  );
}
