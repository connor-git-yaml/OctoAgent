# Claude/Codex 配置同步方案

## 目标

避免 `CLAUDE.md` 与 `AGENTS.md` 双份手动维护导致的配置漂移（Gap）。

## 设计

- 单一事实源：`.agent-config/shared.md`
- Claude 模板：`.agent-config/templates/claude.header.md`
- Codex 模板：`.agent-config/templates/agents.header.md`
- 同步脚本：`scripts/sync-agent-config.sh`

生成关系：

1. `CLAUDE.md` = `claude.header.md` + `shared.md`
2. `AGENTS.md` = `agents.header.md` + `shared.md`

## 使用方式

1. 修改共享内容：编辑 `.agent-config/shared.md`
2. 若有平台差异：编辑对应模板头部文件
3. 运行同步：`./scripts/sync-agent-config.sh`
4. 漂移检查：`./scripts/sync-agent-config.sh --check`

可选本地私有配置同步（不入库）：

- `./scripts/sync-agent-config.sh --sync-local`
- 行为：`CLAUDE.local.md` -> `AGENTS.local.md`

## 约束

- 不要手改 `CLAUDE.md` / `AGENTS.md`（两者为生成文件）
- 如果要上 CI，直接加一条检查命令：`./scripts/sync-agent-config.sh --check`
