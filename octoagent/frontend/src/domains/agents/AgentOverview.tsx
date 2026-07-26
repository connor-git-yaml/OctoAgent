import {
  createPortal,
} from "react-dom";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MouseEvent,
} from "react";
import { presentAdvancedValue } from "../shared/resourcePageState";
import type {
  AgentCardViewModel,
  BehaviorFileInfo,
} from "./agentManagementData";

interface AgentCardProps {
  agent: AgentCardViewModel;
  busyActionId: string | null;
  onDelete?: () => void;
  onEdit: () => void;
  onOpenBehaviorFile: (file: BehaviorFileInfo) => void;
  onOpenHistory: (
    file: BehaviorFileInfo,
    trigger: HTMLButtonElement,
  ) => void;
}

function advancedText(
  value: string,
  kind: "path" | "command",
): string {
  const result = presentAdvancedValue({
    value,
    kind,
    sensitivity: "operator_sensitive",
    sanitized: true,
    copyPermitted: false,
    maxDisplayLength: 72,
  });
  return result.displayValue;
}

function AdvancedAgentDetails({
  agent,
  open,
  onClose,
}: {
  agent: AgentCardViewModel;
  open: boolean;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    closeRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  if (!open || !document.body) {
    return null;
  }

  return createPortal(
    <div
      className="f149-agent-sheet-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <section
        className="f149-agent-sheet"
        role="dialog"
        aria-modal="true"
        aria-label="模型与文件信息"
      >
        <header className="f149-agent-sheet-header">
          <div>
            <p>ADVANCED</p>
            <h2>模型与文件信息</h2>
          </div>
          <button ref={closeRef} type="button" onClick={onClose}>
            关闭
          </button>
        </header>
        <dl className="f149-agent-advanced-list">
          <div>
            <dt>模型配置</dt>
            <dd>{advancedText(agent.modelAlias, "command")}</dd>
          </div>
          <div>
            <dt>智能体编号</dt>
            <dd>{advancedText(agent.profileId, "command")}</dd>
          </div>
          {agent.behaviorFiles.map((file) => (
            <div key={`${agent.profileId}:${file.file_id}`}>
              <dt>{file.file_id}</dt>
              <dd>{advancedText(file.path, "path")}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>,
    document.body,
  );
}

export default function AgentCard({
  agent,
  busyActionId,
  onDelete,
  onEdit,
  onOpenBehaviorFile,
  onOpenHistory,
}: AgentCardProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const advancedTriggerRef = useRef<HTMLButtonElement | null>(null);
  const primaryBehaviorFile = agent.behaviorFiles[0] ?? null;
  const configuredAbilityCount = new Set([
    ...agent.defaultToolGroups,
    ...agent.selectedTools,
  ]).size;

  const closeAdvanced = useCallback(() => {
    setAdvancedOpen(false);
    advancedTriggerRef.current?.focus();
  }, []);

  const handleOpenHistory = (
    event: MouseEvent<HTMLButtonElement>,
    file: BehaviorFileInfo,
  ) => {
    onOpenHistory(file, event.currentTarget);
  };

  return (
    <article
      className={`f149-agent-card ${agent.isMainAgent ? "is-main" : ""}`}
      data-agent-profile={agent.profileId}
    >
      <header className="f149-agent-card-header">
        <div className="f149-agent-title">
          <strong>{agent.name}</strong>
          <span className={agent.isMainAgent ? "is-main" : "is-worker"}>
            {agent.isMainAgent ? "主 Agent" : "Worker"}
          </span>
        </div>
        <div className="f149-agent-card-actions">
          <button type="button" onClick={onEdit}>
            编辑
          </button>
          {onDelete ? (
            <button
              type="button"
              className="is-quiet"
              disabled={busyActionId === "worker_profile.archive"}
              onClick={onDelete}
            >
              删除
            </button>
          ) : null}
        </div>
      </header>

      <p className="f149-agent-summary">{agent.summary}</p>
      <div className="f149-agent-meta" aria-label={`${agent.name} 概要`}>
        {agent.projectName ? <span>项目 · {agent.projectName}</span> : null}
        <span>模型 · {agent.modelLabel}</span>
        <span>能力 · {configuredAbilityCount} 项</span>
        {agent.activeWorkCount > 0 ? (
          <span>进行中 · {agent.activeWorkCount}</span>
        ) : null}
      </div>

      {primaryBehaviorFile ? (
        <div className="f149-agent-behavior-row">
          <button
            type="button"
            className="f149-agent-behavior-open"
            onClick={() => onOpenBehaviorFile(primaryBehaviorFile)}
          >
            <span>行为文件</span>
            <strong>{agent.behaviorFiles.length} 项配置</strong>
          </button>
          <button
            type="button"
            className="f149-agent-version-button"
            disabled={!primaryBehaviorFile.exists_on_disk}
            onClick={(event) => handleOpenHistory(event, primaryBehaviorFile)}
          >
            版本
          </button>
        </div>
      ) : null}

      <footer className="f149-agent-card-footer">
        <button
          ref={advancedTriggerRef}
          type="button"
          className="f149-agent-advanced-trigger"
          aria-expanded={advancedOpen}
          onClick={() => setAdvancedOpen(true)}
        >
          高级 · 模型与文件信息
        </button>
        <span>{agent.profileStatus}</span>
      </footer>

      <AdvancedAgentDetails
        agent={agent}
        open={advancedOpen}
        onClose={closeAdvanced}
      />
    </article>
  );
}
