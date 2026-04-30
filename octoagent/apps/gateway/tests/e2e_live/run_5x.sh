#!/usr/bin/env bash
# F087 P3 T-P3-11: smoke 5 域 5x 循环 0 regression 验证脚本。
#
# 不写进 conftest.py（违反 atomic 测试原则）；放独立脚本，CI / 开发本地手动跑。
#
# 用法:
#   bash apps/gateway/tests/e2e_live/run_5x.sh
#
# 退出码:
#   0  - 5 次循环全 PASS（含每次 6 测试 = 5 smoke 域 + 1 sanity flaky marker）
#   1  - 任意一次循环 FAIL

set -e

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
OCTOAGENT_DIR="${REPO_ROOT}/octoagent"

cd "${OCTOAGENT_DIR}"

TOTAL_RUNS=5
PASS_COUNT=0
FAIL_COUNT=0
SUMMARY_LOG="${HOME}/.octoagent/logs/e2e/run_5x-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$(dirname "${SUMMARY_LOG}")"

echo "[5x] 开始 ${TOTAL_RUNS} 次 e2e_smoke 循环，日志: ${SUMMARY_LOG}"
echo "[5x] start at $(date -Iseconds)" > "${SUMMARY_LOG}"

START_TS=$(date +%s)

for i in $(seq 1 ${TOTAL_RUNS}); do
  ITER_START=$(date +%s)
  set +e
  uv run pytest -m e2e_smoke -q --no-header > "${SUMMARY_LOG}.iter${i}" 2>&1
  RC=$?
  set -e
  ITER_END=$(date +%s)
  ITER_DUR=$((ITER_END - ITER_START))

  if [[ ${RC} -eq 0 ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
    SUMMARY="$(grep -E "^[0-9]+ passed" "${SUMMARY_LOG}.iter${i}" | tail -1 || echo "ok")"
    echo "[5x] iter ${i}/${TOTAL_RUNS} PASS (${ITER_DUR}s) - ${SUMMARY}"
    echo "[5x] iter ${i} PASS ${ITER_DUR}s ${SUMMARY}" >> "${SUMMARY_LOG}"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_NODE="$(grep -E "^FAILED " "${SUMMARY_LOG}.iter${i}" | head -1 || echo "<unknown failure>")"
    echo "[5x] iter ${i}/${TOTAL_RUNS} FAIL (${ITER_DUR}s) - ${FAILED_NODE}"
    echo "[5x] iter ${i} FAIL ${ITER_DUR}s ${FAILED_NODE}" >> "${SUMMARY_LOG}"
  fi
done

END_TS=$(date +%s)
TOTAL_DUR=$((END_TS - START_TS))
AVG_DUR=$((TOTAL_DUR / TOTAL_RUNS))

echo ""
echo "[5x] === 总结 ==="
echo "[5x] 总耗时: ${TOTAL_DUR}s (avg ${AVG_DUR}s/iter)"
echo "[5x] PASS: ${PASS_COUNT}/${TOTAL_RUNS}"
echo "[5x] FAIL: ${FAIL_COUNT}/${TOTAL_RUNS}"
echo "[5x] 详细日志: ${SUMMARY_LOG}"

echo "[5x] total ${TOTAL_DUR}s avg ${AVG_DUR}s pass=${PASS_COUNT}/${TOTAL_RUNS}" >> "${SUMMARY_LOG}"

if [[ ${FAIL_COUNT} -gt 0 ]]; then
  echo "[5x] FAIL: 有 ${FAIL_COUNT} 次循环失败，0 regression 不达标"
  exit 1
fi

# 单次目标 90-120s（DoD: <= 180s）
if [[ ${AVG_DUR} -gt 180 ]]; then
  echo "[5x] WARN: 平均单次耗时 ${AVG_DUR}s 超过 180s 上限"
  exit 1
fi

echo "[5x] PASS: 5x 循环 0 regression + 单次 ${AVG_DUR}s <= 180s"
exit 0
