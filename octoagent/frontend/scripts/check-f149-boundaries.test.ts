import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { inspectBoundaries } from "./check-f149-boundaries.mjs";

type Fixture = {
  code: string;
  files: Record<string, string>;
};

const createdRoots: string[] = [];

async function createFixture(files: Record<string, string>): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), "f149-boundary-"));
  createdRoots.push(root);
  await Promise.all(
    Object.entries(files).map(async ([relativePath, source]) => {
      const target = path.join(root, relativePath);
      await mkdir(path.dirname(target), { recursive: true });
      await writeFile(target, source, "utf8");
    }),
  );
  return root;
}

const violations: Fixture[] = [
  {
    code: "F149_DIRECT_FETCH",
    files: { "pages/Tasks.tsx": "export const load = () => fetch('/api/tasks');" },
  },
  {
    code: "F149_DIRECT_FETCH",
    files: { "pages/Tasks.tsx": "export const load = () => window.fetch('/api/tasks');" },
  },
  {
    code: "F149_AUTH_HEADER_BYPASS",
    files: {
      "api/tasks.ts": "export const headers = { Authorization: 'Bearer local-token' };",
    },
  },
  {
    code: "F149_AUTH_HEADER_BYPASS",
    files: {
      "api/tasks.ts":
        "export const headers = new Headers(); " +
        "headers.set('Authorization', 'Bearer local-token');",
    },
  },
  {
    code: "F149_TOKEN_HELPER_BYPASS",
    files: {
      "api/tasks.ts":
        "import { getFrontDoorToken } from './client'; " +
        "export const token = getFrontDoorToken();",
      "api/client.ts": "export const getFrontDoorToken = () => 'token';",
    },
  },
  {
    code: "F149_QUERY_TOKEN_BYPASS",
    files: {
      "api/tasks.ts":
        "export const build = (path: string, token: string) => " +
        "`${path}?access_token=${encodeURIComponent(token)}`;",
    },
  },
  {
    code: "F149_PAGE_401_OWNER",
    files: { "pages/Tasks.tsx": "export const state = { status: 401 };" },
  },
  {
    code: "F149_GENERATED_PAGE_IMPORT",
    files: {
      "pages/Tasks.tsx": "import type { TaskWire } from '../generated/f149/contracts';",
      "generated/f149/contracts.ts": "export type TaskWire = { id: string };",
    },
  },
  {
    code: "F149_REVERSE_LAYER_IMPORT",
    files: {
      "api/client.ts": "import { TasksPage } from '../pages/Tasks'; export { TasksPage };",
      "pages/Tasks.tsx": "export const TasksPage = () => null;",
    },
  },
  {
    code: "F149_DUPLICATE_TRANSPORT",
    files: { "domains/tasks/transport.ts": "export class TaskHttpClient {}" },
  },
  {
    code: "F149_DUPLICATE_STORE",
    files: { "pages/taskStore.ts": "export const createTaskStore = () => new Map();" },
  },
  {
    code: "F149_OPTIONAL_FALLBACK",
    files: {
      "api/f149/tasks.ts": "export const request = primaryRequest || legacyRequest;",
    },
  },
  {
    code: "F149_IMPORT_CYCLE",
    files: {
      "domains/tasks/a.ts": "import './b'; export const a = 1;",
      "domains/tasks/b.ts": "import './a'; export const b = 1;",
    },
  },
];

afterEach(async () => {
  await Promise.all(createdRoots.splice(0).map((root) => rm(root, { recursive: true })));
});

describe("F149 boundary checker", () => {
  it("rejects every seeded architecture violation", async () => {
    const missed: string[] = [];
    for (const fixture of violations) {
      const root = await createFixture(fixture.files);
      const codes = inspectBoundaries(root).violations.map((item) => item.code);
      if (!codes.includes(fixture.code)) {
        missed.push(fixture.code);
      }
    }
    expect(missed, `F149_BOUNDARY_RULES_MISSING: ${missed.join(",")}`).toEqual([]);
  });

  it("accepts the single transport and downward dependency path", async () => {
    const root = await createFixture({
      "api/client.ts":
        "export const request = async () => ({ ok: true }); " +
        "export const getFrontDoorToken = () => 'token';",
      "components/FrontDoorGate.tsx":
        "import { getFrontDoorToken } from '../api/client'; " +
        "export const FrontDoorGate = () => getFrontDoorToken();",
      "platform/contracts.ts": "export type RequestOptions = { signal?: AbortSignal };",
      "api/f149/tasks.ts":
        "import { request } from '../client'; " +
        "import type { RequestOptions } from '../../platform/contracts'; " +
        "export const load = (options: RequestOptions, fallbackLabel: string) => " +
        "request().then(() => fallbackLabel); export { request };",
      "platform/queries/tasks.ts": "import { request } from '../../api/f149/tasks'; export { request };",
      "domains/tasks/project.ts": "export const projectTask = (id: string) => ({ id });",
      "pages/Tasks.tsx":
        "import { request } from '../platform/queries/tasks'; " +
        "import { projectTask } from '../domains/tasks/project'; " +
        "export const deferStoredTaskIdRestore = false; " +
        "export const TasksPage = () => [request, projectTask];",
    });
    expect(inspectBoundaries(root).violations).toEqual([]);
  });
});
