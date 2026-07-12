/**
 * domains/chat/constants L4 直测 —— F143 件 2 块 E
 */
import { describe, expect, it } from "vitest";
import {
  CHAT_SLASH_COMMANDS,
  TERMINAL_TASK_STATUSES,
  matchSlashCommands,
  readRecordString,
} from "./constants";

describe("matchSlashCommands", () => {
  it("非斜杠输入返回空", () => {
    expect(matchSlashCommands("approve")).toEqual([]);
    expect(matchSlashCommands("")).toEqual([]);
  });

  it("'/' 前缀匹配全部命令，逐字符收窄", () => {
    expect(matchSlashCommands("/")).toHaveLength(CHAT_SLASH_COMMANDS.length);
    expect(matchSlashCommands("/a").map((c) => c.value)).toEqual([
      "/approve",
      "/approve always",
    ]);
    expect(matchSlashCommands("/approve a").map((c) => c.value)).toEqual(["/approve always"]);
    expect(matchSlashCommands("/deny").map((c) => c.value)).toEqual(["/deny"]);
  });

  it("大小写与两端空白不敏感", () => {
    expect(matchSlashCommands("  /APPROVE ")).toHaveLength(2);
  });

  it("无前缀命中返回空", () => {
    expect(matchSlashCommands("/x")).toEqual([]);
  });
});

describe("constants 杂项", () => {
  it("TERMINAL_TASK_STATUSES 覆盖四个终态", () => {
    for (const status of ["SUCCEEDED", "FAILED", "CANCELLED", "REJECTED"]) {
      expect(TERMINAL_TASK_STATUSES.has(status), status).toBe(true);
    }
    expect(TERMINAL_TASK_STATUSES.has("RUNNING")).toBe(false);
  });

  it("readRecordString 只透传 string 值", () => {
    expect(readRecordString({ a: "x" }, "a")).toBe("x");
    expect(readRecordString({ a: 1 }, "a")).toBe("");
    expect(readRecordString({}, "a")).toBe("");
  });
});
