<!-- speckit:section:badges -->
![Version](https://img.shields.io/badge/version-0.1.0-0f766e.svg)
![Python](https://img.shields.io/badge/python-3.12%2B-2563eb.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
<!-- speckit:section:badges:end -->

# OctoAgent

<!-- speckit:section:description -->
OctoAgent is a Personal AI OS for people who need an AI system, not just an AI reply box.

It combines a Butler-owned user experience, durable task execution, internal Butler-to-Worker delegation, governed tool usage, and observable runtime state into one local-first workspace. I built it first for my own use as a long-lived AI environment that can answer, delegate, recover, and explain itself, then turned it into a clean open-source project others can inspect and run.
<!-- speckit:section:description:end -->

<!-- speckit:section:features -->
## Features

- **One accountable Butler**
  You talk to a single default agent. The Butler keeps the main conversation, can directly use mounted governed tools for bounded tasks, decides when work should be delegated, and remains responsible for the final answer.

- **Internal delegation you can actually inspect**
  Specialized Workers run behind the Butler with their own sessions, memory scopes, recall frames, and durable A2A conversations instead of disappearing into a black box.

- **Context that behaves like a system**
  OctoAgent separates project memory, Butler memory, Worker memory, and per-turn recall so context does not collapse into one oversized prompt.

- **Long-running work that survives reality**
  Tasks, events, artifacts, and runtime state are persisted to SQLite WAL so restart, retry, and inspection are first-class behaviors.

- **A setup path for normal users**
  You can start in `echo` mode, verify the product end to end, then switch to real providers through `octo setup` without hand-assembling the whole stack.

- **Operator visibility without reading raw logs**
  The Web UI exposes runtime health, A2A conversations, task state, memory surfaces, configuration hints, and operator actions through Control Plane and Advanced views.

- **Real freshness and research flow**
  Questions such as weather, “latest”, and web-backed research are handled by the Butler decision runtime: many follow `Butler -> Research Worker -> Butler`, while bounded checks can be completed directly by Butler with governed tools and explicit provenance.

- **Web first, Telegram optional**
  Web is the default surface. Telegram can be added later when you want a second control surface.
<!-- speckit:section:features:end -->

## What Makes OctoAgent Different

- It treats runtime state as product truth, not as invisible implementation detail.
- It distinguishes between the agent you talk to and the workers doing delegated execution.
- It is designed to recover, inspect, and continue, not just to answer.
- It lets you begin safely in `echo` mode before trusting real provider credentials and live tools.

## Acknowledgements

OctoAgent is an independent implementation, but its design has been sharpened by studying several open-source projects whose ideas were genuinely useful.

Special thanks to:

- **OpenClaw**, for ideas around onboarding, runtime visibility, tool governance, and operator ergonomics
- **Agent Zero**, for ideas around project-scoped runtime structure, long-running agent execution, and practical system operation
- **Agent Studio**, for ideas around agent workspace design and multi-agent product experience

<!-- speckit:section:getting-started -->
## Getting Started

### Recommended First Experience

If this is your first time, do not start by wiring every provider and channel at once.

The intended onboarding path is:

1. Install the managed local instance.
2. Start OctoAgent in `echo` mode and verify the Web flow.
3. Switch to a real provider with `octo setup`.
4. Ask a freshness or research-style question.
5. Inspect the result in Chat, Workbench, and Control Plane.

This sequence gives you a clean separation between:

- “does the product boot and behave correctly?”
- “is my provider and LiteLLM configuration healthy?”

### Prerequisites

Minimum requirements:

- Git
- Python 3.12+
- Node.js 20+
- npm
- [uv](https://docs.astral.sh/uv/)

Required for real provider mode:

- Docker Desktop or another working Docker daemon

You do **not** need Docker just to test the default `echo` mode.

### Installation

#### Option A: Managed install for end users

This is the recommended path if you want to experience OctoAgent as a normal user and do not want to bootstrap the repo manually.

```bash
curl -fsSL https://raw.githubusercontent.com/connor-git-yaml/OctoAgent/master/repo-scripts/install-octo-user.sh | bash
```

This installs:

- a managed instance under `~/.octoagent`
- the source checkout under `~/.octoagent/app`
- three ready-to-use entrypoints under `~/.octoagent/bin`

Optional: add the CLI to your shell `PATH`.

```bash
export PATH="$HOME/.octoagent/bin:$PATH"
```

#### Option B: Run from source

```bash
git clone https://github.com/connor-git-yaml/OctoAgent.git
cd OctoAgent/octoagent
uv sync
npm install --prefix frontend
./scripts/install-octo-home.sh
```

This initializes a local instance in `~/.octoagent` by default.

### First Start

Managed instance:

```bash
octo-start
```

Source checkout:

```bash
./scripts/run-octo-home.sh
```

In another terminal:

```bash
octo-doctor
curl "http://127.0.0.1:8000/ready?profile=core"
```

When the readiness endpoint returns `status: ready`, open:

- Web UI: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- API docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
<!-- speckit:section:getting-started:end -->

<!-- speckit:section:usage -->
## Usage

### 1. Start in `echo` mode first

The default installation starts in `echo` mode on purpose. This lets you verify the Web UI, task ledger, and runtime wiring before introducing provider credentials or Docker-based LiteLLM.

Typical first-run flow:

1. Install the managed instance.
2. Start with `octo-start`.
3. Run `octo-doctor`.
4. Open the Web UI and send a simple prompt.
5. Confirm that chat, task creation, and event streaming all work.

### 2. Switch to a real provider

Run:

```bash
octo setup
```

The setup flow is the main onboarding command for non-developers. It can:

- choose a provider preset
- collect or connect credentials
- write configuration to the managed instance
- prepare LiteLLM proxy state
- activate the runtime and run live verification

Common presets:

- `openrouter`
- `openai`
- `openai-codex`
- `anthropic`

If you use ChatGPT Pro / Codex-style OAuth, `openai-codex` is the current dedicated path.

### 3. Ask a real question

Once `octo-doctor --live` passes, try a question that benefits from delegation:

```text
Shenzhen weather today
```

The intended product flow is:

1. Butler receives the question.
2. Butler decides this is a freshness query.
3. Butler opens an internal A2A conversation with a Research Worker.
4. The Research Worker gathers current evidence.
5. Butler synthesizes the result and answers the user.

### 4. Inspect what happened

Use the Web UI to inspect:

- **Chat / Workbench**
  For the user-facing thread, task detail, and the current execution snapshot.
- **Control Plane / Advanced**
  For runtime state, A2A conversations, session health, memory surfaces, and deeper diagnostics.

### 5. Optional: connect Telegram

If you want Telegram in addition to Web:

```bash
octo config init --force --enable-telegram --telegram-mode polling
export TELEGRAM_BOT_TOKEN=your_bot_token
octo onboard --channel telegram
```

`polling` is the simplest way to start. Use `webhook` only when you already have a reliable HTTPS endpoint.
<!-- speckit:section:usage:end -->

## Behavior Files

The Butler behavior path is no longer only hidden prompt assembly. OctoAgent now resolves
an explicit behavior workspace built around four core files:

- `AGENTS.md`
- `USER.md`
- `PROJECT.md`
- `TOOLS.md`

Web exposes a read-only operator view at `Settings > Behavior Files`, showing effective
source chain, visible files, and Worker inheritance. The canonical local management path is:

```bash
octo behavior ls
octo behavior show AGENTS
octo behavior init
```

At runtime, Butler combines these files with `RuntimeHintBundle`, a sanitized
session-backed `SessionReplay`/`RecentConversation` view, and hint-first memory context.
Default general requests now enter a single-loop main executor directly: the main model call
runs with the mounted governed tools in one `LLM + SkillRunner` loop instead of forcing a
separate Butler preflight first. Structured `ButlerDecision` and `ButlerLoopPlan` remain for
compatibility, explicit delegation, and legacy preflight paths. Compatibility heuristics now only
retain true guardrails such as weather location boundaries and follow-up resume.

## Initialization and Configuration

### Instance layout

The managed install keeps your personal instance under `~/.octoagent`.

Key files and directories:

- `~/.octoagent/octoagent.yaml`
- `~/.octoagent/litellm-config.yaml`
- `~/.octoagent/.env.litellm`
- `~/.octoagent/data/sqlite`
- `~/.octoagent/data/artifacts`
- `~/.octoagent/app`

### What `octo setup` configures

`octo setup` is the preferred first-time configuration command for non-developers.

It typically updates:

- `octoagent.yaml`
  - provider entries
  - model aliases
  - runtime mode
  - front-door access mode
- `.env.litellm`
  - provider API keys
  - `LITELLM_MASTER_KEY`
  - proxy-related environment variables
- `litellm-config.yaml`
  - generated proxy-side routing config

### Provider configuration model

The example config file is [`octoagent.yaml.example`](./octoagent.yaml.example).

Important sections:

- `providers`
  - defines provider IDs, display names, auth type, and which environment variable stores the credential
- `model_aliases`
  - binds user-facing aliases like `main` and `cheap` to actual provider/model pairs
- `runtime`
  - controls `echo` vs `litellm`, proxy URL, and master key env names
- `front_door`
  - controls whether the owner-facing APIs stay loopback-only or require bearer / trusted proxy protection

### Memory configuration model

OctoAgent treats Memory as a built-in engine first, and retrieval quality as a separate upgrade path.

- `local_only`
  - the default mode
  - uses the built-in canonical store and a built-in local embedding path
  - does not require a separate MemU deployment
  - if no retrieval aliases are bound, the system falls back to `main` for reasoning / expansion and to the built-in `engine-default` embedding layer
  - `engine-default` now prefers local `Qwen/Qwen3-Embedding-0.6B`
  - if the local Qwen runtime is unavailable, it automatically falls back to the built-in bilingual hash embedding, then to lexical / metadata recall
- retrieval model bindings
  - `memory_reasoning`
  - `memory_expand`
  - `memory_embedding`
  - `memory_rerank`
  - these are configured through Providers + model aliases, then bound in `Settings > Memory`
- `memu + command/http`
  - kept only as a compatibility path for old instances or troubleshooting
  - not the primary product path anymore
  - should be treated as an advanced/operator-only bridge surface, not a first-time setup step

The Web `Settings > Memory` page and the CLI now share the same main contract:

- everyday users bind retrieval models and watch embedding migrations in one place
- advanced users can still expand the compatibility section when they must keep an old bridge

Useful commands:

```bash
octo config memory show
octo config memory local
# compatibility only
octo config memory memu-command --command "uv run python scripts/memu_bridge.py"
octo config memory memu-command --command "uv run python scripts/memu_bridge.py" --cwd "/path/to/memu"
octo config memory memu-http --bridge-url "https://memory.example.com" --api-key-env MEMU_API_KEY
```

At the product level, the important rule is:

- basic Memory should already work without extra deployment
- the built-in engine already provides lightweight bilingual semantic retrieval out of the box
- retrieval quality can be upgraded independently from the storage/governance path
- the default embedding layer prefers local `Qwen/Qwen3-Embedding-0.6B`, with hash fallback kept as a safety net
- compatibility bridges exist for migration, not as the default setup story

If you want the built-in Qwen embedding runtime locally, install the provider extra:

```bash
uv pip install -e "packages/provider[local-embedding]"
```

### Credentials and secrets

Do **not** store raw API keys in `octoagent.yaml`.

The intended split is:

- tracked config in `octoagent.yaml`
- secret values in `.env.litellm` or runtime environment variables

### Docker and LiteLLM

For real providers, OctoAgent uses a local LiteLLM proxy by default. That means:

- Docker must be running
- the proxy must be reachable on the configured URL
- `octo-doctor --live` should pass before you trust runtime answers

If the proxy is healthy, you should see:

```bash
octo-doctor --live
```

To update a managed local instance to the latest `master`, use a single command:

```bash
octo update
```

For a managed install, this is the intended operator path. It updates the local source checkout, syncs backend dependencies, rebuilds the frontend, restarts the runtime, and runs verification.

To stop or restart the runtime:

```bash
octo stop              # graceful shutdown (SIGTERM)
octo stop --force      # force kill (SIGKILL)
octo restart           # managed restart
```

### Remote exposure and front-door safety

The default `front_door.mode` is `loopback`, which is the safest default for local-first use.

Other modes exist, but they are operator choices and should be used intentionally:

- `loopback`
  - only local access
- `bearer`
  - requires a bearer token for owner-facing APIs
- `trusted_proxy`
  - expects a trusted reverse proxy plus shared header token

If you expose OctoAgent beyond localhost, you should review `front_door` before doing anything else.

## Architecture at a Glance

```text
Web / Telegram
      |
      v
  Butler Session
      |
      v
  OctoGateway / Runtime
      |
      +--> A2AConversation --> Worker Session --> Tools / Skills / Research
      |
      +--> SQLite WAL (tasks / events / artifacts / runtime state)
```

The important product-level point is not just “multi-agent”. It is that the Butler, Workers, sessions, A2A conversations, and memory surfaces are durable and inspectable.

## Project Structure

<!-- speckit:section:project-structure -->
```text
octoagent/
├── apps/
│   └── gateway/              # FastAPI app and runtime services
├── frontend/                 # React + Vite Web UI
├── packages/
│   ├── core/                 # Domain models, stores, projections
│   ├── memory/               # Memory governance and recall
│   ├── policy/               # Approval and policy logic
│   ├── protocol/             # A2A and normalized protocol models
│   ├── provider/             # Provider bootstrap, doctor, setup DX
│   ├── skills/               # Skill runner and LLM integration
│   └── tooling/              # Tool contracts and broker
├── scripts/                  # install / run / doctor helper scripts
├── tests/                    # contract, unit, and integration tests
├── octoagent.yaml.example    # tracked configuration example
└── pyproject.toml            # uv workspace root
```
<!-- speckit:section:project-structure:end -->

<!-- speckit:section:tech-stack -->
## Tech Stack

- Python 3.12+
- FastAPI + Uvicorn
- Pydantic v2
- SQLite WAL
- React 19 + Vite 6
- TypeScript 5
- uv workspace
- pytest / pytest-asyncio
- ruff
- Docker + LiteLLM Proxy
<!-- speckit:section:tech-stack:end -->

<!-- speckit:section:testing -->
## Testing

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

Useful local operator commands:

```bash
octo update
octo stop
octo restart
octo-doctor
octo-doctor --live
python -m octoagent.core rebuild-projections
```
<!-- speckit:section:testing:end -->

<!-- speckit:section:contributing -->
## Contributing

Please read [../CONTRIBUTING.md](../CONTRIBUTING.md) before submitting a pull request.
<!-- speckit:section:contributing:end -->

<!-- speckit:section:license -->
## License

This project is licensed under the MIT License. See [../LICENSE](../LICENSE).
<!-- speckit:section:license:end -->
