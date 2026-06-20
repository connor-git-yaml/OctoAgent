# F126 Capability 效率改进 — 实现计划（plan.md）

- **Feature ID**: F126 / Slug: capability-efficiency
- **基线**: master cd9a56c3 / 分支 `feature/126-capability-efficiency` / worktree `F126-cap-eff`
- **性质**: 行为变更（非零变更重构）。复杂度 **HIGH**（spec §11）。最高风险 = 项 2 tail eviction prefix-cache 打断。
- **上游制品**: `spec.md`（17 FR / 14 AC / 4 SD / §7 C1–C5 决议）、`clarifications.md`（含 GATE_DESIGN 拍板：C5 store 层纳入、C2 独立工具）、`research/tech-research.md`（全部 file:line 锚点）、`checklist.md`（47 项）。
- **锚点约定**: 所有 `file:line` 相对仓库根，文件位于 `octoagent/` 子目录下。

> 散文中文 / 代码标识符英文 / 英文技术术语保原文。本 plan 不写 tasks（留 tasks 阶段）。

---

## 1. 架构方案（每项落点 + 改动方式，引调研锚点）

### 1.1 总体落点分层

```
执行前层（broker BeforeHook）         ← 项 1：schema 校验          packages/tooling
  └─ ToolBroker.execute 步骤 4（broker.py:396 既有拒绝路径）

执行后 / 历史层（model_client）        ← 项 2：tail eviction        packages/skills
  └─ _maybe_compact_history（provider_model_client.py:267 no-op 落地）

artifact 域 + 工具暴露层               ← 项 3：read-back + per-turn  apps/gateway + packages/core/store + packages/skills
  └─ 新 artifact.read_content 工具 / store Optional task 参数 / runner _call_hook 预算
```

边界纪律（spec §8 Out-of-scope，checklist §5）：
- **不碰** gateway `ContextCompactionService`（context_compaction.py:201）—— tool 结果不在其 turn 序列。
- **不重做** F108 W8 system 组装层 / AmbientRuntime Block 2（harness-and-context.md:172-178）。
- **不扩** `ConversationTurn.tool_call_id`（context_compaction.py:120）。
- **不纳入** `octoagent-sdk` 独立运行面。

### 1.2 项 1 — 执行前 schema 校验 + 结构化 retry feedback（批次 1）

**落点**：新 `SchemaValidationBeforeHook` 类，落 `packages/tooling/src/octoagent/tooling/`（建议新文件 `schema_validation_hook.py`，与 hooks_legacy.py 同层）。实现 BeforeHook 协议（protocols.py:80，`before_execute` protocols.py:101），注册到 broker `self._before_hooks`（broker.py:396）。

**改动方式**：
1. **BeforeHook 主体**：对照 `meta.parameters_json_schema`（models.py:144）对 `current_args` 做 **C1 宽松校验**——仅校验 ① `required` 字段齐全；② 顶层属性 `type`（含 enum 顶层）。**不做** `additionalProperties:false` / 深层 nested 递归 / format/pattern。校验失败返回 `BeforeHookResult(proceed=False, rejection_reason=<结构化 JSON>)`，**复用 broker.py:399-417 既有拒绝路径**（自动 emit `TOOL_CALL_FAILED` + 返回 `is_error` ToolResult），**broker 主干零改动**（FR-1.1/FR-1.4）。fail_mode 设 `CLOSED`（fail-closed）。
2. **结构化反馈承载**：扩 `ToolFeedbackMessage`（skills/models.py:347）新增字段承载字段级 validation errors。**字段命名建议**：`validation_errors: list[dict[str, Any]] = Field(default_factory=list)`（每条 `{"loc": [...], "msg": str, "type": str}`，对齐 pydantic `ValidationError.errors()` 结构），默认空保向后兼容。`_build_tool_feedback`（runner.py:672/695）把 broker 拒绝结果的结构化 errors 透传进该字段；`_append_feedback_to_history`（provider_model_client.py:165）渲染时把 validation_errors 拼进 LLM 可读文本（FR-1.2，回灌 runner.py:406 `feedback.extend`）。
   - **传递通道**：BeforeHook 把结构化 errors 编进 `rejection_reason`（JSON 字符串），broker 拒绝路径已把 `rejection_reason` 写进 `ToolResult.error`（broker.py:412）；runner `_build_tool_feedback` 解析该 JSON 还原到 `validation_errors` 字段。**不引入新 broker 出参**（最小触碰面）。
