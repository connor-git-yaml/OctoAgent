/**
 * F107 W2-D：Files Tab workspace 真 git 视图（浏览历史 + 文件 diff + blame + 回滚）。
 *
 * 平实 UX（SD-8）：版本历史 / 改了哪些文件 / 谁改的 / 恢复到此版本；commit hash 归 Advanced。
 * 复用共享 DiffBody（FR-S-1）。回滚 Two-Phase（propose→确认→approve，SD-10 仅文件态）。
 * git 不可用 → available=false 友好占位（#6 降级）。
 */

import { useCallback, useEffect, useState } from "react";
import {
  approveWorkspaceRollback,
  fetchWorkspaceBlame,
  fetchWorkspaceCommitFiles,
  fetchWorkspaceDiff,
  fetchWorkspaceHistory,
  proposeWorkspaceRollback,
  type WorkspaceBlameLine,
  type WorkspaceCommit,
  type WorkspaceFileChange,
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

export default function WorkspaceGitView(props: { projectSlug: string }) {
  const { projectSlug } = props;
  const [commits, setCommits] = useState<WorkspaceCommit[]>([]);
  const [available, setAvailable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [files, setFiles] = useState<WorkspaceFileChange[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [blame, setBlame] = useState<WorkspaceBlameLine[] | null>(null);
  const [rollback, setRollback] = useState<{ commit: string; requestId?: string } | null>(
    null,
  );
  const [rollbackMsg, setRollbackMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadHistory = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetchWorkspaceHistory(projectSlug);
      setAvailable(resp.available);
      setCommits(resp.commits);
    } catch {
      setCommits([]);
    } finally {
      setLoading(false);
    }
  }, [projectSlug]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const openCommit = async (idx: number) => {
    setSelectedIdx(idx);
    setSelectedFile(null);
    setDiff(null);
    setBlame(null);
    const resp = await fetchWorkspaceCommitFiles(projectSlug, commits[idx].commit);
    setFiles(resp.files);
  };

  const openFile = async (path: string) => {
    if (selectedIdx === null) return;
    setSelectedFile(path);
    setBlame(null);
    const commitA = commits[selectedIdx].commit;
    const commitB = commits[selectedIdx + 1]?.commit; // 次新（父）作上一版
    const d = await fetchWorkspaceDiff({
      project_slug: projectSlug,
      commit_a: commitA,
      commit_b: commitB,
      path,
    });
    setDiff(d);
  };

  const showBlame = async () => {
    if (selectedIdx === null || selectedFile === null) return;
    const resp = await fetchWorkspaceBlame(
      projectSlug,
      commits[selectedIdx].commit,
      selectedFile,
    );
    setBlame(resp.lines);
  };

  const confirmRollback = async () => {
    if (rollback === null) return;
    setBusy(true);
    setRollbackMsg(null);
    try {
      const proposal = await proposeWorkspaceRollback({
        project_slug: projectSlug,
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

  if (loading) {
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
  if (commits.length === 0) {
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
          <h3>{projectSlug}</h3>
        </div>
      </div>

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

      {selectedFile !== null && diff !== null && diff.current && (
        <div className="wb-card">
          <div className="wb-panel-head">
            <p className="wb-card-label">{selectedFile}</p>
            <button type="button" className="wb-chip" onClick={showBlame}>
              谁改的
            </button>
          </div>
          <DiffBody diff={diff} />
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
