---
required: true
mode: full
points_count: 3
tools:
  - perplexity-web-search
queries:
  - "Agent Zero official backup restore settings UI save load chats installation docs"
  - "OpenClaw official doctor repair dashboard migration backup docs"
  - "Python sqlite3 backup official docs WAL checkpoint truncate SQLite online backup API"
findings:
  - Agent Zero 官方文档确认 Backup & Restore 已在 Settings UI 中形成用户可操作的 create backup / restore 路径，并强调 save/load chats。
  - OpenClaw 官方文档把 doctor 定位为迁移后的安全收口命令，并明确提醒 backups 含 secrets，需要按敏感数据处理。
  - Python 与 SQLite 官方文档确认在线 backup API 和 WAL checkpoint 是单机 SQLite 的正确技术路径。
impacts_on_design:
  - 022 必须提供 preview-first 的 restore dry-run，而不是直接 destructive restore。
  - backup bundle 需要 manifest、完整性信息和敏感性提示。
  - SQLite snapshot 应使用在线 backup API，并在备份后执行 WAL checkpoint。
skip_reason: ""
---

# 在线调研证据（Feature 022）

## Findings

1. **Agent Zero 已把 backup/restore 和 chat save/load 做成用户可直接使用的产品入口**
- 在线检索显示 Agent Zero 官方安装文档和 README 都把 Backup & Restore 放到明显的用户路径里，用于升级和迁移。
- 这证明“可恢复能力”对用户来说不是底层实现细节，而是日常运维入口。
- 同时它把 chats 的 save/load 直接暴露在 Web UI 中，说明“可导出”是用户能感知到的核心能力，而不是内部脚本。
- 参考：
  - [Agent Zero 安装文档](https://agent-zero.ai/en/docs/installation/)
  - [Agent Zero GitHub README](https://github.com/agent0ai/agent-zero)

2. **OpenClaw 的迁移路径强调 backup 后还需要 doctor 收口，而且 backup 本身带有 secrets 风险**
- 在线检索结果显示 OpenClaw 官方迁移文档建议先备份状态目录，再在新机器执行 `openclaw doctor` 完成迁移和修复。
- 文档还明确提醒 backup 含 secrets，应按生产敏感数据处理。
- 这意味着 022 不能把“生成 bundle”当成结束，必须同时暴露恢复演练状态和修复建议。
- 参考：
  - [OpenClaw Doctor 文档](https://docs.openclaw.ai/cli/doctor)
  - [OpenClaw Dashboard 文档](https://docs.openclaw.ai/web/dashboard)
  - [OpenClaw 迁移文档](https://docs.openclaw.ai/install/migrating)

3. **SQLite 在线备份和 WAL checkpoint 有官方标准做法**
- Python 标准库 `sqlite3.Connection.backup()` 和 SQLite 官方 Online Backup API 都明确支持在线一致性备份。
- SQLite 官方文档同时说明 WAL 模式下可以执行 `wal_checkpoint(TRUNCATE)` 回收 WAL。
- 这与 blueprint §12.4 完全一致，说明我们无需自创备份机制。
- 参考：
  - [Python sqlite3 backup 文档](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)
  - [SQLite Online Backup API](https://www.sqlite.org/backup.html)
  - [SQLite WAL 文档](https://www.sqlite.org/wal.html)

## impacts_on_design

- 设计决策 D1：`octo restore` 在 022 只做 `dry-run`，先产出 `RestorePlan` 和冲突清单，不做 destructive apply。
- 设计决策 D2：backup bundle 必须自带 manifest / checksum / sensitivity summary，而不是只有压缩文件。
- 设计决策 D3：最近一次 recovery drill 的时间、状态和失败原因必须可查询、可展示。
- 设计决策 D4：SQLite bundle 采用在线 backup API，备份后执行 WAL checkpoint，避免直接复制活跃数据库文件。

## 结论

在线证据与本地 references 结论一致：Feature 022 的核心不是“补脚本”，而是把 backup / export / restore dry-run 做成普通用户能安全使用、能理解风险、能看到恢复准备度的正式产品能力。
