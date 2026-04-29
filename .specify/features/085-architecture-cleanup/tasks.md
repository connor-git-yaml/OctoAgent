# Feature 085 — 任务执行计划

> Baseline：commit `cda8e00`（F084 末），全量 2034 passed
> E2E baseline：`/tmp/f084_e2e_verify.py` 25/25 passed
> 模式：每步独立 commit，每步含验证 + 防跑偏机制

## 通用执行模板（每步必走）

```
1. [改前 baseline]
   - grep 确认改动范围（必须 ≥ 期望值的 80%；过低说明范围理解偏）
   - 跑专项测试记录基线（应该是 100% passed）

2. [实施]
   - 按 spec 改动
   - 改完检查 git diff stat（行数应在预算 ±50% 内）

3. [改后专项验证]
   - 跑改动模块的单元/集成测试
   - 跑 /tmp/f084_e2e_verify.py 25 项必须 ≥ 22 passed（核心黄金路径不变）

4. [跑全量 + 单步 commit]
   - 跑 apps/gateway/tests + packages/* + tests/integration 全量
   - 必须 ≥ 2034 passed（允许 +新增 但不允许 -减少 除 sc3 flaky）
   - git add 仅修改文件 + 单步 commit（防 revert 时连带其他步骤）
```

---

## T1：删除 GatewayToolBroker dead code + ApprovalGate 集成迁移（~2h）

### 改前快照
- `grep -rn "GatewayToolBroker\|services.tool_broker" --include="*.py" octoagent` 应 0 业务调用方（仅文件自身）
- 跑 `apps/gateway/tests/services/test_tool_broker.py`（如有）记录 baseline

### 实施
1. 删除 `apps/gateway/src/octoagent/gateway/services/tool_broker.py`（~190 行）
2. 删除 `apps/gateway/tests/services/test_tool_broker*.py`（如有）
3. **F25 ApprovalGate 集成迁移决策**：
   - 选项 A：迁到 `packages/tooling/broker.py` 真路径（影响 1855 测试主路径，风险高）
   - 选项 B：标记为"后续接入点"——在主 broker 加注释说明 ApprovalGate 集成是 F086 / Phase 6 工作，但**不在 F085 实施**（spec FR-4.1 WARN→ApprovalGate 是 SHOULD 不是 MUST，且 ThreatScanner 当前 17 pattern 全部 BLOCK 级，WARN 触发实际不发生）
   - **采用 B**：F085 是清理 feature 不引入新功能；WARN→ApprovalGate 集成是新功能（即使 spec 提过）
4. 在 `packages/tooling/broker.py` 加注释说明：未来 WARN→ApprovalGate 接入点（不实施代码）

### 改后专项验证
- `grep -rn "GatewayToolBroker"` 仅 commit message / spec 文档命中（生产代码 0）
- 跑 `pytest apps/gateway/tests/services/ -q` 确认无 broker 相关测试失败
- 跑 E2E：`uv run python /tmp/f084_e2e_verify.py` 必须 ≥ 22/25 passed（broker 改动不应影响 J1/J2 路径）

### 防跑偏检查
- ⚠️ 不能跨任务做：仅删除 + 注释，不重构其他 broker 代码
- ⚠️ 不能动 `packages/tooling/broker.py` 的 execute() 主逻辑（除加注释）

### 单步 commit
```
refactor(harness): F085 T1 删除 GatewayToolBroker dead code

services/tool_broker.py (~190 行) 0 调用方，F25 修复（WARN→ApprovalGate）
实际未生效；spec FR-4.1 WARN→ApprovalGate 是 SHOULD 且当前 17 pattern
全 BLOCK 级，WARN 触发实际不发生 → 标记为后续接入点不在 F085 范围。
```

---

## T2：subagents.spawn 接入 DelegationManager（~1.5h）

### 改前快照
- `grep -B5 'name="subagents.spawn"' delegation_tools.py | grep DelegationManager` 应 0 命中（确认未集成）
- 跑现有 delegation_tools 相关测试记录 baseline

