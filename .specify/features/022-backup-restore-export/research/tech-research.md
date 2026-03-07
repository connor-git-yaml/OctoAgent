# Feature 022 技术调研：Backup/Restore + Export + Recovery Drill

**特性分支**: `codex/feat-022-backup-restore-export`
**调研日期**: 2026-03-07
**调研模式**: full（含在线调研）
**产品调研基础**: `research/product-research.md`

## 1. 调研问题

1. 当前代码里哪些数据源已经稳定，可直接纳入 backup/export？
2. 022 应该落在哪些模块，才能兼顾 CLI、Web 和测试可维护性？
3. restore dry-run 最小需要检查哪些冲突，才能对用户有实际价值？
4. recovery drill 结果应该落在哪里，才能被 CLI 与 Web 同时消费？

## 2. 当前代码基线（AgentsStudy）

### 2.1 数据路径已经稳定，但没有恢复服务

- `octoagent/packages/core/src/octoagent/core/config.py`
  - 已定义 `get_db_path()`、`get_artifacts_dir()`，默认使用 `data/sqlite/octoagent.db` 和 `data/artifacts/`。
- `octoagent/apps/gateway/src/octoagent/gateway/main.py`
  - 启动时通过 `create_store_group(db_path, artifacts_dir)` 初始化 Store。

结论：022 不需要重新定义数据根目录，可以直接基于 `core.config` 的路径约定构建 backup bundle。

### 2.2 当前 Web/CLI 入口已具备增量扩展点

- `octoagent/packages/provider/src/octoagent/provider/dx/cli.py`
  - 当前已经有 `config`、`doctor`、`onboard`，适合继续挂载 `backup` / `restore` / `export` 子命令。
- `octoagent/packages/provider/pyproject.toml`
  - `octo` 入口稳定，无需调整打包机制。
- `octoagent/apps/gateway/src/octoagent/gateway/routes/health.py`
  - 已有“最小运维状态”返回结构，适合继续暴露 recovery drill 摘要。
- `octoagent/frontend/src/pages/TaskList.tsx`
  - 首页非常轻量，容易加一个最小的 recovery/backup 状态卡片。

结论：022 不需要新建独立 app。CLI 延续 `provider.dx`，Web 最小入口延续 `gateway + frontend` 即可。

### 2.3 当前没有 backup/export/recovery domain model

现有 core models 里没有：

- `BackupBundle`
- `RestorePlan`
- `ExportManifest`
- `RecoveryDrillRecord`

现有 `EventType` 也没有 backup 生命周期事件。blueprint 中虽定义了 `BACKUP_STARTED / COMPLETED / FAILED`，但代码尚未落地。

结论：022 需要先补齐一组 domain models，并把备份/恢复相关事件类型纳入 core。

### 2.4 当前 chat export 可依赖 Task/Event/Artifact，而不是另建聊天存储

- `routes/chat.py` 已通过 Task + Event 驱动 Web chat。
- `routes/tasks.py` 已能查询 task 详情、events、artifacts。
- `Task` 模型包含 `thread_id`、`scope_id`、`requester`。

结论：022 的 `export chats` 可先基于任务/事件投影视图实现，不需要等待 021 的 Chat Import 内核。

### 2.5 当前没有恢复演练记录存储

代码中没有 recovery drill 持久化路径，也没有最近一次 backup 的结构化摘要。

结论：022 需要引入一个轻量状态文件或投影文件，例如：

- `data/ops/recovery-drill.json`
- `data/ops/latest-backup.json`

这样 CLI 和 Web 都能读取同一份状态。

## 3. 参考实现证据

### 3.1 Agent Zero：bundle + preview_restore + overwrite policy

本地参考显示 Agent Zero 已有：

- `backup_create.py`：创建 ZIP bundle 并返回下载；
- `backup_restore_preview.py`：先预览 restore 结果，再决定是否真正恢复；
- `helpers/backup.py`：支持 metadata、checksum、pattern translation、overwrite policy。

关键启示：

1. restore dry-run 应先生成“文件动作计划”，而不是直接开始恢复；
2. bundle 需要 metadata / manifest，而不仅是裸文件压缩包；
3. 迁移到新机器时，路径需要通过 manifest 中的环境信息做映射，而不是写死绝对路径。

### 3.2 OpenClaw：doctor 与迁移收口

OpenClaw 的迁移文档和 doctor 文档给了两个重要信号：

1. 迁移后的安全收口应该是诊断和修复，而不是只复制完文件；
2. backup 中可能含 secrets，必须显式提醒并按敏感数据处理。

关键启示：

- 022 的 recovery drill 不应该只是“文件存在”；它应该输出修复建议和最近验证结论。
- bundle manifest 需要明确敏感性摘要，避免操作者误把 bundle 当普通日志文件分发。

### 3.3 官方 SQLite / Python 文档：在线备份应走 backup API

在线调研和官方文档确认：

