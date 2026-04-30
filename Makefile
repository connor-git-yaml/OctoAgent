# OctoAgent 工程根 Makefile
#
# F087 P3 T-P3-9：install-hooks target —— 把 .githooks/ 装到 git config core.hooksPath
# 让 pre-commit / post-checkout 等 hook 在 commit 时自动跑。

.PHONY: install-hooks help

help:
	@echo "OctoAgent 根 Makefile targets:"
	@echo "  make install-hooks  - 装 .githooks/ 到 git config core.hooksPath"
	@echo "                        (一次性安装，后续 commit 自动跑 e2e_smoke 套件)"

install-hooks:
	@# 主 repo 设置（git config 默认走 .git/config）
	@git config core.hooksPath .githooks
	@# worktree 也设置（git worktree 下 .git/worktrees/<name>/config.worktree 会
	@# 覆盖主 .git/config 的 hooksPath，必须显式 --worktree 解除覆盖）
	@if git rev-parse --is-inside-work-tree > /dev/null 2>&1; then \
		if git config --worktree --get core.hooksPath > /dev/null 2>&1; then \
			git config --worktree core.hooksPath .githooks; \
		fi; \
	fi
	@echo "[hooks] 已设置 core.hooksPath = .githooks"
	@echo "[hooks] 当前安装的 hooks:"
	@ls -1 .githooks/ | sed 's/^/  - /'
	@echo "[hooks] bypass: SKIP_E2E=1 git commit -m '...'"
	@echo "[hooks] 实测: git config --get core.hooksPath = $$(git config --get core.hooksPath)"