### 实施
1. 在 `delegation_tools.py` 顶部 import `DelegationManager` + `DelegationContext` + `DelegateTaskInput`（与 delegate_task_tool 同模式）
2. `subagents_spawn` handler 内：
   - 从 execution_context 推断 current_depth + active_children（参照 delegate_task_tool.py:109-139 模式）
   - 创建 DelegationManager + 调 `delegate(ctx, input)` 做约束检查
   - 失败 → return `SubagentsSpawnResult(status="rejected", reason=result.reason, requested=N, created=0, children=[])`
   - 通过 → 继续现有 launch_child 路径
3. 注意：subagents.spawn 支持批量 objectives，每个 objective 独立做 DelegationManager check（数组，第 N 个失败前面已派发的不撤销，但失败的不派发）

### 改后专项验证
- 新增测试 `apps/gateway/tests/builtin_tools/test_subagents_spawn_delegation.py`：
  - `test_spawn_blocks_when_depth_exceeded`：current_depth=2 → spawn 应 reject
  - `test_spawn_blocks_when_concurrent_exceeded`：active_children=3 → spawn 应 reject
  - `test_spawn_blocks_when_target_blacklisted`：blacklisted worker → spawn 应 reject
  - `test_spawn_passes_when_within_limits`：depth=0 + active=0 → 正常派发
  - 4 个新测试必须全过
- E2E：观察 spawn 多次连续调用是否会触发约束（验证 LLM 不能绕过）

### 防跑偏检查
- ⚠️ 必须保持 subagents.spawn 现有签名（target_kind / worker_type / objectives）—— 不破坏现有 LLM prompt 描述
- ⚠️ 现有 delegation_tools 测试如果不依赖"无约束"行为，应继续通过；如果依赖（如 fixture 不带约束 ctx），需要更新 fixture 但不能放松约束

### 单步 commit
```
fix(harness): F085 T2 subagents.spawn 接入 DelegationManager 约束检查

修复 spec FR-5 安全 gap：subagents.spawn 之前直接 launch_child 绕过
DelegationManager (max_depth=2 / max_concurrent=3 / blacklist)，LLM 可
创建无限递归 sub-agent。现在两条路径 (subagents.spawn + delegate_task)
都走 DelegationManager。

新增 4 个约束测试覆盖 depth / concurrent / blacklist / 正常路径。
```

---

## T3：抽 system audit task helper（~1.5h）

### 改前快照
- 3 处生产代码：
  - `apps/gateway/.../services/policy.py:_ensure_audit_task`
  - `apps/gateway/.../harness/approval_gate.py:_ensure_audit_task`
  - `apps/gateway/.../services/operator_actions.py:_ensure_operational_task`
- 8 处测试 fixture：`grep -rn 'RequesterInfo(channel="system", sender_id="system")' apps/gateway/tests`

### 实施
1. 新建 `packages/core/src/octoagent/core/store/audit_task.py`：
   ```python
   async def ensure_system_audit_task(
       task_store, task_id: str, *, title: str = "system audit task"
   ) -> bool:
       """统一 system audit task 创建（防 F41 schema 必填遗漏）"""
       # 防 F41：必须传 requester + pointers 必填字段
       # 进程内幂等缓存可选（PolicyGate/ApprovalGate 已在自己内部缓存）
   ```
2. 3 处生产调用方迁移：
   - PolicyGate.\_ensure_audit_task → 内部委托 `ensure_system_audit_task(self._task_store, task_id, title=...)`
   - ApprovalGate.\_ensure_audit_task → 同上
   - operator_actions.\_ensure_operational_task → 同上（保留 operator-specific 字段如 thread_id）
3. 8 处测试 fixture：直接保留（fixture 是测试自己 setUp 用的不归 helper 管），但抽一个 conftest fixture `system_audit_task` 可选简化

### 改后专项验证
- 跑 `apps/gateway/tests/harness/test_approval_gate.py + apps/gateway/tests/services/test_policy.py`（如有）
- 跑 E2E：22+ 项必须不变（F41 修复行为保持）
- 跑 `apps/gateway/tests/integration/test_threat_approval_integration.py`（验证 F41+F42 行为持续）

### 防跑偏检查
- ⚠️ helper 必须接收 task_store 不能内部 import（避免 packages/core 反向依赖 apps/gateway）
- ⚠️ helper 不动 PolicyGate / ApprovalGate 的 `_audit_task_ensured` 进程缓存（保留各自的）
- ⚠️ operator_actions 保留 `thread_id="operator-inbox"` 等 specific 字段（helper 不能 over-generalize）

