/**
 * F107 W1-D（FR-S-1）：共享 diff 渲染组件。
 *
 * 从 F104 FilesCenter.tsx 抽出的**纯 diff 渲染核**（buildDiffLineRows / DiffBody /
 * DiffLineList + 样式），供 F104 Files Tab、F107 Agent 中心 behavior 版本历史、未来 W2
 * workspace git 三处复用。行为与 F104 逐字一致（零变更，由 FilesCenter.test.tsx 守卫）。
 *
 * 注意：F104 的 `DiffView` 外壳 + `AdvancedVersionMeta`（耦合 task/logical-file 模型）保留在
 * FilesCenter.tsx，不进本共享模块——本模块只含与数据源无关的纯渲染。
 */

import { useMemo, type CSSProperties } from "react";
import { diffLines } from "diff";
import type { DiffResponse } from "../../types";

export type DiffLineKind = "added" | "removed" | "unchanged";

export interface DiffLineRow {
  kind: DiffLineKind;
  text: string;
}

/**
 * 用 jsdiff diffLines 把上一版 vs 当前版拆成行级片段，再展开成统一视图的逐行行。
 * - added 行（绿）：前缀 "+"
 * - removed 行（红）：前缀 "-"
 * - unchanged 行：前缀空格
 * jsdiff 的每个 part.value 可能含多行，按换行拆分；末尾空串（trailing newline 产生）剔除。
 */
export function buildDiffLineRows(
  previousText: string,
  currentText: string,
): DiffLineRow[] {
  const parts = diffLines(previousText, currentText);
  const rows: DiffLineRow[] = [];
  for (const part of parts) {
    const kind: DiffLineKind = part.added
      ? "added"
      : part.removed
        ? "removed"
        : "unchanged";
    // 去掉因 trailing "\n" 产生的末尾空片段，避免多渲染一条空行
    const lines = part.value.split("\n");
    if (lines.length > 0 && lines[lines.length - 1] === "") {
      lines.pop();
    }
    for (const line of lines) {
      rows.push({ kind, text: line });
    }
  }
  return rows;
}

/**
 * diff 主内容区：处理降级（binary / oversize / unavailable）+ jsdiff 行级高亮。
 * 调用方须保证 diff && diff.current 非空（与 F104 DiffView 外壳契约一致）。
 */
export function DiffBody(props: { diff: DiffResponse }) {
  const { diff } = props;
  const current = diff.current!;
  const previous = diff.previous;

  // 行级 diff 计算（仅在可文本对比时有意义；useMemo 避免重渲染重复 diff）。
  // previous 为 null（首版）时不计算，走"首版无对比"文案分支。
  const diffRows = useMemo<DiffLineRow[] | null>(() => {
    if (diff.binary || diff.oversize) {
      return null;
    }
    if (!previous) {
      return null;
    }
    if (current.content === null || previous.content === null) {
      return null;
    }
    return buildDiffLineRows(previous.content, current.content);
  }, [diff.binary, diff.oversize, previous, current.content]);

  // 二进制（FR-018）：内容不可展示
  if (diff.binary) {
    return <p>这是二进制文件，暂时无法显示内容对比。</p>;
  }
  // 超限（FR-019 / SC-005）：内容不可展示
  if (diff.oversize) {
    return <p>文件内容过大，暂时无法显示内容对比。</p>;
  }
  // 当前版内容不可用（FR-010）
  if (current.content === null) {
    return (
      <div className="wb-empty-state">
        <strong>无法显示这个文件的内容</strong>
        <span>稍后再试，或选择其他文件。</span>
      </div>
    );
  }
  // 首版（previous=null）：无对比对象，保留「首版无对比」文案 + 展示当前内容
  if (!previous) {
    return (
      <div>
        <div className="wb-empty-state">
          <span>首版无对比</span>
        </div>
        <pre style={DIFF_PLAIN_PRE_STYLE}>{current.content}</pre>
      </div>
    );
  }
  // 上一版内容不可用（FR-010）：无法做行级 diff，退回当前内容纯展示
  if (previous.content === null || diffRows === null) {
    return (
      <div>
        <div className="wb-empty-state">
          <span>上一版内容暂不可用，仅显示当前内容</span>
        </div>
        <pre style={DIFF_PLAIN_PRE_STYLE}>{current.content}</pre>
      </div>
    );
  }

  // 无差异（FR-015）：内容完全相同 → 明确提示，不渲染全 unchanged / 空 diff。
  // 此判断在两侧 content 均非 null 之后，覆盖：相同非空内容 + 两空文件（""===""）。
  if (current.content === previous.content) {
    return (
      <div className="wb-empty-state">
        <strong>无差异</strong>
        <span>当前版与上一版内容相同。</span>
      </div>
    );
  }

  // jsdiff 行级高亮统一视图：绿=新增 / 红=删除 / 普通=未变
  return <DiffLineList rows={diffRows} />;
}

/** 行级高亮列表（统一视图，非并排） */
export function DiffLineList(props: { rows: DiffLineRow[] }) {
  const { rows } = props;
  return (
    <div style={DIFF_LIST_STYLE} role="list" aria-label="逐行差异">
      {rows.map((row, idx) => {
        const prefix =
          row.kind === "added" ? "+" : row.kind === "removed" ? "-" : " ";
        const lineStyle: CSSProperties = {
          ...DIFF_LINE_BASE_STYLE,
          background:
            row.kind === "added"
              ? "var(--cp-success-soft)"
              : row.kind === "removed"
                ? "var(--cp-danger-soft)"
                : "transparent",
        };
        return (
          <div key={idx} role="listitem" style={lineStyle} data-diff-kind={row.kind}>
            <span aria-hidden="true" style={DIFF_PREFIX_STYLE}>
              {prefix}
            </span>
            <span style={DIFF_TEXT_STYLE}>{row.text === "" ? " " : row.text}</span>
          </div>
        );
      })}
    </div>
  );
}

// 纯手工样式（tokens 驱动，不引 CSS 库）---------------------------------------

/** 行级 diff 容器：等宽 + 圆角 + 软底 */
const DIFF_LIST_STYLE: CSSProperties = {
  fontFamily:
    'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
  fontSize: "13px",
  lineHeight: 1.6,
  background: "var(--cp-soft)",
  borderRadius: "var(--cp-radius-md)",
  padding: "var(--space-sm)",
  overflowX: "auto",
};

/** 单行：前缀 + 文本，整行背景色标识增删 */
const DIFF_LINE_BASE_STYLE: CSSProperties = {
  display: "flex",
  gap: "var(--space-sm)",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  borderRadius: "4px",
  padding: "0 4px",
};

/** 前缀列（+ / - / 空格）：固定宽 + 居中 */
const DIFF_PREFIX_STYLE: CSSProperties = {
  flex: "0 0 1ch",
  textAlign: "center",
  userSelect: "none",
  color: "var(--cp-muted)",
};

/** 文本列：占满剩余宽度 */
const DIFF_TEXT_STYLE: CSSProperties = {
  flex: "1 1 auto",
  whiteSpace: "pre-wrap",
};

/** 首版 / 降级时的纯文本展示块 */
export const DIFF_PLAIN_PRE_STYLE: CSSProperties = {
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  fontFamily:
    'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
  fontSize: "13px",
  background: "var(--cp-soft)",
  padding: "var(--space-sm)",
  borderRadius: "var(--cp-radius-md)",
};
