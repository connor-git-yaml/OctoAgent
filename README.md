# OctoAgent

![Version](https://img.shields.io/badge/version-0.1.0-0f766e.svg)
![Python](https://img.shields.io/badge/python-3.12%2B-2563eb.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

OctoAgent is a Personal AI OS for people who need an AI that can keep working, keep context, and stay explainable.

I built it first for my own daily use, then opened it up because the same problems keep showing up in other AI setups too: one giant chat thread, weak recovery, weak delegation, and no clear operator view when things go wrong.

Instead of treating everything as one giant chat thread, OctoAgent gives you a Butler that owns the user conversation, delegates to specialized Workers, keeps durable runtime state, and lets you inspect what actually happened.

## Why OctoAgent

Most AI tools are good at single-turn answers and weak at ongoing work. They start to break when you need:

- long-running tasks that survive restart
- real delegation instead of a single overloaded chat agent
- current-information workflows such as research or freshness queries
- durable memory with boundaries
- an operator surface that shows status, failures, and next actions

OctoAgent is built to close that gap.

## What The Product Feels Like

- **One Butler, multiple Workers**
  You talk to the Butler. The Butler can directly use mounted governed tools for bounded tasks, delegates to Research or other Workers when specialization or parallelism helps, and remains responsible for the final answer.

- **Durable by default**
  Tasks, events, artifacts, A2A conversations, and runtime state are persisted instead of living only inside a prompt window.

- **Inspectable instead of mysterious**
  The Web UI includes chat, workbench, Control Plane, and advanced diagnostics so you can understand both user-facing results and internal execution.

- **Safe local-first setup**
  You can start in `echo` mode to verify the system, then switch to real providers with `octo setup`.

## What You Can Do Today

- Run a local AI workspace through Web, with optional Telegram as a second surface.
- Bootstrap real providers through `octo setup`.
- Handle freshness and research questions through the Butler decision runtime, with many cases flowing through `Butler -> Research Worker -> Butler` and bounded checks handled directly by Butler with governed tools.
- Start a fresh Web conversation explicitly instead of silently restoring the previous task/session chain.
- Inspect runtime truth such as sessions, A2A conversations, memory surfaces, and task status.
- Operate the system with `octo-start`, `octo stop`, `octo restart`, `octo-doctor`, and the Control Plane.

## Who It Is For

OctoAgent is currently best suited for:

- individual operators who want a serious personal AI environment
- builders who care about runtime semantics, observability, and recovery
- users who want more than a prompt box but do not want an opaque automation black box

## Open Source Acknowledgements

OctoAgent draws a lot of design inspiration from the open-source community.

I want to specifically thank:

- **OpenClaw**, for practical ideas around operator surfaces, onboarding flow, runtime truth, and tool governance
- **Agent Zero**, for strong ideas around project-scoped runtime organization, long-running execution, and hands-on agent operation
- **Agent Studio**, for pushing on multi-agent product thinking and agent workspace experience

The implementation in this repository is intended to be independent unless a file explicitly states otherwise. The acknowledgement here is about product and architecture inspiration, not a claim of code reuse.

## Start In Minutes

### End users

Install the managed instance into `~/.octoagent`:

```bash
curl -fsSL https://raw.githubusercontent.com/connor-git-yaml/OctoAgent/master/repo-scripts/install-octo-user.sh | bash
export PATH="$HOME/.octoagent/bin:$PATH"
octo-start
```

In another terminal:

```bash
octo-doctor
curl "http://127.0.0.1:8000/ready?profile=core"
```

Then open:

- Web UI: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- API docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

To switch from `echo` mode to a real model provider:

```bash
octo setup
```

Memory works in `local_only` mode by default. If you later want MemU, the product now supports both a local command path and a remote HTTP bridge:

```bash
octo config memory show
octo config memory local
octo config memory memu-command --command "uv run python scripts/memu_bridge.py"
octo config memory memu-http --bridge-url "https://memory.example.com"
```

The Web `Settings > Memory` screen uses the same three modes: local memory, MemU via local command, and MemU via HTTP bridge.

Behavior files are also explicit now. The Butler default behavior is driven by
`AGENTS.md / USER.md / PROJECT.md / TOOLS.md`, with the Web `Settings > Behavior Files`
screen showing the current effective source chain. The canonical local management path is:

```bash
octo behavior ls
octo behavior show AGENTS
octo behavior init
```

To stop or restart the service:

```bash
octo stop              # graceful shutdown
octo stop --force      # force kill
octo restart           # managed restart
```

To update a managed local install later:

```bash
octo update
```

### Contributors

```bash
git clone https://github.com/connor-git-yaml/OctoAgent.git
cd OctoAgent/octoagent
uv sync
npm install --prefix frontend
./scripts/install-octo-home.sh
./scripts/run-octo-home.sh
```

For the full product guide, detailed setup, and configuration walkthrough, see [`octoagent/README.md`](./octoagent/README.md).

## Documentation Map

- Product guide and detailed setup: [`octoagent/README.md`](./octoagent/README.md)
- Engineering blueprint: [`docs/blueprint.md`](./docs/blueprint.md)
- Runtime refactor plan: [`docs/agent-runtime-refactor-plan.md`](./docs/agent-runtime-refactor-plan.md)
- Contribution guide: [`CONTRIBUTING.md`](./CONTRIBUTING.md)
- Code of conduct: [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md)

## Repository Layout

```text
.
├── docs/                  # Blueprint, milestone splits, runtime plans
├── repo-scripts/          # Repository-level install and maintenance scripts
├── skills/                # Shared prompt / workflow skills
├── .specify/              # Spec-driven feature artifacts
└── octoagent/             # Product source tree and runtime workspace
    ├── apps/              # Gateway app
    ├── packages/          # Core, provider, protocol, tooling, memory, skills, policy
    ├── frontend/          # React + Vite UI
    ├── scripts/           # Local install / run / doctor entrypoints
    └── tests/             # Contract, integration, and unit tests
```

## Contributing

Please read [`CONTRIBUTING.md`](./CONTRIBUTING.md) before opening a pull request.

## License

This repository is licensed under the MIT License. See [`LICENSE`](./LICENSE).
