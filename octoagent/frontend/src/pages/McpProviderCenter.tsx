import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import type { McpProviderItem } from "../types";

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

export default function McpProviderCenter() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const catalog = snapshot!.resources.mcp_provider_catalog;
  const items = catalog.items;
  const [selectedProviderId, setSelectedProviderId] = useState(items[0]?.provider_id ?? "");
  const [editorMode, setEditorMode] = useState<"existing" | "create">(
    items[0] ? "existing" : "create"
  );
  const [draft, setDraft] = useState<McpProviderDraft>(
    items[0] ? draftFromItem(items[0]) : emptyDraft()
  );

  useEffect(() => {
    const selected =
      items.find((item) => item.provider_id === selectedProviderId) ?? items[0] ?? null;
    if (editorMode === "existing" && selected) {
      setSelectedProviderId(selected.provider_id);
      setDraft(draftFromItem(selected));
      return;
    }
    if (!selected) {
      setSelectedProviderId("");
      setDraft(emptyDraft());
      setEditorMode("create");
    }
  }, [catalog.generated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedItem =
    items.find((item) => item.provider_id === selectedProviderId) ?? null;
  const busy = busyActionId === "mcp_provider.save" || busyActionId === "mcp_provider.delete";

  function updateDraft<Key extends keyof McpProviderDraft>(
    key: Key,
    value: McpProviderDraft[Key]
  ) {
    setDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function selectItem(item: McpProviderItem) {
    setEditorMode("existing");
    setSelectedProviderId(item.provider_id);
    setDraft(draftFromItem(item));
  }

  function handleCreate() {
    setEditorMode("create");
    setSelectedProviderId("");
    setDraft(emptyDraft());
  }

  async function handleSave() {
    const result = await submitAction("mcp_provider.save", {
      provider: {
        provider_id: draft.provider_id,
        command: draft.command,
        args: parseLines(draft.args_text),
        cwd: draft.cwd,
        env: parseEnvText(draft.env_text),
        enabled: draft.enabled,
      },
    });
    if (result) {
      setEditorMode("existing");
      setSelectedProviderId(draft.provider_id);
    }
  }

  async function handleDelete() {
    if (!selectedItem) {
      return;
    }
    const result = await submitAction("mcp_provider.delete", {
      provider_id: selectedItem.provider_id,
    });
    if (result) {
      handleCreate();
    }
  }

  return (
    <div className="wb-page">
      <section className="wb-hero wb-hero-compact">
        <div className="wb-hero-copy">
          <p className="wb-kicker">MCP Providers</p>
          <h1>把外部能力接成可治理的 MCP Provider</h1>
          <p>
            这里负责安装、编辑和删除 MCP Provider。安装完成后，先去
            <Link to="/agents?view=providers"> Agents &gt; Providers</Link> 决定当前
            Project 的默认范围，再回 Butler 或 Worker 模板做更细的绑定。
          </p>
          <div className="wb-chip-row">
            <span className="wb-chip">已安装 {items.length}</span>
            <span className="wb-chip">已启用 {Number(catalog.summary.enabled_count ?? 0)}</span>
            <span className="wb-chip">健康 {Number(catalog.summary.healthy_count ?? 0)}</span>
          </div>
        </div>
        <div className="wb-hero-actions">
          <Link className="wb-button wb-button-secondary" to="/agents?view=providers">
            返回 Agents &gt; Providers
          </Link>
          <Link className="wb-button wb-button-tertiary" to="/agents?view=butler">
            去 Butler 绑定
          </Link>
        </div>
      </section>

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">已安装</p>
              <h3>MCP Provider 目录</h3>
            </div>
            <button type="button" className="wb-button wb-button-primary" onClick={handleCreate}>
              新建 Provider
            </button>
          </div>
          <div className="wb-note-stack">
            {items.map((item) => (
              <button
                key={item.provider_id}
                type="button"
                className={`wb-list-row ${selectedProviderId === item.provider_id ? "is-active" : ""}`}
                onClick={() => selectItem(item)}
              >
                <div>
                  <strong>{item.label}</strong>
                  <p>{item.command}</p>
                  <div className="wb-chip-row">
                    <span className={`wb-status-pill is-${item.status}`}>{item.status}</span>
                    <span className="wb-chip">tools {item.tool_count}</span>
                    <span className="wb-chip">{item.enabled ? "enabled" : "disabled"}</span>
                  </div>
                </div>
                <div className="wb-list-meta">
                  <strong>{item.provider_id}</strong>
                  <span>{item.cwd || "stdio"}</span>
                </div>
              </button>
            ))}
            {items.length === 0 ? (
              <div className="wb-empty-state">
                <strong>当前还没有 MCP Provider</strong>
                <span>右侧填命令、参数和环境变量后保存，系统会自动刷新可发现工具。</span>
              </div>
            ) : null}
          </div>
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">编辑器</p>
              <h3>{editorMode === "create" ? "安装新的 MCP Provider" : draft.provider_id}</h3>
            </div>
            {selectedItem ? (
              <button
                type="button"
                className="wb-button wb-button-tertiary"
                onClick={() => void handleDelete()}
                disabled={busy}
              >
                删除
              </button>
            ) : null}
          </div>

          {selectedItem?.error ? (
            <div className="wb-inline-banner is-error">
              <strong>最近一次发现失败</strong>
              <span>{selectedItem.error}</span>
            </div>
          ) : null}

          <div className="wb-agent-form-grid">
            <label className="wb-field">
              <span>Provider ID</span>
              <input
                type="text"
                value={draft.provider_id}
                disabled={editorMode === "existing"}
                onChange={(event) => updateDraft("provider_id", event.target.value)}
                placeholder="例如 local-files"
              />
            </label>
            <label className="wb-field">
              <span>启用状态</span>
              <select
                value={draft.enabled ? "enabled" : "disabled"}
                onChange={(event) => updateDraft("enabled", event.target.value === "enabled")}
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
                onChange={(event) => updateDraft("command", event.target.value)}
                placeholder="例如 npx"
              />
            </label>
            <label className="wb-field">
              <span>Args</span>
              <textarea
                value={draft.args_text}
                onChange={(event) => updateDraft("args_text", event.target.value)}
                placeholder="每行一个参数，例如 -y"
              />
            </label>
            <label className="wb-field">
              <span>工作目录</span>
              <textarea
                value={draft.cwd}
                onChange={(event) => updateDraft("cwd", event.target.value)}
                placeholder="可选，例如 /Users/connorlu/project"
              />
            </label>
            <label className="wb-field wb-field-span-2">
              <span>环境变量</span>
              <textarea
                value={draft.env_text}
                onChange={(event) => updateDraft("env_text", event.target.value)}
                placeholder={"每行一个 KEY=VALUE\n例如 PATH=/usr/local/bin"}
              />
            </label>
          </div>

          {selectedItem?.warnings.length ? (
            <div className="wb-note-stack">
              {selectedItem.warnings.map((warning) => (
                <div key={warning} className="wb-note">
                  <strong>提醒</strong>
                  <span>{warning}</span>
                </div>
              ))}
            </div>
          ) : null}

          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button
              type="button"
              className="wb-button wb-button-primary"
              onClick={() => void handleSave()}
              disabled={busy}
            >
              保存 Provider
            </button>
            <button type="button" className="wb-button wb-button-secondary" onClick={handleCreate}>
              清空表单
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