### 单步 commit
```
refactor(core): F085 T3 抽 ensure_system_audit_task helper

3 处重复实现 (PolicyGate / ApprovalGate / operator_actions) 统一到
packages/core/.../store/audit_task.py。F41 schema 必填错误的根因是
"模板未抽象 → 每处自己实现 → 字段遗漏"，helper 锁定正确字段防回归。
```

---

## T4：删除 OwnerProfile dead 字段（~0.5h）

### 改前快照
- 字段位置：`packages/core/.../models/agent_context.py:200-208`
- 验证 dead：
  - DDL `grep "bootstrap_completed\|last_synced_from_user_md" sqlite_init.py` = 0 列
  - 所有真消费方应是 sync hook 内部 + agent_context.py 旧路径（F35 已绕过）

### 实施
1. 删除 OwnerProfile.bootstrap_completed + last_synced_from_user_md 两个 Field
2. 删除 sync_owner_profile_from_user_md 中对应 dict key（`bootstrap_completed` / `last_synced_from_user_md`）
3. apply_user_md_sync_to_owner_profile 已经在 PERSISTED_FIELDS 元组里没列这两个，无需改

### 改后专项验证
- `grep "bootstrap_completed" packages/core/.../models/agent_context.py` 仅 helper / sync 函数注释命中（如有）；OwnerProfile class 字段定义 0 命中
- 跑 `apps/gateway/tests/integration/test_reinstall_path.py + tests/models/test_owner_profile_sync.py`：可能有 fixture 引用这两字段，需要更新

### 防跑偏检查
- ⚠️ agent_context.py:1105 `bootstrap_completed = _user_md_substantively_filled(...)` 不依赖 OwnerProfile 字段（F35 已修），删字段不影响
- ⚠️ 如果有测试 assert 这两字段 → 删除该 assertion（字段死了相关 assertion 也死）

### 单步 commit
```
refactor(core): F085 T4 删除 OwnerProfile dead 字段 (bootstrap_completed + last_synced_from_user_md)

DDL 没列，sync hook 不写库 (F42 修复 PERSISTED_FIELDS 已不含这两字段)，
F35 修复后 bootstrap_completed 直接读 USER.md 不依赖字段。
保留是 model 层 dead 噪音。
```

---

## T5：删除 user_profile_tools.py dead try-except（~0.1h）

### 改前快照
- 位置：`apps/gateway/.../tools/user_profile_tools.py:262-268`
- 实际：F42 修复后该位置已经被改成 `_sync_and_apply` async helper 包装，不再有原 try-except

### 实施
- 检查 F42 修复后还残留的 dead code：
  - 如果 try-except 还在 → 删除
  - 如果已被 F42 重写 → 跳过 T5（标 [x] 已完成）

### 改后专项验证
- 跑 user_profile_tools 相关测试

### 单步 commit
（如果跳过则不 commit）

---

## T6：gateway/tools/ → builtin_tools/ 迁移（~1h）

### 改前快照
- `gateway/tools/__init__.py + user_profile_tools.py + delegate_task_tool.py`
- register_all 显式 imports（"防 F20 critical"注释）：第 49-55 行

### 实施
1. 移动文件：
   - `apps/gateway/src/octoagent/gateway/tools/user_profile_tools.py` → `services/builtin_tools/user_profile_tools.py`
   - `apps/gateway/src/octoagent/gateway/tools/delegate_task_tool.py` → `services/builtin_tools/delegate_task_tool.py`
2. 更新内部 import：从 `..services.builtin_tools._deps` → `._deps`（同级 import）
3. `services/builtin_tools/__init__.py`：
   - 删除 explicit imports（"防 F20" 注释段）
   - 在顶部 `from . import (...)` 加 `user_profile_tools, delegate_task_tool`
   - register_all 加 `await user_profile_tools.register(broker, deps)` + `await delegate_task_tool.register(broker, deps)` 到内置工具注册序列
