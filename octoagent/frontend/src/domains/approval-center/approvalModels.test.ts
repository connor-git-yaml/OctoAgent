/**
 * approvalModels 纯逻辑测试 — F145 AC-4（diff 解析器）+ D4 失败映射
 */
import { describe, it, expect } from "vitest";
import {
  compactSummary,
  consolidationSummary,
  formatRelativeTime,
  mapApprovalFailure,
  parseUnifiedDiff,
} from "./approvalModels";
import { ApprovalActionError } from "../../api/approval-center";
import type {
  CompactCandidate,
  ConsolidationCandidate,
} from "../../api/approval-center";

describe("parseUnifiedDiff", () => {
  it("空文本与服务端「无行级差异」哨兵返回空数组", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
    expect(parseUnifiedDiff("（无行级差异）\n")).toEqual([]);
  });

  it("解析混合增删：+/- 前缀映射 added/removed，上下文行映射 unchanged", () => {
    const diff = [
      "--- AGENTS.md（当前）",
      "+++ AGENTS.md（精简提议）",
      "@@ -1,4 +1,3 @@",
      " # AGENTS",
      "-- 回复保持简洁，不要冗长啰嗦",
      "-- 回答尽量简短",
      "+- 回复简洁精炼",
      " - commit message 用中文",
      "",
    ].join("\n");
    const rows = parseUnifiedDiff(diff);
    expect(rows).toEqual([
      { kind: "unchanged", text: "# AGENTS" },
      { kind: "removed", text: "- 回复保持简洁，不要冗长啰嗦" },
      { kind: "removed", text: "- 回答尽量简短" },
      { kind: "added", text: "- 回复简洁精炼" },
      { kind: "unchanged", text: "- commit message 用中文" },
    ]);
  });

  it("文件头与 no-newline 噪音被丢弃，hunk 之间插分隔行", () => {
    const diff = [
      "--- a",
      "+++ b",
      "@@ -1 +1 @@",
      "-旧行一",
      "+新行一",
      "@@ -10 +10 @@",
      "-旧行二",
      "+新行二",
      "\\ No newline at end of file",
    ].join("\n");
    const rows = parseUnifiedDiff(diff);
    expect(rows).toEqual([
      { kind: "removed", text: "旧行一" },
      { kind: "added", text: "新行一" },
      { kind: "unchanged", text: "⋯" },
      { kind: "removed", text: "旧行二" },
      { kind: "added", text: "新行二" },
    ]);
  });

  it("首个 hunk 之后内容行首为 ---/+++ 的真实改动不被误吞（Codex final P2）", () => {
    // 被删的 markdown 分隔线 "---" 在 unified diff 里呈现为 "----"；
    // 新增的 "+++" 字面量呈现为 "++++"。只有首个 @@ 之前的才是文件头。
    const diff = [
      "--- AGENTS.md（当前）",
      "+++ AGENTS.md（精简提议）",
      "@@ -1,3 +1,2 @@",
      "----",
      "-旧规则",
      "++++加粗分隔",
      " 上下文",
    ].join("\n");
    const rows = parseUnifiedDiff(diff);
    expect(rows).toEqual([
      { kind: "removed", text: "---" },
      { kind: "removed", text: "旧规则" },
      { kind: "added", text: "+++加粗分隔" },
      { kind: "unchanged", text: "上下文" },
    ]);
  });

  it("服务端超长截断尾注（无前缀行）按原文保留", () => {
    const diff = "@@ -1 +1 @@\n-旧\n+新\n…（diff 超长已截断）";
    const rows = parseUnifiedDiff(diff);
    expect(rows[rows.length - 1]).toEqual({
      kind: "unchanged",
      text: "…（diff 超长已截断）",
    });
  });
});

describe("mapApprovalFailure（D4 三态分流）", () => {
  it("conflict → 终态文案 + 移除卡片", () => {
    const out = mapApprovalFailure(
      new ApprovalActionError({
        httpStatus: 409,
        resultStatus: "conflict",
        detail: "源已变更",
      })
    );
    expect(out.removeCard).toBe(true);
    expect(out.message).toContain("已失效");
  });

  it("pending（回滚）→ 可重试文案 + 保留卡片", () => {
    const out = mapApprovalFailure(
      new ApprovalActionError({
        httpStatus: 409,
        resultStatus: "pending",
        detail: "内部回滚",
      })
    );
    expect(out.removeCard).toBe(false);
    expect(out.message).toContain("重试");
  });

  it("not_found → 已被处理文案 + 移除卡片", () => {
    const out = mapApprovalFailure(
      new ApprovalActionError({
        httpStatus: 404,
        resultStatus: "not_found",
        detail: "不存在",
      })
    );
    expect(out.removeCard).toBe(true);
    expect(out.message).toContain("已被处理");
  });

  it("unknown / 非 ApprovalActionError → 通用失败 + 保留卡片", () => {
    expect(
      mapApprovalFailure(
        new ApprovalActionError({
          httpStatus: 500,
          resultStatus: "unknown",
          detail: "boom",
        })
      ).removeCard
    ).toBe(false);
    expect(mapApprovalFailure(new Error("网络断了")).removeCard).toBe(false);
  });

  it("技术 detail 不进人话文案", () => {
    const out = mapApprovalFailure(
      new ApprovalActionError({
        httpStatus: 409,
        resultStatus: "conflict",
        detail: "source_hash mismatch: deadbeef != cafebabe",
      })
    );
    expect(out.message).not.toContain("deadbeef");
  });
});

describe("摘要文案", () => {
  it("consolidationSummary 给出人话合并摘要", () => {
    const c = { source_count: 3 } as ConsolidationCandidate;
    expect(consolidationSummary(c)).toBe("建议把 3 条相似记忆合并为一条");
  });

  it("compactSummary 给出文件与字数变化", () => {
    const c = {
      file_id: "AGENTS.md",
      size_before: 342,
      size_after: 242,
    } as CompactCandidate;
    expect(compactSummary(c)).toBe("建议精简「AGENTS.md」：约 342 字 → 约 242 字");
  });
});

describe("formatRelativeTime", () => {
  const now = new Date("2026-07-19T12:00:00Z").getTime();

  it("分钟/小时/天分档", () => {
    expect(formatRelativeTime("2026-07-19T11:59:40Z", now)).toBe("刚刚");
    expect(formatRelativeTime("2026-07-19T11:30:00Z", now)).toBe("30 分钟前");
    expect(formatRelativeTime("2026-07-19T09:00:00Z", now)).toBe("3 小时前");
    expect(formatRelativeTime("2026-07-17T12:00:00Z", now)).toBe("2 天前");
  });

  it("非法时间原样返回", () => {
    expect(formatRelativeTime("not-a-date", now)).toBe("not-a-date");
  });
});
