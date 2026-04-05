import { useCallback, useEffect, useRef, useState } from "react";

const NEAR_BOTTOM_THRESHOLD = 150;

/**
 * 智能滚动 hook — "粘底"逻辑。
 *
 * - 用户在底部附近时，新消息自动滚底
 * - 用户主动上滚浏览历史时，不打断
 * - 不在底部时显示"↓ 新消息"提示
 * - 首次进入 / session 切换后直接定位到底部（instant，不从头滑下来）
 *
 * @param deps — 触发滚动检查的依赖（通常是 [messages]）
 * @param resetKey — 变化时重置首屏标记（通常是 sessionId / taskId）
 */
export function useAutoScroll(deps: unknown[], resetKey?: unknown) {
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
      if (nearBottom) {
        setShowNewMessageHint(false);
      }
    };

    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, []);

  // deps 变化时：按粘底状态决定是否滚底（用 rAF 去重防止连续消息卡顿）
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const scrollToBottom = useCallback(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
    setShowNewMessageHint(false);
  }, []);

  const dismissHint = useCallback(() => {
    scrollToBottom();
  }, [scrollToBottom]);

  return { containerRef, endRef, scrollToBottom, showNewMessageHint, dismissHint };
}
