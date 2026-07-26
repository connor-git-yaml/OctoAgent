import {
  type ReactElement,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  fetchF149SkillDetail as fetchSkillDetail,
  fetchF149Skills as fetchSkills,
  installF149Skill as installSkill,
  uninstallF149Skill as uninstallSkill,
} from "../api/f149/adapters";
import { resolveResourcePageState } from "../domains/shared/resourcePageState";
import type { SkillDetail, SkillItem } from "../types";
import "./SkillCenter.css";

type DialogKind = "detail" | "install" | "uninstall" | null;

const SOURCE_LABELS: Record<SkillItem["source"], string> = {
  builtin: "内置",
  user: "用户安装",
  project: "项目",
};

function restoreFocus(
  target: HTMLElement | null,
  fallbackSelector: string | null,
): void {
  window.requestAnimationFrame(() => {
    const fallback = fallbackSelector
      ? document.querySelector<HTMLElement>(fallbackSelector)
      : null;
    const nextTarget = target?.isConnected ? target : fallback;
    nextTarget?.focus();
  });
}

function useEscapeToClose(open: boolean, onClose: () => void): void {
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);
}

function dialogPortal(content: ReactElement): ReactElement {
  return createPortal(content, document.body);
}

function extractSkillName(content: string, filename: string): string {
  const frontmatter = content.match(/^---\s*\n([\s\S]*?)\n---/);
  const name = frontmatter?.[1].match(/^name:\s*(.+?)\s*$/m)?.[1];
  return (
    name?.replace(/^["']|["']$/g, "").trim() || filename.replace(/\.[^.]+$/, "")
  );
}

function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result ?? "")));
    reader.addEventListener("error", () => reject(reader.error));
    reader.readAsText(file);
  });
}

interface SkillCardProps {
  skill: SkillItem;
  onDetail: (skill: SkillItem, trigger: HTMLButtonElement) => void;
  onUninstall: (skill: SkillItem, trigger: HTMLButtonElement) => void;
}

function SkillCard({
  skill,
  onDetail,
  onUninstall,
}: SkillCardProps): ReactElement {
  return (
    <article className="f149-skills-card">
      <div className="f149-skills-card-head">
        <span className="f149-skills-source">
          {SOURCE_LABELS[skill.source]}
        </span>
        {skill.version ? <span>v{skill.version}</span> : null}
      </div>
      <div className="f149-skills-card-copy">
        <h2>{skill.name}</h2>
        <p>{skill.description}</p>
      </div>
      {skill.tags.length > 0 ? (
        <ul className="f149-skills-tags" aria-label="技能标签">
          {skill.tags.slice(0, 5).map((tag) => (
            <li key={tag}>{tag}</li>
          ))}
        </ul>
      ) : null}
      <div className="f149-skills-card-actions">
        <button
          type="button"
          className="f149-skills-button f149-skills-button-primary"
          aria-label={`${skill.name} · 查看详情`}
          onClick={(event) => onDetail(skill, event.currentTarget)}
        >
          查看详情
        </button>
        {skill.source === "user" ? (
          <button
            type="button"
            className="f149-skills-button f149-skills-button-quiet"
            aria-label={`${skill.name} · 卸载`}
            onClick={(event) => onUninstall(skill, event.currentTarget)}
          >
            卸载
          </button>
        ) : null}
      </div>
    </article>
  );
}

interface DetailDialogProps {
  detail: SkillDetail;
  onClose: () => void;
}

