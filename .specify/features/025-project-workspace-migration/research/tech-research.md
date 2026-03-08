# Tech Research: Feature 025 — Project / Workspace Domain Model + Default Project Migration

**Date**: 2026-03-08  
**Mode**: tech-only

---

## 1. 当前代码基线

### 1.1 现状不是 first-class project/workspace

- `provider.dx` 当前的“project”本质上只是 filesystem `project_root`，典型入口是 `resolve_project_root()`、`load_config(project_root)`、`RecoveryStatusStore(project_root)`。
- `Task` / `Memory` / `Chat Import` 的唯一命名空间仍是 `scope_id`，没有任何持久化的 `project_id` / `workspace_id`。
- `backup/recovery` 的 project 信息只出现在 `BackupManifest.source_project_root` 和 `data/ops/*.json` 中，不是产品级实体。

### 1.2 旧 metadata 的真实分布

- Core SQLite:
  - `tasks.scope_id` / `tasks.thread_id` / `requester.channel`
  - `events.payload` 中的 `TaskCreatedPayload.scope_id`、`ChatImportLifecyclePayload.scope_id`、`BackupLifecyclePayload.scope_summary`
- Memory SQLite tables:
  - `memory_fragments.scope_id`
  - `memory_sor.scope_id`
  - `memory_write_proposals.scope_id`
  - `memory_vault.scope_id`
- Chat import SQLite tables:
  - `chat_import_batches.scope_id/channel/thread_id/source_id`
  - `chat_import_cursors.scope_id`
  - `chat_import_dedupe.scope_id`
  - `chat_import_windows.scope_id`
  - `chat_import_reports.scope_id`
- Files / JSON:
  - `octoagent.yaml`
  - `.env`
  - `.env.litellm`
  - `data/ops/latest-backup.json`
  - `data/ops/recovery-drill.json`

### 1.3 现有可复用骨架

- `packages/core` 已经承载跨系统 domain model 与 SQLite store，适合新增 `Project` / `Workspace` / `MigrationRun`。
- `packages/provider/dx` 已经具备：
  - `config_wizard.py` 的原子 YAML 读写
  - `dotenv_loader.py` 的 `.env` 自动加载
  - `backup_service.py` / `recovery_status_store.py` 的 `project_root/data` 文件持久化模式
  - `config migrate` 占位命令，天然可作为迁移入口
- `apps/gateway/main.py` 拥有稳定 startup hook，适合自动触发 default project migration。

---

## 2. 备选方案评估

### 方案 A：只扩展 `octoagent.yaml`

**做法**: 在 `OctoAgentConfig` 根模型中直接加入 `projects/workspaces`，所有映射都塞进 YAML。

**问题**:

- 历史 scope/memory/import/backup 元数据主要在 SQLite 和 JSON snapshot，不在 YAML。
- `config_wizard.save_config()` 会重写 schema，未来若 project mapping 很大，YAML 会成为高 churn 文件。
- YAML 不适合作为大量 runtime-migrated binding 的唯一事实源。

**结论**: 只适合保存人编写配置，不适合承载 migration backfill 主数据。

### 方案 B：`provider/dx` JSON registry

**做法**: 新增 `data/projects.json` / `data/workspaces.json` 等 project registry。

**优点**:

- 实现快，能复用 `OnboardingSessionStore` / `RecoveryStatusStore` 的 filelock 模式。

**问题**:

- `Project/Workspace` 是跨系统 domain object，不应埋在 DX 层。
- 后续 `Task/Memory/Import/Backup` 若要 join project/workspace，JSON registry 查询和一致性都更差。

**结论**: 可作为小型状态存储，但不适合作为 canonical domain store。

### 方案 C：`core` formal model + SQLite bindings + `provider.dx` migration orchestrator

**做法**:

- 在 `packages/core` 新增 `Project` / `Workspace` / `ProjectBinding` / `ProjectMigrationRun`
- 在 core SQLite 新增 project/workspace/binding/migration tables
- 在 `provider.dx` 新增 `ProjectWorkspaceMigrationService`
- Gateway startup 和 `octo config migrate` 调用该服务

**优点**:

- 分层清晰
- 对后续 selector / config center / runtime console 友好
- 能用同一 DB 做 join / replay / validation
- rollback 易于按 `migration_run_id` 精确清理

**结论**: 采用该方案。

---

## 3. 设计决策

### D1: canonical model 放在哪里？

- **Decision**: 放在 `octoagent/packages/core/src/octoagent/core/models/project.py`
- **Why**: project/workspace 是跨 gateway/provider/memory/backup 的通用实体，和 `BackupBundle` 一样属于 domain layer，而不是 DX config 附件。

### D2: 第一阶段是否修改 legacy `scope_id`？

- **Decision**: 不修改
- **Why**:
  - `memory_sor` current 唯一约束依赖 `scope_id`
  - `chat_import_cursors` / `dedupe` 主键依赖 `scope_id`
  - destructive rename 会引入大规模历史数据 rewrite 和回归风险
- **Consequence**: 使用 `workspace_scope_bindings` 风格的旁路映射；读路径优先 project/workspace，缺失时回退 legacy scope。

### D3: 需要哪些持久化对象？

- **Decision**: 最小集合为四类对象
  - `Project`
  - `Workspace`
  - `ProjectBinding`
  - `ProjectMigrationRun`
