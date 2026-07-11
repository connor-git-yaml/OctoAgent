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
});