function DetailDialog({ detail, onClose }: DetailDialogProps): ReactElement {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  useEscapeToClose(true, onClose);

  return dialogPortal(
    <div
      className="f149-skills-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <section
        className="f149-skills-dialog f149-skills-detail-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="f149-skill-detail-title"
      >
        <header className="f149-skills-dialog-head">
          <div>
            <p className="f149-skills-kicker">{SOURCE_LABELS[detail.source]}</p>
            <h2 id="f149-skill-detail-title">{detail.name}</h2>
          </div>
          <button
            type="button"
            className="f149-skills-button f149-skills-button-quiet"
            onClick={onClose}
          >
            关闭
          </button>
        </header>

        <p className="f149-skills-detail-lead">{detail.description}</p>
        <dl className="f149-skills-meta">
          <div>
            <dt>版本</dt>
            <dd>{detail.version || "未标注"}</dd>
          </div>
          <div>
            <dt>作者</dt>
            <dd>{detail.author || "未标注"}</dd>
          </div>
        </dl>
        {detail.tags.length > 0 ? (
          <ul className="f149-skills-tags" aria-label="技能标签">
            {detail.tags.map((tag) => (
              <li key={tag}>{tag}</li>
            ))}
          </ul>
        ) : null}

        <section className="f149-skills-advanced">
          <button
            type="button"
            className="f149-skills-advanced-trigger"
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((current) => !current)}
          >
            高级 · 技术正文
            <span aria-hidden="true">{advancedOpen ? "−" : "+"}</span>
          </button>
          {advancedOpen ? (
            <div className="f149-skills-advanced-body">
              <section>
                <h3>触发方式</h3>
                {detail.trigger_patterns.length > 0 ? (
                  <ul>
                    {detail.trigger_patterns.map((pattern) => (
                      <li key={pattern}>{pattern}</li>
                    ))}
                  </ul>
                ) : (
                  <p>未声明</p>
                )}
              </section>
              <section>
                <h3>所需工具</h3>
                {detail.tools_required.length > 0 ? (
                  <ul>
                    {detail.tools_required.map((tool) => (
                      <li key={tool}>{tool}</li>
                    ))}
                  </ul>
                ) : (
                  <p>未声明</p>
                )}
              </section>
              <section>
                <h3>SKILL.md</h3>
                <pre>
                  <code>{detail.content}</code>
                </pre>
              </section>
            </div>
          ) : null}
        </section>
      </section>
    </div>,
  );
}

interface InstallDialogProps {
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onInstall: (name: string, content: string) => Promise<void>;
}

function InstallDialog({
  busy,
  error,
  onClose,
  onInstall,
}: InstallDialogProps): ReactElement {
  const [content, setContent] = useState("");
  const [name, setName] = useState("");
  const [readError, setReadError] = useState<string | null>(null);
  const [attempted, setAttempted] = useState(false);
  useEscapeToClose(true, onClose);

  const handleFile = async (file: File | undefined) => {
    if (!file) {
      return;
    }
    try {
      const nextContent = await readFileText(file);
      setContent(nextContent);
      setName(extractSkillName(nextContent, file.name));
      setReadError(null);
      setAttempted(false);
    } catch {
      setContent("");
      setName("");
      setReadError("无法读取这个文件，请重新选择。");
    }
  };

  return dialogPortal(
    <div className="f149-skills-dialog-backdrop">
      <section
        className="f149-skills-dialog f149-skills-install-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="f149-skill-install-title"
      >
        <header className="f149-skills-dialog-head">
          <div>
            <p className="f149-skills-kicker">从文件安装</p>
            <h2 id="f149-skill-install-title">安装 Skill</h2>
          </div>
          <button
            type="button"
            className="f149-skills-button f149-skills-button-quiet"
            onClick={onClose}
            disabled={busy}
          >
            关闭
          </button>
        </header>
        <p className="f149-skills-dialog-copy">
          选择一个 SKILL.md。名称与内容是否有效由服务端统一检查。
        </p>
        <label className="f149-skills-file-field">
          <span>选择 SKILL.md</span>
          <input
            type="file"
            accept=".md,text/markdown,text/plain"
            autoFocus
            onChange={(event) =>
              void handleFile(event.currentTarget.files?.[0])
            }
            disabled={busy}
          />
        </label>
        {name ? (
          <div className="f149-skills-file-preview" aria-live="polite">
            <span>准备安装</span>
            <strong>{name}</strong>
          </div>
        ) : null}
        {readError || error ? (
          <p className="f149-skills-action-error" role="alert">
            {readError || error}
          </p>
        ) : null}
        <footer className="f149-skills-dialog-actions">
          <button
            type="button"
            className="f149-skills-button f149-skills-button-primary"
            disabled={busy || !name || !content}
            onClick={() => {
              setAttempted(true);
              void onInstall(name, content);
            }}
          >
            {busy ? "安装中…" : attempted && error ? "重试" : "安装"}
          </button>
        </footer>
      </section>
    </div>,
  );
}