4. 删除空目录 `gateway/tools/`（若 `__init__.py` 也空则一起删）
5. 更新 main.py / orchestrator 等所有 import 路径
6. 测试文件 import 路径更新（约 5+ 个测试文件）

### 改后专项验证
- 跑 `apps/gateway/tests/integration/test_user_profile_write_path.py + test_observation_promote.py + test_threat_approval_integration.py`：必须全过（这些重度依赖 user_profile.update）
- 跑 E2E：22+ 必须保持（注册路径变了但工具行为不变）
- 跑 `apps/gateway/tests/test_capability_pack_tools.py`：验证 ToolRegistry 仍能装载所有工具

### 防跑偏检查
- ⚠️ 移文件时不能改文件内部逻辑（仅修 import 路径）
- ⚠️ register_all 顺序保持（user_profile_tools / delegate_task_tool 仍在最后）
- ⚠️ 如果 `gateway/tools/__init__.py` 还有内容（如 docstring）合并到注释；不能丢任何说明

### 单步 commit
```
refactor: F085 T6 gateway/tools/ 合并到 services/builtin_tools/

消除目录混乱（仅 2 文件 vs 12 文件）+ 解除 register_all "防 F20 critical"
显式 import workaround。AST scan 自动发现机制重新生效。
```

---

## T7：Codex 独立 review + 全量回归 + 最终 commit（~1h）

### 实施
1. 跑 `node "/Users/connorlu/.claude/plugins/cache/openai-codex/codex/1.0.3/scripts/codex-companion.mjs" adversarial-review "--background"`
2. 处理任何 high/critical finding（按 F084 模式：spot-check + 修复 + 再跑全量）
3. 全量回归：`uv run pytest apps/gateway/tests/ packages/skills/tests/ packages/core/tests/ packages/tooling/tests/ tests/integration/ -q`
4. E2E：`uv run python /tmp/f084_e2e_verify.py`
5. 最终 grep 验证 SC：
   ```bash
   grep -F "GatewayToolBroker" --include="*.py"  # SC-085-1
   grep -F "bootstrap_completed" packages/core/.../agent_context.py | grep "Field"  # SC-085-4
   ls apps/gateway/src/octoagent/gateway/tools/ 2>/dev/null  # SC-085-6
   ```
6. 如有 finding 修复 → 单独 commit `fix(F085): Codex review fixes`

### 防跑偏检查
- ⚠️ Codex 抓的 finding 不能"接受现状"——按 F084 经验 Codex 找的 high 90% 是真问题
- ⚠️ 全量必须 ≥ 2034 passed（允许 +新增不允许 -减少；sc3 flaky 例外）

---

## 任务依赖图

```
T1 (删 GatewayToolBroker)  ─┐
T2 (subagents.spawn 接约束) ─┤
T3 (audit task helper)      ─┼─→ T7 (Codex review + 全量回归)
T4 (删 OwnerProfile 字段)   ─┤
T5 (删 dead try-except)     ─┤
T6 (目录迁移)               ─┘
```

T1-T6 可独立并行（互不依赖），但实施时按顺序做（每步独立 commit + 验证），避免一次改太多导致 regression 难定位。

## 防跑偏总策略

| 风险 | 防御 |
|------|------|
| 单步 regression | 每步独立 commit + 跑专项 + 全量；fail 立即 revert 单步 |
| 范围蔓延 | 改前 grep 确认范围；改后 git diff stat 必须在预算 ±50% 内（T1 ~190 行；T2 ~100 行；T3 ~150 行；T4 ~30 行；T5 ~10 行；T6 ~50 行净） |
| E2E 黄金路径破坏 | 每步必跑 `/tmp/f084_e2e_verify.py`，22+ 项必须保持 |
| Codex 后期 finding | T7 是必走步骤，不能跳过 |
| 改完合并多步导致 commit 混乱 | T1-T6 各自一个 commit message，T7 另一个；总共 ≤ 7 commits |

## 完成标准

- ✅ T1-T7 全部完成
- ✅ 全量 ≥ 2034 passed
- ✅ E2E ≥ 22/25 passed
- ✅ SC-085-1 到 SC-085-7 全部通过
- ✅ 净删 dead code ≥ 200 行
- ✅ Codex review 0 high finding
- ✅ 7 个 commits 全部 push