3. **opt-out 标记**：`ToolMeta` 加 `skip_arg_validation: bool = Field(default=False)`（models.py:144 附近可选字段区），注册期声明。BeforeHook 内 `if meta.skip_arg_validation: return BeforeHookResult(proceed=True)` 直接放行（FR-1.6 / C1，回退 broker except 兜底 broker.py:478-493）。
4. **schema 单一事实源**（FR-1.3 / #3）：校验直接读 `meta.parameters_json_schema`，与 LLM 收到的 schema（provider_model_client.py:254）同源，不另建事实源。

**风险（低）**：唯一风险是误拒——C1 宽松校验 + opt-out 双保险将其降至趋零（spec §9）。prefix-cache 无影响（不碰上下文组装）。

### 1.3 项 2 — tool_call_id 确定性 tail eviction（批次 2-step2）

**落点**：`_maybe_compact_history`（provider_model_client.py:267，当前 no-op provider_model_client.py:288-294）落地 tail eviction。**复用** `_append_feedback_to_history` 的 `tool_call_id` 去重 / 寻址逻辑（provider_model_client.py:170）。

**改动方式**：
1. **触发判定**：history token 估算接近 `compaction_threshold_ratio × max_context_tokens`（provider_model_client.py:275-282 已有阈值读取）时触发。**确定性选块**：从 history 前段（最旧）选已卸载为 artifact 的 `role:tool` 消息折叠。⚠️ 选块逻辑必须确定性（同一 history 状态选同一批），不得引入"每轮重算哪些该折叠"的可变行为（tech-research.md:99/173 警告）。
2. **占位改写（C4 / FR-2.2）**：把选中 tool 消息 `content` 改写为 **`[已折叠，见 artifact:<artifact_id>（工具 <tool_name>，原始 <N> 字节）]`**，三插值元素全部折叠时刻冻结：`artifact_id`=卸载 ULID（hooks_legacy.py:257）、`tool_name`=tool_call 固有属性、`<N>`=卸载时刻测定原始字节数。`tool_call_id` 不变、位置不动、配对 assistant tool_call 不动（FR-2.3）。
3. **首折叠构造一次（C4 关键纪律）**：占位首次折叠构造一次写入 history（成为该 tool 消息新 content），后续 compaction 检测 content 已是占位形态（正则/前缀匹配 `[已折叠，见 artifact:`）则跳过不重构。**禁止每轮重拼**（埋可变内容风险）。
4. **artifact 来源**：折叠的 tool 消息须已有对应 artifact。若该 tool 结果尚未卸载（小于 `LargeOutputHandler` 阈值未存 artifact），则 **不折叠**（无 artifact_ref 无法占位）——保守不折叠，避免信息丢失。占位 `artifact_ref` 格式与项 3 read-back 统一（FR-2.6 / SD-2）。
5. **resume 配对（FR-2.4 / AC-2.3）**：eviction 改写后 history 进 checkpoint，resume 重建已折叠版本。保证 tool_call/tool_result 配对不错位，无 `conversation_state_lost`（provider_model_client.py:378）。

**与 system 折叠层边界声明（FR-2.3）**：本项只动 `self._histories[key]` 的 `role:tool` 消息 content。**不触及** `_merge_system_messages_to_front`（provider_client.py:113-149）的 system 合并折叠，**不触及** F108 W8 AmbientRuntime（Block 2 尾，harness-and-context.md:173）。前缀 = [合并 system] + [history 前段]；本项保证 history 前段被折叠块**位置不动 + 占位永久冻结** → 前缀单调收敛（详见 §6）。

**风险（最高）**：见 §7。

### 1.4 项 3 — artifact read-back + per-turn 预算（批次 2-step1 + step3）

