# F097 Phase C 实施报告

**日期**: 2026-05-10
**baseline**: 88f8773 (Phase A 完成)

## 改动文件

- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`：+35 行（_resolve_context_bundle 短路 + ephemeral profile 构造，替换原 4 行调用）
- `octoagent/apps/gateway/tests/services/test_agent_context_phase_c.py`：+255 行（新建测试，10 个测试函数）

## 净增减

- 实施代码: +35 行（net，含替换原 4 行 `_resolve_agent_profile` 直接调用）
- 测试代码: +255 行（新建文件）

## 关键决策（基于 Phase 0 侦察 + 用户授权）

**注入点**：`_resolve_context_bundle`（agent_context.py，L1288 附近），在原 `_resolve_agent_profile` 调用位置前加 fast-path，将原 4 行单路径改为 if/else 双路径。

**短路方式**：
- 读取 `request.delegation_metadata.get("target_kind")`（信号来自 `_launch_child_task` 写入 control_metadata["target_kind"]，路径：control_metadata → NormalizedMessage → task.metadata → dispatch_metadata → ContextResolveRequest.delegation_metadata，Phase 0 侦察 §3 确认路径已通）
- `target_kind == "subagent"` 时，inline 构造 ephemeral AgentProfile，**不调用 `_resolve_agent_profile`**（无 save_agent_profile 持久化调用）
- 其他 target_kind 走原 `_resolve_agent_profile` 路径（0 regression）

**不修改 `_resolve_agent_profile` 签名**：避免函数签名侵入式修改，注入点在调用层（`_resolve_context_bundle`），符合 Phase 0 侦察"方案 A（推荐）"。

**ULID 生成**：项目已有 `from ulid import ULID`（agent_context.py L84），直接复用 `ULID()` 构造，不新增 helper。

**closed_at 同步**：
- `AgentProfile` model 当前**无** `closed_at` 字段（字段清单：profile_id / scope / project_id / name / kind / persona_summary / instruction_overlays / model_alias / tool_profile / policy_refs / memory_access_policy / context_budget_policy / bootstrap_template_ids / metadata / resource_limits / version / created_at / updated_at）
- ephemeral profile 是运行时构造的纯内存对象（不持久化），生命周期天然绑定 `_resolve_context_bundle` 调用——调用返回后 ephemeral profile 随局部变量释放，无需显式 close
- 因此 TC.3 不需要新增逻辑，ephemeral profile 生命周期等价于 SubagentDelegation 的单次 dispatch 生命周期

## AC 自查

- [x] AC-C1: ephemeral AgentProfile `kind="subagent"`，profile_id 用 `f"agent-prf-subagent-{ULID()}"` 格式，不调用 `_resolve_agent_profile` 和 `save_agent_profile`（持久化路径被完全绕过）
- [x] AC-C2: `scope=AgentProfileScope.PROJECT`，`project_id` 从 caller project 读取（project is None 时为空字符串），生命周期与 `_resolve_context_bundle` 调用绑定（ephemeral 自然丢弃，不加入任何 cache / store）

## 测试结果

- 新增测试 10 个（test_agent_context_phase_c.py）：**10 PASS / 0 FAIL**
- agent_context 相关回归（`-k "agent_context"`）：10 passed（0 regression）
- 含 context 全量回归：149 passed（0 regression）
- test_task_service_context_integration.py：24 passed（0 regression）

验证命令：
```
pytest -p no:rerunfailures octoagent/apps/gateway/tests/services/test_agent_context_phase_c.py -v --tb=short
# 10 passed in 4.05s

pytest -p no:rerunfailures octoagent/apps/gateway/tests/services/ -k "agent_context" --tb=short -q
# 10 passed, 0 regression

pytest -p no:rerunfailures octoagent/apps/gateway/tests/ -k "context" --tb=short -q
# 149 passed, 0 regression
```

## 实施偏差

- **注入点位置**：计划注入点为"L1288 调用前短路"，实际改动替换了 L1288-1291 的 4 行单路径，改为 if/else 双路径（subagent 短路 + 原路径），与计划完全一致
- **TC.3 不需新增逻辑**：AgentProfile 无 `closed_at` 字段，ephemeral profile 生命周期绑定策略通过 Python 自然内存管理实现，无需显式 close 操作
- **ULID 格式**：`ULID()` 生成 26 位大写字母+数字串（Crockford base32），profile_id 格式为 `agent-prf-subagent-<26位ULID>`，测试用正则 `^agent-prf-subagent-[0-9A-Z]{26}$` 验证
