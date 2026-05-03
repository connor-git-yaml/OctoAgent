# OctoAgent 工程根 Makefile
#
# F087 P3 T-P3-9：install-hooks target —— 把 .githooks/ 装到 git config core.hooksPath
# 让 pre-commit / post-checkout 等 hook 在 commit 时自动跑。
#
# F087 P3 fixup#4 (Codex finding-4)：worktree-aware install。
# 在 linked worktree 安装时只写 .git/worktrees/<name>/config.worktree，
# 绝不写 common .git/config——避免污染主仓和其他 worktree 的 hook 配置。

.PHONY: install-hooks help

help:
	@echo "OctoAgent 根 Makefile targets:"
	@echo "  make install-hooks  - 装 .githooks/ 到 git config core.hooksPath"
	@echo "                        (一次性安装，后续 commit 自动跑 e2e_smoke 套件)"
	@echo "                        worktree-aware: linked worktree 只写 worktree config"

install-hooks:
	@# 检测是否在 linked worktree（git common-dir != git-dir 表示 linked worktree）
	@COMMON_DIR="$$(git rev-parse --git-common-dir 2>/dev/null)"; \
	GIT_DIR="$$(git rev-parse --git-dir 2>/dev/null)"; \
	if [ -z "$$COMMON_DIR" ] || [ -z "$$GIT_DIR" ]; then \
		echo "[hooks] FATAL: 不在 git 仓库内（git rev-parse 失败）"; \
		exit 1; \
	fi; \
	if [ "$$(cd "$$COMMON_DIR" && pwd)" != "$$(cd "$$GIT_DIR" && pwd)" ]; then \
		echo "[hooks] linked worktree 检测到 (common=$$COMMON_DIR, dir=$$GIT_DIR)"; \
		echo "[hooks] 只写 --worktree 级别配置，不污染主仓 .git/config"; \
		git config --worktree core.hooksPath .githooks; \
	else \
		echo "[hooks] 主 worktree（common == dir），写 common .git/config"; \
		git config core.hooksPath .githooks; \
	fi
	@echo "[hooks] 已设置 core.hooksPath = .githooks"
	@echo "[hooks] 当前安装的 hooks:"
	@ls -1 .githooks/ | sed 's/^/  - /'
	@echo "[hooks] bypass: SKIP_E2E=1 git commit -m '...'"
	@echo "[hooks] 来源验证（show-origin）:"
	@git config --show-origin --get core.hooksPath || echo "[hooks] WARN: 未读到 hooksPath"
