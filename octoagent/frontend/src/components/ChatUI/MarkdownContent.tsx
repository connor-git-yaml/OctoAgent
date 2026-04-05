import { memo, useMemo } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";

// 外部链接自动 target="_blank"
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

interface MarkdownContentProps {
  content: string;
}

/**
 * Markdown 渲染组件。
 *
 * 使用 marked（GFM 完整支持）+ DOMPurify（XSS 防护）。
 * memo 化：content 不变时不重渲染。
 */
export const MarkdownContent = memo(function MarkdownContent({
  content,
}: MarkdownContentProps) {
  const html = useMemo(() => {
    if (!content) return "";
    const raw = marked.parse(content, { breaks: true, gfm: true });
    return DOMPurify.sanitize(raw as string);
  }, [content]);

  if (!html) return null;

  return (
    <div
      className="wb-markdown"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
});
