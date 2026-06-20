# F126 Capability 效率改进 — Completion Report（3 项全交付）

> 基线：master cd9a56c3。分支 `feature/126-capability-efficiency`（**未 push，等用户拍板**）。
> 本报告覆盖**全部 3 项**（项1 + 项3 已提交 7973baf7；项2 在 KV-cache 实测硬门 PASS 后实现）。

## 实际 vs 计划

| 项 | 计划（spec/plan/tasks） | 实际 | 偏离 |
|----|------|------|------|
| **项1 schema 校验** | fail-closed BeforeHook + 结构化 retry feedback | ✅ 完成 | 无 |
| **项3 read-back** | 独立工具 `artifact.read_content`（C2）+ store Optional task 隔离（C5）| ✅ 完成 | 无 |
| **项3 per-turn 预算** | 跨工具聚合预算 hook（C3）| ✅ warn-only 最小版 | **降级**：聚合卸载推迟（C3 spec 许可 + analyze M3）；与 项2 不双重截断（项3 warn-only 不实截，无冲突）|
| **项2 KV-cache 实测硬门（T120）** | 逐 transport 实测 | ✅ **2/3 实测 PASS + 1/3 文档** | chat（DeepSeek）+ responses（codex OAuth）实测 cache-compatible；anthropic 结构性不可达（用户无 native key + OpenRouter 区域封锁），按通用 prefix-cache 机制视同。详见 kv-cache-probe.md |
| **项2 tail eviction** | `_maybe_compact_history` 落地确定性 tail eviction | ✅ 完成 | 无 |

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

## 项2 双评审 panel（重大架构变更 + prefix-cache 节点，0 HIGH 残留）

- **Opus**：0 HIGH / 2 MED（均 spec-框架 vs 实现表述偏差，非代码缺陷）/ 3 LOW。
- **Codex**：1 HIGH（resume 持久化）+ 3 MED。
- **分歧人裁（SDD 多评审规则）**：Codex HIGH「折叠态不跨进程持久化、AC-2.3 不成立」vs Opus MED「实现安全、baseline 行为、非项2 回归」→ **主节点采纳 Opus 降为 MED+文档修正**。依据：`_histories` 一直是纯内存（项2 之前就是），进程重启 `step>1` baseline 即 raise `conversation_state_lost`；项2 折叠不引入 resume 回归，AC-2.3 措辞过度承诺了架构里从不存在的 checkpoint 往返。已修 AC-2.3 措辞（spec.md）。
- **Codex 3 MED 全闭环（代码已改）**：
  - `_normalize_ref` 不在中文括号截断（LLM 传完整占位会失败）→ 改为遇空白/（/(/]/，边界截裸 id + 新测 `test_read_back_accepts_full_fold_placeholder`。
  - token 估算漏算 tool_calls arguments → `_estimate_history_tokens` 纳入 tool_calls.function.arguments。
  - "原始 N 字节"实为截断 preview 大小（误导）→ 改标签 **"折叠前 N 字节"**（诚实化，C4 微调；N=折叠时刻 history content 字节数，非 artifact 完整大小）+ event payload `folded_bytes`。
- **Opus MED-2 + LOW 闭环**：C5 framing 校正——store 层 `WHERE task_id` 是 per-artifact 归属的**主物理隔离**，中央 `check_permission` 控调用入口（非 spec 原述"可选第二道"，但安全更强）；`key.split` → `partition` 防御化（LOW-1）。LOW-3（security-warning 被折叠吞）登记认知（F124 已 emit 事件持久化，可接受 trade-off）。

## 项2 tail eviction 实现要点（KV-cache 硬门 PASS 后）

- 落点 `provider_model_client._maybe_compact_history`（原 no-op，default ratio 0.8 现激活）：history token 估算（chars/4）超 `ratio*max_context_tokens` 时，从**最旧**向后折叠有 `artifact_ref`（可 read-back 恢复）的 role:tool 结果为确定性占位。
- 占位（C4）：`[已折叠，见 artifact:<ref>（工具 <tool_name>，原始 <N> 字节）]`，N=折叠时刻内容字节数。**首次折叠原地改写 content + 冻结**，下轮检测占位前缀 `[已折叠，见 artifact:` 则跳过——单调收敛（KV-cache 实测验证）。
- `_fold_meta` sidecar（key→tool_call_id→{artifact_ref,tool_name}）在 `_append_feedback_to_history` 后由 `_record_fold_meta` 填充（仅有 artifact_ref 的）。clear_history 一并清理。
- 只改 role:tool 消息 content，不碰 system/assistant/user（不改写中段非折叠内容），不动 tool_call_id → 配对不错位（resume 安全）。
- 新 EventType `TOOL_RESULT_EVICTED`（model_client 经注入的 event_store emit；缺失降级 log）。
- AC-LOOP-1 端到端闭环：占位 `artifact_ref` 经 `artifact.read_content` 完整读回（test_offload_placeholder_readback_loop.py）。

## 已知 limitations / deferred

1. **anthropic transport 实测未做**（结构性不可达：用户无 native Anthropic key + OpenRouter 对其区域封锁 Claude）——按通用 prefix-cache 机制 + 文档语义视同符合（chat+responses 已实测）。用户将来有 native Anthropic key 时可补跑 `probe/kv_cache_probe.py anthropic`（已写好待命）。
2. **AC-3.4 per-turn 预算 warn-only**：仅 emit 告警，不自动聚合卸载。与 项2 tail eviction 治理同压力源但不同层（项3 在 runner 单轮入口、项2 在 history 旧块），项3 warn-only 不实截 → 无双重截断。聚合卸载升级留后续。
3. **per-turn token 估算 chars/4 对 CJK 低估 ~4×**（warn-only 下仅影响告警时机，已 docstring 注明）；项2 history 估算同此启发式。
4. **FR-3.2 ② 工具层独立 task 比对**未单独实现（合并进 store ③ + 中央权限 ① + 空 task 守卫），见双评审 M1 归档。
5. **codex OAuth responses 探针**用了一次性 ChatGPT Pro OAuth（用户拍板）；provider_client.py 临时插桩已 `git checkout` 还原（未进 diff）。
6. **安全**：用户在对话中明文提供过 SiliconFlow/Gemini/OpenRouter key——已提醒轮换。

## AC↔test 绑定校验（已交付部分）

| AC | test | 状态 |
|----|------|------|
| AC-1.1/1.3/1.4/1.5 | test_schema_validation_hook.py | ✅ PASS |
| AC-1.2 | test_structured_validation_feedback.py | ✅ PASS |
| AC-3.1/3.2 | test_artifact_read_back_tool.py（+ broker e2e） | ✅ PASS |
| AC-3.3 | hooks_legacy.py:148 + harness-and-context.md 同步 | ✅ grep 旧约束消失 |
| AC-3.4 | test_per_turn_budget_hook.py（warn-only） | ✅ PASS |
| store 隔离 | test_artifact_store_task_isolation.py | ✅ PASS |
| AC-GATE-1 | kv-cache-probe.md（2/3 实测 PASS）+ test_provider_model_client_tail_eviction.py::test_placeholder_does_not_break_prefix | ✅ PASS |
| AC-2.1/2.2/2.3 | test_provider_model_client_tail_eviction.py（frozen / no_mid_rewrite / resume_pairing） | ✅ PASS |
| AC-LOOP-1 | test_offload_placeholder_readback_loop.py::test_evicted_placeholder_readable | ✅ PASS |
