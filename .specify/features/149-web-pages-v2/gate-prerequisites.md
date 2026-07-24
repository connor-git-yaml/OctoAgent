# F149 尚不存在的 Gate Prerequisites

> 目的：防止把未来 script/npm alias 当成当前可运行 Gate，或把“文件不存在”伪装为 TDD RED。

| 目标 Gate | 当前事实 | Tasks Gate 前置顺序 | 最终独立命令 |
|---|---|---|---|
| OpenAPI clean diff | 无 `frontend/scripts/check-openapi-generated.mjs`，无 `openapi:check` | 先建 checker fixture/test 并因预期 drift 判定失败；再创建 script + npm alias；最后接 artifact | `cd octoagent/frontend && npm run openapi:check` |
| F149 boundary AST | 无 `check-f149-boundaries.mjs` | 先以 synthetic import graph/direct-fetch fixture 得到 checker behavior RED；再实现 checker；最后扫真实 closure | `cd octoagent/frontend && node scripts/check-f149-boundaries.mjs` |
| Style ratchet | 无 `check-f149-style-ratchet.mjs` | 先以 index growth/hard-coded palette fixture RED；再实现 checker | `cd octoagent/frontend && node scripts/check-f149-style-ratchet.mjs` |
| Frontend coverage | package 无 `test:coverage`；无 frontend changed-lines checker | 先为 executable/type-only/generated/tests 行分类建 checker RED；再建 npm alias/script | `cd octoagent/frontend && npm run test:coverage` |
| Changed-lines ≥90% | 无 `repo-scripts/check-frontend-changed-lines-coverage.mjs` | 与上一行同一 checker task；script 存在后才可接 Gate | `node repo-scripts/check-frontend-changed-lines-coverage.mjs --lcov octoagent/frontend/coverage/lcov.info --base origin/master --min-percent 90` |

每条最终命令都从仓库根独立可复制；不得依赖上一条 `cd`。RED 必须是 checker 对 fixture 的错误判定/遗漏，不得是 script、package alias、test file 或依赖不存在。
