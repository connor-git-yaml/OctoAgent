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

# Docker daemon best-effort 预热：提前触发 Docker Desktop / systemd docker 启动，
# 使后续 LiteLLM Proxy 激活时 daemon 已就绪，缩短感知延迟。
# OCTOAGENT_AUTOSTART_DOCKER=0 禁用；OCTOAGENT_DOCKER_DAEMON_TIMEOUT 控制超时。
if [[ "${OCTOAGENT_AUTOSTART_DOCKER:-1}" == "1" ]]; then
  (
    cd "${PROJECT_ROOT}"
    uv run python -m octoagent.provider.dx.docker_daemon \
      --ensure --quiet \
      --timeout "${OCTOAGENT_DOCKER_DAEMON_TIMEOUT:-30}" \
      >/dev/null 2>&1 || true
  ) &
fi

cd "${PROJECT_ROOT}"
exec uv run uvicorn octoagent.gateway.main:app \
  --host "${OCTOAGENT_HOST:-127.0.0.1}" \
  --port "${OCTOAGENT_PORT:-8000}" \
  "$@"
