# F149 TDD 与 Command Policy

> 本文件约束未来 Tasks/Implement/Review；当前 Design Gate 不生成 `tasks.md`，也不伪造 RED 证据。

## 1. Tasks Gate command 结构

每个行为 task 必须有三个独立 command 字段，顺序固定：

1. `RED_COMMAND`：只加入目标测试/fixture 后执行；预期非零退出，并写明当前生产行为为何违反 oracle。
2. `GREEN_COMMAND`：实施最小行为后执行；必须与 RED 验证同一行为，预期退出 0。
3. `REFACTOR_COMMAND`：重构后执行同一焦点命令及必要的相邻回归，预期退出 0。

每个 command 必须是可从仓库根复制执行的完整命令。Review 逐 task 解析 command 字段，不扫描说明文字，因此“禁止 uv sync”这类文档文字不会造成自我失败。

## 2. Python command 唯一模板

所有 Python pytest command 必须从 `octoagent` cwd 使用以下前缀；只允许替换末尾选择器：

```bash
cd octoagent && env PYTHONNOUSERSITE=1 PYTHONPATH="$(pwd)/packages/core/src:$(pwd)/packages/provider/src:$(pwd)/packages/protocol/src:$(pwd)/packages/tooling/src:$(pwd)/packages/skills/src:$(pwd)/packages/policy/src:$(pwd)/packages/memory/src:$(pwd)/packages/sdk/src:$(pwd)/apps/gateway/src" uv run --project . --no-sync python -m pytest
```

Review 对每个实际 command 做结构化检查：

- 必须同时包含 `PYTHONNOUSERSITE=1`、上述 8 个 package src 和 gateway src；
- 必须包含 `uv run --project . --no-sync python -m pytest`；
- command 不得包含 `uv sync`、裸 `pytest`、shell/Python 固定 sleep、`--reruns` 或宿主 `~/.octoagent`；
- 时序敏感测试必须在测试源码标 `xdist_group`，并用条件轮询/受控时钟提供 oracle。

不使用“对整份 tasks.md 做负向 rg”作为门禁，因为禁令说明本身会命中；Review 只解析 command 字段并逐条复制执行。

## 3. 实际 evidence 要求

Tasks Gate 只审任务设计，不等于获得 TDD 证据。Implement/Review 时，每个行为 task 必须在 `evidence/tdd/<task-id>/` 保存 RED、GREEN、REFACTOR 三份执行记录；每份至少包含：

```yaml
task_id: Txxx
phase: RED | GREEN | REFACTOR
behavior: 一句可观察行为
command: 完整原始命令
executed_at_utc: ISO-8601
head_sha: 40 位 Git SHA
worktree_status_before: git status --short 原文
exit_code: 整数
oracle_expected: 预期的可观察失败/成功
oracle_observed: 实际观察
output_file: 对应 stdout/stderr 原始文件
secret_scan_command: 对本 task evidence 运行的合成 sentinel 扫描命令
secret_scan_exit_code: 整数
```

- RED 必须是目标行为断言失败，不能是 test 文件不存在、import error、依赖缺失、selector 选中 0 tests 或基础设施错误。
- “稳定见红”要求相同输入下失败原因确定且 oracle 精确；禁止用 blanket rerun 掩盖不稳定。若必须证明时序稳定，使用受控时钟/条件同步，不用 sleep。
- GREEN/REFACTOR 必须对同一行为 oracle 退出 0；只运行另一个更宽松测试不算闭环。
- `head_sha` 与 `worktree_status_before` 共同记录未提交 TDD 工作树的准确基线；不得伪造 commit。
- secret 测试只使用合成 sentinel `F149_SECRET_SENTINEL_DO_NOT_STORE`；evidence 保存前必须扫描 YAML/stdout/stderr，sentinel 零命中。真实 secret 永不得作为测试输入或证据内容。

## 3.1 尚不存在的 Gate prerequisite

当前仓库没有 `openapi:check`、`test:coverage`、F149 boundary/style checker 或 frontend changed-lines checker。Design Spec 可以记录目标命令，但不得把它们当现成 Gate。未来 Tasks 必须为 checker 本身先创建输入 fixture 与稳定行为 RED，再创建 script/npm alias，随后才可把命令升级为 Gate；“script/package alias 不存在”不是合格 RED。

每个 shell command 必须独立从仓库根运行，不能依赖上一条命令留下的 cwd。尤其 coverage 两步必须分别为 `cd octoagent/frontend && ...` 与仓库根 `node repo-scripts/...`，不得用后续 `cd ../..`。

## 4. Atomic relocation

纯机械搬迁不写 RED/GREEN/REFACTOR。task 必须标 `atomic relocation`，并回存：

- 搬迁前后同一契约测试的真实输出与 HEAD SHA；
- 旧路径 absence command 与退出码；
- import-boundary gate 的真实输出；
- diff 证明只发生位置/import 变化，没有顺手行为修改。

若搬迁同时改变行为，必须拆出独立行为 task 并回到 TDD 流程。

## 5. Tasks Gate Review 问题

Reviewer 对每个 task 逐项回答：

- command 是否在声明 cwd 下机械可运行？
- RED 是否由目标行为触发，而非环境/导入错误？
- GREEN/REFACTOR 是否保持同一 oracle？
- 测试层级是否最低且正确？
- test double 是否只提供输入/边界，而非复制生产算法或验证 mock 自己？
- 若为 relocation，是否具备 before/after/absence/import 四项证据？

任一回答为否，Tasks Gate 或 Review Gate 不通过。
