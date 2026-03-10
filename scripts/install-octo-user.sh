#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF_REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_ROOT="${OCTOAGENT_HOME:-$HOME/.octoagent}"
APP_ROOT="${INSTALL_ROOT}/app"
REPO_URL="${OCTOAGENT_REPO_URL:-https://github.com/connor-git-yaml/OctoAgent.git}"
BRANCH="${OCTOAGENT_BRANCH:-master}"
PROJECT_ROOT="${APP_ROOT}/octoagent"
LOCAL_SOURCE=""

need_cmd() {
  local cmd="$1"
  local hint="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[install-octo-user] 缺少依赖: $cmd"
    echo "  $hint"
    exit 1
  fi
}

install_uv_if_needed() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  need_cmd curl "请先安装 curl。"
  echo "[install-octo-user] 未检测到 uv，开始自动安装..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.cargo/bin:$PATH"
  need_cmd uv "uv 安装失败，请手动执行 https://docs.astral.sh/uv/getting-started/installation/"
}

resolve_local_source() {
  if [[ -z "${OCTOAGENT_REPO_URL:-}" ]] && [[ -x "$SELF_REPO_ROOT/octoagent/scripts/install-octo-home.sh" ]]; then
    LOCAL_SOURCE="$SELF_REPO_ROOT"
  elif [[ "$REPO_URL" == file://* ]]; then
    LOCAL_SOURCE="${REPO_URL#file://}"
  elif [[ -d "$REPO_URL" ]]; then
    LOCAL_SOURCE="$REPO_URL"
  fi
}

need_cmd git "请先安装 git。"
need_cmd python3 "请先安装 Python 3.12+。"
need_cmd node "请先安装 Node.js 20+。"
need_cmd npm "请先安装 npm。"
install_uv_if_needed
resolve_local_source

mkdir -p "$INSTALL_ROOT"

if [[ -n "$LOCAL_SOURCE" ]]; then
  need_cmd tar "请先安装 tar。"
  rm -rf "$APP_ROOT"
  mkdir -p "$APP_ROOT"
  echo "[install-octo-user] 检测到本地源码目录，直接复制工作树..."
  (
    cd "$LOCAL_SOURCE"
    tar \
      --exclude .git \
      --exclude .venv \
      --exclude .pytest_cache \
      --exclude .ruff_cache \
      --exclude data \
      -cf - .
  ) | (
    cd "$APP_ROOT"
    tar -xf -
  )
elif [[ -d "$APP_ROOT/.git" ]]; then
  echo "[install-octo-user] 检测到已有源码，尝试更新..."
  git -C "$APP_ROOT" fetch --depth=1 origin "$BRANCH"
  git -C "$APP_ROOT" checkout "$BRANCH"
  git -C "$APP_ROOT" pull --ff-only origin "$BRANCH"
else
  rm -rf "$APP_ROOT"
  echo "[install-octo-user] 正在拉取源码到 $APP_ROOT ..."
  git clone --depth=1 --branch "$BRANCH" "$REPO_URL" "$APP_ROOT"
fi

if [[ ! -x "$PROJECT_ROOT/scripts/install-octo-home.sh" ]]; then
  echo "[install-octo-user] 未找到内部安装脚本: $PROJECT_ROOT/scripts/install-octo-home.sh"
  exit 1
fi

echo "[install-octo-user] 正在初始化个人实例..."
"$PROJECT_ROOT/scripts/install-octo-home.sh" --instance-root "$INSTALL_ROOT" "$@"

cat <<EOF

[install-octo-user] 安装完成。
  实例根目录: $INSTALL_ROOT
  源码目录:   $APP_ROOT

下一步:
  1. 启动 Web:     $INSTALL_ROOT/bin/octo-start
  2. 健康检查:     $INSTALL_ROOT/bin/octo-doctor
  3. 命令行入口:   $INSTALL_ROOT/bin/octo

如需把 CLI 加入 PATH:
  export PATH="$INSTALL_ROOT/bin:\$PATH"
EOF
