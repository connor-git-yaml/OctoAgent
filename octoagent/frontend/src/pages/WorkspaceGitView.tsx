/**
 * F107 W2-D：Files Tab workspace 真 git 视图（浏览历史 + 文件 diff + blame + 回滚）。
 *
 * 平实 UX（SD-8）：版本历史 / 改了哪些文件 / 谁改的 / 恢复到此版本；commit hash 归 Advanced。
 * 复用共享 DiffBody（FR-S-1）。回滚 Two-Phase（propose→确认→approve，SD-10 仅文件态）。
 * git 不可用 → available=false 友好占位（#6 降级）。
 *
 * 项目解析（Opus W2-H1 修复）：不再写死 "default"——经 /projects 列出有历史的项目，下拉切换，
 * 默认选最近提交的项目；slug 即工具写快照的归一化目录名，与后端 _worktree 同款解析一致。
 */

import { useCallback, useEffect, useState } from "react";
import {
  approveWorkspaceRollback,
  fetchWorkspaceBlame,
  fetchWorkspaceCommitFiles,
  fetchWorkspaceDiff,
  fetchWorkspaceHistory,
  fetchWorkspaceProjects,
  proposeWorkspaceRollback,
  type WorkspaceBlameLine,
  type WorkspaceCommit,
  type WorkspaceFileChange,
  type WorkspaceProjectItem,
} from "../api/client";
import { DiffBody } from "../components/diff/DiffBody";
import type { DiffResponse } from "../types";

function fmtTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function WorkspaceGitView(props: { projectSlug?: string }) {
  const [projects, setProjects] = useState<WorkspaceProjectItem[]>([]);
  const [activeSlug, setActiveSlug] = useState<string | null>(
    props.projectSlug ?? null,
  );
  const [resolving, setResolving] = useState(props.projectSlug == null);
  const [available, setAvailable] = useState(true);
  const [commits, setCommits] = useState<WorkspaceCommit[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [files, setFiles] = useState<WorkspaceFileChange[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [blame, setBlame] = useState<WorkspaceBlameLine[] | null>(null);
  const [rollback, setRollback] = useState<{ commit: string } | null>(null);
  const [rollbackMsg, setRollbackMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 无显式 prop → 解析有历史的项目列表，默认选第一个（最近提交）
  useEffect(() => {
    if (props.projectSlug != null) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetchWorkspaceProjects();
        if (cancelled) return;
        setAvailable(resp.available);
        setProjects(resp.projects);
        setActiveSlug(resp.projects[0]?.slug ?? null);
      } catch {
        if (!cancelled) setProjects([]);
      } finally {
        if (!cancelled) setResolving(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [props.projectSlug]);

  const loadHistory = useCallback(async () => {
    if (activeSlug == null) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setSelectedIdx(null);
    setSelectedFile(null);
    setDiff(null);
    try {
      const resp = await fetchWorkspaceHistory(activeSlug);
      setAvailable(resp.available);
      setCommits(resp.commits);
    } catch {
      setCommits([]);
    } finally {
      setLoading(false);
    }
  }, [activeSlug]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const openCommit = async (idx: number) => {
    if (activeSlug == null) return;
    setSelectedIdx(idx);
    setSelectedFile(null);
    setDiff(null);
    setBlame(null);
    const resp = await fetchWorkspaceCommitFiles(activeSlug, commits[idx].commit);
    setFiles(resp.files);
  };

  const openFile = async (path: string) => {
    if (selectedIdx === null || activeSlug == null) return;
    setSelectedFile(path);
    setBlame(null);
    const commitA = commits[selectedIdx].commit;
    const commitB = commits[selectedIdx + 1]?.commit; // 次新（父）作上一版
    const d = await fetchWorkspaceDiff({
      project_slug: activeSlug,
      commit_a: commitA,
      commit_b: commitB,
      path,
    });
    setDiff(d);
  };

  const showBlame = async () => {
    if (selectedIdx === null || selectedFile === null || activeSlug == null) return;
    const resp = await fetchWorkspaceBlame(
      activeSlug,
      commits[selectedIdx].commit,
      selectedFile,
    );
    setBlame(resp.lines);
  };

  const confirmRollback = async () => {
    if (rollback === null || activeSlug == null) return;
    setBusy(true);
    setRollbackMsg(null);
    try {
      const proposal = await proposeWorkspaceRollback({
        project_slug: activeSlug,
        target_commit: rollback.commit,
      });
      const result = await approveWorkspaceRollback(proposal.request_id);
      setRollbackMsg(
        result.status === "executed" ? "已恢复到此版本" : `回滚未完成：${result.status}`,
      );
      setRollback(null);
      await loadHistory();
    } catch (e) {
      setRollbackMsg(e instanceof Error ? e.message : "回滚失败");
    } finally {
      setBusy(false);
    }
  };

  if (resolving || loading) {
    return (
      <div className="wb-note">
        <span>正在加载工作区版本历史…</span>
      </div>
    );
  }
  if (!available) {
    return (
      <div className="wb-empty-state">
        <strong>工作区版本历史暂不可用</strong>
        <span>此功能需要系统安装 git。其它功能不受影响。</span>
      </div>
    );
  }
  if (activeSlug == null || (commits.length === 0 && projects.length === 0)) {
    return (
      <div className="wb-empty-state">
        <strong>暂无工作区版本历史</strong>
        <span>当 Agent 在工作区里改动文件后，这里会显示历史版本。</span>
      </div>
    );
  }

  return (
    <section className="wb-panel" aria-label="工作区版本历史">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">工作区版本历史</p>
          {projects.length > 1 ? (
            <select
              aria-label="选择项目"
              value={activeSlug}
              onChange={(e) => setActiveSlug(e.target.value)}
            >
              {projects.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.name}
                </option>
              ))}
            </select>
          ) : (
            <h3>{projects[0]?.name ?? activeSlug}</h3>
          )}
        </div>
      </div>

      {commits.length === 0 ? (
        <div className="wb-note">
          <span>此项目暂无历史版本。</span>
        </div>
      ) : (
        <div className="wb-note-stack">
          {commits.map((c, idx) => (
            <div key={c.commit} className="wb-agent-tool-row">
              <button
                type="button"
                className="wb-chip"
                style={{ textAlign: "left", flex: 1 }}
                onClick={() => openCommit(idx)}
              >
                <strong>{c.summary || "（无说明）"}</strong>
                <small style={{ display: "block", color: "var(--cp-muted)" }}>
                  {fmtTs(c.ts)} · <span title={c.commit}>{c.short}</span>
                </small>
              </button>
              <button
                type="button"
                className="wb-chip"
                onClick={() => {
                  setRollback({ commit: c.commit });
                  setRollbackMsg(null);
                }}
              >
                恢复到此版本
              </button>
            </div>
          ))}
        </div>
      )}

      {selectedIdx !== null && (
        <div className="wb-card">
          <p className="wb-card-label">改了哪些文件</p>
          {files.length === 0 ? (
            <span>（无文件改动）</span>
          ) : (
            <div className="wb-agent-check-grid">
              {files.map((f) => (
                <button
                  key={f.path}
                  type="button"
                  className="wb-chip"
                  onClick={() => openFile(f.path)}
                >
                  {f.path} · {f.status}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {selectedFile !== null && diff !== null && (
        <div className="wb-card">
          <div className="wb-panel-head">
            <p className="wb-card-label">{selectedFile}</p>
            <button type="button" className="wb-chip" onClick={showBlame}>
              谁改的
            </button>
          </div>
          {diff.binary ? (
            <span>（二进制文件，不显示内容）</span>
          ) : diff.oversize ? (
            <span>（文件较大，不在此处显示内容）</span>
          ) : (
            <DiffBody diff={diff} />
          )}
          {blame !== null && (
            <details open>
              <summary>逐行修改记录</summary>
              <div style={{ fontFamily: "monospace", fontSize: "12px" }}>
                {blame.map((ln) => (
                  <div key={ln.line_no}>
                    <span style={{ color: "var(--cp-muted)" }} title={ln.commit}>
                      {ln.short} {fmtTs(ln.ts)}
                    </span>{" "}
                    {ln.content}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      {rollback !== null && (
        <div className="wb-inline-banner is-warning">
          <span>将把工作区文件恢复到此版本（会记为一次新的版本）。确认吗？</span>
          <div style={{ display: "flex", gap: "var(--space-sm)" }}>
            <button type="button" className="wb-chip" disabled={busy} onClick={confirmRollback}>
              {busy ? "恢复中…" : "确认恢复"}
            </button>
            <button
              type="button"
              className="wb-chip"
              disabled={busy}
              onClick={() => setRollback(null)}
            >
              取消
            </button>
          </div>
        </div>
      )}
      {rollbackMsg && (
        <div className="wb-note">
          <span>{rollbackMsg}</span>
        </div>
      )}
    </section>
  );
}
