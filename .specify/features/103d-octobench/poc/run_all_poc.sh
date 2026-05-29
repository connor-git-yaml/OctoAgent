#!/usr/bin/env bash
# F103d OctoBench Phase 0 PoC 一键运行脚本
# Usage:
#   bash run_all_poc.sh                # 全跑（含 uv pip install）
#   bash run_all_poc.sh --skip-install # 跳过 install（依赖已装好时复跑用）
#   POC_TIMEOUT=300 bash run_all_poc.sh # 单 task timeout 5 min（默认 600s）
#
# 完成后所有 stdout/stderr 在 /tmp/octobench-poc-<ts>/ 下；
# 把全部 .json 文件 cat 给 Claude 主 session 即可。

set -uo pipefail
# 不 set -e — 允许单 task 失败继续跑剩下的

# Codex Phase A review P2 修复（2026-05-29）：从脚本自身位置推导 WORKTREE_ROOT，
# 避免硬编码绝对路径（不同 worktree / 不同开发者机器都能直接跑）。
# 本脚本位于 <WORKTREE_ROOT>/.specify/features/103d-octobench/poc/run_all_poc.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKTREE_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
OCTO_DIR="$WORKTREE_ROOT/octoagent"
POC_DIR="$SCRIPT_DIR"
OUTPUT_DIR="/tmp/octobench-poc-$(date +%Y%m%d-%H%M%S)"
POC_TIMEOUT="${POC_TIMEOUT:-600}"  # 单 task 超时秒（默认 10 min）

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

mkdir -p "$OUTPUT_DIR"
echo -e "${BLUE}📦 输出目录: $OUTPUT_DIR${NC}"
echo -e "${BLUE}⏱  单 task timeout: ${POC_TIMEOUT}s${NC}"

# ──────────────────────────────────────────
# Step 0: 环境检查
# ──────────────────────────────────────────
echo -e "\n${BLUE}=== Step 0: 环境检查 ===${NC}"

if [ ! -x "$OCTO_DIR/.venv/bin/python" ]; then
    echo -e "${RED}❌ $OCTO_DIR/.venv 不存在或不可执行${NC}"
    echo "请先 cd $OCTO_DIR && uv venv"
    exit 1
fi
PYTHON="$OCTO_DIR/.venv/bin/python"
echo -e "${GREEN}✓ Python: $PYTHON${NC}"
"$PYTHON" --version

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo -e "${YELLOW}⚠️  ANTHROPIC_API_KEY 未设置 → poc_t1/t3/concurrent 会失败${NC}"
    KEY_OK=0
else
    echo -e "${GREEN}✓ ANTHROPIC_API_KEY 已设置 (${#ANTHROPIC_API_KEY} 字符)${NC}"
    KEY_OK=1
fi

if [ -z "${HF_TOKEN:-}" ] && [ -z "${HUGGING_FACE_HUB_TOKEN:-}" ]; then
    echo -e "${YELLOW}⚠️  HF_TOKEN / HUGGING_FACE_HUB_TOKEN 未设置 → poc_gaia 会跳过 GAIA dataset${NC}"
    HF_OK=0
else
    echo -e "${GREEN}✓ HF token 已设置${NC}"
    HF_OK=1
fi

# ──────────────────────────────────────────
# Step 1: 装依赖
# ──────────────────────────────────────────
if [ "${1:-}" = "--skip-install" ]; then
    echo -e "\n${YELLOW}=== Step 1: SKIP install（--skip-install 模式）===${NC}"
else
    echo -e "\n${BLUE}=== Step 1: 装 tau-bench + datasets ===${NC}"
    cd "$OCTO_DIR"
    if uv pip list 2>/dev/null | grep -qiE "tau.?bench"; then
        echo -e "${GREEN}✓ tau-bench 已装，skip${NC}"
    else
        echo "运行: uv pip install git+https://github.com/sierra-research/tau-bench.git datasets"
        if uv pip install "git+https://github.com/sierra-research/tau-bench.git" datasets > "$OUTPUT_DIR/install.log" 2>&1; then
            echo -e "${GREEN}✓ install 成功${NC}"
            tail -3 "$OUTPUT_DIR/install.log"
        else
            echo -e "${RED}❌ install 失败，查看 $OUTPUT_DIR/install.log${NC}"
            tail -10 "$OUTPUT_DIR/install.log"
            echo -e "${YELLOW}继续后续 task，poc_tau / poc_gaia 可能失败${NC}"
        fi
    fi
fi

# ──────────────────────────────────────────
# Step 2: 跑 6 个 PoC 脚本
# ──────────────────────────────────────────
cd "$OCTO_DIR"

# 检测 timeout 命令（macOS 默认无，用 coreutils 的 gtimeout 或 perl 兜底）
if command -v gtimeout >/dev/null 2>&1; then
    TIMEOUT_CMD="gtimeout"
