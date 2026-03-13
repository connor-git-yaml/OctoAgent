#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_SOURCE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MANIFEST_FILE="${DEFAULT_SOURCE_ROOT}/repo-scripts/worktree-shared-paths.txt"

SOURCE_ROOT="${DEFAULT_SOURCE_ROOT}"
TARGET_ROOT=""
DRY_RUN="false"
FORCE="false"

usage() {
  cat <<'EOF'
用法:
  ./repo-scripts/sync-worktree-links.sh [--dry-run] [--force] [--source <repo-root>] <worktree-path>

说明:
  - 默认把当前仓库根目录作为共享源目录
  - 只处理 repo-scripts/worktree-shared-paths.txt 中声明的路径
  - 默认不会覆盖普通文件或目录；如果目标已有实体文件，请使用 --force
EOF
}

log() {
  printf '[sync-worktree-links] %s\n' "$*"
}

fail() {
  log "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      [[ $# -ge 2 ]] || fail "--source 缺少参数"
      SOURCE_ROOT="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --force)
      FORCE="true"
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
      [[ -z "${TARGET_ROOT}" ]] || fail "只接受一个 worktree 路径参数"
      TARGET_ROOT="$1"
      shift
      ;;
  esac
done

[[ -n "${TARGET_ROOT}" ]] || {
  usage >&2
  exit 1
}

[[ -d "${SOURCE_ROOT}" ]] || fail "源目录不存在: ${SOURCE_ROOT}"
[[ -f "${MANIFEST_FILE}" ]] || fail "未找到共享清单: ${MANIFEST_FILE}"
[[ -d "${TARGET_ROOT}" ]] || fail "worktree 目录不存在: ${TARGET_ROOT}"

SOURCE_ROOT="$(cd "${SOURCE_ROOT}" && pwd)"
TARGET_ROOT="$(cd "${TARGET_ROOT}" && pwd)"

if [[ "${SOURCE_ROOT}" == "${TARGET_ROOT}" ]]; then
  log "目标目录就是源目录，跳过。"
  exit 0
fi

linked_count=0
skipped_count=0
conflict_count=0

while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
  line="${raw_line#"${raw_line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"

  [[ -n "${line}" ]] || continue
  [[ "${line}" == \#* ]] && continue

  source_path="${SOURCE_ROOT}/${line}"
  target_path="${TARGET_ROOT}/${line}"

  if [[ ! -e "${source_path}" && ! -L "${source_path}" ]]; then
    log "跳过缺失源路径: ${line}"
    skipped_count=$((skipped_count + 1))
    continue
  fi

  mkdir -p "$(dirname "${target_path}")"

  if [[ -L "${target_path}" ]]; then
    current_target="$(readlink "${target_path}")"
    if [[ "${current_target}" == "${source_path}" ]]; then
      skipped_count=$((skipped_count + 1))
      continue
    fi

    if [[ "${DRY_RUN}" == "true" ]]; then
      log "将更新软链接: ${line} -> ${source_path}"
    else
      ln -snf "${source_path}" "${target_path}"
      log "已更新软链接: ${line} -> ${source_path}"
    fi
    linked_count=$((linked_count + 1))
    continue
  fi

  if [[ -e "${target_path}" ]]; then
    if [[ "${FORCE}" != "true" ]]; then
      log "冲突，目标已存在且不是软链接: ${line}"
      conflict_count=$((conflict_count + 1))
      continue
    fi

    if [[ "${DRY_RUN}" == "true" ]]; then
      log "将替换现有路径: ${line}"
    else
      rm -rf "${target_path}"
      log "已替换现有路径: ${line}"
    fi
  fi

  if [[ "${DRY_RUN}" == "true" ]]; then
    log "将创建软链接: ${line} -> ${source_path}"
  else
    ln -snf "${source_path}" "${target_path}"
    log "已创建软链接: ${line} -> ${source_path}"
  fi
  linked_count=$((linked_count + 1))
done < "${MANIFEST_FILE}"

log "完成: linked=${linked_count} skipped=${skipped_count} conflicts=${conflict_count}"

if [[ "${conflict_count}" -gt 0 ]]; then
  exit 2
fi
