import { useEffect, useState } from "react";
import { readRemoteAccessStatus } from "../../api/remote-access";
import "./RemoteAccessSettings.css";

type RemoteAccessStatus = Awaited<ReturnType<typeof readRemoteAccessStatus>>;

interface RemoteAccessSettingsProps {
  loadStatus?: () => Promise<RemoteAccessStatus>;
}

type StatusCopy = {
  title: string;
  summary: string;
};

const _STATUS_COPY: Record<RemoteAccessStatus["state"], StatusCopy> = {
  unconfigured: {
    title: "远程访问尚未设置",
    summary: "本机使用不受影响。完成远程访问设置后，可从其他电脑安全打开 Octo。",
  },
  pending_verification: {
    title: "正在确认远程访问",
    summary: "入口已经设置，Octo 正在确认网页、连接和访问保护是否都可用。",
  },
  ready: {
    title: "远程访问已就绪",
    summary: "你可以从其他电脑打开受保护的 Octo 网页。",
  },
  fault: {
    title: "远程访问需要处理",
    summary: "本机使用仍然可用。请按下方建议处理后重新检查。",
  },
};

const _RECOVERY_COPY: Record<string, string> = {
  configure_remote_access: "完成远程访问设置后再检查",
  verify_remote_access: "稍后重新检查",
  review_remote_access_config: "检查远程访问设置",
  restart_remote_access_service: "重新启动远程访问服务后再检查",
  restart_gateway: "重新启动 Octo 后再检查",
  reauthenticate_access: "重新登录远程访问",
};

function _formatVerifiedAt(value: string | null): string {
  if (!value) {
    return "尚未完成验证";
  }
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return "验证时间不可用";
  }
  return timestamp.toLocaleString("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function _StatusActions({ status }: { status: RemoteAccessStatus }) {
  if (!status.desktop_web_url && !status.access_logout_url) {
    return null;
  }
  return (
    <div className="remote-access-settings__actions" aria-label="远程访问动作">
      {status.desktop_web_url ? (
        <a href={status.desktop_web_url} target="_blank" rel="noreferrer">
          打开电脑网页
        </a>
      ) : null}
      {status.access_logout_url ? (
        <a href={status.access_logout_url} target="_blank" rel="noreferrer">
          退出远程登录
        </a>
      ) : null}
    </div>
  );
}

export function RemoteAccessSettings({
  loadStatus = readRemoteAccessStatus,
}: RemoteAccessSettingsProps) {
  const [status, setStatus] = useState<RemoteAccessStatus | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [requestVersion, setRequestVersion] = useState(0);

  useEffect(() => {
    let active = true;
    setLoadError(false);
    loadStatus()
      .then((nextStatus) => {
        if (active) {
          setStatus(nextStatus);
        }
      })
      .catch(() => {
        if (active) {
          setStatus(null);
          setLoadError(true);
        }
      });
    return () => {
      active = false;
    };
  }, [loadStatus, requestVersion]);

  return (
    <section
      className="remote-access-settings f149-settings-panel"
      aria-labelledby="remote-access-settings-title"
      data-visual-baseline="claude-design-original"
      data-composition="status-card-actions-advanced"
    >
      <div data-content-level="ordinary">
        <header className="remote-access-settings__header">
          <p className="remote-access-settings__eyebrow">远程连接</p>
          <h2 id="remote-access-settings-title">从电脑安全访问 Octo</h2>
          <p>保留熟悉的网页体验，同时让远程入口保持受保护。</p>
        </header>

        {loadError ? (
          <div className="remote-access-settings__status-card" role="alert">
            <h3>暂时无法读取远程访问状态</h3>
            <p>本机使用不受影响。请检查连接后再试。</p>
            <button type="button" onClick={() => setRequestVersion((value) => value + 1)}>
              重新读取
            </button>
          </div>
        ) : status ? (
          <article
            className="remote-access-settings__status-card"
            data-status={status.state}
          >
            <div className="remote-access-settings__status-copy">
              <h3>{_STATUS_COPY[status.state].title}</h3>
              <p>{_STATUS_COPY[status.state].summary}</p>
            </div>
            {status.hostname || status.owner_email ? (
              <dl className="remote-access-settings__facts">
                {status.hostname ? (
                  <>
                    <dt>网页地址</dt>
                    <dd>{status.hostname}</dd>
                  </>
                ) : null}
                {status.owner_email ? (
                  <>
                    <dt>允许使用者</dt>
                    <dd>{status.owner_email}</dd>
                  </>
                ) : null}
              </dl>
            ) : null}
            {status.recovery_action ? (
              <p className="remote-access-settings__recovery">
                建议：{_RECOVERY_COPY[status.recovery_action] ?? "检查设置后重新读取"}
              </p>
            ) : null}
            <_StatusActions status={status} />
            {status.state !== "ready" ? (
              <button
                type="button"
                onClick={() => setRequestVersion((value) => value + 1)}
              >
                重新检查
              </button>
            ) : null}
          </article>
        ) : (
          <div className="remote-access-settings__status-card" role="status">
            正在读取远程访问状态…
          </div>
        )}
      </div>

      {status ? (
        <details className="remote-access-settings__advanced">
          <summary>高级诊断</summary>
          <dl>
            <dt>最近验证</dt>
            <dd>{_formatVerifiedAt(status.last_verified_at)}</dd>
            <dt>诊断代码</dt>
            <dd>{status.reason_code ?? "无"}</dd>
          </dl>
        </details>
      ) : null}
    </section>
  );
}
