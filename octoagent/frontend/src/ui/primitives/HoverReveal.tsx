import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

interface HoverRevealProps {
  label: string;
  children: ReactNode;
  expanded: boolean;
  onToggle: (expanded: boolean) => void;
  ariaLabel: string;
  triggerClassName?: string;
  triggerContent?: ReactNode;
  wrapperClassName?: string;
}

export default function HoverReveal({
  label,
  children,
  expanded,
  onToggle,
  ariaLabel,
  triggerClassName,
  triggerContent,
  wrapperClassName,
}: HoverRevealProps) {
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const closeTimerRef = useRef<number | null>(null);
  const [portalReady, setPortalReady] = useState(false);
  const [cardStyle, setCardStyle] = useState<CSSProperties>({});

  function clearCloseTimer() {
    if (closeTimerRef.current != null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }

  function openCard() {
    clearCloseTimer();
    onToggle(true);
  }

  function scheduleClose() {
    clearCloseTimer();
    closeTimerRef.current = window.setTimeout(() => {
      onToggle(false);
      closeTimerRef.current = null;
    }, 90);
  }

  useEffect(() => {
    setPortalReady(true);
    return () => {
      clearCloseTimer();
    };
  }, []);

  useLayoutEffect(() => {
    if (!expanded || !triggerRef.current || !cardRef.current) {
      return;
    }

    const margin = 12;
    const gap = 10;
    const triggerRect = triggerRef.current.getBoundingClientRect();
    const cardRect = cardRef.current.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    let left = triggerRect.left;
    if (left + cardRect.width > viewportWidth - margin) {
      left = triggerRect.right - cardRect.width;
    }
    left = Math.max(margin, Math.min(left, viewportWidth - cardRect.width - margin));

    let top = triggerRect.bottom + gap;
    if (top + cardRect.height > viewportHeight - margin) {
      const above = triggerRect.top - cardRect.height - gap;
      top = above >= margin ? above : viewportHeight - cardRect.height - margin;
    }
    top = Math.max(margin, Math.min(top, viewportHeight - cardRect.height - margin));

    setCardStyle({
      position: "fixed",
      left: `${left}px`,
      top: `${top}px`,
    });
  }, [expanded, children]);

  return (
    <div
      className={`wb-hover-reveal${wrapperClassName ? ` ${wrapperClassName}` : ""}`}
      onMouseEnter={openCard}
      onMouseLeave={scheduleClose}
    >
      <button
        ref={triggerRef}
        type="button"
        className={`wb-hover-reveal-trigger${triggerClassName ? ` ${triggerClassName}` : ""}`}
        aria-expanded={expanded}
        onClick={() => onToggle(!expanded)}
        onFocus={openCard}
        onBlur={scheduleClose}
      >
        {triggerContent ?? label}
      </button>
      {expanded && portalReady
        ? createPortal(
            <div
              ref={cardRef}
              className="wb-hover-reveal-card is-floating"
              role="note"
              aria-label={ariaLabel}
              style={cardStyle}
              onMouseEnter={openCard}
              onMouseLeave={scheduleClose}
            >
              {children}
            </div>,
            document.body
          )
        : null}
    </div>
  );
}