**(a) read-back 工具（C2，批次 2-step1，先于项 2）**：
- **落点**：新 builtin 工具 `artifact.read_content(artifact_ref, offset?, limit?)`，落 `apps/gateway/.../builtin_tools/`（新文件 `artifact_tools.py`），走 broker 注入的 `artifact_store`（broker.py:126/149）。**不扩展** filesystem 工具识别 `artifact:` 前缀（避免 #9 前缀嗅探分支）。
- **后端**：复用 `get_artifact_content`（store/protocols.py:99）。offset/limit 按**字节**语义与 `get_artifact_content -> bytes` 对齐（UTF-8 安全边界截断）。
- **task 隔离（C5 / FR-3.2，GATE_DESIGN 拍板纳入）**：三道防护——① 权限走中央 `check_permission`（broker.py:370，#10 单一入口）；② 工具层取回后比对 `artifact.task_id == 当前 task_id`，不匹配拒绝 + emit 失败事件；③ **store 层纵深防御**：`get_artifact`/`get_artifact_content`（store/protocols.py:91/99 + artifact_store.py:385/405）加 `task: str | None = None` 参数，`task=None`=内部信任跳过过滤（保 context_compaction.py:839 零变更）、传 task → SQL `WHERE task_id=?`。read-back 工具调用**必传 task**。
- **推翻旧约束（FR-3.3）**：改写 hooks_legacy.py:148 注释「ArtifactStore 仅审计、不作为 LLM 恢复途径」+ 同步 harness-and-context.md（§9 living-docs）。

**(b) per-turn 预算 hook（C3，批次 2-step3，SHOULD/P2）**：
- **落点**：runner `_call_hook`（runner.py:653），在该轮所有 tool call 执行完之后聚合（runner.py:642 `_execute_tool_calls` 之后），**非** AfterHook（per-tool 粒度做不到跨工具加总）。
- **机制**：单轮所有 tool 输出 token 加总 > `per_turn_tool_output_budget`（默认 ≈ 8000 token，env/config 可覆盖）时 → ① emit 告警事件；② 对超额部分复用 `LargeOutputHandler._store_as_artifact`（hooks_legacy.py:241）卸载为 artifact，占位格式与项 2 统一（SD-4/FR-3.5）。
- **降级兜底**：若复杂度超预期，GATE_DESIGN 可降为"仅 emit 告警不自动卸载"最小版（AC-3.4 相应调整）。

---

## 2. 批次 / 阶段拆分（依据含 SD 依赖序）

| 序 | 阶段 | 内容 | 依据 |
|----|------|------|------|
| **批次 1** | 项 1（独立先行） | schema 校验 BeforeHook + ToolFeedbackMessage.validation_errors + ToolMeta.skip_arg_validation | SD-3 弱耦合（broker BeforeHook 执行前 / packages/tooling，与项 2/3 触碰面几乎不重叠），低风险先建信心 |
| **批次 2-step0** | **KV-cache 实测硬门**（不可跳过） | 逐 transport（chat/responses/anthropic）实测占位改写是否触发 KV-cache 整段重算 → 产出 `kv-cache-probe.md` → **通过才允许写项 2 实现代码** | **决策 B / SD-1 / AC-GATE-1**。`_maybe_compact_history` no-op 起点零既有验证，实测前置门由此而来 |
| **批次 2-step1** | 项 3 read-back + store task 隔离（先于项 2） | `artifact.read_content` 工具 + store Optional task 参数 + 工具层/中央权限 task 隔离 | **关键排序**：read-back 先做，让项 2 占位"卸载-占位-读回"天然可验闭环（AC-LOOP-1）。占位指向的 artifact 须能被读回才不是信息单向丢失（SD-2） |
| **批次 2-step2** | 项 2 tail eviction | `_maybe_compact_history` 落地确定性 tail eviction + C4 占位冻结 + resume 配对 | SD-1 PASS 后开工；占位 read-back 已就位（step1）可端到端验证 |
| **批次 2-step3** | per-turn 预算 hook（SHOULD/P2） | runner `_call_hook` 聚合预算 + 告警 + 复用 `LargeOutputHandler` 卸载 | SD-4 统一治理同一压力源；P2 末位，复杂度超预期可降级 |

**排序核心论证**：
- 项 1（批次 1）与项 2/3 零重叠 → 独立先合建立信心（SD-3）。
- 批次 2 内 **step0 实测硬门 → step1 read-back → step2 eviction** 是强制序：实测不通过项 2 不开工（SD-1）；read-back 先于 eviction 让占位有可验闭环（SD-2，AC-LOOP-1 e2e 在 step2 完成后跑通）。
- step3 预算 P2 末位，与 step2 共享 artifact 占位语义（SD-4），可降级不阻闭环。

---

