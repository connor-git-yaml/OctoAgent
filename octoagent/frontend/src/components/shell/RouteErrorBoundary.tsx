/**
 * RouteErrorBoundary — 单条 Route 级别的错误边界（Feature 079 Phase 1）。
 *
 * 与 RootErrorBoundary 的区别：
 * - RootErrorBoundary：App 级兜底，接管整个页面（shell + route）
 * - RouteErrorBoundary：仅包裹单条 lazy route，捕获 chunk 加载失败、渲染异常等
 *
 * 事故背景：Feature 078 发布后，用户长时间打开的 WebUI 加载旧 HTML，触发 lazy
 * chunk 404。此时 RootErrorBoundary 接管整个 App，shell + 顶栏 + banner 全被
 * 替换成"请刷新一次"卡片，保存时的 workbench.error banner / modal 都被吞掉，
 * 用户无法判断到底是"保存失败"还是"缓存失败"。
 *
 * 解决方案：每条 route 自己有 boundary。chunk 404 只影响对应子树，shell 和其他
 * route 的 banner 保持可见。用户可以点 "重试本页" 或切到其他 route 继续操作。
 *
 * 与 RootErrorBoundary 的分工：
 * - 子树 throw（lazy 失败 / 组件 bug）→ 这里捕获
 * - window global error（脚本加载失败等）→ 仍由 RootErrorBoundary 捕获
 */

import React, { type ErrorInfo, type ReactNode } from "react";

interface RouteErrorBoundaryProps {
  children: ReactNode;
  /** 用于 key 重置 —— path 变化时自动 remount */
  routeKey?: string;
  /** 显示给用户的页面名（如 "设置中心"），影响卡片标题 */
  pageLabel?: string;
}

interface RouteErrorBoundaryState {
  error: Error | null;
  isChunkMismatch: boolean;
  retryCount: number;
}

function isChunkMismatchError(error: Error): boolean {
  const lower = (error.message ?? "").toLowerCase();
  return (
    lower.includes("chunkloaderror") ||
    lower.includes("loading chunk") ||
    lower.includes("failed to fetch dynamically imported module") ||
    lower.includes("importing a module script failed") ||
    lower.includes("failed to load module script")
  );
}

export default class RouteErrorBoundary extends React.Component<
  RouteErrorBoundaryProps,
  RouteErrorBoundaryState
> {
  state: RouteErrorBoundaryState = {
    error: null,
    isChunkMismatch: false,
    retryCount: 0,
  };

  static getDerivedStateFromError(error: Error): Partial<RouteErrorBoundaryState> {
    return {
      error,
      isChunkMismatch: isChunkMismatchError(error),
    };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 不 console.error 成上游那样遮遮掩掩——这里明确打到前端 console，便于开发定位
    // RootErrorBoundary 只负责 shell 级兜底，不重复打
    console.error("RouteErrorBoundary caught", {
      error,
      componentStack: info.componentStack,
      routeKey: this.props.routeKey,
    });
  }

  componentDidUpdate(prev: RouteErrorBoundaryProps) {
    // 路由切换时重置 state，确保上一个 route 的错误不会影响新 route 的渲染
    if (prev.routeKey !== this.props.routeKey && this.state.error) {
      this.setState({ error: null, isChunkMismatch: false, retryCount: 0 });
    }
  }

  private handleRetry = () => {
    // 仅 reset boundary state。如果是 chunk 404，需要用户刷新整个页面才能拿到新
    // dist；如果是组件渲染异常，reset 可能足以恢复。
    // retryCount ≥ 2 后只允许硬刷新，避免无效循环。
    this.setState((prev) => ({
      error: null,
      isChunkMismatch: false,
      retryCount: prev.retryCount + 1,
    }));
  };

  private handleReload = () => {
    // 加时间戳绕缓存，强制浏览器向 server 拉最新 HTML
    const url = new URL(window.location.href);
    url.searchParams.set("_cb", String(Date.now()));
    window.location.href = url.toString();
  };

  render() {
    const { error, isChunkMismatch, retryCount } = this.state;
    if (!error) {
      return this.props.children;
    }

    const pageLabel = this.props.pageLabel || "当前页面";
    const title = isChunkMismatch
      ? `${pageLabel}需要刷新才能加载`
      : `${pageLabel}没有正常加载`;
    const message = isChunkMismatch
      ? "系统刚更新过，这一块的前端资源已经是旧版本。点下方按钮刷新，就能拿到最新界面。"
      : "这次渲染出了点问题。你可以重试本页，或者切到其他页面继续操作——其他页面和未保存的内容都还在。";
    const allowInBoundaryRetry = !isChunkMismatch && retryCount < 2;

    return (
      <div className="wb-route-fallback" role="alert">
        <div className="wb-route-fallback-card">
          <p className="wb-kicker">OctoAgent Workbench</p>
          <h1>{title}</h1>
          <p>{message}</p>
          <p className="wb-route-fallback-detail">{error.message}</p>
          <div className="wb-action-bar wb-route-fallback-actions">
            {allowInBoundaryRetry ? (
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={this.handleRetry}
              >
                重试本页
              </button>
            ) : (
              <button
                type="button"
                className="wb-button wb-button-primary"
                onClick={this.handleReload}
              >
                刷新页面
              </button>
            )}
            <a href="/" className="wb-button wb-button-secondary">
              回到首页
            </a>
          </div>
          <span className="wb-route-fallback-note">
            其他页面不会受影响，shell 和左侧导航仍然可用。
          </span>
        </div>
      </div>
    );
  }
}
