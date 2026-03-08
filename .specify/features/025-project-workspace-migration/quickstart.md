# Quickstart: Feature 025 第一阶段验证

## 1. 准备 legacy 项目目录

```bash
mkdir -p /tmp/octo-f025/data/sqlite
mkdir -p /tmp/octo-f025/data/ops
```

准备：

- `octoagent.yaml`
- `.env`
- `.env.litellm`
- 至少一条 legacy task
- 可选的 memory/import/backup/recovery 状态

## 2. dry-run migration

```bash
uv run --project octoagent python -m octoagent.provider.dx.cli config migrate --dry-run
```

预期：

- 输出将创建的 `default project`
- 输出 primary workspace root
- 输出 legacy scope / memory / import / backup / env bridge 统计
- 不写入最终 project/workspace/binding 记录

## 3. apply migration

```bash
uv run --project octoagent python -m octoagent.provider.dx.cli config migrate --yes
```

预期：

- 默认 project/workspace 已创建
- validation report 显示 `ok=true`
- 重复执行时结果幂等

## 4. rollback latest

```bash
uv run --project octoagent python -m octoagent.provider.dx.cli config migrate --rollback latest --yes
```

预期：

- 删除该 run 创建的 project/workspace/bindings
- legacy `tasks/memory/import/backup` 数据不受影响

## 5. 自动 bootstrap 验证

启动 Gateway 或调用 Backup / Chat Import 服务：

```bash
uv run --project octoagent python -m pytest \
  octoagent/apps/gateway/tests/test_main.py \
  octoagent/packages/provider/tests/test_project_migration.py -q
```

预期：

- startup 路径会自动确保 default project
- 相关服务在 legacy 实例上也能得到 project/workspace bindings