interface UninstallDialogProps {
  skill: SkillItem;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}

function UninstallDialog({
  skill,
  busy,
  error,
  onClose,
  onConfirm,
}: UninstallDialogProps): ReactElement {
  useEscapeToClose(true, onClose);

  return dialogPortal(
    <div className="f149-skills-dialog-backdrop">
      <section
        className="f149-skills-dialog f149-skills-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="f149-skill-uninstall-title"
      >
        <p className="f149-skills-kicker">需要确认</p>
        <h2 id="f149-skill-uninstall-title">确认卸载技能</h2>
        <p>
          将从当前空间卸载 <strong>{skill.name}</strong>。内置技能不会受影响。
        </p>
        {error ? (
          <p className="f149-skills-action-error" role="alert">
            {error}
          </p>
        ) : null}
        <footer className="f149-skills-dialog-actions">
          <button
            type="button"
            className="f149-skills-button f149-skills-button-quiet"
            onClick={onClose}
            disabled={busy}
          >
            取消
          </button>
          <button
            type="button"
            className="f149-skills-button f149-skills-button-danger"
            onClick={() => void onConfirm()}
            disabled={busy}
          >
            {busy ? "正在卸载…" : "确认卸载"}
          </button>
        </footer>
      </section>
    </div>,
  );
}

