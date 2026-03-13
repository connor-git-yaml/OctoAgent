import { describe, expect, it } from "vitest";
import {
  CANONICAL_CONTROL_RESOURCE_MANIFEST,
  RESOURCE_ROUTE_BY_TYPE,
  SNAPSHOT_RESOURCE_KEY_BY_ROUTE,
} from "./controlPlane";

describe("controlPlane contract manifest", () => {
  it("保留稳定的 canonical resource manifest 快照", () => {
    const manifest = Object.values(CANONICAL_CONTROL_RESOURCE_MANIFEST).map((entry) => ({
      route: entry.route,
      snapshotKey: entry.snapshotKey,
      endpointPath: entry.endpointPath,
      queryMode: entry.queryMode,
    }));

    expect(manifest).toMatchInlineSnapshot(`
      [
        {
          "endpointPath": "/api/control/resources/wizard",
          "queryMode": "snapshot-resource",
          "route": "wizard",
          "snapshotKey": "wizard",
        },
        {
          "endpointPath": "/api/control/resources/config",
          "queryMode": "snapshot-resource",
          "route": "config",
          "snapshotKey": "config",
        },
        {
          "endpointPath": "/api/control/resources/project-selector",
          "queryMode": "snapshot-resource",
          "route": "project-selector",
          "snapshotKey": "project_selector",
        },
        {
          "endpointPath": "/api/control/resources/sessions",
          "queryMode": "snapshot-resource",
          "route": "sessions",
          "snapshotKey": "sessions",
        },
        {
          "endpointPath": "/api/control/resources/worker-profiles",
          "queryMode": "snapshot-resource",
          "route": "worker-profiles",
          "snapshotKey": "worker_profiles",
        },
        {
          "endpointPath": "/api/control/resources/context-frames",
          "queryMode": "snapshot-resource",
          "route": "context-frames",
          "snapshotKey": "context_continuity",
        },
        {
          "endpointPath": "/api/control/resources/policy-profiles",
          "queryMode": "snapshot-resource",
          "route": "policy-profiles",
          "snapshotKey": "policy_profiles",
        },
        {
          "endpointPath": "/api/control/resources/capability-pack",
          "queryMode": "snapshot-resource",
          "route": "capability-pack",
          "snapshotKey": "capability_pack",
        },
        {
          "endpointPath": "/api/control/resources/skill-governance",
          "queryMode": "snapshot-resource",
          "route": "skill-governance",
          "snapshotKey": "skill_governance",
        },
        {
          "endpointPath": "/api/control/resources/skill-provider-catalog",
          "queryMode": "snapshot-resource",
          "route": "skill-provider-catalog",
          "snapshotKey": "skill_provider_catalog",
        },
        {
          "endpointPath": "/api/control/resources/mcp-provider-catalog",
          "queryMode": "snapshot-resource",
          "route": "mcp-provider-catalog",
          "snapshotKey": "mcp_provider_catalog",
        },
        {
          "endpointPath": "/api/control/resources/setup-governance",
          "queryMode": "snapshot-resource",
          "route": "setup-governance",
          "snapshotKey": "setup_governance",
        },
        {
          "endpointPath": "/api/control/resources/delegation",
          "queryMode": "snapshot-resource",
          "route": "delegation",
          "snapshotKey": "delegation",
        },
        {
          "endpointPath": "/api/control/resources/pipelines",
          "queryMode": "snapshot-resource",
          "route": "pipelines",
          "snapshotKey": "pipelines",
        },
        {
          "endpointPath": "/api/control/resources/automation",
          "queryMode": "snapshot-resource",
          "route": "automation",
          "snapshotKey": "automation",
        },
        {
          "endpointPath": "/api/control/resources/diagnostics",
          "queryMode": "snapshot-resource",
          "route": "diagnostics",
          "snapshotKey": "diagnostics",
        },
        {
          "endpointPath": "/api/control/resources/memory",
          "queryMode": "memory-query",
          "route": "memory",
          "snapshotKey": "memory",
        },
        {
          "endpointPath": "/api/control/resources/import-workbench",
          "queryMode": "import-query",
          "route": "import-workbench",
          "snapshotKey": "imports",
        },
      ]
    `);
  });

  it("resource type 映射和 snapshot key 映射只引用已声明的 routes", () => {
    const declaredRoutes = new Set(Object.keys(CANONICAL_CONTROL_RESOURCE_MANIFEST));

    Object.values(RESOURCE_ROUTE_BY_TYPE).forEach((route) => {
      expect(declaredRoutes.has(route)).toBe(true);
    });
    Object.keys(SNAPSHOT_RESOURCE_KEY_BY_ROUTE).forEach((route) => {
      expect(declaredRoutes.has(route)).toBe(true);
    });
  });
});
