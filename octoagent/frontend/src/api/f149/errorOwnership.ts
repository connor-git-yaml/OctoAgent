import { ApiError } from "../client";

export type F149ErrorOwnership =
  | {
      owner: "global-auth";
      state: "authentication";
      status: 401;
    }
  | {
      owner: "surface";
      state: "forbidden" | "not-found" | "conflict" | "recoverable";
      status: number;
    };

export function mapF149ErrorOwnership(error: unknown): F149ErrorOwnership {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 401) {
    return {
      owner: "global-auth",
      state: "authentication",
      status,
    };
  }
  const state =
    status === 403
      ? "forbidden"
      : status === 404
        ? "not-found"
        : status === 409
          ? "conflict"
          : "recoverable";
  return {
    owner: "surface",
    state,
    status,
  };
}
