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

# Feature 081 P3：兼容窗口——老用户的 .env.litellm 在 P4 删除前继续读取。
# 推荐用户跑 `octo config migrate-080` 把凭证合并到 .env。
if [[ -f "${INSTANCE_ROOT}/.env.litellm" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${INSTANCE_ROOT}/.env.litellm"
  set +a
fi

cd "${PROJECT_ROOT}"
exec uv run octo doctor "$@"