export default function SkillCenter(): ReactElement {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<Error | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [selectedSkill, setSelectedSkill] = useState<SkillItem | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const focusReturnRef = useRef<HTMLElement | null>(null);
  const focusReturnSelectorRef = useRef<string | null>(null);

  const loadSkills = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const response = await fetchSkills();
      setSkills(response.items);
    } catch (error) {
      setListError(error instanceof Error ? error : new Error("unknown"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSkills();
  }, [loadSkills]);

  const closeDialog = useCallback(() => {
    setDialog(null);
    setSelectedSkill(null);
    setDetail(null);
    setActionError(null);
    restoreFocus(focusReturnRef.current, focusReturnSelectorRef.current);
  }, []);

  const openInstall = (trigger: HTMLButtonElement) => {
    focusReturnRef.current = trigger;
    focusReturnSelectorRef.current = '[data-focus-return="skill-install"]';
    setActionError(null);
    setDialog("install");
  };

  const openDetail = async (skill: SkillItem, trigger: HTMLButtonElement) => {
    focusReturnRef.current = trigger;
    focusReturnSelectorRef.current = null;
    setSelectedSkill(skill);
    setActionError(null);
    try {
      const response = await fetchSkillDetail(skill.name);
      setDetail(response);
      setDialog("detail");
    } catch {
      setActionError("技能详情加载失败，请重试。");
    }
  };

  const openUninstall = (skill: SkillItem, trigger: HTMLButtonElement) => {
    focusReturnRef.current = trigger;
    focusReturnSelectorRef.current = null;
    setSelectedSkill(skill);
    setActionError(null);
    setDialog("uninstall");
  };

  const handleInstall = async (name: string, content: string) => {
    setBusy(true);
    setActionError(null);
    try {
      await installSkill(name, content);
      await loadSkills();
      closeDialog();
    } catch (error) {
      setActionError(
        error instanceof ApiError && error.status === 409
          ? error.message
          : "安装失败，请重试。",
      );
    } finally {
      setBusy(false);
    }
  };

  const handleUninstall = async () => {
    if (!selectedSkill) {
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await uninstallSkill(selectedSkill.name);
      closeDialog();
      await loadSkills();
    } catch {
      setActionError("卸载失败，请重试。");
    } finally {
      setBusy(false);
    }
  };

  const resolution = resolveResourcePageState({
    loading,
    hasContent: skills.length > 0,
    connected: true,
    error: listError,
  });
  const state = resolution.owner === "surface" ? resolution.state.kind : null;

  return (
    <main className="f149-skills-page">
      <header className="f149-skills-hero">
        <div>
          <p className="f149-skills-kicker">Skills</p>
          <h1>让常用工作，成为可复用的能力。</h1>
          <p>
            浏览已安装的技能，了解它们能做什么；需要新能力时，从 SKILL.md 安装。
          </p>
        </div>
        <div className="f149-skills-hero-actions">
          {state === "ready" ? (
            <button
              type="button"
              className="f149-skills-button f149-skills-button-primary"
              data-focus-return="skill-install"
              onClick={(event) => openInstall(event.currentTarget)}
            >
              安装 Skill
            </button>
          ) : null}
          <Link to="/agents" className="f149-skills-link">
            返回 Agents
          </Link>
        </div>
      </header>

      <section className="f149-skills-library" aria-labelledby="skills-title">
        <header className="f149-skills-library-head">
          <div>
            <p className="f149-skills-kicker">技能库</p>
            <h2 id="skills-title">已安装</h2>
          </div>
          {state === "ready" ? <strong>共 {skills.length} 个</strong> : null}
        </header>

        {state === "loading" ? (
          <section className="f149-skills-state" role="status">
            <span className="f149-skills-loader" aria-hidden="true" />
            <strong>正在加载技能…</strong>
          </section>
        ) : null}

        {state === "empty" ? (
          <section className="f149-skills-state">
            <strong>还没有安装技能</strong>
            <p>从一份 SKILL.md 开始，把重复工作变成随时可用的能力。</p>
            <button
              type="button"
              className="f149-skills-button f149-skills-button-primary"
              data-focus-return="skill-install"
              onClick={(event) => openInstall(event.currentTarget)}
            >
              安装 Skill
            </button>
          </section>
        ) : null}

        {state === "recoverable-error" ? (
          <section className="f149-skills-state" role="alert">
            <strong>技能列表加载失败</strong>
            <p>暂时无法取得技能列表，请稍后再试。</p>
            <button
              type="button"
              className="f149-skills-button f149-skills-button-primary"
              onClick={() => void loadSkills()}
            >
              重试
            </button>
          </section>
        ) : null}

        {state === "permission-denied" ? (
          <section className="f149-skills-state" role="alert">
            <strong>当前账号没有权限管理技能</strong>
            <p>如需安装或卸载技能，请联系管理员调整资源权限。</p>
          </section>
        ) : null}

        {state === "ready" ? (
          <div className="f149-skills-grid">
            {skills.map((skill) => (
              <SkillCard
                key={`${skill.source}:${skill.name}`}
                skill={skill}
                onDetail={(item, trigger) => void openDetail(item, trigger)}
                onUninstall={openUninstall}
              />
            ))}
          </div>
        ) : null}

        {actionError && dialog === null ? (
          <p className="f149-skills-action-error" role="alert">
            {actionError}
          </p>
        ) : null}
      </section>

      {dialog === "detail" && detail ? (
        <DetailDialog detail={detail} onClose={closeDialog} />
      ) : null}
      {dialog === "install" ? (
        <InstallDialog
          busy={busy}
          error={actionError}
          onClose={closeDialog}
          onInstall={handleInstall}
        />
      ) : null}
      {dialog === "uninstall" && selectedSkill ? (
        <UninstallDialog
          skill={selectedSkill}
          busy={busy}
          error={actionError}
          onClose={closeDialog}
          onConfirm={handleUninstall}
        />
      ) : null}
    </main>
  );
}
