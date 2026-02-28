#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_DIR="${ROOT_DIR}/.agent-config"
SHARED_FILE="${CONFIG_DIR}/shared.md"
CLAUDE_TEMPLATE="${CONFIG_DIR}/templates/claude.header.md"
AGENTS_TEMPLATE="${CONFIG_DIR}/templates/agents.header.md"
CLAUDE_OUT="${ROOT_DIR}/CLAUDE.md"
AGENTS_OUT="${ROOT_DIR}/AGENTS.md"
CLAUDE_LOCAL="${ROOT_DIR}/CLAUDE.local.md"
AGENTS_LOCAL="${ROOT_DIR}/AGENTS.local.md"

MODE="sync"
SYNC_LOCAL="false"

usage() {
  cat <<'EOF'
用法:
  ./scripts/sync-agent-config.sh              # 生成/覆盖 CLAUDE.md 与 AGENTS.md
  ./scripts/sync-agent-config.sh --check      # 仅检查是否已同步
  ./scripts/sync-agent-config.sh --sync-local # 额外同步 CLAUDE.local.md -> AGENTS.local.md
EOF
}

for arg in "$@"; do
  case "${arg}" in
    --check)
      MODE="check"
      ;;
    --sync-local)
      SYNC_LOCAL="true"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: ${arg}" >&2
      usage >&2
      exit 1
      ;;
  esac
done

for required_file in "${SHARED_FILE}" "${CLAUDE_TEMPLATE}" "${AGENTS_TEMPLATE}"; do
  if [[ ! -f "${required_file}" ]]; then
    echo "缺少必需文件: ${required_file}" >&2
    exit 1
  fi
done

generate_target() {
  local template_file="$1"
  local output_file="$2"

  local tmp_file
  tmp_file="$(mktemp)"
  {
    cat "${template_file}"
    printf '\n'
    cat "${SHARED_FILE}"
    printf '\n'
  } > "${tmp_file}"

  if [[ "${MODE}" == "check" ]]; then
    if [[ ! -f "${output_file}" ]] || ! cmp -s "${tmp_file}" "${output_file}"; then
      echo "未同步: ${output_file}" >&2
      rm -f "${tmp_file}"
      return 1
    fi
    rm -f "${tmp_file}"
    return 0
  fi

  mv "${tmp_file}" "${output_file}"
  echo "已生成: ${output_file}"
}

status=0
generate_target "${CLAUDE_TEMPLATE}" "${CLAUDE_OUT}" || status=1
generate_target "${AGENTS_TEMPLATE}" "${AGENTS_OUT}" || status=1

if [[ "${SYNC_LOCAL}" == "true" ]]; then
  if [[ -f "${CLAUDE_LOCAL}" ]]; then
    cp "${CLAUDE_LOCAL}" "${AGENTS_LOCAL}"
    echo "已同步本地文件: ${CLAUDE_LOCAL} -> ${AGENTS_LOCAL}"
  else
    echo "跳过本地同步: 未找到 ${CLAUDE_LOCAL}"
  fi
fi

if [[ "${MODE}" == "check" && "${status}" -eq 0 ]]; then
  echo "检查通过: CLAUDE.md 与 AGENTS.md 已与共享源一致。"
fi

exit "${status}"
