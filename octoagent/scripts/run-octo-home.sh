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

# Feature 081 P3：兼容窗口——老用户仍可能在 .env.litellm 内有 API key，
# 在 P4 删除 .env.litellm 之前继续读取，让用户跑 `octo config migrate-080`
# 主动迁移。P4 完成后此分支会随 .env.litellm 文件一起被清理。
if [[ -f "${INSTANCE_ROOT}/.env.litellm" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${INSTANCE_ROOT}/.env.litellm"
  set +a
fi

# Feature 081 P3：删除 Docker daemon 预热逻辑。
# Provider 直连后 Gateway 不再启动 LiteLLM Proxy 子进程（也不需要 docker-compose
# 启动 LiteLLM 容器），用户机不需要 Docker daemon。

cd "${PROJECT_ROOT}"
exec uv run uvicorn octoagent.gateway.main:app \
  --host "${OCTOAGENT_HOST:-127.0.0.1}" \
  --port "${OCTOAGENT_PORT:-8000}" \
  "$@"
