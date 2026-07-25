import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mapF149ErrorOwnership } from "./f149/errorOwnership";
import {
  bulkDiscardCandidates,
  fetchMemoryCandidates,
  promoteCandidate,
} from "./memory-candidates";
import { ApiError } from "./client";

const clientMocks = vi.hoisted(() => ({
  frontDoorRequest: vi.fn(),
}));

vi.mock("./client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./client")>();
  return {
    ...actual,
    frontDoorRequest: clientMocks.frontDoorRequest,
  };
});

function jsonResponse(status: number, body: object): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("memory candidates unified transport", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    clientMocks.frontDoorRequest.mockReset();
    clientMocks.frontDoorRequest.mockImplementation((path, init) =>
      fetchMock(path, init)
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("list/promote/bulk只经api client request seam", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(200, { candidates: [], total: 0, pending_count: 0 })
      )
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }))
      .mockResolvedValueOnce(
        jsonResponse(200, { discarded_count: 2, skipped_ids: [] })
      );

    await fetchMemoryCandidates();
    await promoteCandidate("memory-1", "更新后的事实");
    await bulkDiscardCandidates(["memory-1", "memory-2"]);

    expect(
      clientMocks.frontDoorRequest,
      "F149_UNIFIED_TRANSPORT_MISSING"
    ).toHaveBeenCalledTimes(3);
    expect(clientMocks.frontDoorRequest.mock.calls).toEqual([
      ["/api/memory/candidates", undefined],
      [
        "/api/memory/candidates/memory-1/promote",
        {
          method: "POST",
          body: JSON.stringify({ fact_content: "更新后的事实" }),
        },
      ],
      [
        "/api/memory/candidates/bulk_discard",
        {
          method: "PUT",
          body: JSON.stringify({ candidate_ids: ["memory-1", "memory-2"] }),
        },
      ],
    ]);
  });

  it.each([
    [401, "global-auth", "authentication"],
    [403, "surface", "forbidden"],
    [404, "surface", "not-found"],
    [409, "surface", "conflict"],
  ] as const)(
    "HTTP %i按owner映射为%s/%s",
    (status, owner, state) => {
      expect(
        mapF149ErrorOwnership(
          new ApiError(`HTTP ${status}`, {
            status,
          })
        ),
        "F149_ERROR_OWNERSHIP_MISSING"
      ).toEqual({ owner, state, status });
    }
  );
});
