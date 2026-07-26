import {
  type ReactElement,
  type RefObject,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import McpInstallWizard from "../components/McpInstallWizard";
import type { McpProviderItem } from "../types";
import "./McpProviderCenter.css";

type SecretMode = "keep" | "replace" | "remove";

interface SecretDraft {
  name: string;
  mode: SecretMode;
  value: string;
}

interface McpProviderDraft {
  displayName: string;
  providerId: string;
  command: string;
  argsText: string;
  cwd: string;
  enabled: boolean;
  secrets: SecretDraft[];
}

const STATUS_LABELS: Record<string, string> = {
  available: "运行正常",
  error: "需要检查",
  unconfigured: "等待配置",
  discovering: "正在连接",
  disabled: "已停用",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? "状态检查中";
}

function emptyDraft(): McpProviderDraft {
  return {
    displayName: "",
    providerId: "",
    command: "",
    argsText: "",
    cwd: "",
    enabled: true,
    secrets: [{ name: "", mode: "replace", value: "" }],
  };
}

function draftFromItem(item: McpProviderItem): McpProviderDraft {
  const secretNames = Object.keys(item.env).filter(Boolean);
  return {
    displayName: item.label,
    providerId: item.provider_id,
    command: item.command,
    argsText: item.args.join("\n"),
    cwd: item.cwd,
    enabled: item.enabled,
    secrets: (secretNames.length > 0 ? secretNames : ["API_KEY"]).map(
      (name) => ({
        name,
        mode: "keep" as const,
        value: "",
      }),
    ),
  };
}

function parseLines(value: string): string[] {
  return value
    .split(/\n+/g)
    .map((item) => item.trim())
    .filter(Boolean);
}

function secretMutations(
  secrets: SecretDraft[],
): Record<string, { mode: SecretMode; value?: string }> {
  return Object.fromEntries(
    secrets
      .filter((secret) => secret.name.trim())
      .map((secret) => [
        secret.name.trim(),
        secret.mode === "replace"
          ? { mode: secret.mode, value: secret.value }
          : { mode: secret.mode },
      ]),
  );
}

function toolsFrom(item: McpProviderItem): string[] {
  const raw = item.details.tools;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw
    .map((tool) => {
      if (typeof tool === "string") {
        return tool;
      }
      if (tool && typeof tool === "object" && "name" in tool) {
        return String(tool.name);
      }
      return "";
    })
    .filter(Boolean)
    .slice(0, 12);
}

function safeCommand(item: McpProviderItem): string {
  const command = [item.command, ...item.args].join(" ").trim();
  return /(?:token|secret|password|key)=/i.test(command)
    ? "已隐藏敏感参数"
    : command;
}

function providerDescription(item: McpProviderItem): string {
  const description = item.description?.trim();
  const technicalValues = new Set([
    item.command.trim(),
    [item.command, ...item.args].join(" ").trim(),
    item.provider_id.trim(),
  ]);
  return description && !technicalValues.has(description)
    ? description
    : "已连接到 OctoAgent，可供 Agent 按权限使用。";
}

function restoreFocus(target: HTMLElement | null): void {
  if (target) {
    window.requestAnimationFrame(() => target.focus());
  }
}

function ProviderCard({
  item,
  onEdit,
}: {
  item: McpProviderItem;
  onEdit: (item: McpProviderItem, trigger: HTMLButtonElement) => void;
}): ReactElement {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const titleId = `mcp-provider-${item.provider_id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const tools = toolsFrom(item);
  return (
    <article
      className="f149-mcp-card"
      aria-labelledby={titleId}
      aria-label={item.label}
    >
      <div className="f149-mcp-card-head">
        <div>
          <span className={`f149-mcp-status is-${item.status}`}>
            {statusLabel(item.status)}
          </span>
          <h2 id={titleId}>{item.label}</h2>
        </div>
        <button
          type="button"
          className="f149-mcp-button f149-mcp-button-quiet"
          aria-label={`编辑${item.label}`}
          onClick={(event) => onEdit(item, event.currentTarget)}
        >
          编辑
        </button>
      </div>
      <p className="f149-mcp-description">
        {providerDescription(item)}
      </p>
      <dl className="f149-mcp-card-meta">
        <div>
          <dt>可用工具</dt>
          <dd>{item.tool_count}</dd>
        </div>
        <div>
          <dt>连接方式</dt>
          <dd>
            {item.install_source && item.install_source !== "manual"
              ? "自动安装"
              : "手动添加"}
          </dd>
        </div>
      </dl>
      <section className="f149-mcp-advanced">
        <button
          type="button"
          className="f149-mcp-advanced-trigger"
          aria-expanded={advancedOpen}
          onClick={() => setAdvancedOpen((current) => !current)}
        >
          高级 · 技术信息
          <span aria-hidden="true">{advancedOpen ? "−" : "+"}</span>
        </button>
        {advancedOpen ? (
          <div className="f149-mcp-advanced-body">
            <dl>
              <div>
                <dt>启动命令</dt>
                <dd>
                  <code>{safeCommand(item)}</code>
                </dd>
              </div>
              <div>
                <dt>服务标识</dt>
                <dd>
                  <code>{item.provider_id}</code>
                </dd>
              </div>
            </dl>
            {tools.length > 0 ? (
              <ul aria-label="工具列表">
                {tools.map((tool) => (
                  <li key={tool}>{tool}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </section>
    </article>
  );
}

function SecretEditor({
  secret,
  index,
  onChange,
}: {
  secret: SecretDraft;
  index: number;
  onChange: (index: number, next: SecretDraft) => void;
}): ReactElement {
  const suffix = index === 0 ? "" : ` ${index + 1}`;
  return (
    <fieldset className="f149-mcp-secret">
      <legend>访问密钥{suffix}</legend>
      <p>现有值不可查看。保存时只提交你的处理选择。</p>
      <label>
        <span>密钥名称</span>
        <input
          value={secret.name}
          onChange={(event) =>
            onChange(index, { ...secret, name: event.target.value })
          }
        />
      </label>
      <div className="f149-mcp-secret-options">
        <label>
          <input
            type="radio"
            name={`secret-mode-${index}`}
            checked={secret.mode === "keep"}
            onChange={() =>
              onChange(index, { ...secret, mode: "keep", value: "" })
            }
          />
          保留现有值
        </label>
        <label>
          <input
            type="radio"
            name={`secret-mode-${index}`}
            checked={secret.mode === "replace"}
            onChange={() =>
              onChange(index, { ...secret, mode: "replace", value: "" })
            }
          />
          替换访问密钥
        </label>
        <label>
          <input
            type="radio"
            name={`secret-mode-${index}`}
            checked={secret.mode === "remove"}
            onChange={() =>
              onChange(index, { ...secret, mode: "remove", value: "" })
            }
          />
          清空访问密钥
        </label>
      </div>
      {secret.mode === "replace" ? (
        <label>
          <span>新的访问密钥</span>
          <input
            type="password"
            autoComplete="new-password"
            value={secret.value}
            onChange={(event) =>
              onChange(index, { ...secret, value: event.target.value })
            }
          />
        </label>
      ) : null}
    </fieldset>
  );
}

function ProviderDialog({
  mode,
  draft,
  busy,
  onChange,
  onSave,
  onDelete,
  onClose,
}: {
  mode: "create" | "edit";
  draft: McpProviderDraft;
  busy: boolean;
  onChange: (next: McpProviderDraft) => void;
  onSave: () => void;
  onDelete: (() => void) | null;
  onClose: () => void;
}): ReactElement {
  const title =
    mode === "edit"
      ? `编辑${draft.displayName || draft.providerId}`
      : "手动添加服务";
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const updateSecret = (index: number, next: SecretDraft) => {
    onChange({
      ...draft,
      secrets: draft.secrets.map((secret, current) =>
        current === index ? next : secret,
      ),
    });
  };

  return createPortal(
    <div
      className="f149-mcp-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <section
        className="f149-mcp-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <div>
            <p className="f149-mcp-kicker">
              {mode === "edit" ? "连接设置" : "高级配置"}
            </p>
            <h2>{title}</h2>
          </div>
          <button
            type="button"
            className="f149-mcp-button f149-mcp-button-quiet"
            onClick={onClose}
          >
            关闭
          </button>
        </header>
        <div className="f149-mcp-form">
          <label>
            <span>服务标识</span>
            <input
              value={draft.providerId}
              disabled={mode === "edit"}
              onChange={(event) =>
                onChange({ ...draft, providerId: event.target.value })
              }
            />
          </label>
          <label>
            <span>启用状态</span>
            <select
              value={draft.enabled ? "enabled" : "disabled"}
              onChange={(event) =>
                onChange({
                  ...draft,
                  enabled: event.target.value === "enabled",
                })
              }
            >
              <option value="enabled">启用</option>
              <option value="disabled">停用</option>
            </select>
          </label>
          <label className="is-wide">
            <span>启动方式</span>
            <input
              value={draft.command}
              onChange={(event) =>
                onChange({ ...draft, command: event.target.value })
              }
            />
          </label>
          <label>
            <span>启动参数（每行一项）</span>
            <textarea
              value={draft.argsText}
              onChange={(event) =>
                onChange({ ...draft, argsText: event.target.value })
              }
            />
          </label>
          <label>
            <span>工作目录</span>
            <textarea
              value={draft.cwd}
              onChange={(event) =>
                onChange({ ...draft, cwd: event.target.value })
              }
            />
          </label>
        </div>
        {draft.secrets.map((secret, index) => (
          <SecretEditor
            key={index}
            secret={secret}
            index={index}
            onChange={updateSecret}
          />
        ))}
        <footer>
          {onDelete ? (
            <button
              type="button"
              className="f149-mcp-button f149-mcp-button-danger"
              onClick={onDelete}
              disabled={busy}
            >
              删除连接
            </button>
          ) : (
            <span />
          )}
          <button
            type="button"
            className="f149-mcp-button f149-mcp-button-primary"
            onClick={onSave}
            disabled={
              busy ||
              !draft.providerId.trim() ||
              !draft.command.trim() ||
              draft.secrets.some(
                (secret) =>
                  secret.name.trim() &&
                  secret.mode === "replace" &&
                  !secret.value,
              )
            }
          >
            {busy ? "正在保存" : "保存并生效"}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}

function PageState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactElement;
}): ReactElement {
  return (
    <section className="f149-mcp-state" aria-live="polite">
      <span aria-hidden="true">◇</span>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </section>
  );
}

export default function McpProviderCenter(): ReactElement {
  const {
    snapshot,
    loading,
    error,
    authError,
    submitAction,
    refreshSnapshot,
    busyActionId,
  } = useWorkbench();
  const catalog = snapshot?.resources.mcp_provider_catalog;
  const items = catalog?.items ?? [];
  const installTriggerRef = useRef<HTMLButtonElement>(null);
  const manualTriggerRef = useRef<HTMLButtonElement>(null);
  const dialogTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [dialogMode, setDialogMode] = useState<"create" | "edit" | null>(null);
  const [editingProviderId, setEditingProviderId] = useState<string | null>(
    null,
  );
  const [draft, setDraft] = useState<McpProviderDraft>(emptyDraft());
  const [installOpen, setInstallOpen] = useState(false);
  const busy =
    busyActionId === "mcp_provider.save" ||
    busyActionId === "mcp_provider.delete";

  const closeDialog = useCallback(() => {
    setDialogMode(null);
    setEditingProviderId(null);
    setDraft(emptyDraft());
    restoreFocus(dialogTriggerRef.current);
  }, []);

  const openCreate = () => {
    dialogTriggerRef.current = manualTriggerRef.current;
    setDraft(emptyDraft());
    setEditingProviderId(null);
    setDialogMode("create");
  };

  const openEdit = (item: McpProviderItem, trigger: HTMLButtonElement) => {
    dialogTriggerRef.current = trigger;
    setDraft(draftFromItem(item));
    setEditingProviderId(item.provider_id);
    setDialogMode("edit");
  };

  const saveProvider = async () => {
    const outcome = await submitAction("mcp_provider.save", {
      provider: {
        provider_id: draft.providerId,
        command: draft.command,
        args: parseLines(draft.argsText),
        cwd: draft.cwd,
        enabled: draft.enabled,
        env: secretMutations(draft.secrets),
      },
    });
    if (outcome) {
      closeDialog();
    }
  };

  const deleteProvider = async () => {
    if (!editingProviderId) {
      return;
    }
    const outcome = await submitAction("mcp_provider.delete", {
      provider_id: editingProviderId,
    });
    if (outcome) {
      closeDialog();
    }
  };

  let content: ReactElement;
  if (authError?.status === 403) {
    content = (
      <PageState
        title="当前账号没有权限管理外部服务"
        description="你仍可使用已有能力。如需调整连接，请联系管理员。"
      />
    );
  } else if (loading && !catalog) {
    content = (
      <PageState
        title="正在加载外部服务"
        description="正在确认已经连接的服务和可用工具。"
      />
    );
  } else if (error && !catalog) {
    content = (
      <PageState
        title="服务列表加载失败"
        description="刚才没有取得最新列表，你可以重新尝试。"
        action={
          <button
            type="button"
            className="f149-mcp-button f149-mcp-button-primary"
            onClick={() => void refreshSnapshot()}
          >
            重试
          </button>
        }
      />
    );
  } else if (items.length === 0) {
    content = (
      <PageState
        title="还没有连接外部服务"
        description="安装一个服务，或使用高级配置手动添加。"
      />
    );
  } else {
    content = (
      <div className="f149-mcp-grid">
        {items.map((item) => (
          <ProviderCard key={item.provider_id} item={item} onEdit={openEdit} />
        ))}
      </div>
    );
  }

  return (
    <main className="f149-mcp-page">
      <header className="f149-mcp-hero">
        <div>
          <p className="f149-mcp-kicker">能力连接</p>
          <h1>外部服务</h1>
          <p>把常用服务安全地交给 Agent 使用，并随时查看连接健康状态。</p>
        </div>
        <dl>
          <div>
            <dt>已连接</dt>
            <dd>{items.length}</dd>
          </div>
          <div>
            <dt>已启用</dt>
            <dd>{Number(catalog?.summary?.enabled_count ?? 0)}</dd>
          </div>
          <div>
            <dt>运行正常</dt>
            <dd>{Number(catalog?.summary?.healthy_count ?? 0)}</dd>
          </div>
        </dl>
      </header>
      <div className="f149-mcp-actions">
        <button
          ref={installTriggerRef}
          type="button"
          className="f149-mcp-button f149-mcp-button-primary"
          onClick={() => setInstallOpen(true)}
        >
          安装服务
        </button>
        <button
          ref={manualTriggerRef}
          type="button"
          className="f149-mcp-button f149-mcp-button-secondary"
          onClick={openCreate}
        >
          手动添加
        </button>
      </div>
      {content}
      {dialogMode ? (
        <ProviderDialog
          mode={dialogMode}
          draft={draft}
          busy={busy}
          onChange={setDraft}
          onSave={() => void saveProvider()}
          onDelete={dialogMode === "edit" ? () => void deleteProvider() : null}
          onClose={closeDialog}
        />
      ) : null}
      <McpInstallWizard
        open={installOpen}
        onClose={() => setInstallOpen(false)}
        onComplete={() => setInstallOpen(false)}
        submitAction={submitAction}
        returnFocusRef={installTriggerRef as RefObject<HTMLButtonElement>}
      />
    </main>
  );
}