- Python `sqlite3.Connection.backup()` 是官方封装的在线备份接口；
- SQLite Online Backup API 支持在数据库在线时生成一致性快照；
- WAL 模式下可在备份后执行 `PRAGMA wal_checkpoint(TRUNCATE)` 回收 WAL。

结论：022 的 SQLite bundle 不应依赖“先停服务再复制 db 文件”。MVP 应直接用 backup API。

## 4. 方案对比

### 方案 A：直接用 tar/zip 打包整个 `data/` 目录

- 优点：实现最快
- 缺点：
  - 无 manifest，无法做 dry-run 冲突解释；
  - 无法区分 config metadata / chats / artifacts；
  - SQLite 在线复制有一致性风险；
  - 很难给用户输出最近恢复演练状态

### 方案 B：建立结构化 backup service + manifest + dry-run planner（推荐）

- 优点：
  - 与 blueprint 的 durability / observability 要求一致；
  - 可先交付 dry-run，不强行做 destructive restore；
  - Web/CLI 共享同一组模型和状态文件
- 缺点：需要补一组 models / services / tests

### 方案 C：先做 Web 面板，再补 CLI

- 优点：界面更直观
- 缺点：CLI 才是最稳的系统入口；没有 CLI 很难支撑恢复演练和 CI 级验证

## 5. 技术决策建议

1. **核心模型放在 `packages/core`**
   - `BackupBundle`
   - `BackupScope`
   - `RestorePlan`
   - `RestoreConflict`
   - `ExportManifest`
   - `RecoveryDrillRecord`

2. **操作服务放在 `packages/provider/dx`**
   - `backup_service.py`
   - `backup_models.py`（如果 CLI 展示模型需要与 core 分离）
   - `backup_commands.py`
   理由：022 首先是 operator-facing DX 能力，与 014/015 的 CLI 体系一致。

3. **Web API 放在 `apps/gateway`**
   - 新增 `routes/ops.py` 或等价 route：
     - `GET /api/ops/recovery`
     - `POST /api/ops/export/chats`
   理由：前端应通过 gateway 读取摘要，而不是直接读文件。

4. **最近状态采用文件持久化**
   - `data/ops/latest-backup.json`
   - `data/ops/recovery-drill.json`
   理由：022 不需要为此引入新表；文件足以满足最小入口和 CLI/Web 共读。

5. **restore 范围只到 dry-run**
   - 检查 manifest schema version
   - 检查 bundle 完整性
   - 检查目标路径是否存在
   - 检查 config / db / artifacts / chats 是否会覆盖
   - 输出建议动作，不执行 apply

6. **chat export 基于 task/event/artifact 投影**
   - 支持按 `thread_id`、`task_id`、时间窗口筛选
   - 导出 JSON manifest，可附最小文本摘要
   - 不直接绑定未来的 Chat Import schema

7. **恢复演练记录需要进入健康面**
   - `/ready` 或新 ops route 至少暴露：
     - `last_backup_at`
     - `last_recovery_drill_at`
     - `last_recovery_drill_status`
     - `last_failure_reason`

## 6. 推荐的最小 bundle 范围

### 默认包含

- SQLite snapshot（tasks / events / checkpoints / projections）
- artifact 文件树
- `octoagent.yaml`
- `litellm-config.yaml`
- recovery metadata / manifests

### 默认不包含

- `.env`
- `.env.litellm`
- 其他明文 secrets 文件
- `node_modules` / `.venv` / cache / 临时运行目录

### 原因

- 这与 blueprint 中“config metadata”而不是“全部 secrets”一致；
- 也更符合 OpenClaw 对 backup secrets 风险的显式提醒；
- 避免用户把敏感数据无感打包后扩散。

## 7. 风险与缓解

- 风险：把 destructive restore 一起做进来，扩大测试面
  - 缓解：022 只交付 dry-run，不交付 apply。
- 风险：backup 只做文件压缩，缺少结构化信息
  - 缓解：manifest + checksum + sensitivity summary 成为必选项。
- 风险：Web 入口直接读取本地文件，破坏架构边界
  - 缓解：前端只走 gateway route。
- 风险：chat export 提前绑定 021 schema
  - 缓解：用 task/event/artifact 最小投影导出。

## 8. 在线补充结论（摘要）

详见 `research/online-research.md`。

- Agent Zero 的 Backup & Restore UI 已验证“先 preview 再 restore”的用户路径；
- OpenClaw 的迁移文档强调 backup 含 secrets，且迁移后需要 `doctor` 收口；
- Python/SQLite 官方文档支持在线 backup API + WAL checkpoint，适合我们的单机架构。

## 9. 结论

Feature 022 的最佳技术路径是：

1. 先补 core backup/export/recovery models；
2. 在 `provider.dx` 上交付 CLI 主入口；
3. 在 `gateway + frontend` 上交付最小摘要入口；
4. 只做到 restore dry-run，不做 destructive restore apply；
5. 用在线 SQLite backup + manifest/checksum/sensitivity summary 保证 bundle 可解释、可验证、可审计。
