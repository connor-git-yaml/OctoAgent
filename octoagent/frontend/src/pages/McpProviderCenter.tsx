import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import McpInstallWizard from "../components/McpInstallWizard";
import type { McpProviderItem } from "../types";

/* ── draft helpers ─────────────────────────────────────────── */

interface McpProviderDraft {
  provider_id: string;
  command: string;
  args_text: string;
  cwd: string;
  env_text: string;
  enabled: boolean;
}

function emptyDraft(): McpProviderDraft {
  return {
    provider_id: "",
    command: "",
    args_text: "",
    cwd: "",
    env_text: "",
    enabled: true,
  };
}

function joinLines(values: string[]): string {
  return values.join("\n");
}

function parseLines(value: string): string[] {
  return value
    .split(/\n+/g)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseEnvText(value: string): Record<string, string> {
  const entries = parseLines(value)
    .map((line) => {
      const [key, ...rest] = line.split("=");
      return [key?.trim() ?? "", rest.join("=").trim()] as const;
    })
    .filter(([key]) => key);
  return Object.fromEntries(entries);
}

function stringifyEnvText(source: Record<string, string>): string {
  return Object.entries(source)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

function draftFromItem(item: McpProviderItem): McpProviderDraft {
  return {
    provider_id: item.provider_id,
    command: item.command,
    args_text: joinLines(item.args),
    cwd: item.cwd,
    env_text: stringifyEnvText(item.env),
    enabled: item.enabled,
  };
}

/* ── 状态文案 ────────────────────────────────────────────── */

const STATUS_LABELS: Record<string, string> = {
  available: "运行中",
  error: "异常",
  unconfigured: "未配置",
  discovering: "发现中",
  disabled: "已停用",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/* ── modal 编辑器 ────────────────────────────────────────── */

function McpProviderModal({
  mode,
  draft,
  busy,
  error,
  warnings,
  onDraftChange,
  onSave,
  onDelete,
  onClose,
}: {
  mode: "create" | "edit";
  draft: McpProviderDraft;
  busy: boolean;
  error: string | null;
  warnings: string[];
  onDraftChange: <K extends keyof McpProviderDraft>(key: K, value: McpProviderDraft[K]) => void;
  onSave: () => void;
  onDelete: (() => void) | null;
  onClose: () => void;
}) {
  return document.body
    ? createPortal(
        <div
          className="wb-modal-overlay"
          onClick={(e) => {
            // 仅响应真实指针点击（detail > 0），排除键盘或输入法触发的合成 click
            if (e.target === e.currentTarget && e.detail > 0) onClose();
          }}
        >
          <div className="wb-modal-body wb-mcp-modal" onKeyDown={(e) => e.stopPropagation()}>
            <div className="wb-panel-head">
              <h3>{mode === "create" ? "安装 MCP Provider" : `编辑 ${draft.provider_id}`}</h3>
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={onClose}
              >
                关闭
              </button>
            </div>

            {error ? (
              <div className="wb-inline-banner is-error">
                <strong>发现失败</strong>
                <span>{error}</span>
              </div>
            ) : null}

            <div className="wb-agent-form-grid">
              <label className="wb-field">
                <span>Provider ID</span>
                <input
                  type="text"
                  value={draft.provider_id}
                  disabled={mode === "edit"}
                  onChange={(e) => onDraftChange("provider_id", e.target.value)}
                  placeholder="例如 local-files"
                />
              </label>
              <label className="wb-field">
                <span>启用状态</span>
                <select
                  value={draft.enabled ? "enabled" : "disabled"}
                  onChange={(e) => onDraftChange("enabled", e.target.value === "enabled")}
                >
                  <option value="enabled">启用</option>
                  <option value="disabled">停用</option>
                </select>
              </label>
              <label className="wb-field wb-field-span-2">
                <span>Command</span>
                <input
                  type="text"
                  value={draft.command}
                  onChange={(e) => onDraftChange("command", e.target.value)}
                  placeholder="例如 npx"
                />
              </label>
              <label className="wb-field">
                <span>Args</span>
                <textarea
                  value={draft.args_text}
                  onChange={(e) => onDraftChange("args_text", e.target.value)}
                  placeholder="每行一个参数，例如 -y"
                />
              </label>
              <label className="wb-field">
                <span>工作目录</span>
                <textarea
                  value={draft.cwd}
                  onChange={(e) => onDraftChange("cwd", e.target.value)}
                  placeholder="可选，例如 /Users/connorlu/project"
                />
              </label>
              <label className="wb-field wb-field-span-2">
                <span>环境变量</span>
                <textarea
                  value={draft.env_text}
                  onChange={(e) => onDraftChange("env_text", e.target.value)}
                  placeholder={"每行一个 KEY=VALUE\n例如 PATH=/usr/local/bin"}
                />
              </label>
            </div>

            {warnings.length ? (
              <div className="wb-note-stack">
                {warnings.map((w) => (
                  <div key={w} className="wb-note">
                    <strong>提醒</strong>
                    <span>{w}</span>
                  </div>
                ))}
              </div>
            ) : null}

            <div className="wb-inline-actions">
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={onSave}
                disabled={busy}
              >
                {mode === "create" ? "安装" : "保存修改"}
              </button>
              {onDelete ? (
                <button
                  type="button"
                  className="wb-button wb-button-tertiary"
                  onClick={onDelete}
                  disabled={busy}
                >
                  删除
                </button>
              ) : null}
            </div>
          </div>
        </div>,
        document.body,
      )
    : null;
}

/* ── 页面主体 ────────────────────────────────────────────── */

export default function McpProviderCenter() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const catalog = snapshot!.resources.mcp_provider_catalog;
  const items = catalog.items;

  const [modalOpen, setModalOpen] = useState(false);
  const [modalMode, setModalMode] = useState<"create" | "edit">("create");
  const [editingProviderId, setEditingProviderId] = useState<string | null>(null);
  const [draft, setDraft] = useState<McpProviderDraft>(emptyDraft());
  const [installWizardOpen, setInstallWizardOpen] = useState(false);

  const busy = busyActionId === "mcp_provider.save" || busyActionId === "mcp_provider.delete";

  // action 完成后自动关闭 modal
  const [prevBusy, setPrevBusy] = useState(busy);
  useEffect(() => {
    if (prevBusy && !busy && modalOpen) {
      setModalOpen(false);
    }
    setPrevBusy(busy);
  }, [busy]); // eslint-disable-line react-hooks/exhaustive-deps

  function openCreate() {
    setModalMode("create");
    setEditingProviderId(null);
    setDraft(emptyDraft());
    setModalOpen(true);
  }

  function openEdit(item: McpProviderItem) {
    setModalMode("edit");
    setEditingProviderId(item.provider_id);
    setDraft(draftFromItem(item));
    setModalOpen(true);
  }

  function updateDraft<K extends keyof McpProviderDraft>(key: K, value: McpProviderDraft[K]) {
    setDraft((cur) => ({ ...cur, [key]: value }));
  }

  async function handleSave() {
    await submitAction("mcp_provider.save", {
      provider: {
        provider_id: draft.provider_id,
        command: draft.command,
        args: parseLines(draft.args_text),
        cwd: draft.cwd,
        env: parseEnvText(draft.env_text),
        enabled: draft.enabled,
      },
    });
  }

  async function handleDelete() {
    if (!editingProviderId) return;
    await submitAction("mcp_provider.delete", {
      provider_id: editingProviderId,
    });
  }

  const editingItem = editingProviderId
    ? items.find((i) => i.provider_id === editingProviderId) ?? null
    : null;

  return (
    <div className="wb-page">
      {/* 顶栏：标题 + 新建按钮 */}
      <div className="wb-topbar">
        <div className="wb-topbar-copy">
          <h2>MCP Providers</h2>
          <p className="wb-topbar-meta">
            已安装 {items.length} · 已启用 {Number(catalog.summary.enabled_count ?? 0)} · 健康{" "}
            {Number(catalog.summary.healthy_count ?? 0)}
          </p>
        </div>
        <div className="wb-inline-actions">
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={() => setInstallWizardOpen(true)}
          >
            安装
          </button>
          <button type="button" className="wb-button wb-button-secondary" onClick={openCreate}>
            手动添加
          </button>
        </div>
      </div>

      {/* Provider 列表 */}
      {items.length === 0 ? (
        <p className="wb-mcp-empty">当前未安装 MCP Provider</p>
      ) : (
        <div className="wb-note-stack">
          {items.map((item) => (
            <div key={item.provider_id} className="wb-list-row is-static wb-mcp-row">
              <div>
                <strong>{item.label}</strong>
                <p>{item.command} {item.args.length ? item.args.join(" ") : ""}</p>
                <div className="wb-chip-row">
                  <span className={`wb-status-pill is-${item.status}`}>
                    {statusLabel(item.status)}
                  </span>
                  {item.install_source && item.install_source !== "manual" ? (
                    <span className="wb-chip">{item.install_source}</span>
                  ) : (
                    <span className="wb-chip">手动配置</span>
                  )}
                  {item.install_version ? (
                    <span className="wb-chip">v{item.install_version}</span>
                  ) : null}
                  {item.tool_count > 0 ? (
                    <span className="wb-chip">{item.tool_count} 个工具</span>
                  ) : null}
                </div>
              </div>
              <button
                type="button"
                className="wb-button wb-button-secondary"
                onClick={() => openEdit(item)}
              >
                编辑
              </button>
            </div>
          ))}
        </div>
      )}

      {/* modal */}
      {modalOpen ? (
        <McpProviderModal
          mode={modalMode}
          draft={draft}
          busy={busy}
          error={editingItem?.error ?? null}
          warnings={editingItem?.warnings ?? []}
          onDraftChange={updateDraft}
          onSave={() => void handleSave()}
          onDelete={modalMode === "edit" ? () => void handleDelete() : null}
          onClose={() => setModalOpen(false)}
        />
      ) : null}

      {/* 安装向导 */}
      <McpInstallWizard
        open={installWizardOpen}
        onClose={() => setInstallWizardOpen(false)}
        onComplete={() => setInstallWizardOpen(false)}
        submitAction={submitAction}
      />
    </div>
  );
}
