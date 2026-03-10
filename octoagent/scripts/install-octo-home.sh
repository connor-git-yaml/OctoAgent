#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTANCE_ROOT="${OCTOAGENT_INSTANCE_ROOT:-$HOME/.octoagent}"

cd "${PROJECT_ROOT}"
exec uv run python -m octoagent.provider.dx.install_bootstrap \
  --project-root "${PROJECT_ROOT}" \
  --instance-root "${INSTANCE_ROOT}" \
  "$@"
