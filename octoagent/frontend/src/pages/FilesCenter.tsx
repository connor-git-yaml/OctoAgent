/**
 * FilesCenter 页面（F104 文件工作台 v0.1）
 *
 * 两级导航：
 * 1. 一级 -- 列出有产出文件的任务（fetchFileTasks）
 * 2. 二级 -- 选中任务后列出逻辑文件（fetchLogicalFiles，version_count >= 2）
 * 3. 详情 -- 选中逻辑文件后取 diff 数据（fetchLogicalFileDiff），jsdiff 行级高亮展示当前版 vs 上一版
 *
 * 约束：
 * - 主 diff 视图用 jsdiff 行级高亮（绿=新增 / 红=删除），面向非技术用户，0 技术字段（SC-004）
 * - 技术字段（版本号 / hash / size / storage_kind）只在 Advanced 折叠区出现（FR-017，默认收起）
 * - 面向非技术用户：主视图仅展示 display_name，不暴露 logical_file_id / version_no 等技术字段
 * - 所有请求经 src/api/client 的内部 apiFetch（front-door 鉴权）
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { diffLines } from "diff";
import {
  fetchFileTasks,
  fetchLogicalFileDiff,
  fetchLogicalFiles,
  fetchLogicalFileVersions,
} from "../api/client";
import type {
  DiffResponse,
  FileTaskItem,
  LogicalFileItem,
  VersionMetaItem,
} from "../types";

type ViewLevel = "tasks" | "files" | "diff";

export default function FilesCenter() {
  // 当前导航层级
  const [level, setLevel] = useState<ViewLevel>("tasks");

  // 一级：任务列表
  const [tasks, setTasks] = useState<FileTaskItem[]>([]);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [tasksError, setTasksError] = useState<string | null>(null);

  // 二级：选中的任务 + 逻辑文件列表
  const [selectedTask, setSelectedTask] = useState<FileTaskItem | null>(null);
  const [files, setFiles] = useState<LogicalFileItem[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState<string | null>(null);

  // 详情：选中的逻辑文件 + diff 数据
  const [selectedFile, setSelectedFile] = useState<LogicalFileItem | null>(null);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);

  // 异步竞态防护：单调递增的请求序号。
  // 任何会发起异步加载或改变当前选择的操作（openTask / openFile / 回退）
  // 都会自增 requestSeq；在途请求 await 返回后先校验自身 seq 是否仍是最新，
  // 过期则丢弃响应，避免旧响应覆盖新选择（显示错任务/错文件内容）。
  const requestSeq = useRef(0);

  // 一级：加载任务列表
  useEffect(() => {
    let cancelled = false;
    async function load() {
      setTasksLoading(true);
      setTasksError(null);
      try {
        const data = await fetchFileTasks();
        if (!cancelled) {
          setTasks(data.tasks);
        }
      } catch (err) {
        if (!cancelled) {
          setTasksError(err instanceof Error ? err.message : "加载任务失败");
        }
      } finally {
        if (!cancelled) {
          setTasksLoading(false);
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  // 二级：选中任务 -> 加载逻辑文件
  const openTask = useCallback(async (task: FileTaskItem) => {
    const seq = ++requestSeq.current;
    setSelectedTask(task);
    setSelectedFile(null);
    setDiff(null);
    setDiffError(null);
    setLevel("files");
    setFilesLoading(true);
    setFilesError(null);
    setFiles([]);
    try {
      const data = await fetchLogicalFiles(task.task_id);
      if (seq !== requestSeq.current) {
        return; // 已有更新的操作，丢弃过期响应
      }
      setFiles(data.files);
    } catch (err) {
      if (seq !== requestSeq.current) {
        return;
      }
      setFilesError(err instanceof Error ? err.message : "加载文件失败");
    } finally {
      if (seq === requestSeq.current) {
        setFilesLoading(false);
      }
    }
  }, []);

  // 详情：选中逻辑文件 -> 取 diff
  const openFile = useCallback(
    async (file: LogicalFileItem) => {
      if (!selectedTask) {
        return;
      }
      const seq = ++requestSeq.current;
      setSelectedFile(file);
      setLevel("diff");
      setDiffLoading(true);
      setDiffError(null);
      setDiff(null);
      try {
        const data = await fetchLogicalFileDiff(
          selectedTask.task_id,
          file.logical_file_id
        );
        if (seq !== requestSeq.current) {
          return; // 已有更新的操作，丢弃过期响应
        }
        setDiff(data);
      } catch (err) {
        if (seq !== requestSeq.current) {
          return;
        }
        setDiffError(err instanceof Error ? err.message : "加载差异失败");
      } finally {
        if (seq === requestSeq.current) {
          setDiffLoading(false);
        }
      }
    },
    [selectedTask]
  );

  // 回退：详情 -> 文件列表
  const backToFiles = useCallback(() => {
    // 自增 seq 使在途 diff 请求失效，避免回退后旧响应又写入
    requestSeq.current += 1;
    setSelectedFile(null);
    setDiff(null);
    setDiffError(null);
    setDiffLoading(false);
    setLevel("files");
  }, []);

  // 回退：文件列表 -> 任务列表
  const backToTasks = useCallback(() => {
    // 自增 seq 使在途 files / diff 请求失效，避免回退后旧响应又写入
    requestSeq.current += 1;
    setSelectedTask(null);
    setSelectedFile(null);
    setFiles([]);
    setFilesError(null);
    setFilesLoading(false);
    setDiff(null);
    setDiffError(null);
    setDiffLoading(false);
    setLevel("tasks");
  }, []);

  return (
    <div className="wb-page">
      <section className="wb-hero wb-hero-compact">
        <div className="wb-hero-copy">
          <p className="wb-kicker">文件</p>
          <h1>文件工作台</h1>
          <p>查看任务产出文件的版本变化，对比当前版本和上一个版本的内容。</p>
        </div>
      </section>

      <FilesBreadcrumb
        level={level}
        taskTitle={selectedTask?.title ?? null}
        fileName={selectedFile?.display_name ?? null}
        onBackToTasks={backToTasks}
        onBackToFiles={backToFiles}
      />

      {level === "tasks" && (
        <TasksView
          tasks={tasks}
          loading={tasksLoading}
          error={tasksError}
          onOpenTask={openTask}
        />
      )}

      {level === "files" && (
        <FilesView
          files={files}
          loading={filesLoading}
          error={filesError}
          onOpenFile={openFile}
        />
      )}

      {level === "diff" && selectedFile && selectedTask && (
        <DiffView
          fileName={selectedFile.display_name}
          taskId={selectedTask.task_id}
          logicalFileId={selectedFile.logical_file_id}
          diff={diff}
          loading={diffLoading}
          error={diffError}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 面包屑 / 回退
// ---------------------------------------------------------------------------

function FilesBreadcrumb(props: {
  level: ViewLevel;
  taskTitle: string | null;
  fileName: string | null;
  onBackToTasks: () => void;
  onBackToFiles: () => void;
}) {
  const { level, taskTitle, fileName, onBackToTasks, onBackToFiles } = props;
  return (
    <nav className="wb-chip-row" aria-label="文件工作台导航">
      <button
        type="button"
        className="wb-button wb-button-tertiary"
        onClick={onBackToTasks}
        disabled={level === "tasks"}
      >
        任务
      </button>
      {level !== "tasks" && taskTitle && (
        <>
          <span aria-hidden="true">/</span>
          <button
            type="button"
            className="wb-button wb-button-tertiary"
            onClick={onBackToFiles}
            disabled={level === "files"}
          >
            {taskTitle}
          </button>
        </>
      )}
      {level === "diff" && fileName && (
        <>
          <span aria-hidden="true">/</span>
          <span className="wb-chip">{fileName}</span>
        </>
      )}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// 一级：任务列表
// ---------------------------------------------------------------------------

function TasksView(props: {
  tasks: FileTaskItem[];
  loading: boolean;
  error: string | null;
  onOpenTask: (task: FileTaskItem) => void;
}) {
  const { tasks, loading, error, onOpenTask } = props;

  if (loading) {
    return (
      <div className="wb-empty-state">
        <span>正在加载任务列表…</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="wb-inline-banner is-warning">
        <span>加载失败：{error}</span>
      </div>
    );
  }
  if (tasks.length === 0) {
    return (
      <div className="wb-empty-state">
        <strong>还没有可对比的文件</strong>
        <span>当任务产出文件并有多个版本时，会出现在这里。</span>
      </div>
    );
  }

  return (
    <section className="wb-card-grid wb-card-grid-3">
      {tasks.map((task) => (
        <button
          key={task.task_id}
          type="button"
          className="wb-card"
          onClick={() => onOpenTask(task)}
        >
          <strong>{task.title}</strong>
        </button>
      ))}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 二级：逻辑文件列表
// ---------------------------------------------------------------------------

function FilesView(props: {
  files: LogicalFileItem[];
  loading: boolean;
  error: string | null;
  onOpenFile: (file: LogicalFileItem) => void;
}) {
  const { files, loading, error, onOpenFile } = props;

  if (loading) {
    return (
      <div className="wb-empty-state">
        <span>正在加载文件列表…</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="wb-inline-banner is-warning">
        <span>加载失败：{error}</span>
      </div>
    );
  }
  if (files.length === 0) {
    return (
      <div className="wb-empty-state">
        <strong>这个任务暂无可对比的文件</strong>
        <span>只有产生了多个版本的文件才能查看变化。</span>
      </div>
    );
  }

  return (
    <section className="wb-card-grid wb-card-grid-3">
      {files.map((file) => (
        <button
          key={file.logical_file_id}
          type="button"
          className="wb-card"
          onClick={() => onOpenFile(file)}
        >
          <strong>{file.display_name}</strong>
          <span className="wb-chip">{file.version_count} 个版本</span>
        </button>
      ))}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 详情：diff jsdiff 行级高亮 + Advanced 版本元信息折叠区
// ---------------------------------------------------------------------------

/** 单行 diff 渲染原子：状态（新增 / 删除 / 未变）+ 文本 */
type DiffLineKind = "added" | "removed" | "unchanged";
interface DiffLineRow {
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
function buildDiffLineRows(previousText: string, currentText: string): DiffLineRow[] {
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

function DiffView(props: {
  fileName: string;
  taskId: string;
  logicalFileId: string;
  diff: DiffResponse | null;
  loading: boolean;
  error: string | null;
}) {
  const { fileName, taskId, logicalFileId, diff, loading, error } = props;

  if (loading) {
    return (
      <div className="wb-empty-state">
        <span>正在加载差异…</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="wb-inline-banner is-warning">
        <span>加载失败：{error}</span>
      </div>
    );
  }
  if (!diff || !diff.current) {
    return (
      <div className="wb-empty-state">
        <strong>无法显示这个文件的内容</strong>
        <span>稍后再试，或选择其他文件。</span>
      </div>
    );
  }

  return (
    <section className="wb-card">
      <p className="wb-card-label">{fileName}</p>
      <DiffBody diff={diff} />
      {/* FR-017：技术字段（版本号 / hash / size / storage_kind）仅在 Advanced 折叠区，
          即使主内容不可 diff（二进制 / 超限 / 不可用），版本元信息仍可展开查看 */}
      <AdvancedVersionMeta taskId={taskId} logicalFileId={logicalFileId} />
    </section>
  );
}

/**
 * diff 主内容区：处理降级（binary / oversize / unavailable）+ jsdiff 行级高亮。
 * 注意：DiffView 已保证 diff && diff.current 非空。
 */
function DiffBody(props: { diff: DiffResponse }) {
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
  // 首版（previous=null）：无对比对象，保留 Phase 3「首版无对比」文案 + 展示当前内容
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
function DiffLineList(props: { rows: DiffLineRow[] }) {
  const { rows } = props;
  return (
    <div style={DIFF_LIST_STYLE} role="list" aria-label="逐行差异">
      {rows.map((row, idx) => {
        const prefix = row.kind === "added" ? "+" : row.kind === "removed" ? "-" : " ";
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
            <span style={DIFF_TEXT_STYLE}>{row.text === "" ? " " : row.text}</span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Advanced 版本元信息折叠区（FR-017）。
 * - 默认收起；展开（onToggle open）时才懒加载 fetchLogicalFileVersions
 * - 技术字段（版本号 / hash 前 8 位 / size / storage_kind）只在这里出现
 * - 独立 seq 保护，避免与主 diff 加载 race；与主视图加载状态隔离
 */
function AdvancedVersionMeta(props: { taskId: string; logicalFileId: string }) {
  const { taskId, logicalFileId } = props;
  const [versions, setVersions] = useState<VersionMetaItem[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 仅加载一次：已成功取过则不重复请求。
  const loadedRef = useRef(false);
  // 独立 seq：折叠区与主 diff 各自的异步生命周期隔离。
  const seqRef = useRef(0);

  const handleToggle = useCallback(
    async (event: React.SyntheticEvent<HTMLDetailsElement>) => {
      if (!event.currentTarget.open) {
        return; // 收起不触发加载
      }
      if (loadedRef.current || loading) {
        return; // 已加载或加载中
      }
      const seq = ++seqRef.current;
      setLoading(true);
      setLoadError(null);
      try {
        const data = await fetchLogicalFileVersions(taskId, logicalFileId);
        if (seq !== seqRef.current) {
          return; // 过期响应丢弃
        }
        setVersions(data.versions);
        loadedRef.current = true;
      } catch (err) {
        if (seq !== seqRef.current) {
          return;
        }
        setLoadError(err instanceof Error ? err.message : "加载版本详情失败");
      } finally {
        if (seq === seqRef.current) {
          setLoading(false);
        }
      }
    },
    [taskId, logicalFileId, loading]
  );

  return (
    <details
      className="wb-field-guide wb-field-guide-disclosure"
      style={ADVANCED_DETAILS_STYLE}
      onToggle={handleToggle}
    >
      <summary>高级信息（版本详情）</summary>
      {loading && <p>正在加载版本详情…</p>}
      {loadError && (
        <p className="wb-field-error">加载失败：{loadError}</p>
      )}
      {!loading && !loadError && versions && versions.length === 0 && (
        <p>暂无版本元信息。</p>
      )}
      {!loading && !loadError && versions && versions.length > 0 && (
        <ul style={VERSION_LIST_STYLE}>
          {versions.map((v) => (
            <li key={v.version_no} style={VERSION_ITEM_STYLE}>
              <span>版本号：v{v.version_no}</span>
              <span>时间：{v.ts}</span>
              <span>大小：{v.size} 字节</span>
              <span>哈希：{v.hash.slice(0, 8)}</span>
              <span>存储：{v.storage_kind}</span>
            </li>
          ))}
        </ul>
      )}
    </details>
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
const DIFF_PLAIN_PRE_STYLE: CSSProperties = {
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  fontFamily:
    'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
  fontSize: "13px",
  background: "var(--cp-soft)",
  padding: "var(--space-sm)",
  borderRadius: "var(--cp-radius-md)",
  margin: 0,
};

const ADVANCED_DETAILS_STYLE: CSSProperties = {
  marginTop: "var(--space-md)",
};

const VERSION_LIST_STYLE: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
};

const VERSION_ITEM_STYLE: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "var(--space-md)",
  fontSize: "13px",
  color: "var(--cp-muted)",
  borderTop: "1px solid var(--cp-border)",
  paddingTop: "var(--space-sm)",
};
