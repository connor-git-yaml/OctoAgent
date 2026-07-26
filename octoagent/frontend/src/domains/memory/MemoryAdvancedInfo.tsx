import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { MemoryRecordProjection } from "../../types";

interface MemoryAdvancedInfoProps {
  records: MemoryRecordProjection[];
  retrievalBackend: string;
  embeddingTarget: string;
}

export default function MemoryAdvancedInfo({
  records,
  retrievalBackend,
  embeddingTarget,
}: MemoryAdvancedInfoProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const firstRecord = records[0] ?? null;

  const closeDialog = useCallback(() => {
    setOpen(false);
    window.requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        closeDialog();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [closeDialog, open]);

  return (
    <section className="f149-memory-advanced">
      <button
        ref={triggerRef}
        type="button"
        className="f149-memory-advanced-trigger"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        高级 · 检索与记录信息
      </button>

      {open && document.body
        ? createPortal(
            <div
              className="f149-memory-dialog-backdrop"
              onMouseDown={(event) => {
                if (event.target === event.currentTarget) closeDialog();
              }}
            >
              <section
                className="f149-memory-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="memory-advanced-title"
              >
                <header>
                  <div>
                    <p>ADVANCED</p>
                    <h2 id="memory-advanced-title">检索与记录信息</h2>
                  </div>
                  <button type="button" onClick={closeDialog}>
                    关闭
                  </button>
                </header>
                <p className="f149-memory-dialog-copy">
                  这些值用于排查检索和记录问题，不影响日常使用。
                </p>
                <dl className="f149-memory-technical-list">
                  <div>
                    <dt>记录 ID</dt>
                    <dd>{firstRecord?.record_id || "暂无记录"}</dd>
                  </div>
                  <div>
                    <dt>检索实现</dt>
                    <dd>{firstRecord?.retrieval_backend || retrievalBackend || "未提供"}</dd>
                  </div>
                  <div>
                    <dt>模型 alias</dt>
                    <dd>{embeddingTarget || "未提供"}</dd>
                  </div>
                </dl>
              </section>
            </div>,
            document.body
          )
        : null}
    </section>
  );
}
