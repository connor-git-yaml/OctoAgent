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
      const result = await executeWorkbenchAction(
        undefined,
        "behavior.restore_version",
        { ...keyParams, target_version: restoreTarget, confirmed: true },
      );
      setRestoreMsg(result.message || "已恢复");
      setRestoreTarget(null);
      await loadVersions();
    } catch (e) {
      setRestoreMsg(e instanceof Error ? e.message : "恢复失败");
    } finally {
      setRestoreBusy(false);
    }
  };

  return (
    <section className="wb-panel" aria-label={`${fileId} 版本历史`}>
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">版本历史</p>
          <h3>{fileId}</h3>
        </div>
        <button type="button" className="wb-chip" onClick={onClose}>
          关闭
        </button>
      </div>

      {loading ? (
        <div className="wb-note">
          <span>正在加载版本历史…</span>
        </div>
      ) : error ? (
        <div className="wb-inline-banner is-warning">
          <span>{error}</span>
        </div>
      ) : versions.length === 0 ? (
        <div className="wb-empty-state">
          <strong>暂无版本历史</strong>
          <span>这个文件被改过之后，这里会显示它的历史版本。</span>
        </div>
      ) : (
        <>
          {/* 版本对比选择（平实：选两版看差异） */}
          <div className="wb-field">
            <span>对比版本</span>
            <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap" }}>
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
              <span style={{ alignSelf: "center" }}>对比</span>
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
            <div className="wb-note">
              <span>正在加载差异…</span>
            </div>
          ) : diff && diff.current ? (
            <DiffBody diff={diff} />
          ) : (
            <div className="wb-empty-state">
              <span>选择版本以查看差异。</span>
            </div>
          )}

          {/* 版本时间线 + 恢复 */}
          <div className="wb-note-stack">
            <div className="wb-panel-head">
              <strong>历史版本</strong>
            </div>
            {versions.map((v) => (
              <div key={v.version_no} className="wb-agent-tool-row">
                <div>
                  <strong>版本 {v.version_no}</strong>
                  <small style={{ display: "block", color: "var(--cp-muted)" }}>
                    {formatTs(v.ts)}
                  </small>
                </div>
                <button
                  type="button"
                  className="wb-chip"
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
            <div className="wb-inline-banner is-warning">
              <span>
                将把 {fileId} 恢复到版本 {restoreTarget}，并记为一个新版本。确认吗？
              </span>
              <div style={{ display: "flex", gap: "var(--space-sm)" }}>
                <button
                  type="button"
                  className="wb-chip"
                  disabled={restoreBusy}
                  onClick={confirmRestore}
                >
                  {restoreBusy ? "恢复中…" : "确认恢复"}
                </button>
                <button
                  type="button"
                  className="wb-chip"
                  disabled={restoreBusy}
                  onClick={() => setRestoreTarget(null)}
                >
                  取消
                </button>
              </div>
            </div>
          )}
          {restoreMsg && (
            <div className="wb-note">
              <span>{restoreMsg}</span>
            </div>
          )}
        </>
      )}
    </section>
  );
}
