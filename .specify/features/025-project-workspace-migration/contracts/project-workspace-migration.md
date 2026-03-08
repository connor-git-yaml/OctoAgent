# Contract: Project / Workspace Migration Service

## 1. Domain API

### `ProjectStore`

- `get_default_project() -> Project | None`
- `upsert_project(project: Project) -> Project`
- `list_projects() -> list[Project]`
- `get_project(project_id: str) -> Project | None`
- `upsert_workspace(workspace: Workspace) -> Workspace`
- `list_workspaces(project_id: str) -> list[Workspace]`
- `replace_bindings(project_id: str, migration_run_id: str, bindings: list[ProjectBinding]) -> None`
- `list_bindings(project_id: str, binding_type: ProjectBindingType | None = None) -> list[ProjectBinding]`
- `save_migration_run(run: ProjectMigrationRun) -> None`
- `get_latest_migration_run(project_root: str) -> ProjectMigrationRun | None`
- `delete_run_artifacts(run_id: str) -> None`

## 2. Migration Orchestrator

### `ProjectWorkspaceMigrationService.plan()`

**Input**

- `project_root: Path`

**Output**

- `ProjectMigrationRun(status=dry_run, summary=..., validation=..., rollback_plan=...)`

**Behavior**

- 枚举 legacy metadata
- 计算将创建的 project/workspace/bindings
- 不写入持久化主记录

### `ProjectWorkspaceMigrationService.apply()`

**Input**

- `project_root: Path`
- `allow_existing_default: bool = True`

**Output**

- `ProjectMigrationRun(status=succeeded|failed, ...)`

**Behavior**

- 幂等创建 `default project`
- 幂等创建 primary workspace
- 回填 legacy bindings
- 执行 validation
- 失败时 rollback 当前 run

### `ProjectWorkspaceMigrationService.rollback(run_id: str | Literal["latest"])`

**Output**

- `ProjectMigrationRun(status=rolled_back, ...)`

**Behavior**

- 删除指定 run 创建的 bindings / workspaces / projects
- 不修改 legacy task/memory/import/backup 数据

## 3. Bootstrap Contract

### Gateway startup

- `apps/gateway/main.py` 在创建关键服务前调用 `ensure_default_project(project_root, conn)`
- 若 migration 失败，startup 失败并输出结构化错误

### CLI

- `octo config migrate --dry-run`
- `octo config migrate`
- `octo config migrate --rollback latest`

## 4. Validation Contract

最少校验项：

- 唯一 default project
- 唯一 primary workspace
- legacy scope 全覆盖
- env bridge 全覆盖（对配置/文件中真实存在的 legacy entry）
- 若数据库存在相关表，则执行 SQLite integrity checks

## 5. Rollback Contract

- 只能删除 `migration_run_id` 属于当前 run 的新增记录
- 回滚后系统仍然回到 legacy-only 可运行态
- rollback 不删除：
  - `tasks/events/artifacts`
  - `memory_*`
  - `chat_import_*`
  - `data/ops/*.json`
  - `.env` / `.env.litellm`
  - `octoagent.yaml`
