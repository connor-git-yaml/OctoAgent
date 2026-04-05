import { memo, useMemo } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";

// 懒初始化独立实例（SSR 安全 + 避免全局污染）
let purify: ReturnType<typeof DOMPurify> | null = null;
function getPurify() {
  if (!purify && typeof window !== "undefined") {
    purify = DOMPurify(window);
    purify.addHook("afterSanitizeAttributes", (node) => {
      if (node.tagName === "A") {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
      }
    });
  }
  return purify;
}

interface MarkdownContentProps {
  content: string;
}

/**
 * Markdown 渲染组件。
 *
 * 使用 marked（GFM 完整支持）+ DOMPurify（XSS 防护）。
 * memo 化：content 不变时不重渲染和重解析。
 */
export const MarkdownContent = memo(function MarkdownContent({
  content,
}: MarkdownContentProps) {
  const html = useMemo(() => {
    if (!content) return "";
    const raw = marked.parse(content, { breaks: true, gfm: true, async: false }) as string;
    const p = getPurify();
    return p ? p.sanitize(raw) : raw;
  }, [content]);

  if (!html) return null;

  return (
    <div
      className="wb-markdown"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
});
