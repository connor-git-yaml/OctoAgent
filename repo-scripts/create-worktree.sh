#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

resolve_repo_root() {
  local candidate_repo
  local common_dir
  candidate_repo="$(git -C "${SCRIPT_DIR}/.." rev-parse --show-toplevel 2>/dev/null || true)"
  common_dir="$(git -C "${SCRIPT_DIR}/.." rev-parse --git-common-dir 2>/dev/null || true)"

  if [[ -n "${common_dir}" ]]; then
    if [[ "${common_dir}" = /* ]]; then
      local root_from_common
      root_from_common="$(cd "${common_dir}/.." && pwd)"
      if [[ -d "${root_from_common}" ]]; then
        printf '%s\n' "${root_from_common}"
        return 0
      fi
    elif [[ -n "${candidate_repo}" ]]; then
      local root_from_relative_common
      root_from_relative_common="$(cd "${candidate_repo}/${common_dir}/.." && pwd)"
      if [[ -d "${root_from_relative_common}" ]]; then
        printf '%s\n' "${root_from_relative_common}"
        return 0
      fi
    fi
  fi

  if [[ -n "${candidate_repo}" ]]; then
    printf '%s\n' "${candidate_repo}"
    return 0
  fi

  cd "${SCRIPT_DIR}/.." && pwd
}

ROOT_DIR="$(resolve_repo_root)"
SYNC_SCRIPT="${ROOT_DIR}/repo-scripts/sync-worktree-links.sh"

WORKTREE_PATH=""
BRANCH_NAME=""
START_POINT="HEAD"
FORCE_SYNC="false"

usage() {
  cat <<'EOF'
用法:
  ./repo-scripts/create-worktree.sh [--force-sync] <worktree-path> <branch-name> [start-point]

示例:
  ./repo-scripts/create-worktree.sh ../OctoAgent-parallel-4 codex/parallel-4
  ./repo-scripts/create-worktree.sh ../OctoAgent-parallel-4 codex/parallel-4 master

说明:
  - 如果 branch 已存在，则直接把该 branch 挂到新的 worktree
  - 如果 branch 不存在，则基于 start-point 创建 branch，默认从 HEAD 创建
  - 创建完成后会自动执行共享软链接同步
EOF
}

log() {
  printf '[create-worktree] %s\n' "$*"
}

fail() {
  log "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force-sync)
      FORCE_SYNC="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      fail "未知参数: $1"
      ;;
    *)
      break
      ;;
  esac
done

[[ $# -ge 2 && $# -le 3 ]] || {
  usage >&2
  exit 1
}

WORKTREE_PATH="$1"
BRANCH_NAME="$2"
START_POINT="${3:-HEAD}"

[[ -x "${SYNC_SCRIPT}" ]] || fail "未找到同步脚本: ${SYNC_SCRIPT}"

cd "${ROOT_DIR}"

if git show-ref --verify --quiet "refs/heads/${BRANCH_NAME}"; then
  log "检测到已有分支，直接创建 worktree: ${BRANCH_NAME}"
  git worktree add "${WORKTREE_PATH}" "${BRANCH_NAME}"
else
  log "分支不存在，将基于 ${START_POINT} 创建: ${BRANCH_NAME}"
  git worktree add -b "${BRANCH_NAME}" "${WORKTREE_PATH}" "${START_POINT}"
fi

if [[ "${FORCE_SYNC}" == "true" ]]; then
  "${SYNC_SCRIPT}" --force "${WORKTREE_PATH}"
else
  "${SYNC_SCRIPT}" "${WORKTREE_PATH}"
fi

log "完成: ${WORKTREE_PATH}"
