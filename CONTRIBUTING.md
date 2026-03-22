# Contributing to OctoAgent

Thank you for considering contributing to OctoAgent.

OctoAgent is developed as a Personal AI OS with a durable runtime, a Butler + Worker execution model, governed tools, and spec-driven delivery. Contributions are welcome, but changes should fit the architecture instead of adding short-lived patches.

## Before You Start

Please read these documents first:

- [`docs/blueprint.md`](./docs/blueprint.md)
- [`docs/codebase-architecture/README.md`](./docs/codebase-architecture/README.md)
- [`docs/milestone/README.md`](./docs/milestone/README.md)
- The relevant feature directory under `.specify/features/`

Blueprint rules are the upstream source of truth. If implementation and documentation disagree, update the spec trail first or as part of the same change.

## Development Setup

1. Clone the repository:

```bash
git clone https://github.com/connor-git-yaml/OctoAgent.git
cd OctoAgent/octoagent
```

2. Install backend dependencies:

```bash
uv sync
```

3. Install frontend dependencies:

```bash
npm install --prefix frontend
```

4. Install repository git hooks (required for automatic worktree shared-link sync):

```bash
./repo-scripts/install-git-hooks.sh
```

5. Initialize a local instance:

```bash
./scripts/install-octo-home.sh
```

6. Start the local runtime:

```bash
./scripts/run-octo-home.sh
```

7. Optional: run the frontend dev server separately:

```bash
cd frontend
npm run dev
```

## Development Workflow

1. Start from the relevant feature spec under `.specify/features/<feature-id>-<slug>/`.
2. Update `spec.md`, `plan.md`, `tasks.md`, or verification artifacts when the change affects behavior, scope, or acceptance.
3. Prefer structural fixes over incremental patching when the current module boundary is already wrong.
4. Keep durable runtime semantics intact:
   - long-running work must survive restart
   - important state changes must remain observable
   - high-risk actions must stay governable

## Repository Conventions

- Public APIs and shared functions should include full type annotations.
- Use Pydantic models for structured domain data.
- Prefer async I/O.
- Use `rg` for search when possible.
- Do not introduce undocumented bypasses around policy, approvals, or durability.
- Formal feature artifacts belong in `.specify/features/...`, not in a top-level `specs/` directory.

## Language Conventions

This repository currently uses a Chinese-first internal engineering workflow:

- Internal design discussions, commit messages, comments, and most engineering documents are expected to be written in Chinese.
- Code identifiers remain in English.
- Public open-source documents such as `README.md`, `CONTRIBUTING.md`, and `CODE_OF_CONDUCT.md` may be written in English for discoverability.

## Testing

Run the relevant checks before opening a pull request.

Backend:

```bash
cd octoagent
uv run pytest -q
uv run ruff check packages apps tests
```

Frontend:

```bash
cd octoagent/frontend
npm test
npm run build
```

For large changes, prefer targeted verification while iterating and include the exact commands you ran in your change summary.

## Pull Requests

Please keep pull requests focused and explain:

- what changed
- why the change is needed
- which specs or blueprint sections it aligns with
- what you tested
- any remaining risks or follow-up work

If the change affects operator workflows, setup, or product behavior, update the relevant documentation in the same pull request.

## Reporting Issues

Bug reports and feature proposals are welcome through GitHub Issues. When possible, include:

- expected behavior
- actual behavior
- reproduction steps
- logs, screenshots, or task IDs
- your runtime mode (`echo` or `litellm`)

## Code of Conduct

By participating in this project, you agree to follow the guidelines in [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md).
