/**
 * workbench/utils L4 直测 —— F143 件 3
 */
import { describe, expect, it } from "vitest";
import type { ConfigFieldHint } from "../types";
import {
  categoryForHint,
  deepClone,
  findSchemaNode,
  formatSessionDisplayTitle,
  formatSupportStatus,
  formatWorkerTemplateLabel,
  formatWorkerTemplateName,
  getValueAtPath,
  parseFieldStateValue,
  setValueAtPath,
  widgetValueToFieldState,
} from "./utils";

function makeHint(overrides: Partial<ConfigFieldHint> = {}): ConfigFieldHint {
  return {
    path: "runtime.mode",
    label: "运行模式",
    section: "runtime",
    widget: "text",
    ...overrides,
  } as ConfigFieldHint;
}

describe("formatSessionDisplayTitle", () => {
  it("级联：alias > title > latestMessageSummary（截断）> fallback > 未命名", () => {
    expect(formatSessionDisplayTitle({ alias: " 我的会话 ", title: "标题" })).toBe("我的会话");
    expect(formatSessionDisplayTitle({ title: "标题" })).toBe("标题");
    expect(
      formatSessionDisplayTitle({ latestMessageSummary: "x".repeat(50), latestMessageLimit: 10 })
    ).toBe("x".repeat(10));
    expect(formatSessionDisplayTitle({ fallbackTitle: "兜底" })).toBe("兜底");
    expect(formatSessionDisplayTitle({})).toBe("未命名对话");
  });
});

describe("worker 模板名格式化", () => {
  it("剥 Root Agent 后缀 + 空名兜底 + 模板后缀幂等", () => {
    expect(formatWorkerTemplateName("Research Root Agent")).toBe("Research");
    expect(formatWorkerTemplateName("  ")).toBe("未命名模板");
    expect(formatWorkerTemplateLabel("Research Root Agent")).toBe("Research 模板");
    expect(formatWorkerTemplateLabel("研究模板")).toBe("研究模板");
  });

  it("formatSupportStatus 全枚举", () => {
    expect(formatSupportStatus("supported")).toBe("可用");
    expect(formatSupportStatus("degraded")).toBe("降级");
    expect(formatSupportStatus("hidden")).toBe("隐藏");
    expect(formatSupportStatus("unsupported")).toBe("不支持");
    expect(formatSupportStatus(undefined)).toBe("未知");
  });
});

describe("路径读写", () => {
  it("getValueAtPath：嵌套对象/数组索引/未命中", () => {
    const source = { a: { b: [{ c: 1 }] } };
    expect(getValueAtPath(source, "a.b.0.c")).toBe(1);
    expect(getValueAtPath(source, "a.x")).toBeUndefined();
    expect(getValueAtPath(source, "a.b.zz")).toBeUndefined();
  });

  it("setValueAtPath：就地写入并按下一段类型自动建容器", () => {
    const source: Record<string, unknown> = {};
    setValueAtPath(source, "a.b.0.c", 42);
    expect(source).toEqual({ a: { b: [{ c: 42 }] } });
    setValueAtPath(source, "a.d", "x");
    expect(getValueAtPath(source, "a.d")).toBe("x");
  });

  it("setValueAtPath：空路径与非法数组下标为 no-op", () => {
    const source: Record<string, unknown> = { arr: [1] };
    setValueAtPath(source, "", 1);
    setValueAtPath(source, "arr.notNumber", 9);
    expect(source).toEqual({ arr: [1] });
  });

  it("deepClone 是值拷贝", () => {
    const source = { a: { b: 1 } };
    const cloned = deepClone(source);
    cloned.a.b = 2;
    expect(source.a.b).toBe(1);
  });
});

describe("findSchemaNode", () => {
  const schema = {
    properties: {
      providers: {
        items: {
          properties: {
            name: { type: "string" },
          },
        },
      },
    },
  };

  it("按 properties/items 走 JSON schema 路径", () => {
    expect(findSchemaNode(schema, "providers.0.name")).toEqual({ type: "string" });
    expect(findSchemaNode(schema, "providers")).toBe(schema.properties.providers);
  });

  it("未命中返回 null", () => {
    expect(findSchemaNode(schema, "missing")).toBeNull();
    expect(findSchemaNode(schema, "providers.0.missing")).toBeNull();
  });
});

describe("widget 值 ↔ 字段态往返", () => {
  it("toggle：布尔化往返", () => {
    const hint = makeHint({ widget: "toggle" });
    expect(widgetValueToFieldState(hint, 1)).toBe(true);
    expect(parseFieldStateValue(hint, true)).toEqual({ value: true, error: null });
  });

  it("string-list：数组 ↔ 换行文本，过滤空行", () => {
    const hint = makeHint({ widget: "string-list" });
    expect(widgetValueToFieldState(hint, ["a", "b"])).toBe("a\nb");
    expect(parseFieldStateValue(hint, "a\n \nb\n")).toEqual({ value: ["a", "b"], error: null });
  });

  it("provider-list/alias-map：JSON 文本往返 + 非法 JSON 报错", () => {
    const hint = makeHint({ widget: "provider-list", label: "Providers" });
    expect(widgetValueToFieldState(hint, [{ name: "x" }])).toBe(
      JSON.stringify([{ name: "x" }], null, 2)
    );
    expect(parseFieldStateValue(hint, "")).toEqual({ value: [], error: null });
    expect(parseFieldStateValue(hint, "not json").error).toContain("合法 JSON");
    const mapHint = makeHint({ widget: "alias-map" });
    expect(parseFieldStateValue(mapHint, "")).toEqual({ value: {}, error: null });
  });

  it("普通 text：null/undefined 归空串，其余 String 化", () => {
    const hint = makeHint();
    expect(widgetValueToFieldState(hint, null)).toBe("");
    expect(widgetValueToFieldState(hint, 42)).toBe("42");
    expect(parseFieldStateValue(hint, "x")).toEqual({ value: "x", error: null });
  });
});

describe("categoryForHint", () => {
  it("section → 分类映射", () => {
    expect(categoryForHint(makeHint({ section: "channels" }))).toBe("channels");
    expect(categoryForHint(makeHint({ section: "memory.recall" }))).toBe("memory");
    expect(categoryForHint(makeHint({ section: "providers" }))).toBe("main-agent");
    expect(categoryForHint(makeHint({ section: "runtime" }))).toBe("main-agent");
    expect(categoryForHint(makeHint({ section: "misc" }))).toBe("advanced");
  });
});
