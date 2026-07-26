/**
 * F107 W1-D：Agent 中心 behavior 文件版本历史面板。
 *
 * 平实 UX（SD-8）：版本历史 / 上一版 / 恢复到此版本；原始技术字段（hash/size）归 Advanced。
 * 复用共享 DiffBody（FR-S-1）渲染任意两版 diff。恢复经 control_plane behavior.restore_version
 * Two-Phase（先 proposal 预览，确认后写入并记为新版，SD-6 守 #4/#7）。
 */

import { useCallback, useEffect, useState } from "react";
import {
  fetchBehaviorVersionDiff,
  fetchBehaviorVersions,
  type BehaviorVersionKeyParams,
  type BehaviorVersionMetaItem,
} from "../../api/client";
import { DiffBody } from "../../components/diff/DiffBody";
import { executeWorkbenchAction } from "../../platform/actions/controlPlaneActions";
import { executeF149Action } from "../../platform/actions/f149Actions";
import type { DiffResponse } from "../../types";

interface BehaviorVersionHistoryProps {
  fileId: string;
  scope?: string;
  agentSlug?: string;
  projectSlug?: string;
  onClose: () => void;
}

function formatTs(iso: string): string {
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

export default function BehaviorVersionHistory({
  fileId,
  scope,
  agentSlug,
  projectSlug,
  onClose,
}: BehaviorVersionHistoryProps) {
  const keyParams: BehaviorVersionKeyParams = {
    file_id: fileId,
    scope,
    agent_slug: agentSlug,
    project_slug: projectSlug,
  };

  const [versions, setVersions] = useState<BehaviorVersionMetaItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 对比的两版：newer（当前）+ older（上一版）
  const [newerNo, setNewerNo] = useState<number | null>(null);
  const [olderNo, setOlderNo] = useState<number | null>(null);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  // 恢复 Two-Phase：restoreTarget 非空即 proposal 待确认
  const [restoreTarget, setRestoreTarget] = useState<number | null>(null);
  const [restoreBusy, setRestoreBusy] = useState(false);
  const [restoreMsg, setRestoreMsg] = useState<string | null>(null);

  const loadVersions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetchBehaviorVersions(keyParams);
      setVersions(resp.versions);
      if (resp.versions.length >= 2) {
        setNewerNo(resp.versions[0].version_no);
        setOlderNo(resp.versions[1].version_no);
      } else if (resp.versions.length === 1) {
        setNewerNo(resp.versions[0].version_no);
        setOlderNo(null);
      } else {
        setNewerNo(null);
        setOlderNo(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载版本历史失败");
    } finally {
      setLoading(false);
    }
    // keyParams 由 fileId/scope/agentSlug/projectSlug 决定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileId, scope, agentSlug, projectSlug]);

  useEffect(() => {
    void loadVersions();
  }, [loadVersions]);

  // 加载选中两版的 diff
  useEffect(() => {
    if (newerNo === null) {
      setDiff(null);
      return;
    }
    let cancelled = false;
    setDiffLoading(true);
    fetchBehaviorVersionDiff({
      ...keyParams,
      version_a: newerNo,
      version_b: olderNo ?? undefined,
    })
      .then((d) => {
        if (!cancelled) setDiff(d);
      })
      .catch(() => {
        if (!cancelled) setDiff(null);
      })
      .finally(() => {
        if (!cancelled) setDiffLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newerNo, olderNo, fileId, scope, agentSlug, projectSlug]);

  const confirmRestore = async () => {
    if (restoreTarget === null) return;
    setRestoreBusy(true);
    setRestoreMsg(null);
    try {
      const outcome = await executeF149Action(
        {
          actionId: "behavior.restore_version",
          params: {
            agent_slug: agentSlug ?? "",
            confirmed: true,
            file_id: fileId,
            project_slug: projectSlug ?? "",
            target_version: restoreTarget,
          },
        },
        (actionId, params) =>
          executeWorkbenchAction(undefined, actionId, params),
      );
      if (!outcome.ok) {
        if (
          outcome.error.kind === "action-failed" &&
          outcome.error.code?.includes("CONFLICT")
        ) {
          setRestoreMsg("行为文件已被更新，请重新加载");
          setRestoreTarget(null);
          return;
        }
        setRestoreMsg("恢复未完成，请稍后重试");
        return;
      }
      setRestoreMsg(outcome.envelope.message || "已恢复");
      setRestoreTarget(null);
      await loadVersions();
    } catch (e) {
      setRestoreMsg(e instanceof Error ? e.message : "恢复失败");
    } finally {
      setRestoreBusy(false);
    }
  };

  return (
    <section className="f149-agent-history" aria-label={`${fileId} 版本历史`}>
      <div className="f149-agent-history-header">
        <div>
          <p>VERSION HISTORY</p>
          <h3>{fileId}</h3>
        </div>
        <button type="button" onClick={onClose}>
          关闭
        </button>
      </div>

      {loading ? (
        <div className="f149-agent-history-state">
          <span>正在加载版本历史</span>
        </div>
      ) : error ? (
        <div className="f149-agent-history-state is-error" role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => void loadVersions()}>
            重试
          </button>
        </div>
      ) : versions.length === 0 ? (
        <div className="f149-agent-history-state">
          <strong>暂无版本历史</strong>
          <span>这个文件被改过之后，这里会显示它的历史版本。</span>
        </div>
      ) : (
        <>
          <div className="f149-agent-version-selectors">
            <span>对比版本</span>
            <div>
              <select
                aria-label="较新版本"
                value={newerNo ?? ""}
                onChange={(e) => setNewerNo(Number(e.target.value))}
              >
                {versions.map((v) => (
                  <option key={v.version_no} value={v.version_no}>
                    版本 {v.version_no} · {formatTs(v.ts)}
                  </option>
                ))}
              </select>
              <span>对比</span>
              <select
                aria-label="较旧版本"
                value={olderNo ?? ""}
                onChange={(e) =>
                  setOlderNo(e.target.value === "" ? null : Number(e.target.value))
                }
              >
                <option value="">（无上一版）</option>
                {versions.map((v) => (
                  <option key={v.version_no} value={v.version_no}>
                    版本 {v.version_no} · {formatTs(v.ts)}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* diff 主视图（复用共享 DiffBody） */}
          {diffLoading ? (
            <div className="f149-agent-history-state">
              <span>正在加载差异…</span>
            </div>
          ) : diff && diff.current ? (
            <DiffBody diff={diff} />
          ) : (
            <div className="f149-agent-history-state">
              <span>选择版本以查看差异。</span>
            </div>
          )}

          <div className="f149-agent-version-list">
            <strong>历史版本</strong>
            {versions.map((v) => (
              <div key={v.version_no} className="f149-agent-version-row">
                <div>
                  <strong>版本 {v.version_no}</strong>
                  <small>{formatTs(v.ts)}</small>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setRestoreTarget(v.version_no);
                    setRestoreMsg(null);
                  }}
                >
                  恢复到此版本
                </button>
              </div>
            ))}
          </div>

          {/* 恢复确认（Two-Phase：proposal → 确认） */}
          {restoreTarget !== null && (
            <div className="f149-agent-restore-confirm" role="alert">
              <span>
                将把 {fileId} 恢复到版本 {restoreTarget}，并记为一个新版本。确认吗？
              </span>
              <div>
                <button
                  type="button"
                  disabled={restoreBusy}
                  onClick={confirmRestore}
                >
                  {restoreBusy ? "恢复中…" : "确认恢复"}
                </button>
                <button
                  type="button"
                  disabled={restoreBusy}
                  onClick={() => setRestoreTarget(null)}
                >
                  取消
                </button>
              </div>
            </div>
          )}
          {restoreMsg && (
            <div
              className={`f149-agent-restore-message ${
                restoreMsg.includes("更新") ? "is-conflict" : ""
              }`}
              role="status"
            >
              <span>{restoreMsg}</span>
              {restoreMsg.includes("更新") ? (
                <button
                  type="button"
                  onClick={() => {
                    setRestoreMsg(null);
                    void loadVersions();
                  }}
                >
                  重新加载
                </button>
              ) : null}
            </div>
          )}
        </>
      )}
    </section>
  );
}