## 3. 每项的 emit 事件设计（Constitution #2）

| 路径 | 触发点 | 事件 | 复用 / 新增 |
|------|--------|------|-------------|
| 项 1 校验拒绝 | BeforeHook `proceed=False` | `TOOL_CALL_FAILED`（含 `error_type="rejection"` + 结构化 reason） | **复用**（broker.py:402 `_emit_failed_event` 既有路径，零新增） |
| 项 2 折叠 | `_maybe_compact_history` 折叠 tool 消息为占位 | 折叠审计事件，payload 含 `tool_call_id` / `artifact_id` / `tool_name` / `原始字节数` / `step` | **新增候选**：`TOOL_RESULT_EVICTED`（或 `HISTORY_TOOL_RESULT_FOLDED`）。需查 EventType 枚举有无可复用项，无则新增（每折叠一条 → 可审计前缀演进） |
| 项 3 read-back | `artifact.read_content` 执行成功 / 越权拒绝 | 成功走 broker `TOOL_CALL_COMPLETED`；越权走 `TOOL_CALL_FAILED`（中央权限 / 工具层 task 比对拒绝） | **复用**（工具走 broker.execute 标准事件路径，零新增） |
| 项 3 per-turn 预算 | 单轮加总超阈值 | 预算告警事件，payload 含 `turn_total_tokens` / `budget` / `offloaded_count` | **新增候选**：`PER_TURN_BUDGET_EXCEEDED`（或复用现有 context-budget 告警 EventType，需查 context_budget.py 是否已有）。先 emit 告警再卸载，两步均可追溯 |

> 实施时先 grep EventType 枚举确认可复用项，新增遵循现有命名 convention（如 `TOOL_*` / `*_COMPLETED`）。所有新增事件落 EventStore，payload 用 JSON-native 字段抗 replay。

---

## 4. 测试策略（对照 spec §5 AC↔test 绑定）

| test 文件（预期落点） | 关键用例 | 绑定 AC |
|----------------------|----------|---------|
| `packages/tooling/tests/test_schema_validation_hook.py` | `test_missing_required_rejected_before_handler`（缺 required → _invoke_handler 前拒、handler 零调用、emit TOOL_CALL_FAILED）；`test_lenient_valid_call_not_rejected`（体内 coerce 合法调用不误拒）；`test_validation_uses_same_schema_source`（schema 同源）；`test_optout_tool_skips_validation`（skip_arg_validation 跳过） | AC-1.1/1.3/1.4/1.5 |
| `packages/skills/tests/test_structured_validation_feedback.py` | `test_field_level_errors_in_feedback`（validation_errors 字段级 loc/msg/type 回灌 retry loop） | AC-1.2 |
| `packages/skills/tests/test_provider_model_client_tail_eviction.py` | `test_placeholder_does_not_break_prefix`（**KV-cache 实测结论转化为确定性回归断言**）；`test_deterministic_frozen_placeholder`（同 tool_call_id 多轮 compaction 占位**字节级一致** `==`、位置不动、无可变内容）；`test_no_mid_history_rewrite`（只折旧块、中段字节不变、配对 assistant 不动）；`test_resume_pairing_intact`（折叠后 checkpoint+resume 配对完整、无 conversation_state_lost） | AC-GATE-1 / AC-2.1/2.2/2.3 |
| `apps/gateway/tests/test_artifact_read_back_tool.py` | `test_read_back_returns_content`（含 offset/limit 分页）；`test_cross_task_read_denied`（跨 task 读被中央权限/工具层/store WHERE 拒绝） | AC-3.1/3.2 |
| `packages/skills/tests/test_per_turn_budget_hook.py` | `test_aggregate_overflow_offloaded`（单轮加总超阈值 → 告警 + 聚合卸载、下轮不携超额原文） | AC-3.4 |
| `apps/gateway/tests/e2e/test_offload_placeholder_readback_loop.py` | `test_evicted_placeholder_readable`（**e2e 闭环**：项 2 折叠占位 artifact_ref → 项 3 read-back 成功读回） | AC-LOOP-1 |
| （store 单测，C5 纵深） | store 层 `get_artifact(task=...)` SQL WHERE 隔离 + `task=None` 内部 caller 零变更 | FR-3.2 store 纵深 |

