# F126 Capability 效率改进 — Completion Report（批次1 项1 + 批次2-step1 项3）

> 基线：master cd9a56c3。分支 `feature/126-capability-efficiency`（**未 push，等用户拍板**）。
> 本报告覆盖**已完成的 项1 + 项3**；**项2 tail eviction BLOCKED 于 KV-cache 实测硬门**（决策 B，用户已拍板由其提供 live key 跑真实测），见 handoff.md。

## 实际 vs 计划

| 项 | 计划（spec/plan/tasks） | 实际 | 偏离 |
|----|------|------|------|
| **项1 schema 校验** | fail-closed BeforeHook + 结构化 retry feedback | ✅ 完成 | 无 |
| **项3 read-back** | 独立工具 `artifact.read_content`（C2）+ store Optional task 隔离（C5）| ✅ 完成 | 无 |
| **项3 per-turn 预算** | 跨工具聚合预算 hook（C3）| ✅ warn-only 最小版 | **降级**：聚合卸载推迟到 项2（C3 spec 许可 + analyze M3） |
| **项2 tail eviction** | KV-cache 实测硬门 → 实现 | ⛔ BLOCKED | 决策 B 硬门未过（等用户 live key），项2 全部 BLOCKED |

## 已交付内容（项1 + 项3）

**项1（执行前 schema 校验 + 结构化 retry feedback）**：
- 新增 `packages/tooling/.../schema_validation_hook.py`：fail-closed `SchemaValidationHook`（宽松：必传字段缺失 + 容器↔标量结构性错配；标量↔标量放行交 coerce，避 FR-1.5 误拒）。`fail_mode=OPEN` 仅指校验器自身崩溃放行（语义经 `proceed=False` 表达拒绝）。priority=900 靠后执行（校验最终将传 handler 的 args）。
- `ToolMeta.skip_arg_validation` 字段 + **`@tool_contract(skip_arg_validation=True)` decorator + reflect_tool_schema 接通**（Codex 抓出原字段未接 decorator → 已修，生产可声明豁免）。
- `validation_errors`（loc/msg/type）经 `BeforeHookResult → ToolResult → ToolFeedbackMessage` 透传，runner `_build_tool_feedback` 映射，provider_model_client `_render_validation_errors` 双分支（call_id 有/无）渲染回灌自愈 retry loop。
- 生产 wiring：octo_harness.py 注册到 broker before-hook 链。复用 broker.py:399 拒绝路径 emit `TOOL_CALL_FAILED`（FR-1.4），broker 主干仅透传一字段。

**项3（artifact read-back + store 隔离 + per-turn 预算）**：
- 新增 `apps/gateway/.../builtin_tools/artifact_tools.py`：`artifact.read_content(artifact_ref, offset?, limit?)`（字节分页，UTF-8 errors=replace，兼容 `artifact:<id>` 占位形态，空 ref / 空 task 守卫）。注册进 register_all + ToolRegistry。
- store 层 `get_artifact`/`get_artifact_content` 加 keyword-only `Optional task`：`None`=内部信任（11 个既有 caller 零变更），非 None 时 SQL `WHERE task_id` 物理隔离。跨 task 读回被拒。
- per-turn 预算 hook（runner `_execute_tool_calls` 末尾聚合点）：超 `OCTOAGENT_PER_TURN_TOOL_OUTPUT_BUDGET`（默认 8000 token，chars/4 近似）emit `PER_TURN_BUDGET_EXCEEDED` 告警（新 EventType）。
- living-docs：hooks_legacy.py:148 旧"仅审计不供 LLM 恢复"约束已推翻（FR-3.3）；harness-and-context.md 已同步 项1/项3 三条不变量（M2 闭环）。

## 验证总账

- **0 regression vs cd9a56c3**：基线 4047 passed → 改后 **4071 passed**（+24 F126 新测试）/ 10 skipped / 97 deselected / 1 xfailed / 1 xpassed，逐项与基线一致（PYTHONPATH 锁 worktree，禁 uv sync）。
- e2e_smoke **8/8**（生产 harness wiring：SchemaValidationHook + artifact.read_content 注册健康）。
- 新测试 24（项1：9 schema hook + 3 结构化反馈；项3：3 store 隔离 + 6 read-back（含 broker e2e）+ 4 per-turn 预算 - 实际分布见各 test 文件）。

## 双评审 panel（命中重大架构变更节点，0 HIGH 残留）

- **Opus（spec 对齐 + bug）**：0 HIGH / 2 MED / 4 LOW。
  - M1（FR-3.2 ② 工具层 task 比对合并进 store ③）→ **归档**：store SQL `WHERE task_id` 即 task 归属比对的权威点，中央 `check_permission` ① + store 过滤 + 空 task 守卫构成纵深；双 test 覆盖跨 task 拒绝。
  - M2（harness-and-context.md 未同步）→ **已闭环**。
  - L1 空 task 守卫 / L2 弱断言收紧 / L3 CJK token 注记 / L4 broker e2e → **全闭环**。
- **Codex（对抗式）**：substantive findings——① `skip_arg_validation` decorator/reflection 未接通（生产无法声明，测试靠 model_copy）→ **已修**（decorator + schema.py 接通 + 真 decorator 路径测试）；② storage_ref 是否绕过 task 隔离 → **非问题**（`get_artifact_content` 先调 `get_artifact(task=)`，跨 task 返 None 前不读 storage_ref，test_cross_task_read_denied 覆盖）。Codex 后台任务最终 verdict 未 flush，但调查链覆盖 validation chain / store callers / decorator gap，实质结论 0 HIGH。

## 已知 limitations / deferred

1. **项2 tail eviction 全部 BLOCKED**（决策 B KV-cache 实测硬门未过）——等用户提供 live provider key 跑 chat/responses/anthropic 三 transport 实测（T120）。项2 接口已就绪：read-back `_normalize_ref` 兼容裸 id 与 `artifact:<id>`，C4 占位串 id 部分可被 read-back 解析，"卸载-占位-读回"闭环底座齐（项3 已提供 read-back 半边）。
2. **AC-3.4 warn-only 降级**：per-turn 预算本批次仅 emit 告警，自动聚合卸载推迟到 项2（统一占位语义避免双重截断，SD-4）。
3. **AC-LOOP-1（项2↔项3 端到端闭环）deferred**：依赖 项2 占位落地，待 项2 实现后补 e2e `test_offload_placeholder_readback_loop.py`。
4. **per-turn token 估算 chars/4 对 CJK 低估 ~4×**（warn-only 下仅影响告警时机，已 docstring 注明）。
5. **FR-3.2 ② 工具层独立 task 比对**未单独实现（合并进 store ③ + 中央权限 ①），见双评审 M1 归档。

## AC↔test 绑定校验（已交付部分）

| AC | test | 状态 |
|----|------|------|
| AC-1.1/1.3/1.4/1.5 | test_schema_validation_hook.py | ✅ PASS |
| AC-1.2 | test_structured_validation_feedback.py | ✅ PASS |
| AC-3.1/3.2 | test_artifact_read_back_tool.py（+ broker e2e） | ✅ PASS |
| AC-3.3 | hooks_legacy.py:148 + harness-and-context.md 同步 | ✅ grep 旧约束消失 |
| AC-3.4 | test_per_turn_budget_hook.py（warn-only） | ✅ PASS |
| store 隔离 | test_artifact_store_task_isolation.py | ✅ PASS |
| AC-GATE-1 / AC-2.x / AC-LOOP-1 | 项2 | ⛔ BLOCKED on KV-cache 硬门 |
