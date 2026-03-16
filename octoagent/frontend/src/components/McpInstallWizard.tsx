import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/* ── 向导步骤定义 ────────────────────────────────────────── */

type WizardStep =
  | "source_select"
  | "package_input"
  | "confirm"
  | "installing"
  | "result";

type InstallSource = "npm" | "pip";

interface InstallResult {
  server_id: string;
  version: string;
  install_path: string;
  command: string;
  tools_count: number;
  tools: Array<{ name: string; description: string }>;
}

interface McpInstallWizardProps {
  open: boolean;
  onClose: () => void;
  onComplete: () => void;
  submitAction: (actionId: string, params: Record<string, unknown>) => Promise<unknown>;
}

/* ── 主组件 ──────────────────────────────────────────────── */

export default function McpInstallWizard({
  open,
  onClose,
  onComplete,
  submitAction,
}: McpInstallWizardProps) {
  const [step, setStep] = useState<WizardStep>("source_select");
  const [source, setSource] = useState<InstallSource>("npm");
  const [packageName, setPackageName] = useState("");
  const [envText, setEnvText] = useState("");
  const [, setTaskId] = useState<string | null>(null);
  const [progressMessage, setProgressMessage] = useState("");
  const [error, setError] = useState("");
  const [installResult, setInstallResult] = useState<InstallResult | null>(null);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollCountRef = useRef(0);

  // 重置状态
  useEffect(() => {
    if (open) {
      setStep("source_select");
      setSource("npm");
      setPackageName("");
      setEnvText("");
      setTaskId(null);
      setProgressMessage("");
      setError("");
      setInstallResult(null);
      setBusy(false);
      pollCountRef.current = 0;
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [open]);

  // 解析环境变量
  function parseEnv(): Record<string, string> {
    const result: Record<string, string> = {};
    for (const line of envText.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || !trimmed.includes("=")) continue;
      const [key, ...rest] = trimmed.split("=");
      if (key?.trim()) {
        result[key.trim()] = rest.join("=").trim();
      }
    }
    return result;
  }

  // 步骤 3: 确认安装 -> 发起安装请求
  async function handleConfirmInstall() {
    setBusy(true);
    setError("");
    try {
      const resp = (await submitAction("mcp_provider.install", {
        install_source: source,
        package_name: packageName.trim(),
        env: parseEnv(),
      })) as { data?: { task_id?: string } };

      const id = resp?.data?.task_id;
      if (!id) {
        setError("未获取到安装任务 ID");
        setBusy(false);
        return;
      }
      setTaskId(id);
      setStep("installing");
      setProgressMessage("安装任务已启动...");
      startPolling(id);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    }
    setBusy(false);
  }

  // 轮询安装状态
  function startPolling(id: string) {
    pollCountRef.current = 0;
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      pollCountRef.current += 1;
      // 超时保护：300 秒 / 2 秒 = 150 次
      if (pollCountRef.current > 150) {
        if (pollRef.current) clearInterval(pollRef.current);
        setError("安装超时（超过 5 分钟）");
        setStep("result");
        return;
      }
      try {
        const resp = (await submitAction("mcp_provider.install_status", {
          task_id: id,
        })) as {
          data?: {
            status?: string;
            progress_message?: string;
            error?: string;
            result?: InstallResult | null;
          };
        };
        const data = resp?.data;
        if (!data) return;
        if (data.progress_message) setProgressMessage(data.progress_message);

        if (data.status === "completed") {
          if (pollRef.current) clearInterval(pollRef.current);
          setInstallResult(data.result ?? null);
          setStep("result");
        } else if (data.status === "failed") {
          if (pollRef.current) clearInterval(pollRef.current);
          setError(data.error || "安装失败");
          setStep("result");
        }
      } catch {
        // 轮询失败不中断
      }
    }, 2000);
  }

  // 重试
  function handleRetry() {
    setStep("package_input");
    setError("");
    setTaskId(null);
    setInstallResult(null);
  }

  // 完成
  function handleFinish() {
    onComplete();
    onClose();
  }

  if (!open) return null;

  return document.body
    ? createPortal(
        <div
          className="wb-modal-overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget && step !== "installing") onClose();
          }}
        >
          <div className="wb-modal-body wb-mcp-modal" style={{ maxWidth: 520 }}>
            <div className="wb-panel-head">
              <h3>安装 MCP Server</h3>
              {step !== "installing" && (
                <button
                  type="button"
                  className="wb-button wb-button-secondary"
                  onClick={onClose}
                >
                  关闭
                </button>
              )}
            </div>

            {/* 步骤指示器 */}
            <div className="wb-chip-row" style={{ marginBottom: 16 }}>
              <span className={`wb-chip ${step === "source_select" ? "is-active" : ""}`}>
                1. 选择来源
              </span>
              <span className={`wb-chip ${step === "package_input" ? "is-active" : ""}`}>
                2. 输入包名
              </span>
              <span className={`wb-chip ${step === "confirm" ? "is-active" : ""}`}>
                3. 确认
              </span>
              <span
                className={`wb-chip ${step === "installing" || step === "result" ? "is-active" : ""}`}
              >
                4. 安装
              </span>
            </div>

            {/* Step 1: 来源选择 */}
            {step === "source_select" && (
              <div>
                <p style={{ marginBottom: 12 }}>选择 MCP Server 的安装来源：</p>
                <div style={{ display: "flex", gap: 12 }}>
                  <button
                    type="button"
                    className={`wb-button ${source === "npm" ? "wb-button-primary" : "wb-button-secondary"}`}
                    onClick={() => setSource("npm")}
                    style={{ flex: 1, padding: "12px 16px" }}
                  >
                    <strong>npm</strong>
                    <br />
                    <small>适用于大多数 MCP server</small>
                  </button>
                  <button
                    type="button"
                    className={`wb-button ${source === "pip" ? "wb-button-primary" : "wb-button-secondary"}`}
                    onClick={() => setSource("pip")}
                    style={{ flex: 1, padding: "12px 16px" }}
                  >
                    <strong>pip</strong>
                    <br />
                    <small>适用于 Python 生态</small>
                  </button>
                </div>
                <div className="wb-inline-actions" style={{ marginTop: 16 }}>
                  <button
                    type="button"
                    className="wb-button wb-button-primary"
                    onClick={() => setStep("package_input")}
                  >
                    下一步
                  </button>
                </div>
              </div>
            )}

            {/* Step 2: 包名输入 */}
            {step === "package_input" && (
              <div>
                <label className="wb-field">
                  <span>
                    {source === "npm" ? "npm 包名" : "pip 包名"}
                  </span>
                  <input
                    type="text"
                    value={packageName}
                    onChange={(e) => setPackageName(e.target.value)}
                    placeholder={
                      source === "npm"
                        ? "例如 @anthropic/mcp-server-files"
                        : "例如 mcp-server-fetch"
                    }
                    autoFocus
                  />
                  <small style={{ color: "var(--text-secondary)" }}>
                    {source === "npm"
                      ? "支持 @scope/name 格式"
                      : "支持 PyPI 包名格式"}
                  </small>
                </label>
                <label className="wb-field" style={{ marginTop: 12 }}>
                  <span>环境变量（可选）</span>
                  <textarea
                    value={envText}
                    onChange={(e) => setEnvText(e.target.value)}
                    placeholder={"每行一个 KEY=VALUE\n例如 API_KEY=sk-xxx"}
                    rows={3}
                  />
                </label>
                {error && (
                  <div className="wb-inline-banner is-error" style={{ marginTop: 8 }}>
                    <span>{error}</span>
                  </div>
                )}
                <div className="wb-inline-actions" style={{ marginTop: 16 }}>
                  <button
                    type="button"
                    className="wb-button wb-button-secondary"
                    onClick={() => {
                      setStep("source_select");
                      setError("");
                    }}
                  >
                    上一步
                  </button>
                  <button
                    type="button"
                    className="wb-button wb-button-primary"
                    disabled={!packageName.trim()}
                    onClick={() => {
                      setError("");
                      setStep("confirm");
                    }}
                  >
                    下一步
                  </button>
                </div>
              </div>
            )}

            {/* Step 3: 确认安装 */}
            {step === "confirm" && (
              <div>
                <p style={{ marginBottom: 12 }}>确认安装以下 MCP Server：</p>
                <div className="wb-note-stack">
                  <div className="wb-note">
                    <strong>安装来源</strong>
                    <span>{source === "npm" ? "NPM" : "PyPI"}</span>
                  </div>
                  <div className="wb-note">
                    <strong>包名</strong>
                    <span>{packageName.trim()}</span>
                  </div>
                  {envText.trim() && (
                    <div className="wb-note">
                      <strong>环境变量</strong>
                      <span>{Object.keys(parseEnv()).length} 个</span>
                    </div>
                  )}
                </div>
                {error && (
                  <div className="wb-inline-banner is-error" style={{ marginTop: 8 }}>
                    <span>{error}</span>
                  </div>
                )}
                <div className="wb-inline-actions" style={{ marginTop: 16 }}>
                  <button
                    type="button"
                    className="wb-button wb-button-secondary"
                    onClick={() => {
                      setStep("package_input");
                      setError("");
                    }}
                  >
                    上一步
                  </button>
                  <button
                    type="button"
                    className="wb-button wb-button-primary"
                    disabled={busy}
                    onClick={() => void handleConfirmInstall()}
                  >
                    {busy ? "正在启动..." : "确认安装"}
                  </button>
                </div>
              </div>
            )}

            {/* Step 4: 安装进行中 */}
            {step === "installing" && (
              <div style={{ textAlign: "center", padding: "24px 0" }}>
                <div className="wb-spinner" style={{ marginBottom: 16 }} />
                <p style={{ fontSize: 16, fontWeight: 500 }}>正在安装...</p>
                <p style={{ color: "var(--text-secondary)", marginTop: 8 }}>
                  {progressMessage || "请稍候..."}
                </p>
                <p style={{ color: "var(--text-tertiary)", fontSize: 12, marginTop: 16 }}>
                  安装过程可能需要 1-3 分钟，请勿关闭此窗口
                </p>
              </div>
            )}

            {/* Step 5: 安装结果 */}
            {step === "result" && (
              <div>
                {installResult ? (
                  <>
                    <div
                      className="wb-inline-banner is-success"
                      style={{ marginBottom: 12 }}
                    >
                      <strong>安装成功</strong>
                    </div>
                    <div className="wb-note-stack">
                      <div className="wb-note">
                        <strong>Server ID</strong>
                        <span>{installResult.server_id}</span>
                      </div>
                      {installResult.version && (
                        <div className="wb-note">
                          <strong>版本</strong>
                          <span>{installResult.version}</span>
                        </div>
                      )}
                      <div className="wb-note">
                        <strong>发现工具</strong>
                        <span>{installResult.tools_count} 个</span>
                      </div>
                    </div>
                    {installResult.tools.length > 0 && (
                      <div style={{ marginTop: 12 }}>
                        <p style={{ fontWeight: 500, marginBottom: 4 }}>工具列表：</p>
                        <ul style={{ fontSize: 13, margin: 0, paddingLeft: 20 }}>
                          {installResult.tools.slice(0, 10).map((t) => (
                            <li key={t.name}>
                              <code>{t.name}</code>
                              {t.description && (
                                <span style={{ color: "var(--text-secondary)", marginLeft: 4 }}>
                                  {t.description.slice(0, 60)}
                                </span>
                              )}
                            </li>
                          ))}
                          {installResult.tools.length > 10 && (
                            <li style={{ color: "var(--text-secondary)" }}>
                              ... 及其他 {installResult.tools.length - 10} 个工具
                            </li>
                          )}
                        </ul>
                      </div>
                    )}
                    <div className="wb-inline-actions" style={{ marginTop: 16 }}>
                      <button
                        type="button"
                        className="wb-button wb-button-primary"
                        onClick={handleFinish}
                      >
                        完成
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <div
                      className="wb-inline-banner is-error"
                      style={{ marginBottom: 12 }}
                    >
                      <strong>安装失败</strong>
                      <span>{error || "未知错误"}</span>
                    </div>
                    <div className="wb-inline-actions" style={{ marginTop: 16 }}>
                      <button
                        type="button"
                        className="wb-button wb-button-secondary"
                        onClick={handleRetry}
                      >
                        重试
                      </button>
                      <button
                        type="button"
                        className="wb-button wb-button-tertiary"
                        onClick={onClose}
                      >
                        关闭
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>,
        document.body,
      )
    : null;
}
