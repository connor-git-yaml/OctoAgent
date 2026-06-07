/**
 * FilesCenter 页面（F104 文件工作台 v0.1）
 *
 * 两级导航：
 * 1. 一级 -- 列出有产出文件的任务（fetchFileTasks）
 * 2. 二级 -- 选中任务后列出逻辑文件（fetchLogicalFiles，version_count >= 2）
 * 3. 详情 -- 选中逻辑文件后取 diff 数据（fetchLogicalFileDiff），基础并排展示当前版/上一版 content
 *
 * 约束：
 * - 本 Phase 仅基础文本并排展示，不做 diff 高亮（Phase 4 用 jsdiff）
 * - 面向非技术用户：仅展示 display_name，不暴露 logical_file_id / hash / storage_kind 等技术字段
 * - 所有请求经 src/api/client 的内部 apiFetch（front-door 鉴权）
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import {
  fetchFileTasks,
  fetchLogicalFileDiff,
  fetchLogicalFiles,
} from "../api/client";
import type {
  DiffResponse,
  FileTaskItem,
  LogicalFileItem,
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

      {level === "diff" && selectedFile && (
        <DiffView
          fileName={selectedFile.display_name}
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
// 详情：diff 基础并排展示（Phase 4 才做高亮）
// ---------------------------------------------------------------------------

function DiffView(props: {
  fileName: string;
  diff: DiffResponse | null;
  loading: boolean;
  error: string | null;
}) {
  const { fileName, diff, loading, error } = props;

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

  // 二进制 / 超限：内容不可展示
  if (diff.binary) {
    return (
      <div className="wb-card">
        <p className="wb-card-label">{fileName}</p>
        <p>这是二进制文件，暂时无法显示内容对比。</p>
      </div>
    );
  }
  if (diff.oversize) {
    return (
      <div className="wb-card">
        <p className="wb-card-label">{fileName}</p>
        <p>文件内容过大，暂时无法显示内容对比。</p>
      </div>
    );
  }

  const current = diff.current;
  const previous = diff.previous;

  // diff 并排两栏（Phase 3 仅基础文本，Phase 4 接 jsdiff 高亮）
  const sideGridStyle: CSSProperties = {
    display: "grid",
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
    gap: "var(--space-md)",
  };
  const preStyle: CSSProperties = {
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    background: "var(--cp-soft)",
    padding: "var(--space-sm)",
    borderRadius: "var(--cp-radius-md)",
    margin: 0,
  };

  return (
    <section className="wb-card">
      <p className="wb-card-label">{fileName}</p>
      <div style={sideGridStyle}>
        <div>
          {/* 主视图不暴露内部版本号 version_no（技术字段），仅纯文案 */}
          <p className="wb-card-label">上一版</p>
          {previous ? (
            <pre style={preStyle}>{previous.content ?? "（内容暂不可用）"}</pre>
          ) : (
            <div className="wb-empty-state">
              <span>首版无对比</span>
            </div>
          )}
        </div>
        <div>
          <p className="wb-card-label">当前版</p>
          <pre style={preStyle}>{current.content ?? "（内容暂不可用）"}</pre>
        </div>
      </div>
    </section>
  );
}
