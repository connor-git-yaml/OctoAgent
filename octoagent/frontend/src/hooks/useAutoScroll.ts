import { useCallback, useEffect, useRef, useState } from "react";

const NEAR_BOTTOM_THRESHOLD = 150; // px — 距底部多少以内视为"在底部"

/**
 * 智能滚动 hook — "粘底"逻辑。
 *
 * - 用户在底部附近时，新消息自动滚底
 * - 用户主动上滚浏览历史时，不打断
 * - 不在底部时显示"↓ 新消息"提示
 * - 首次进入直接定位到底部（不从头滑下来）
 */
export function useAutoScroll(deps: unknown[]) {
  const containerRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const wasNearBottomRef = useRef(true);
  const isFirstRenderRef = useRef(true);
  const [showNewMessageHint, setShowNewMessageHint] = useState(false);

  // 滚动事件：实时追踪用户是否在底部附近
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const handleScroll = () => {
      const nearBottom =
        el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_THRESHOLD;
      wasNearBottomRef.current = nearBottom;
      // 用户手动滚到底部时，自动消除提示
      if (nearBottom) {
        setShowNewMessageHint(false);
      }
    };

    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, []);

  // deps 变化时（通常是 messages 变化）：按粘底状态决定是否滚底
  useEffect(() => {
    if (!endRef.current) return;

    if (isFirstRenderRef.current) {
      // 首次：直接定位到底部，不用 smooth（避免从头滑下来）
      endRef.current.scrollIntoView({ behavior: "instant" });
      isFirstRenderRef.current = false;
      return;
    }

    if (wasNearBottomRef.current) {
      endRef.current.scrollIntoView({ behavior: "smooth" });
    } else {
      setShowNewMessageHint(true);
    }
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
