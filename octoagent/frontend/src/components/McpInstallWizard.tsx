import {
  type RefObject,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import {
  executeF149Action,
  type F149ActionResultById,
} from "../platform/actions/f149Actions";
import type { WorkbenchDataState } from "../platform/queries";

type WizardStep = "source" | "package" | "confirm" | "installing" | "result";
type InstallSource = "npm" | "pip";
type InstallStatusResult = F149ActionResultById["mcp_provider.install_status"];

interface InstallationSummary {
  command: string;
  serverId: string;
  tools: Array<{ name: string; description: string }>;
  toolsCount: number;
  version: string;
}

interface McpInstallWizardProps {
  open: boolean;
  onClose: () => void;
  onComplete: () => void;
  submitAction: WorkbenchDataState["submitAction"];
  returnFocusRef?: RefObject<HTMLButtonElement | null>;
}

const MAX_STATUS_CHECKS = 150;
const POLL_DELAY_MS = 2_000;

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function resultView(
  result: InstallStatusResult["result"],
): InstallationSummary | null {
  const object = objectValue(result);
  if (!object) {
    return null;
  }
  const tools = Array.isArray(object.tools)
    ? object.tools.flatMap((raw) => {
        const tool = objectValue(raw);
        const name = stringValue(tool?.name);
        return name
          ? [{ name, description: stringValue(tool?.description) }]
          : [];
      })
    : [];
  const count = Number(object.tools_count);
  return {
    command: stringValue(object.command),
    serverId: stringValue(object.server_id),
    tools,
    toolsCount: Number.isFinite(count) ? count : tools.length,
    version: stringValue(object.version),
  };
}

function restoreFocus(target: HTMLElement | null): void {
  if (target) {
    window.requestAnimationFrame(() => target.focus());
  }
}

export default function McpInstallWizard({
  open,
  onClose,
  onComplete,
  submitAction,
  returnFocusRef,
}: McpInstallWizardProps) {
  const [step, setStep] = useState<WizardStep>("source");
  const [source, setSource] = useState<InstallSource>("npm");
  const [packageName, setPackageName] = useState("");
  const [secretName, setSecretName] = useState("API_KEY");
  const [secretValue, setSecretValue] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [progressMessage, setProgressMessage] = useState("");
  const [problem, setProblem] = useState<
    "failed" | "disconnected" | "timeout" | null
  >(null);
  const [result, setResult] = useState<InstallationSummary | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const checkCountRef = useRef(0);
  const mountedRef = useRef(false);

  const cancelTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const reset = useCallback(() => {
    cancelTimer();
    setStep("source");
    setSource("npm");
    setPackageName("");
    setSecretName("API_KEY");
    setSecretValue("");
    setTaskId(null);
    setProgressMessage("");
    setProblem(null);
    setResult(null);
    setAdvancedOpen(false);
    setBusy(false);
    checkCountRef.current = 0;
  }, [cancelTimer]);

  const close = useCallback(() => {
    reset();
    onClose();
    restoreFocus(returnFocusRef?.current ?? null);
  }, [onClose, reset, returnFocusRef]);

  useEffect(() => {
    mountedRef.current = open;
    if (open) {
      reset();
    }
    return () => {
      mountedRef.current = false;
      cancelTimer();
    };
  }, [cancelTimer, open, reset]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        close();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [busy, close, open]);

  const scheduleCheck = useCallback(
    (id: string, check: (task: string) => Promise<void>) => {
      cancelTimer();
      timerRef.current = setTimeout(() => void check(id), POLL_DELAY_MS);
    },
    [cancelTimer],
  );

  const checkStatus = useCallback(
    async function check(task: string): Promise<void> {
      cancelTimer();
      checkCountRef.current += 1;
      if (checkCountRef.current > MAX_STATUS_CHECKS) {
        setProblem("timeout");
        setStep("result");
        return;
      }
      const outcome = await executeF149Action(
        {
          actionId: "mcp_provider.install_status",
          params: { task_id: task },
        },
        submitAction,
      );
      if (!mountedRef.current) {
        return;
      }
      if (!outcome.ok) {
        setProblem("disconnected");
        setStep("result");
        return;
      }
      const data = outcome.data;
      if (data.progress_message) {
        setProgressMessage(data.progress_message);
      }
      if (data.status === "completed") {
        setResult(resultView(data.result));
        setProblem(null);
        setStep("result");
        return;
      }
      if (data.status === "failed") {
        setProblem("failed");
        setStep("result");
        return;
      }
      scheduleCheck(task, check);
    },
    [cancelTimer, scheduleCheck, submitAction],
  );

  const startInstall = async () => {
    setBusy(true);
    setProblem(null);
    const env =
      secretName.trim() && secretValue
        ? { [secretName.trim()]: secretValue }
        : {};
    const outcome = await executeF149Action(
      {
        actionId: "mcp_provider.install",
        params: {
          install_source: source,
          package_name: packageName.trim(),
          env,
        },
      },
      submitAction,
    );
    if (!mountedRef.current) {
      return;
    }
    setBusy(false);
    if (!outcome.ok) {
      setProblem("failed");
      return;
    }
    setSecretValue("");
    setTaskId(outcome.data.task_id);
    setProgressMessage("安装已经开始");
    setStep("installing");
    scheduleCheck(outcome.data.task_id, checkStatus);
  };

  const retryStatus = () => {
    if (!taskId) {
      setProblem(null);
      setStep("package");
      return;
    }
    setProblem(null);
    setStep("installing");
    void checkStatus(taskId);
  };

  if (!open || !document.body) {
    return null;
  }

  const stepNumber =
    step === "source" ? 1 : step === "package" ? 2 : step === "confirm" ? 3 : 4;
  return createPortal(
    <div
      className="f149-mcp-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) {
          close();
        }
      }}
    >
      <section
        className="f149-mcp-dialog f149-mcp-install-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="f149-mcp-install-title"
      >
        <header>
          <div>
            <p className="f149-mcp-kicker">安全安装</p>
            <h2 id="f149-mcp-install-title">安装外部服务</h2>
          </div>
          <button
            type="button"
            className="f149-mcp-button f149-mcp-button-quiet"
            onClick={close}
            disabled={busy}
          >
            关闭
          </button>
        </header>
        <ol className="f149-mcp-steps" aria-label="安装进度">
          {["选择来源", "填写信息", "检查改动", "安装"].map((label, index) => (
            <li
              key={label}
              className={stepNumber === index + 1 ? "is-current" : ""}
            >
              <span>{index + 1}</span>
              {label}
            </li>
          ))}
        </ol>

        {step === "source" ? (
          <div className="f149-mcp-step">
            <h3>这个服务从哪里安装？</h3>
            <div className="f149-mcp-source-grid">
              {(["npm", "pip"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  className={source === option ? "is-selected" : ""}
                  onClick={() => setSource(option)}
                >
                  <strong>{option === "npm" ? "npm" : "Python"}</strong>
                  <span>
                    {option === "npm"
                      ? "适合 Node.js 服务"
                      : "适合 Python 服务"}
                  </span>
                </button>
              ))}
            </div>
            <footer>
              <span />
              <button
                type="button"
                className="f149-mcp-button f149-mcp-button-primary"
                onClick={() => setStep("package")}
              >
                下一步
              </button>
            </footer>
          </div>
        ) : null}

        {step === "package" ? (
          <div className="f149-mcp-step">
            <h3>填写安装信息</h3>
            <label>
              <span>{source === "npm" ? "npm 包名" : "Python 包名"}</span>
              <input
                autoFocus
                value={packageName}
                onChange={(event) => setPackageName(event.target.value)}
              />
            </label>
            <div className="f149-mcp-secret-pair">
              <label>
                <span>密钥名称（可选）</span>
                <input
                  value={secretName}
                  onChange={(event) => setSecretName(event.target.value)}
                />
              </label>
              <label>
                <span>访问密钥（可选）</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  value={secretValue}
                  onChange={(event) => setSecretValue(event.target.value)}
                />
              </label>
            </div>
            <p className="f149-mcp-privacy-note">
              访问密钥只用于本次保存，之后不会再次显示。
            </p>
            <footer>
              <button
                type="button"
                className="f149-mcp-button f149-mcp-button-quiet"
                onClick={() => setStep("source")}
              >
                上一步
              </button>
              <button
                type="button"
                className="f149-mcp-button f149-mcp-button-primary"
                disabled={!packageName.trim()}
                onClick={() => setStep("confirm")}
              >
                下一步
              </button>
            </footer>
          </div>
        ) : null}

        {step === "confirm" ? (
          <div className="f149-mcp-step">
            <h3>检查改动</h3>
            <dl className="f149-mcp-review">
              <div>
                <dt>安装来源</dt>
                <dd>{source === "npm" ? "npm" : "Python"}</dd>
              </div>
              <div>
                <dt>服务包</dt>
                <dd>{packageName.trim()}</dd>
              </div>
              <div>
                <dt>访问密钥</dt>
                <dd>{secretValue ? "将安全保存" : "未填写"}</dd>
              </div>
            </dl>
            {problem === "failed" ? (
              <p className="f149-mcp-error">安装未能开始，请检查后重试。</p>
            ) : null}
            <footer>
              <button
                type="button"
                className="f149-mcp-button f149-mcp-button-quiet"
                onClick={() => setStep("package")}
              >
                上一步
              </button>
              <button
                type="button"
                className="f149-mcp-button f149-mcp-button-primary"
                disabled={busy}
                onClick={() => void startInstall()}
              >
                {busy ? "正在启动" : "确认安装"}
              </button>
            </footer>
          </div>
        ) : null}

        {step === "installing" ? (
          <div className="f149-mcp-progress" aria-live="polite">
            <span className="f149-mcp-spinner" aria-hidden="true" />
            <h3>正在安装</h3>
            <p>{progressMessage || "正在准备服务，请稍候。"}</p>
          </div>
        ) : null}

        {step === "result" ? (
          <div className="f149-mcp-step" aria-live="polite">
            {result ? (
              <>
                <div className="f149-mcp-result is-success">
                  <span aria-hidden="true">✓</span>
                  <div>
                    <h3>安装成功</h3>
                    <p>发现 {result.toolsCount} 个可用工具</p>
                  </div>
                </div>
                <section className="f149-mcp-advanced">
                  <button
                    type="button"
                    className="f149-mcp-advanced-trigger"
                    aria-expanded={advancedOpen}
                    onClick={() => setAdvancedOpen((current) => !current)}
                  >
                    高级 · 安装详情
                    <span aria-hidden="true">{advancedOpen ? "−" : "+"}</span>
                  </button>
                  {advancedOpen ? (
                    <div
                      className="f149-mcp-advanced-body"
                      role="region"
                      aria-label="安装详情"
                    >
                      {result.serverId ? (
                        <p>
                          服务标识：<code>{result.serverId}</code>
                        </p>
                      ) : null}
                      {result.version ? <p>版本：{result.version}</p> : null}
                      {result.tools.length > 0 ? (
                        <ul>
                          {result.tools.map((tool) => (
                            <li key={tool.name}>{tool.name}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ) : null}
                </section>
                <footer>
                  <span />
                  <button
                    type="button"
                    className="f149-mcp-button f149-mcp-button-primary"
                    onClick={() => {
                      onComplete();
                      close();
                    }}
                  >
                    完成
                  </button>
                </footer>
              </>
            ) : (
              <>
                <div className="f149-mcp-result is-error">
                  <span aria-hidden="true">!</span>
                  <div>
                    <h3>
                      {problem === "disconnected"
                        ? "状态检查暂时中断"
                        : problem === "timeout"
                          ? "状态确认超时"
                          : "安装未完成"}
                    </h3>
                    <p>
                      {problem === "disconnected"
                        ? "上次已知的安装状态已保留。"
                        : "你可以重新检查，不会重复创建安装任务。"}
                    </p>
                  </div>
                </div>
                <footer>
                  <button
                    type="button"
                    className="f149-mcp-button f149-mcp-button-quiet"
                    onClick={close}
                  >
                    关闭
                  </button>
                  <button
                    type="button"
                    className="f149-mcp-button f149-mcp-button-primary"
                    onClick={retryStatus}
                  >
                    重新检查
                  </button>
                </footer>
              </>
            )}
          </div>
        ) : null}
      </section>
    </div>,
    document.body,
  );
}
