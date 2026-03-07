# Feature 022 调研汇总

- 调研模式：`full`
- 在线调研：`perplexity-web-search`，3 个调研点（见 `research/online-research.md`）

## 关键参考证据

1. Agent Zero backup/restore 与 chat save/load：
   - `_references/opensource/agent-zero/knowledge/main/about/installation.md`
   - `_references/opensource/agent-zero/knowledge/main/about/github_readme.md`
   - `_references/opensource/agent-zero/python/api/backup_create.py`
   - `_references/opensource/agent-zero/python/api/backup_restore_preview.py`
   - `_references/opensource/agent-zero/python/helpers/backup.py`
2. OpenClaw doctor / migration / backup 安全提示：
   - `_references/opensource/openclaw/docs/cli/doctor.md`
   - `_references/opensource/openclaw/docs/install/migrating.md`
   - `_references/opensource/openclaw/src/config/backup-rotation.ts`
3. 当前代码基线：
   - `octoagent/packages/core/src/octoagent/core/config.py`
   - `octoagent/packages/provider/src/octoagent/provider/dx/cli.py`
   - `octoagent/apps/gateway/src/octoagent/gateway/routes/health.py`
   - `octoagent/apps/gateway/src/octoagent/gateway/routes/tasks.py`

## 结论

1. 022 应交付 `backup create + restore dry-run + export chats + recovery status` 四件套，而不是只补一个压缩脚本。
2. `restore` 在本 Feature 只做到 `dry-run`，不做 destructive apply。
3. `latest-backup.json` 与 `recovery-drill.json` 应作为 CLI/Web 共读状态源。
4. backup 默认不包含明文 secrets 文件，但必须显式输出敏感性提示。
5. chat export 可以直接基于现有 `task/event/artifact` 投影，不需要等待 021。
