import { Fragment, type ReactNode } from "react";

interface MarkdownContentProps {
  content: string;
}

type MarkdownBlock =
  | { type: "heading"; level: 1 | 2 | 3; text: string }
  | { type: "unordered-list"; items: string[] }
  | { type: "ordered-list"; items: string[] }
  | { type: "code"; code: string }
  | { type: "paragraph"; text: string };

function parseInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\[([^\]]+)\]\((https?:\/\/[^\s)]+)\))|(\*\*([^*]+)\*\*)|(`([^`]+)`)/g;
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index));
    }
    if (match[2] && match[3]) {
      nodes.push(
        <a
          key={`link-${match.index}`}
          href={match[3]}
          target="_blank"
          rel="noreferrer"
          className="wb-markdown-link"
        >
          {match[2]}
        </a>
      );
    } else if (match[5]) {
      nodes.push(<strong key={`strong-${match.index}`}>{match[5]}</strong>);
    } else if (match[7]) {
      nodes.push(<code key={`code-${match.index}`}>{match[7]}</code>);
    }
    cursor = pattern.lastIndex;
  }

  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }

  return nodes;
}

function renderInline(text: string): ReactNode {
  const lines = text.split("\n");
  return lines.map((line, index) => (
    <Fragment key={`line-${index}`}>
      {index > 0 ? <br /> : null}
      {parseInline(line)}
    </Fragment>
  ));
}

function pushTextBlocks(blocks: MarkdownBlock[], text: string): void {
  const rawBlocks = text.split(/\n{2,}/);
  for (const rawBlock of rawBlocks) {
    const block = rawBlock.trim();
    if (!block) {
      continue;
    }
    const lines = block.split("\n").map((line) => line.trimEnd());
    const heading = lines.length === 1 ? /^(#{1,3})\s+(.+)$/.exec(lines[0].trim()) : null;
    if (heading) {
      blocks.push({
        type: "heading",
        level: heading[1].length as 1 | 2 | 3,
        text: heading[2],
      });
      continue;
    }

    const unordered = lines.every((line) => /^[-*]\s+/.test(line.trim()));
    if (unordered) {
      blocks.push({
        type: "unordered-list",
        items: lines.map((line) => line.trim().replace(/^[-*]\s+/, "")),
      });
      continue;
    }

    const ordered = lines.every((line) => /^\d+\.\s+/.test(line.trim()));
    if (ordered) {
      blocks.push({
        type: "ordered-list",
        items: lines.map((line) => line.trim().replace(/^\d+\.\s+/, "")),
      });
      continue;
    }

    blocks.push({ type: "paragraph", text: lines.join("\n") });
  }
}

function stripCodeFence(block: string): string {
  return block.replace(/^```[\w-]*\n?/, "").replace(/\n?```$/, "");
}

function parseBlocks(markdown: string): MarkdownBlock[] {
  const trimmed = markdown.trim();
  if (!trimmed) {
    return [];
  }

  const blocks: MarkdownBlock[] = [];
  const codeBlockPattern = /```[\w-]*\n?[\s\S]*?```/g;
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = codeBlockPattern.exec(trimmed)) !== null) {
    if (match.index > cursor) {
      pushTextBlocks(blocks, trimmed.slice(cursor, match.index));
    }
    blocks.push({
      type: "code",
      code: stripCodeFence(match[0]),
    });
    cursor = codeBlockPattern.lastIndex;
  }

  if (cursor < trimmed.length) {
    pushTextBlocks(blocks, trimmed.slice(cursor));
  }

  return blocks;
}

export function MarkdownContent({ content }: MarkdownContentProps) {
  const blocks = parseBlocks(content);
  if (blocks.length === 0) {
    return <p className="wb-markdown-paragraph">{content}</p>;
  }

  return (
    <div className="wb-markdown">
      {blocks.map((block, index) => {
        switch (block.type) {
          case "heading": {
            if (block.level === 1) {
              return <h1 key={`block-${index}`}>{renderInline(block.text)}</h1>;
            }
            if (block.level === 2) {
              return <h2 key={`block-${index}`}>{renderInline(block.text)}</h2>;
            }
            return <h3 key={`block-${index}`}>{renderInline(block.text)}</h3>;
          }
          case "unordered-list":
            return (
              <ul key={`block-${index}`}>
                {block.items.map((item, itemIndex) => (
                  <li key={`item-${itemIndex}`}>{renderInline(item)}</li>
                ))}
              </ul>
            );
          case "ordered-list":
            return (
              <ol key={`block-${index}`}>
                {block.items.map((item, itemIndex) => (
                  <li key={`item-${itemIndex}`}>{renderInline(item)}</li>
                ))}
              </ol>
            );
          case "code":
            return (
              <pre key={`block-${index}`}>
                <code>{block.code}</code>
              </pre>
            );
          case "paragraph":
          default:
            return (
              <p key={`block-${index}`} className="wb-markdown-paragraph">
                {renderInline(block.text)}
              </p>
            );
        }
      })}
    </div>
  );
}
