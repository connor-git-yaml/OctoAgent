import { describe, expect, it } from "vitest";
import {
  F149_SNAPSHOT_RESOURCE_NAMES,
  decodeF149Snapshot,
} from "./index";
import type { RawSnapshotObject } from "./raw/snapshotDecoder";

const GENERATED_AT = "2026-07-25T12:00:00Z";

function buildRegistry(): RawSnapshotObject {
  return {
    contract_version: "1.0.0",
    resource_type: "action_registry",
    resource_id: "actions:registry",
    schema_version: 1,
    generated_at: GENERATED_AT,
    updated_at: GENERATED_AT,
    status: "ready",
    degraded: {
      is_degraded: false,
      reasons: [],
      unavailable_sections: [],
    },
    warnings: [],
    capabilities: [],
    refs: {},
    actions: [
      { action_id: "project.select", params_schema: {}, result_schema: {} },
      { action_id: "mcp.install", params_schema: {}, result_schema: {} },
    ],
  };
}

function buildSnapshot(): RawSnapshotObject {
  return {
    status: "ready",
    contract_version: "1.0.0",
    generated_at: GENERATED_AT,
    registry: buildRegistry(),
    degraded_sections: [],
    resource_errors: {},
    resources: Object.fromEntries(
      F149_SNAPSHOT_RESOURCE_NAMES.map((name) => [
        name,
        {
          resource_type: `${name}:document`,
          resource_id: `${name}:primary`,
          status: "ready",
          metadata: {
            raw_only_sentinel: "must-not-cross-adapter",
          },
        },
      ])
    ),
  };
}

describe("decodeF149Snapshot", () => {
  it("把完整raw envelope收窄为实际消费字段", () => {
    const result = decodeF149Snapshot(buildSnapshot());

    expect(result, "F149_SNAPSHOT_DECODER_MISSING").toEqual({
      ok: true,
      value: {
        status: "ready",
        contractVersion: "1.0.0",
        generatedAt: GENERATED_AT,
        resources: F149_SNAPSHOT_RESOURCE_NAMES.map((name) => ({
          name,
          resourceType: `${name}:document`,
          resourceId: `${name}:primary`,
          status: "ready",
        })),
        degradedSections: [],
        resourceErrors: [],
        actionIds: ["mcp.install", "project.select"],
      },
    });
    expect(JSON.stringify(result)).not.toContain("raw_only_sentinel");
  });

  it("缺任一冻结section时fail closed", () => {
    const snapshot = buildSnapshot();
    const resources = snapshot.resources as RawSnapshotObject;
    delete resources.memory;

    expect(decodeF149Snapshot(snapshot)).toEqual({
      ok: false,
      error: {
        code: "F149_SNAPSHOT_RESOURCE_SET_INVALID",
        path: "$.resources",
      },
    });
  });

  it("保留degraded与resource error的稳定摘要但丢弃开放metadata", () => {
    const snapshot = buildSnapshot();
    snapshot.status = "degraded";
    snapshot.degraded_sections = ["diagnostics"];
    snapshot.resource_errors = {
      diagnostics: {
        code: "DIAGNOSTICS_UNAVAILABLE",
        error_type: "dependency",
        message: "暂时不可用",
        metadata: { secret: "must-not-cross-adapter" },
      },
    };

    const result = decodeF149Snapshot(snapshot);

    expect(result.ok).toBe(true);
    if (!result.ok) {
      throw new Error("F149_SNAPSHOT_DECODER_MISSING");
    }
    expect(result.value.degradedSections).toEqual(["diagnostics"]);
    expect(result.value.resourceErrors).toEqual([
      {
        name: "diagnostics",
        code: "DIAGNOSTICS_UNAVAILABLE",
        errorType: "dependency",
        message: "暂时不可用",
      },
    ]);
    expect(JSON.stringify(result)).not.toContain("must-not-cross-adapter");
  });
});
