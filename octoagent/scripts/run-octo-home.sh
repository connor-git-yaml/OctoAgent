#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTANCE_ROOT="${OCTOAGENT_INSTANCE_ROOT:-$HOME/.octoagent}"

export OCTOAGENT_PROJECT_ROOT="${INSTANCE_ROOT}"
export OCTOAGENT_DATA_DIR="${OCTOAGENT_DATA_DIR:-${INSTANCE_ROOT}/data}"

if [[ -f "${INSTANCE_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${INSTANCE_ROOT}/.env"
  set +a
fi

if [[ -f "${INSTANCE_ROOT}/.env.litellm" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${INSTANCE_ROOT}/.env.litellm"
  set +a
fi

cd "${PROJECT_ROOT}"
exec uv run uvicorn octoagent.gateway.main:app \
  --host "${OCTOAGENT_HOST:-127.0.0.1}" \
  --port "${OCTOAGENT_PORT:-8000}" \
  "$@"
