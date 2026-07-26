import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { WorkspaceBlameLine } from "../api/client";
import { presentAdvancedValue } from "../domains/shared/resourcePageState";

interface FilesAdvancedInfoProps {
  commit: string;
  path: string;
  onLoadBlame: () => Promise<WorkspaceBlameLine[]>;
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}

export default function FilesAdvancedInfo({
  commit,
  path,
  onLoadBlame,
}: FilesAdvancedInfoProps) {
  const [open, setOpen] = useState(false);
  const [blame, setBlame] = useState<WorkspaceBlameLine[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const pathValue = presentAdvancedValue({
    value: path,
    kind: "path",
    sensitivity: "operator_sensitive",
    sanitized: true,
    copyPermitted: true,
    maxDisplayLength: 54,
  });

  const closeDialog = useCallback(() => {
    setOpen(false);
    setCopyStatus(null);
    window.requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") closeDialog();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [closeDialog, open]);

  const loadBlame = async () => {
    setLoading(true);
    setLoadError(false);
    try {
      setBlame(await onLoadBlame());
    } catch {
      setBlame(null);
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  };

  const copyPath = async () => {
    if (!pathValue.canCopy || !pathValue.copyValue) return;
    await navigator.clipboard.writeText(pathValue.copyValue);
    setCopyStatus("已复制相对路径");
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="f149-files-advanced-trigger"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        高级 · 版本与文件信息
      </button>
      {open && document.body
        ? createPortal(
            <div
              className="f149-files-dialog-backdrop"
              onMouseDown={(event) => {
                if (event.target === event.currentTarget) closeDialog();
              }}
            >
              <section
                className="f149-files-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="files-advanced-title"
              >
                <header>
                  <div>
                    <p>ADVANCED</p>
                    <h2 id="files-advanced-title">版本与文件信息</h2>
                  </div>
                  <button type="button" onClick={closeDialog}>
                    关闭
                  </button>
                </header>
                <dl className="f149-files-technical-list">
                  <div>
                    <dt>版本标识</dt>
                    <dd>{commit}</dd>
                  </div>
                  <div>
                    <dt>工作区相对路径</dt>
                    <dd>{pathValue.displayValue}</dd>
                  </div>
                </dl>
                {pathValue.canCopy && (
                  <button type="button" onClick={() => void copyPath()}>
                    复制路径
                  </button>
                )}
                {copyStatus && <p role="status">{copyStatus}</p>}
                <div className="f149-files-blame">
                  <button
                    type="button"
                    disabled={loading}
                    onClick={() => void loadBlame()}
                  >
                    {loading ? "正在加载逐行记录…" : "查看逐行修改记录"}
                  </button>
                  {loadError && (
                    <p role="alert">逐行修改记录暂时无法加载，请重试。</p>
                  )}
                  {blame && blame.length === 0 && <p>暂无逐行修改记录。</p>}
                  {blame && blame.length > 0 && (
                    <ul>
                      {blame.map((line) => (
                        <li key={line.line_no}>
                          <span>{line.short}</span>
                          <span>{formatTimestamp(line.ts)}</span>
                          <code>{line.content}</code>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </section>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
