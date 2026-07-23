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

# Feature 081 P3：删除 Docker daemon 预热逻辑。
# Provider 直连后 Gateway 不再启动 LiteLLM Proxy 子进程（也不需要 docker-compose
# 启动 LiteLLM 容器），用户机不需要 Docker daemon。

cd "${PROJECT_ROOT}"
exec uv run python -m octoagent.gateway \
  --host "${OCTOAGENT_HOST:-127.0.0.1}" \
  --port "${OCTOAGENT_PORT:-8000}" \
  "$@"