elif command -v timeout >/dev/null 2>&1; then
    TIMEOUT_CMD="timeout"
else
    # perl 兜底（macOS 都有 perl）
    TIMEOUT_CMD=""
fi

run_poc() {
    local task_id="$1"
    local script="$2"
    local script_path="$POC_DIR/$script"
    local output="$OUTPUT_DIR/${task_id}.json"
    local log="$OUTPUT_DIR/${task_id}.log"

    if [ ! -f "$script_path" ]; then
        echo -e "${RED}❌ $task_id: $script_path 不存在${NC}"
        return 1
    fi

    echo -e "\n${BLUE}▶ $task_id ($script)${NC}"
    local start=$(date +%s)

    local exit_code
    if [ -n "$TIMEOUT_CMD" ]; then
        $TIMEOUT_CMD "$POC_TIMEOUT" "$PYTHON" "$script_path" > "$output" 2> "$log"
        exit_code=$?
    else
        # perl alarm 兜底
        perl -e "alarm $POC_TIMEOUT; exec @ARGV" -- "$PYTHON" "$script_path" > "$output" 2> "$log"
        exit_code=$?
    fi

    local end=$(date +%s)
    local duration=$((end - start))

    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}✓ $task_id 完成 (${duration}s)${NC}"
        # 显示 stdout 前 300 字符给你预览
        if [ -s "$output" ]; then
            echo -e "${BLUE}  stdout 预览:${NC}"
            head -c 300 "$output"
            echo
        else
            echo -e "${YELLOW}  ⚠️  stdout 为空${NC}"
        fi
    elif [ $exit_code -eq 124 ] || [ $exit_code -eq 142 ]; then
        echo -e "${RED}❌ $task_id 超时 (${duration}s ≥ ${POC_TIMEOUT}s)${NC}"
    else
        echo -e "${RED}❌ $task_id 失败 (exit=$exit_code, ${duration}s)${NC}"
        echo -e "${YELLOW}  stderr 最后 5 行:${NC}"
        tail -5 "$log" | sed 's/^/    /'
    fi
}

echo -e "\n${BLUE}=== Step 2: 跑 6 个 PoC 脚本 ===${NC}"
run_poc "install_check"  "install_check.py"
run_poc "poc_t1"         "poc_t1.py"
run_poc "poc_tau"        "poc_tau.py"
run_poc "poc_gaia"       "poc_gaia.py"
run_poc "poc_t3"         "poc_t3.py"
run_poc "poc_concurrent" "poc_concurrent.py"

# ──────────────────────────────────────────
# Step 3: 汇总
# ──────────────────────────────────────────
echo -e "\n${BLUE}=== Step 3: 汇总 ===${NC}"

# 生成一份 combined report
COMBINED="$OUTPUT_DIR/combined.txt"
{
    echo "==================================================="
    echo " F103d OctoBench Phase 0 PoC 实测汇总"
    echo " 时间: $(date)"
    echo " 输出目录: $OUTPUT_DIR"
    echo " ANTHROPIC_API_KEY: $([ $KEY_OK -eq 1 ] && echo SET || echo MISSING)"
    echo " HF_TOKEN:          $([ $HF_OK -eq 1 ] && echo SET || echo MISSING)"
    echo "==================================================="
    for task in install_check poc_t1 poc_tau poc_gaia poc_t3 poc_concurrent; do
        echo
        echo "─── $task ───"
        echo "[stdout (${task}.json)]"
        if [ -s "$OUTPUT_DIR/${task}.json" ]; then
            cat "$OUTPUT_DIR/${task}.json"
        else
            echo "(空)"
        fi
        echo
        echo "[stderr 最后 20 行 (${task}.log)]"
        if [ -s "$OUTPUT_DIR/${task}.log" ]; then
            tail -20 "$OUTPUT_DIR/${task}.log"
        else
            echo "(空)"
        fi
    done
} > "$COMBINED"

echo -e "${GREEN}✓ 所有 6 个 PoC 跑完${NC}"
echo -e "📁 输出目录: ${BLUE}$OUTPUT_DIR${NC}"
echo -e "📄 汇总文件: ${BLUE}$COMBINED${NC}"
echo
echo -e "${YELLOW}↓ 给 Claude 主 session 整合 phase-0-poc-report.md 的最简方式：${NC}"
echo
echo -e "  ${GREEN}cat $COMBINED | pbcopy${NC}     # 复制到剪贴板，直接粘贴回 Claude"
echo -e "  ${GREEN}cat $COMBINED${NC}              # 或者直接在终端读"
echo
echo -e "${BLUE}== 输出文件清单 ==${NC}"
ls -la "$OUTPUT_DIR/" | tail -n +4