**KV-cache 实测 → 回归断言转化**：probe.md 三 transport 结论（"确定性占位改写不触发整段重算"）固化为 `test_placeholder_does_not_break_prefix`——断言占位改写后送 provider 的前缀消息序列（[合并 system] + [history 前段] 直到首个被折叠块前）**字节级不变**，被折叠块占位对同一 tool_call_id 多轮 `==`。非一次性 probe，CI 可重跑（checklist §2 CRIT）。

---

## 5. 0 regression 验证方式（防假 0）

- **基线**: cd9a56c3。先抓 baseline 全量 passed 数（master 干净 checkout 或主仓直接跑），记录 `N passed / 0 failed / 0 error`。
- **PYTHONPATH 锁定 worktree**（禁 `uv sync`，引 project memory `project_worktree_venv_symlink`）——worktree `.venv` 是主仓 symlink，裸 pytest 跑 master src 会假 0：
  ```bash
  WT=/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F126-cap-eff/octoagent
  export PYTHONPATH="$WT/packages/core/src:$WT/packages/tooling/src:$WT/packages/memory/src:$WT/packages/provider/src:$WT/packages/protocol/src:$WT/packages/sdk/src:$WT/packages/skills/src:$WT/packages/policy/src:$WT/apps/gateway/src"
  uv run --no-sync python -m pytest <targets>
  ```
- **判别跑的是 worktree**：`uv run python -c "import octoagent.core.store as s; print(s.__file__)"` 须含 `.claude/worktrees/F126-cap-eff`（不含则跑 master）。
- **baseline 抓取命令思路**：在主仓 cd9a56c3 跑全量 `python -m pytest -q`（同 PYTHONPATH 锁主仓）取 passed 数；worktree 改后须 ≥ baseline + 新增测试数、0 failed/0 error，deselect 情况说明。
- **e2e_smoke**：pre-commit hook 跑 master src（引 `project_precommit_hook_execution_model`：hook 跑的是 master 版本非 worktree 编辑，裸 uv run pytest 逃逸 venv 须改 `python -m pytest`）。worktree 新代码须手动 PYTHONPATH 跑 e2e_smoke 验证；commit 时 `SKIP_E2E=1` bypass 并记录理由。

---

## 6. prefix-cache 不变量协同（供双评审）

**前缀构成**：[合并后单条 system]（provider_client.py:113-149 `_merge_system_messages_to_front`）+ [history 前段稳定消息]。

**项 2 改写与 F108 W8 边界声明（FR-2.3）**：
- F108 W8 AmbientRuntime 在 **system 组装层 Block 2 尾**（harness-and-context.md:173），与本项 **history 层** `role:tool` 消息改写**不同层、不直接冲突**。本项**零触碰** system 组装层。
- 二者同属 prefix-cache 治理 → living-docs 须把本项 history 层不变量与 harness-and-context.md:172-178 的 context-assembly 侧不变量**合并表述**（§9）。

**占位单调收敛论证要点（评审重点）**：
1. **位置不动**：折叠只改写 `role:tool` 消息的 `content`，不删消息、不移位、不动配对 assistant tool_call → history 长度与结构不变。
2. **永久冻结（C4）**：占位三插值元素（artifact_id/tool_name/字节数）全为折叠时刻冻结的稳定值；首折叠构造一次写入、后续检测占位形态跳过不重拼 → 同一 tool_call_id 占位**字节级永久一致**。
3. **单向折叠**：折叠后不再变回原文、不再二次改写 → 前缀只可能"从原文变占位"一次，之后稳定 → **前缀单调收敛**。
4. **反例护栏**：任何"每轮重算哪些该折叠 / 占位含可变计数/时间戳/剩余预算"都会反复打断前缀，**比不做更糟**（tech-research.md:99/173）→ 测试 `test_deterministic_frozen_placeholder` 字节级断言钉死。
5. **实测前置门**：占位改写是否触发 transport 侧 KV-cache 整段重算无既有验证 → AC-GATE-1 逐 transport 实测 + probe.md 证据（决策 B）。

---

## 7. 风险与回滚

