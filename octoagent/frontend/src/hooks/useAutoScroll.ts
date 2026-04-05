import { useCallback, useEffect, useRef, useState } from "react";

const NEAR_BOTTOM_THRESHOLD = 150; // px — 距底部多少以内视为"在底部附近"

/**
 * 智能滚动 hook — "粘底"逻辑。
 *
 * - 用户在底部附近时，新消息自动滚底
 * - 用户主动上滚浏览历史时，不打断
 * - 不在底部时显示"↓ 新消息"提示
 * - 首次进入 / session 切换后直接定位到底部（instant）
 */
export function useAutoScroll(
  deps: React.DependencyList,
  resetKey?: string | null,
) {
  const containerRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const wasNearBottomRef = useRef(true);
  const isFirstRenderRef = useRef(true);
  const scrollRAFRef = useRef(0);
  const [showNewMessageHint, setShowNewMessageHint] = useState(false);

  // resetKey 变化时（session 切换）重置首屏标记
  useEffect(() => {
    isFirstRenderRef.current = true;
    wasNearBottomRef.current = true;
    setShowNewMessageHint(false);
  }, [resetKey]);

  // 滚动事件：实时追踪用户是否在底部附近
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const handleScroll = () => {
      const nearBottom =
        el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_THRESHOLD;
      wasNearBottomRef.current = nearBottom;
      if (nearBottom && showNewMessageHint) {
        setShowNewMessageHint(false);
      }
    };

    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, [showNewMessageHint]);

  // deps 变化时：按粘底状态决定是否滚底（rAF 去重防连续消息卡顿）
  useEffect(() => {
    if (!endRef.current) return;

    cancelAnimationFrame(scrollRAFRef.current);
    scrollRAFRef.current = requestAnimationFrame(() => {
      if (!endRef.current) return;

      if (isFirstRenderRef.current) {
        endRef.current.scrollIntoView({ behavior: "instant" });
        isFirstRenderRef.current = false;
        return;
      }

      if (wasNearBottomRef.current) {
        endRef.current.scrollIntoView({ behavior: "smooth" });
      } else {
        setShowNewMessageHint(true);
      }
    });

    // 清理：组件卸载时取消未执行的 rAF
    return () => cancelAnimationFrame(scrollRAFRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const scrollToBottom = useCallback(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
    setShowNewMessageHint(false);
  }, []);

  return { containerRef, endRef, scrollToBottom, showNewMessageHint };
}
