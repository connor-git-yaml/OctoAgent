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

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  approveWorkspaceRollback,
  fetchWorkspaceBlame,
  fetchWorkspaceCommitFiles,
  fetchWorkspaceDiff,
  fetchWorkspaceHistory,
  fetchWorkspaceProjects,
  proposeWorkspaceRollback,
  rejectWorkspaceRollback,
  type WorkspaceBlameLine,
  type WorkspaceCommit,
  type WorkspaceFileChange,
  type WorkspaceProjectItem,
} from "../api/client";
import { DiffBody } from "../components/diff/DiffBody";
import { presentAdvancedValue } from "../domains/shared/resourcePageState";
import type { DiffResponse } from "../types";
import FilesAdvancedInfo from "./FilesAdvancedInfo";

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

function fileStatusLabel(status: string): string {
  return (
    {
      added: "新文件",
      deleted: "已删除",
      modified: "已修改",
      renamed: "已重命名",
    }[status] ?? "有变化"
  );
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
  const [filesError, setFilesError] = useState(false);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [diffError, setDiffError] = useState(false);
  const [rollback, setRollback] = useState<{ commit: string } | null>(null);
  const [pendingRollback, setPendingRollback] = useState<{
    requestId: string;
  } | null>(null);
  const [rollbackMsg, setRollbackMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const rollbackTriggerRef = useRef<HTMLButtonElement | null>(null);
  const fileTriggerRef = useRef<HTMLButtonElement | null>(null);

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
    setFilesError(false);
    try {
      const resp = await fetchWorkspaceCommitFiles(
        activeSlug,
        commits[idx].commit,
      );
      setFiles(resp.files);
    } catch {
      setFiles([]);
      setFilesError(true);
    }
  };

  const openFile = async (path: string) => {
    if (selectedIdx === null || activeSlug == null) return;
    setSelectedFile(path);
    setDiffError(false);
    const commitA = commits[selectedIdx].commit;
    const commitB = commits[selectedIdx + 1]?.commit; // 次新（父）作上一版
    try {
      const d = await fetchWorkspaceDiff({
        project_slug: activeSlug,
        commit_a: commitA,
        commit_b: commitB,
        path,
      });
      setDiff(d);
    } catch {
      setDiff(null);
      setDiffError(true);
    }
  };

  const loadBlame = async (): Promise<WorkspaceBlameLine[]> => {
    if (selectedIdx === null || selectedFile === null || activeSlug == null) {
      return [];
    }
    const resp = await fetchWorkspaceBlame(
      activeSlug,
      commits[selectedIdx].commit,
      selectedFile,
    );
    return resp.lines;
  };

  const submitRollback = async () => {
    if (rollback === null || activeSlug == null) return;
    setBusy(true);
    setRollbackMsg(null);
    try {
      const proposal = await proposeWorkspaceRollback({
        project_slug: activeSlug,
        target_commit: rollback.commit,
      });
      setPendingRollback({
        requestId: proposal.request_id,
      });
      setRollback(null);
      window.requestAnimationFrame(() => rollbackTriggerRef.current?.focus());
    } catch {
      setRollbackMsg("回滚申请提交失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  const decideRollback = async (decision: "approve" | "reject") => {
    if (!pendingRollback) return;
    setBusy(true);
    setRollbackMsg(null);
    try {
      if (decision === "approve") {
        const result = await approveWorkspaceRollback(
          pendingRollback.requestId,
        );
        setRollbackMsg(
          result.status === "executed" ? "已恢复到此版本" : "回滚申请尚未完成",
        );
        await loadHistory();
      } else {
        await rejectWorkspaceRollback(pendingRollback.requestId);
        setRollbackMsg("已拒绝回滚申请");
      }
      setPendingRollback(null);
    } catch (error) {
      const status =
        error && typeof error === "object" && "status" in error
          ? Number(error.status)
          : 0;
      setRollbackMsg(
        status === 409 ? "回滚目标已变化，请重新发起" : "回滚处理失败，请重试",
      );
      if (status === 409) setPendingRollback(null);
    } finally {
      setBusy(false);
    }
  };

  const closeRollbackDialog = useCallback(() => {
    setRollback(null);
    window.requestAnimationFrame(() => rollbackTriggerRef.current?.focus());
  }, []);

  useEffect(() => {
    if (!rollback) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") closeRollbackDialog();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [closeRollbackDialog, rollback]);

  const closeDiff = () => {
    setSelectedFile(null);
    setDiff(null);
    setDiffError(false);
    window.requestAnimationFrame(() => fileTriggerRef.current?.focus());
  };

  if (resolving || loading) {
    return (
      <div className="f149-files-state">
        <span>正在加载工作区版本历史…</span>
      </div>
    );
  }
  if (!available) {
    return (
      <div className="f149-files-state">
        <strong>工作区版本历史暂不可用</strong>
        <span>此功能需要系统安装 git。其它功能不受影响。</span>
      </div>
    );
  }
  if (activeSlug == null || (commits.length === 0 && projects.length === 0)) {
    return (
      <div className="f149-files-state">
        <strong>暂无工作区版本历史</strong>
        <span>当 Agent 在工作区里改动文件后，这里会显示历史版本。</span>
      </div>
    );
  }

  return (
    <section className="f149-workspace" aria-label="工作区版本历史">
      <div className="f149-workspace-head">
        <div>
          <p className="f149-files-kicker">工作区版本</p>
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
        <div className="f149-files-state">
          <span>此项目暂无历史版本。</span>
        </div>
      ) : (
        <div className="f149-workspace-history">
          {commits.map((c, idx) => (
            <article key={c.commit} className="f149-workspace-commit">
              <button
                type="button"
                className="f149-workspace-commit-open"
                onClick={() => openCommit(idx)}
              >
                <strong>{c.summary || "（无说明）"}</strong>
                <small>
                  {fmtTs(c.ts)} · {c.files_changed} 个文件有变化
                </small>
              </button>
              <button
                type="button"
                className="f149-workspace-restore"
                onClick={(event) => {
                  rollbackTriggerRef.current = event.currentTarget;
                  setRollback({ commit: c.commit });
                  setRollbackMsg(null);
                }}
              >
                恢复到此版本
              </button>
            </article>
          ))}
        </div>
      )}

      {selectedIdx !== null && (
        <div className="f149-workspace-files">
          <p className="f149-files-kicker">改了哪些文件</p>
          {filesError ? (
            <div className="f149-files-state" role="alert">
              <strong>文件列表加载失败</strong>
              <span>暂时无法取得这个版本的文件，请重试。</span>
            </div>
          ) : files.length === 0 ? (
            <span>（无文件改动）</span>
          ) : (
            <div className="f149-workspace-file-list">
              {files.map((file) => {
                const path = presentAdvancedValue({
                  value: file.path,
                  kind: "path",
                  sensitivity: "operator_sensitive",
                  sanitized: true,
                  copyPermitted: false,
                  maxDisplayLength: 44,
                });
                return (
                  <button
                    key={file.path}
                    type="button"
                    onClick={(event) => {
                      fileTriggerRef.current = event.currentTarget;
                      void openFile(file.path);
                    }}
                  >
                    <span>{path.displayValue}</span>
                    <small>{fileStatusLabel(file.status)}</small>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {selectedFile !== null && diffError && (
        <div className="f149-files-state" role="alert">
          <strong>文件对比加载失败</strong>
          <span>暂时无法显示这个文件的变化。</span>
          <button type="button" onClick={() => void openFile(selectedFile)}>
            重试
          </button>
        </div>
      )}

      {selectedFile !== null && diff !== null && (
        <section className="f149-workspace-diff" aria-label="文件版本对比">
          <header>
            <div>
              <p className="f149-files-kicker">只读对比</p>
              <h3>
                {
                  presentAdvancedValue({
                    value: selectedFile,
                    kind: "path",
                    sensitivity: "operator_sensitive",
                    sanitized: true,
                    copyPermitted: false,
                    maxDisplayLength: 44,
                  }).displayValue
                }
              </h3>
            </div>
            <button type="button" onClick={closeDiff}>
              返回文件列表
            </button>
          </header>
          {diff.binary ? (
            <span>（二进制文件，不显示内容）</span>
          ) : diff.oversize ? (
            <span>（文件较大，不在此处显示内容）</span>
          ) : (
            <DiffBody diff={diff} />
          )}
          <p className="f149-workspace-readonly">
            对比只读；需要修改内容，请回到对话让助手更新。
          </p>
          {selectedIdx !== null && (
            <FilesAdvancedInfo
              commit={commits[selectedIdx].commit}
              path={selectedFile}
              onLoadBlame={loadBlame}
            />
          )}
        </section>
      )}

      {pendingRollback && (
        <section
          className="f149-workspace-pending"
          aria-label="待批准的回滚申请"
        >
          <div>
            <strong>回滚申请 · 待批准</strong>
            <span>批准后才会执行，期间文件保持只读。</span>
          </div>
          <div>
            <button
              type="button"
              disabled={busy}
              onClick={() => void decideRollback("approve")}
            >
              批准
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void decideRollback("reject")}
            >
              拒绝
            </button>
          </div>
        </section>
      )}
      {rollbackMsg && (
        <div className="f149-workspace-message" role="status">
          <span>{rollbackMsg}</span>
        </div>
      )}

      {rollback && document.body
        ? createPortal(
            <div
              className="f149-files-dialog-backdrop"
              onMouseDown={(event) => {
                if (event.target === event.currentTarget) closeRollbackDialog();
              }}
            >
              <section
                className="f149-files-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="files-rollback-title"
              >
                <header>
                  <div>
                    <p>需要确认</p>
                    <h2 id="files-rollback-title">提交回滚申请</h2>
                  </div>
                  <button type="button" onClick={closeRollbackDialog}>
                    关闭
                  </button>
                </header>
                <p>
                  这会提交一份回滚申请。申请获批后，系统才会把工作区恢复到所选版本。
                </p>
                <div className="f149-files-dialog-actions">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void submitRollback()}
                  >
                    {busy ? "正在提交…" : "提交申请"}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={closeRollbackDialog}
                  >
                    取消
                  </button>
                </div>
              </section>
            </div>,
            document.body,
          )
        : null}
    </section>
  );
}