| 风险 | 等级 | 缓解 / 回滚 |
|------|------|-------------|
| **项 2 prefix-cache 打断**（中段改写 / 占位含可变内容 / 每轮重算选块） | **最高** | C4 占位冻结 + 首折叠构造一次 + 位置不动 + 确定性选块；字节级测试 + KV-cache 实测硬门双护栏 |
| **KV-cache 实测不通过**（SD-1 fail） | 高 | **fallback 决策树**：① 优先降级——项 2 改为"仅 emit 告警事件 + 不实际折叠"（history 不改写、保留溢出兜底现状 runner.py:210，但提前告警可观测）；② 若降级仍不满足闭环价值，**推迟项 2**（项 1 + 项 3 read-back 可独立交付，AC-LOOP-1 标记 deferred）。任一路径须在 completion-report 显式归档"项 2 跳过/降级，理由 X，影响 Y" |
| resume 配对错位 | 中 | FR-2.4 折叠后 checkpoint 重建已折叠版本；`test_resume_pairing_intact` 覆盖 |
| read-back 越权读 | 中 | C5 三道防护（中央权限 + 工具层 task 比对 + store WHERE）；`test_cross_task_read_denied` 强测试 |
| store 接口变更影响内部 caller | 中 | task 参数 `Optional` 默认 None=内部信任跳过过滤，保 context_compaction.py:839 零变更；store 单测覆盖两路径 |
| per-turn 预算双重截断 | 低 | SD-4 与项 2 共用 artifact 占位语义；作用窗口不重叠（项 2 折旧 / 项 3 拦新）。复杂度超预期降级仅告警 |
| 项 1 误拒合法调用 | 低（趋零） | C1 宽松校验 + skip_arg_validation opt-out；`test_lenient_valid_call_not_rejected` 覆盖 |

---

## 8. 双评审 panel 触发点

- **批次 1（项 1）**：动 tooling 层 BeforeHook + ToolFeedbackMessage schema。命中"重大架构变更"（capability/tooling 层）→ 走 Codex review（可 foreground）。
- **批次 2（项 2+3）**：命中 **prefix-cache 不变量节点 + capability/tooling/context 层 + 行为变更** → **Codex + Opus 双评审 panel**（spec §10，checklist §7）。
  - **0 HIGH 门**：含 re-review（大改 commit 后再 review，参照 F099 三轮收敛先例，至少 2 轮收敛到 0 HIGH）。
  - **评审重点（三确定性论证）**：① 确定性占位生成规则（C4）；② 折叠单调收敛论证（§6）；③ 与 F108 W8 system 折叠边界（FR-2.3）。
  - **LLM judge 配确定性打底**：双评审分歧项配测试 / 类型 / probe 实测证据，非纯主观；分歧显式列"必须人裁"清单。
  - **KV-cache 实测（AC-GATE-1）** 作为评审前置证据输入。

---

## 9. living-docs 同步清单（漂移闸）

| 文档 / 注释 | 同步内容 | 依据 |
|------------|----------|------|
| `docs/codebase-architecture/harness-and-context.md` | ① **推翻** artifact read-back 旧约束（artifact 现可作 LLM 恢复途径）；② 新增 `artifact.read_content` 工具 + per-turn 预算 hook + store `Optional` task 参数描述；③ tail eviction history 层不变量与 :172-178 context-assembly 侧 prefix-cache 不变量**合并表述** | FR-3.3 / AC-3.3 / checklist §6 |
| `octoagent/.../hooks_legacy.py:148` 注释 | 改写「ArtifactStore 仅审计、不作为 LLM 恢复完整内容途径」→ 反映 read-back 已暴露；`grep` 旧约束文案须消失 | FR-3.3 / AC-3.3 |
| `completion-report.md` | living-docs 比对表（本 Feature 触碰模块 code↔doc drift 入"已知 limitations"）+ Phase 列表"实际做 vs 计划"+ Phase 跳过/降级显式归档 + opt-out 工具清单 | checklist §6/§7 |

---

## 10. 交付物

- `completion-report.md` + `handoff.md` + living-docs 同步（§9）+ `kv-cache-probe.md`（批次 2-step0 产出）。
- **不主动 push，等用户拍板**（CLAUDE.local.md spawn task 流程）。
- Constitution 遵守：#2（校验拒绝/折叠/read-back/预算触发均 emit）、#3（校验源同 schema）、#4（read-back 只读、eviction 可由 artifact 恢复，无新不可逆副作用）、#9（read-back/预算/豁免为机制非硬编码规则）、#10（权限统一走中央 check_permission，C5 隔离不下沉 store 层为权限事实源）。
