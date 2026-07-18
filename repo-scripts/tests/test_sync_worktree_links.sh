#!/bin/bash
# sync-worktree-links.sh 的 hermetic fixture 回归测试。
#
# 守卫脚本对 manifest 条目的既有语义：
#   1. worktree 缺目标目录 → 建软链指向主仓
#   2. worktree 已有真实目录 → 冲突告警、skip 不覆盖（不阻断整体同步）
#   3. 幂等（既有软链指向一致 → skip）
#   4. --dry-run 不落盘
#   5. --force 显式替换真实路径
# 另守卫清单硬规则：git-tracked 路径禁入 manifest（2026-07 Codex review，
# .claude/skills 曾试图加入被否——软链会堵死旧 worktree 升级路径、
# --force 会删 tracked 目录弄脏工作树；详见 worktree-shared-paths.txt 注释）。
#
# 运行方式（无外部依赖，仅 bash + coreutils）：
#   ./repo-scripts/tests/test_sync_worktree_links.sh
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_SCRIPTS_DIR="$(cd "${TEST_DIR}/.." && pwd)"
REAL_SCRIPT="${REPO_SCRIPTS_DIR}/sync-worktree-links.sh"
REAL_MANIFEST="${REPO_SCRIPTS_DIR}/worktree-shared-paths.txt"

FIXTURE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/sync-worktree-links-test.XXXXXX")"
trap 'rm -rf "${FIXTURE_ROOT}"' EXIT
# macOS 上 mktemp 返回 /var/... 而脚本内部 cd&&pwd 解析为 /private/var/...，
# 先做同款规范化，保证 readlink 断言路径一致。
FIXTURE_ROOT="$(cd "${FIXTURE_ROOT}" && pwd)"

pass_count=0
fail_count=0

assert() {
  local label="$1"
  shift
  if "$@"; then
    printf 'PASS: %s\n' "${label}"
    pass_count=$((pass_count + 1))
  else
    printf 'FAIL: %s\n' "${label}" >&2
    fail_count=$((fail_count + 1))
  fi
}

# --- fixture 仓搭建 ---------------------------------------------------------
# 脚本从自身所在仓库根解析 manifest（sync-worktree-links.sh:39），
# 因此把真实脚本复制进临时 fixture 仓、配自定义 manifest，
# 行为语义仍由真实脚本代码决定，无副本漂移（每次运行时 cp）。
SOURCE_ROOT="${FIXTURE_ROOT}/main-repo"
mkdir -p "${SOURCE_ROOT}/repo-scripts"
cp "${REAL_SCRIPT}" "${SOURCE_ROOT}/repo-scripts/sync-worktree-links.sh"
SCRIPT="${SOURCE_ROOT}/repo-scripts/sync-worktree-links.sh"

cat > "${SOURCE_ROOT}/repo-scripts/worktree-shared-paths.txt" <<'EOF'
# fixture manifest
shared/config-dir
LOCAL.md
missing-source-entry
EOF

# 主仓侧真实内容
mkdir -p "${SOURCE_ROOT}/shared/config-dir/nested"
echo "shared body" > "${SOURCE_ROOT}/shared/config-dir/nested/file.md"
echo "local md" > "${SOURCE_ROOT}/LOCAL.md"
# 注意：manifest 中 missing-source-entry 在主仓侧刻意不存在

run_sync() {
  local target="$1"
  shift
  "${SCRIPT}" --source "${SOURCE_ROOT}" "$@" "${target}" 2>&1
}

# --- case 1: worktree 缺目标目录 → 建链指向主仓 -----------------------------
WT1="${FIXTURE_ROOT}/wt-missing"
mkdir -p "${WT1}"
out1="$(run_sync "${WT1}")"

assert "case1: 目录软链已创建" test -L "${WT1}/shared/config-dir"
assert "case1: 软链指向主仓真实目录" \
  test "$(readlink "${WT1}/shared/config-dir")" = "${SOURCE_ROOT}/shared/config-dir"
assert "case1: 经软链可读到内容" \
  test -f "${WT1}/shared/config-dir/nested/file.md"
assert "case1: 缺失源路径按既有语义跳过" \
  grep -q "跳过缺失源路径: missing-source-entry" <<< "${out1}"
assert "case1: 计数 linked=2 skipped=1 conflicts=0" \
  grep -q "完成: linked=2 skipped=1 conflicts=0" <<< "${out1}"

# --- case 2: worktree 已有真实目录 → 冲突告警、skip 不覆盖 ------------------
WT2="${FIXTURE_ROOT}/wt-real-dir"
mkdir -p "${WT2}/shared/config-dir/nested"
echo "worktree own body" > "${WT2}/shared/config-dir/nested/file.md"
out2="$(run_sync "${WT2}")"

assert "case2: 真实目录未被替换为软链" \
  bash -c "test -d '${WT2}/shared/config-dir' && ! test -L '${WT2}/shared/config-dir'"
assert "case2: 目录内容未被覆盖" \
  grep -q "worktree own body" "${WT2}/shared/config-dir/nested/file.md"
assert "case2: 冲突按既有语义告警不阻断" \
  grep -q "冲突，目标已存在且不是软链接: shared/config-dir" <<< "${out2}"
assert "case2: 冲突不影响其余条目建链（exit 0 且 LOCAL.md 已链）" \
  test -L "${WT2}/LOCAL.md"

# --- case 3: 重复运行幂等（既有软链且指向一致 → skip）----------------------
out3="$(run_sync "${WT1}")"
assert "case3: 二次运行 linked=0（幂等）" \
  grep -q "完成: linked=0" <<< "${out3}"
assert "case3: 软链保持不变" \
  test "$(readlink "${WT1}/shared/config-dir")" = "${SOURCE_ROOT}/shared/config-dir"

# --- case 4: --dry-run 不落盘 -----------------------------------------------
WT4="${FIXTURE_ROOT}/wt-dry-run"
mkdir -p "${WT4}"
out4="$(run_sync "${WT4}" --dry-run)"
assert "case4: dry-run 输出创建计划" \
  grep -q "将创建软链接: shared/config-dir" <<< "${out4}"
assert "case4: dry-run 未真正建链" \
  bash -c "! test -e '${WT4}/shared/config-dir' && ! test -L '${WT4}/shared/config-dir'"

# --- case 5: --force 显式替换真实目录（既有语义）----------------------------
out5="$(run_sync "${WT2}" --force)"
assert "case5: --force 后目标变为软链" test -L "${WT2}/shared/config-dir"
assert "case5: --force 输出替换日志" \
  grep -q "已替换现有路径: shared/config-dir" <<< "${out5}"

# --- case 6: 真实 manifest 硬规则守卫 ---------------------------------------
# git-tracked 路径禁入清单：.claude/skills 曾被否（见文件头注释），
# 断言它没有作为条目（非注释行）回流。
assert "case6: 真实 manifest 不含 .claude/skills 条目（硬规则守卫）" \
  bash -c "! grep -qx '\.claude/skills' '${REAL_MANIFEST}'"
assert "case6: 真实 manifest 保留 settings.local.json 条目（回归护栏）" \
  grep -qx '\.claude/settings\.local\.json' "${REAL_MANIFEST}"

# --- 汇总 -------------------------------------------------------------------
printf '\n结果: %d passed, %d failed\n' "${pass_count}" "${fail_count}"
[[ "${fail_count}" -eq 0 ]] || exit 1
exit 0
