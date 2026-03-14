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
    expect(code?.textContent).toBe("const first = 1;\n\nconst second = 2;");
    expect(container.textContent ?? "").not.toContain("```");
  });
});
