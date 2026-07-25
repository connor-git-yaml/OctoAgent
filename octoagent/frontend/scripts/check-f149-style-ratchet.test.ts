import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { inspectStyleRatchet } from "./check-f149-style-ratchet.mjs";

type Fixture = {
  code: string;
  indexCss: string;
  source: string;
};

const createdRoots: string[] = [];
const zeroPolicy = {
  indexMaxLines: 2,
  baseline: { palette: 0, spotify: 0, nonCpToken: 0, secondTheme: 0 },
};

async function createFixture(indexCss: string, source: string): Promise<string> {
  const root = await mkdtemp(path.join(tmpdir(), "f149-style-ratchet-"));
  createdRoots.push(root);
  await mkdir(path.join(root, "styles"), { recursive: true });
  await Promise.all([
    writeFile(path.join(root, "index.css"), indexCss, "utf8"),
    writeFile(path.join(root, "styles", "surface.css"), source, "utf8"),
  ]);
  return root;
}

const violations: Fixture[] = [
  {
    code: "F149_INDEX_CSS_GROWTH",
    indexCss: ":root {}\nbody {}\nmain {}\n",
    source: ".card { color: var(--cp-ink); }\n",
  },
  {
    code: "F149_HARDCODED_PALETTE_GROWTH",
    indexCss: ":root {}\nbody {}\n",
    source: ".card { background: #ffffff; }\n",
  },
  {
    code: "F149_SPOTIFY_THEME_GROWTH",
    indexCss: ":root {}\nbody {}\n",
    source: ".card { color: var(--cp-spotify-green); }\n",
  },
  {
    code: "F149_SECOND_THEME_GROWTH",
    indexCss: ":root {}\nbody {}\n",
    source: "[data-theme='light'] .card { color: var(--cp-ink); }\n",
  },
  {
    code: "F149_NON_CP_TOKEN_GROWTH",
    indexCss: ":root {}\nbody {}\n",
    source: ":root { --surface: white; } .card { color: var(--surface); }\n",
  },
];

afterEach(async () => {
  await Promise.all(createdRoots.splice(0).map((root) => rm(root, { recursive: true })));
});

describe("F149 style ratchet", () => {
  it("rejects every seeded style and theme regression", async () => {
    const missed: string[] = [];
    for (const fixture of violations) {
      const root = await createFixture(fixture.indexCss, fixture.source);
      const codes = inspectStyleRatchet(root, zeroPolicy).violations.map((item) => item.code);
      if (!codes.includes(fixture.code)) {
        missed.push(fixture.code);
      }
    }
    expect(missed, `F149_STYLE_RATCHET_RULES_MISSING: ${missed.join(",")}`).toEqual([]);
  });

  it("accepts co-located styles that only consume cp tokens", async () => {
    const root = await createFixture(
      ":root {}\nbody {}\n",
      ".card { color: var(--cp-ink); background: var(--cp-card); }\n",
    );
    expect(inspectStyleRatchet(root, zeroPolicy).violations).toEqual([]);
  });
});
