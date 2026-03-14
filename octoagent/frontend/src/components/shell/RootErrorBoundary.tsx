import React, { type ErrorInfo, type ReactNode } from "react";

type RootErrorBoundaryProps = {
  children: ReactNode;
};

type BoundaryErrorState = {
  title: string;
  message: string;
  detail?: string;
  suggestRefresh: boolean;
};

type RootErrorBoundaryState = {
  error: BoundaryErrorState | null;
};

function normalizeError(input: unknown): Error | null {
  if (input instanceof Error) {
    return input;
  }
  if (typeof input === "string" && input.trim()) {
    return new Error(input);
  }
  if (typeof input === "object" && input !== null && "message" in input) {
    const message = String((input as { message?: unknown }).message ?? "").trim();
    if (message) {
      return new Error(message);
    }
  }
  return null;
}

function isBundleMismatchMessage(message: string): boolean {
  const lower = message.toLowerCase();
  return (
    lower.includes("chunkloaderror") ||
    lower.includes("loading chunk") ||
    lower.includes("failed to fetch dynamically imported module") ||
    lower.includes("importing a module script failed") ||
    lower.includes("failed to load module script")
  );
}

function describeBoundaryError(error: Error): BoundaryErrorState {
  if (isBundleMismatchMessage(error.message)) {
    return {
      title: "页面刚更新，请刷新一次",
      message: "当前标签页拿到的是旧页面资源。刷新后会重新加载最新界面，不会再只剩白屏。",
      detail: error.message,
      suggestRefresh: true,
    };
  }
  return {
    title: "页面刚才没有正常加载",
    message: "我们已经拦下这次前端错误。你可以先刷新一次；如果反复出现，再去 Advanced 看诊断。",
    detail: error.message,
    suggestRefresh: false,
  };
}

export default class RootErrorBoundary extends React.Component<
  RootErrorBoundaryProps,
  RootErrorBoundaryState
> {
  state: RootErrorBoundaryState = {
    error: null,
  };

  componentDidMount() {
    window.addEventListener("error", this.handleWindowError);
    window.addEventListener("unhandledrejection", this.handleUnhandledRejection);
  }

  componentWillUnmount() {
    window.removeEventListener("error", this.handleWindowError);
    window.removeEventListener("unhandledrejection", this.handleUnhandledRejection);
  }

  static getDerivedStateFromError(error: Error): RootErrorBoundaryState {
    return {
      error: describeBoundaryError(error),
    };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("RootErrorBoundary caught a frontend error", error, info);
  }

  private setBoundaryError = (error: Error) => {
    this.setState((current) => {
      if (current.error) {
        return current;
      }
      return { error: describeBoundaryError(error) };
    });
  };

  private handleWindowError = (event: Event) => {
    if (event instanceof ErrorEvent && event.error instanceof Error) {
      this.setBoundaryError(event.error);
      return;
    }
    const target = event.target;
    if (target instanceof HTMLScriptElement && /\.js(?:$|\?)/.test(target.src)) {
      this.setBoundaryError(
        new Error(`Failed to load script asset: ${target.src || "unknown script"}`)
      );
      return;
    }
    if (target instanceof HTMLLinkElement && /\.css(?:$|\?)/.test(target.href)) {
      this.setBoundaryError(
        new Error(`Failed to load style asset: ${target.href || "unknown stylesheet"}`)
      );
    }
  };

  private handleUnhandledRejection = (event: PromiseRejectionEvent) => {
    const error = normalizeError(event.reason);
    if (error) {
      this.setBoundaryError(error);
    }
  };

  private handleRefresh = () => {
    window.location.reload();
  };

  render() {
    if (this.state.error) {
      const { title, message, detail, suggestRefresh } = this.state.error;
      return (
        <div className="wb-route-fallback">
          <div className="wb-route-fallback-card">
            <p className="wb-kicker">OctoAgent Workbench</p>
            <h1>{title}</h1>
            <p>{message}</p>
            {detail ? <p className="wb-route-fallback-detail">{detail}</p> : null}
            <div className="wb-action-bar wb-route-fallback-actions">
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={this.handleRefresh}
              >
                刷新页面
              </button>
              <a href="/" className="wb-button wb-button-secondary">
                回到首页
              </a>
              {suggestRefresh ? (
                <span className="wb-route-fallback-note">
                  这通常发生在系统刚更新、而你还停留在旧标签页时。
                </span>
              ) : null}
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
