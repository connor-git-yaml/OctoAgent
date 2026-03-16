/**
 * SkillCenter -- Feature 057 Skill 管理页面
 *
 * 替代旧的 SkillProviderCenter，展示 SKILL.md 驱动的 Skill 卡片列表。
 * 支持查看详情、安装新 Skill、卸载用户 Skill。
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { SkillDeleteResponse, SkillDetail, SkillInstallResponse, SkillItem, SkillListResponse } from "../types";

// ============================================================
// 数据获取
// ============================================================

async function fetchSkills(): Promise<SkillListResponse> {
  const resp = await fetch("/api/skills");
  if (!resp.ok) throw new Error(`获取 Skill 列表失败: ${resp.status}`);
  return resp.json();
}

async function fetchSkillDetail(name: string): Promise<SkillDetail> {
  const resp = await fetch(`/api/skills/${encodeURIComponent(name)}`);
  if (!resp.ok) throw new Error(`获取 Skill 详情失败: ${resp.status}`);
  return resp.json();
}

async function installSkill(
  name: string,
  content: string
): Promise<SkillInstallResponse> {
  const resp = await fetch("/api/skills", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, content }),
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail ?? `安装失败: ${resp.status}`);
  }
  return resp.json();
}

async function uninstallSkill(name: string): Promise<SkillDeleteResponse> {
  const resp = await fetch(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail ?? `卸载失败: ${resp.status}`);
  }
  return resp.json();
}

// ============================================================
// 来源标记颜色
// ============================================================

function sourceBadge(source: SkillItem["source"]): string {
  switch (source) {
    case "builtin":
      return "内置";
    case "user":
      return "用户";
    case "project":
      return "项目";
    default:
      return source;
  }
}

// ============================================================
// 卡片组件
// ============================================================

function SkillCard({
  skill,
  onSelect,
}: {
  skill: SkillItem;
  onSelect: (name: string) => void;
}) {
  return (
    <button
      type="button"
      className="wb-card"
      style={{ textAlign: "left", cursor: "pointer" }}
      onClick={() => onSelect(skill.name)}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <strong>{skill.name}</strong>
        <span className="wb-chip">{sourceBadge(skill.source)}</span>
      </div>
      <p style={{ margin: "0.25rem 0 0.5rem", opacity: 0.8, fontSize: "0.9rem" }}>
        {skill.description}
      </p>
      {skill.tags.length > 0 && (
        <div className="wb-chip-row">
          {skill.tags.slice(0, 5).map((tag) => (
            <span key={tag} className="wb-chip">
              {tag}
            </span>
          ))}
        </div>
      )}
      {skill.version && (
        <span style={{ fontSize: "0.8rem", opacity: 0.6 }}>v{skill.version}</span>
      )}
    </button>
  );
}

// ============================================================
// 详情 Modal
// ============================================================

function SkillDetailModal({
  detail,
  onClose,
  onUninstall,
  busy,
}: {
  detail: SkillDetail;
  onClose: () => void;
  onUninstall: (name: string) => void;
  busy: boolean;
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div
      className="wb-modal-overlay"
      role="dialog"
      aria-modal="true"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        className="wb-panel"
        style={{
          maxWidth: 640,
          maxHeight: "80vh",
          overflow: "auto",
          background: "var(--wb-surface, #fff)",
          borderRadius: 8,
          padding: "1.5rem",
          position: "relative",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="wb-panel-head">
          <div>
            <p className="wb-card-label">{sourceBadge(detail.source)}</p>
            <h3>{detail.name}</h3>
          </div>
          <button type="button" className="wb-button wb-button-tertiary" onClick={onClose}>
            关闭
          </button>
        </div>

        <p>{detail.description}</p>

        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", margin: "0.75rem 0" }}>
          {detail.version && <span className="wb-chip">v{detail.version}</span>}
          {detail.author && <span className="wb-chip">{detail.author}</span>}
          {detail.tags.map((tag) => (
            <span key={tag} className="wb-chip">
              {tag}
            </span>
          ))}
        </div>

        {detail.trigger_patterns.length > 0 && (
          <div style={{ margin: "0.5rem 0" }}>
            <strong style={{ fontSize: "0.85rem" }}>触发模式</strong>
            <div className="wb-chip-row">
              {detail.trigger_patterns.map((pat) => (
                <span key={pat} className="wb-chip">
                  {pat}
                </span>
              ))}
            </div>
          </div>
        )}

        {detail.tools_required.length > 0 && (
          <div style={{ margin: "0.5rem 0" }}>
            <strong style={{ fontSize: "0.85rem" }}>依赖工具</strong>
            <div className="wb-chip-row">
              {detail.tools_required.map((tool) => (
                <span key={tool} className="wb-chip">
                  {tool}
                </span>
              ))}
            </div>
          </div>
        )}

        {detail.content && (
          <div style={{ margin: "1rem 0" }}>
            <strong style={{ fontSize: "0.85rem" }}>Skill 指令内容</strong>
            <pre
              style={{
                marginTop: "0.5rem",
                padding: "1rem",
                background: "var(--wb-surface-alt, #f5f5f5)",
                borderRadius: 6,
                overflow: "auto",
                maxHeight: 300,
                fontSize: "0.85rem",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {detail.content}
            </pre>
          </div>
        )}

        {detail.source === "user" && (
          <div className="wb-inline-actions" style={{ marginTop: "1rem" }}>
            <button
              type="button"
              className="wb-button wb-button-tertiary"
              onClick={() => {
                if (window.confirm(`确认卸载 Skill "${detail.name}"？此操作不可恢复。`)) {
                  onUninstall(detail.name);
                }
              }}
              disabled={busy}
            >
              {busy ? "卸载中..." : "卸载此 Skill"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================
// 安装 Modal
// ============================================================

function InstallModal({
  onClose,
  onInstall,
  busy,
}: {
  onClose: () => void;
  onInstall: (name: string, content: string) => void;
  busy: boolean;
}) {
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = reader.result as string;
      setContent(text);
      // 尝试从 frontmatter 提取 name
      const match = text.match(/^---[\s\S]*?name:\s*(.+?)[\s\r\n]/m);
      if (match) {
        setName(match[1].trim());
      }
    };
    reader.onerror = () => setError("文件读取失败，请重试");
    reader.readAsText(file);
  }

  function handleSubmit() {
    if (!name.trim()) {
      setError("请输入 Skill 名称");
      return;
    }
    // 客户端 kebab-case 校验（与后端正则一致）
    if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(name.trim()) || name.trim().length > 64) {
      setError("名称仅支持小写字母、数字和连字符（kebab-case），长度 1-64 字符");
      return;
    }
    if (!content.trim()) {
      setError("请上传或粘贴 SKILL.md 内容");
      return;
    }
    setError("");
    onInstall(name.trim(), content);
  }

  return (
    <div
      className="wb-modal-overlay"
      role="dialog"
      aria-modal="true"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.5)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        className="wb-panel"
        style={{
          maxWidth: 560,
          background: "var(--wb-surface, #fff)",
          borderRadius: 8,
          padding: "1.5rem",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="wb-panel-head">
          <h3>安装新 Skill</h3>
          <button type="button" className="wb-button wb-button-tertiary" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="wb-agent-form-grid" style={{ gap: "1rem" }}>
          <label className="wb-field">
            <span>Skill 名称 (kebab-case)</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如 my-custom-skill"
            />
          </label>

          <label className="wb-field">
            <span>上传 SKILL.md 文件</span>
            <input type="file" accept=".md" onChange={handleFileUpload} />
          </label>

          <label className="wb-field">
            <span>或粘贴 SKILL.md 内容</span>
            <textarea
              rows={10}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="---&#10;name: my-custom-skill&#10;description: ...&#10;---&#10;&#10;# My Skill&#10;..."
              style={{ fontFamily: "monospace", fontSize: "0.85rem" }}
            />
          </label>
        </div>

        {error && (
          <div className="wb-inline-banner is-warning" style={{ marginTop: "0.75rem" }}>
            <span>{error}</span>
          </div>
        )}

        <div className="wb-inline-actions" style={{ marginTop: "1rem" }}>
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={handleSubmit}
            disabled={busy}
          >
            {busy ? "安装中..." : "安装"}
          </button>
          <button type="button" className="wb-button wb-button-secondary" onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// 主页面
// ============================================================

export default function SkillCenter() {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedDetail, setSelectedDetail] = useState<SkillDetail | null>(null);
  const [showInstall, setShowInstall] = useState(false);
  const [busy, setBusy] = useState(false);

  const loadSkills = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchSkills();
      setSkills(data.items);
      setTotal(data.total);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSkills();
  }, [loadSkills]);

  async function handleSelect(name: string) {
    try {
      setBusy(true);
      setError("");
      const detail = await fetchSkillDetail(name);
      setSelectedDetail(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "获取详情失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleUninstall(name: string) {
    try {
      setBusy(true);
      setError("");
      await uninstallSkill(name);
      setSelectedDetail(null);
      await loadSkills();
    } catch (err) {
      setError(err instanceof Error ? err.message : "卸载失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleInstall(name: string, content: string) {
    try {
      setBusy(true);
      setError("");
      await installSkill(name, content);
      setShowInstall(false);
      await loadSkills();
    } catch (err) {
      setError(err instanceof Error ? err.message : "安装失败");
    } finally {
      setBusy(false);
    }
  }

  const builtinCount = skills.filter((s) => s.source === "builtin").length;
  const userCount = skills.filter((s) => s.source === "user").length;
  const projectCount = skills.filter((s) => s.source === "project").length;

  return (
    <div className="wb-page">
      <section className="wb-hero wb-hero-compact">
        <div className="wb-hero-copy">
          <p className="wb-kicker">Skills</p>
          <h1>Skill 管理中心</h1>
          <p>
            通过 SKILL.md 文件定义的可加载指令集。在 Chat 中输入"列出可用 Skill"即可让 AI
            自动发现和加载。
          </p>
          <div className="wb-chip-row">
            <span className="wb-chip">共 {total} 个</span>
            <span className="wb-chip">内置 {builtinCount}</span>
            {userCount > 0 && <span className="wb-chip">用户 {userCount}</span>}
            {projectCount > 0 && <span className="wb-chip">项目 {projectCount}</span>}
          </div>
        </div>
        <div className="wb-hero-actions">
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={() => setShowInstall(true)}
          >
            安装 Skill
          </button>
          <Link className="wb-button wb-button-secondary" to="/agents">
            返回 Agents
          </Link>
        </div>
      </section>

      {error && (
        <div className="wb-inline-banner is-warning">
          <span>{error}</span>
          <button type="button" className="wb-button wb-button-tertiary" onClick={() => setError("")}>
            关闭
          </button>
        </div>
      )}

      {loading ? (
        <div className="wb-empty-state">
          <span>正在加载 Skill 列表...</span>
        </div>
      ) : skills.length === 0 ? (
        <div className="wb-empty-state">
          <strong>还没有可用的 Skill</strong>
          <span>可以点击"安装 Skill"上传自定义的 SKILL.md 文件。</span>
        </div>
      ) : (
        <section className="wb-card-grid wb-card-grid-3">
          {skills.map((skill) => (
            <SkillCard key={skill.name} skill={skill} onSelect={handleSelect} />
          ))}
        </section>
      )}

      {selectedDetail && (
        <SkillDetailModal
          detail={selectedDetail}
          onClose={() => setSelectedDetail(null)}
          onUninstall={handleUninstall}
          busy={busy}
        />
      )}

      {showInstall && (
        <InstallModal
          onClose={() => setShowInstall(false)}
          onInstall={handleInstall}
          busy={busy}
        />
      )}
    </div>
  );
}
