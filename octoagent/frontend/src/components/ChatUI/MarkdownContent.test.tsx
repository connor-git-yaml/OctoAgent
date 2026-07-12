import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownContent } from "./MarkdownContent";

describe("MarkdownContent", () => {
  it("会保留带空行的 fenced code block", () => {
    const { container } = render(
      <MarkdownContent
        content={"```ts\nconst first = 1;\n\nconst second = 2;\n```"}
      />
    );

    const code = container.querySelector("pre code");
    expect(code).not.toBeNull();
    // marked 按 CommonMark 规范保留 code block 末尾换行（<pre> 内不可见）
    expect(code?.textContent).toBe("const first = 1;\n\nconst second = 2;\n");
    expect(container.textContent ?? "").not.toContain("```");
  });

  // ── F143 件 5：XSS 消毒断言（LLM 输出是不可信内容，DOMPurify 是唯一防线）──
  // jsdom 可测部分；真浏览器 DOM 渲染差异留 L1（已归档）。
  describe("XSS 消毒（marked + DOMPurify）", () => {
    it("剥离 <script> 标签（含内联脚本体）", () => {
      const { container } = render(
        <MarkdownContent content={'正文<script>window.__pwned = true;</script>继续'} />
      );
      expect(container.querySelector("script")).toBeNull();
      expect(container.innerHTML).not.toContain("__pwned");
      expect(container.textContent).toContain("正文");
      expect(container.textContent).toContain("继续");
    });

    it("剥离事件处理器属性（img onerror / div onclick）", () => {
      const { container } = render(
        <MarkdownContent
          content={'<img src="x" onerror="window.__pwned=1"><div onclick="window.__pwned=2">点我</div>'}
        />
      );
      expect(container.querySelector("[onerror]")).toBeNull();
      expect(container.querySelector("[onclick]")).toBeNull();
      expect(container.innerHTML).not.toContain("__pwned");
    });

    it("javascript: 协议链接被消毒（markdown 链接与裸 HTML 两种形态）", () => {
      const { container } = render(
        <MarkdownContent
          content={'[点这里](javascript:alert(1))\n\n<a href="javascript:alert(2)">再点</a>'}
        />
      );
      for (const anchor of Array.from(container.querySelectorAll("a"))) {
        expect(anchor.getAttribute("href") ?? "").not.toMatch(/^\s*javascript:/i);
      }
      expect(container.innerHTML).not.toContain("javascript:alert");
    });

    it("嵌套/编码注入不复活：code block 内的 script 只作为文本呈现", () => {
      const { container } = render(
        <MarkdownContent
          content={"```html\n<script>window.__pwned=1</script>\n```\n\n&lt;script&gt;window.__pwned=2&lt;/script&gt;"}
        />
      );
      expect(container.querySelector("script")).toBeNull();
      // code block 内内容以纯文本保留（可见但不可执行）
      expect(container.querySelector("pre code")?.textContent).toContain("<script>");
    });

    it("iframe/object 等嵌入向量被剥离", () => {
      const { container } = render(
        <MarkdownContent
          content={'<iframe src="https://evil.example"></iframe><object data="https://evil.example"></object>'}
        />
      );
      expect(container.querySelector("iframe")).toBeNull();
      expect(container.querySelector("object")).toBeNull();
    });

    it("正常链接保留并强制 target=_blank + rel=noopener noreferrer（afterSanitizeAttributes 钩子）", () => {
      const { container } = render(
        <MarkdownContent content={"[官网](https://example.com)"} />
      );
      const anchor = container.querySelector("a");
      expect(anchor?.getAttribute("href")).toBe("https://example.com");
      expect(anchor?.getAttribute("target")).toBe("_blank");
      expect(anchor?.getAttribute("rel")).toBe("noopener noreferrer");
    });

    it("常规 markdown（表格/加粗/列表）消毒后保留结构", () => {
      const { container } = render(
        <MarkdownContent
          content={"| A | B |\n| - | - |\n| 1 | 2 |\n\n**加粗** 与\n\n- 列表项"}
        />
      );
      expect(container.querySelector("table")).not.toBeNull();
      expect(container.querySelector("strong")?.textContent).toBe("加粗");
      expect(container.querySelector("li")?.textContent).toBe("列表项");
    });

    it("空内容渲染为 null", () => {
      const { container } = render(<MarkdownContent content="" />);
      expect(container.firstChild).toBeNull();
    });
  });
});