- **Why**:
  - 先把 domain 主键和 migration audit 建立起来
  - bindings 用统一 typed 表承载 scope/channel/import/backup/env references
  - 不在第一阶段引入完整 Secret Store / project selector state

### D4: env 兼容桥怎么做？

- **Decision**: 只登记引用，不搬 secret 值
- **具体**:
  - 持久化 `.env` / `.env.litellm` 文件存在性与路径
  - 持久化 `runtime.master_key_env`、provider `api_key_env`、Telegram `bot_token_env` / `webhook_secret_env`
  - runtime 继续沿用现有 env 读取逻辑
- **Why**: 用户要求排除 Secret Store 实值存储；第一阶段的目标是 bridge metadata 和 migration gate。

### D5: migration validation / rollback 怎么做？

- **Decision**: 显式 `ProjectMigrationRun`
- **Validation**:
  - default project 存在且唯一
  - primary workspace 存在且 root 指向当前 `project_root`
  - 所有 legacy scope 都有 binding
  - 若存在 memory/import/backup/env/channel 元数据，则对应 binding 均存在
- **Rollback**:
  - 所有新增 row 都带 `migration_run_id`
  - rollback 时按 run_id 删除 bindings / workspaces / projects（仅删除该 run 创建的记录）
  - 不修改 legacy task/memory/import/backup snapshot，因此无需恢复历史 payload

### D6: 自动迁移挂在哪些入口？

- **Decision**:
  - `apps/gateway/main.py` startup 自动 `ensure_default_project()`
  - `octo config migrate` 提供手工 dry-run / apply / rollback 入口
  - `BackupService` / `ChatImportService` 在进入持久化路径前确保 migration 已完成
- **Why**: 这几条路径同时覆盖“实例启动”“显式升级”“关键遗留元数据消费者”。

---

## 4. 数据模型草案

### 4.1 Project

- `project_id: str`
- `slug: str`
- `name: str`
- `description: str`
- `status: active|archived`
- `is_default: bool`
- `created_at / updated_at`
- `metadata: dict[str, Any]`

### 4.2 Workspace

- `workspace_id: str`
- `project_id: str`
- `slug: str`
- `name: str`
- `kind: primary|chat|ops|legacy`
- `root_path: str`
- `created_at / updated_at`
- `metadata: dict[str, Any]`

### 4.3 ProjectBinding

- `binding_id: str`
- `project_id: str`
- `workspace_id: str | None`
- `binding_type: scope|memory_scope|import_scope|channel|backup_root|env_ref|env_file`
- `binding_key: str`
- `binding_value: str`
- `source: str`
- `metadata_json`
- `migration_run_id: str`
- `created_at / updated_at`

### 4.4 ProjectMigrationRun

- `run_id: str`
- `project_root: str`
- `status: pending|succeeded|failed|rolled_back`
- `started_at / completed_at`
- `summary_json`
- `validation_json`
- `rollback_json`
- `error_message`

---

## 5. 迁移策略

### Phase 1: additive schema

- `core.store.sqlite_init` 增加 project/workspace/binding/migration tables
- 不改 legacy tables 主键/唯一约束

### Phase 2: default project seed

- 若不存在 `is_default=1` project，则创建：
  - `slug=default`
  - `name=Default Project`
  - `primary workspace` root 指向当前 `project_root`

### Phase 3: backfill bindings

- `tasks.scope_id` -> `binding_type=scope`
- memory tables `scope_id` -> `binding_type=memory_scope`
- `chat_import_*` -> `binding_type=import_scope`
- YAML channel config / task requester channels -> `binding_type=channel`
- backup/export/recovery paths -> `binding_type=backup_root`
- env names / env files -> `binding_type=env_ref` / `env_file`

### Phase 4: validation

- 枚举 legacy sources，比较“发现集”与“binding 集”
- 生成 validation report
- 失败则 rollback 当前 run

### Phase 5: dual-read bridge

- project-aware 读路径使用：
  - `default project`
  - `binding lookup`
  - fallback to legacy `scope_id`

---

## 6. 测试矩阵

### 6.1 Domain / schema

- Project / Workspace / Binding / MigrationRun 模型校验
- Core SQLite init 创建四类新表

### 6.2 Migration service

- 空实例自动创建 default project + primary workspace
- 旧 task scope 回填为 scope bindings
- memory/import/backup/env/channel metadata 回填成功
- 重复执行幂等，无重复 binding
- validation 失败触发 rollback

### 6.3 Integration surfaces

- Gateway startup 自动完成 default project migration
- `octo config migrate --dry-run` 输出 validation summary
- `octo config migrate --rollback latest` 清理 run 产物
- Backup / ChatImport 在 legacy 实例上也能拿到 project bindings

---

## 7. 风险与防线

- **风险**: 扫描不存在的 memory/import tables 导致 migration 失败  
  **防线**: 先检查 `sqlite_master`，仅扫描已存在表

- **风险**: 自动迁移把实例留在半更新状态  
  **防线**: 单 run 事务 + `migration_run_id` + rollback

- **风险**: 提前把 secret 实值写入新表  
  **防线**: bridge 只存 env/file reference，不存 resolved values

- **风险**: 当前系统尚未消费 `project_id`，迁移看似“落了数据但没人用”  
  **防线**: Gateway startup、BackupService、ChatImportService、CLI migrate 先接入 `ensure_default_project()`，保证读写面至少能验证新层存在。
