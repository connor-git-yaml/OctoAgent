# F126 Capability 效率改进 — 任务清单（tasks.md）

- **Feature ID**: F126 / Slug: capability-efficiency
- **基线**: master cd9a56c3 / 分支 `feature/126-capability-efficiency` / worktree `F126-cap-eff`
- **性质**: 行为变更（非零变更重构）。复杂度 **HIGH**。最高风险 = 项 2 tail eviction prefix-cache 打断。
- **上游**: `spec.md`（17 FR / 14 AC / 4 SD）、`plan.md`（架构落点+批次序+emit 事件+测试策略+回滚）、`clarifications.md`（C1–C5 + GATE_DESIGN 拍板）。
- **锚点约定**: 所有 `file:line` 相对仓库根，文件位于 `octoagent/` 子目录下。

> 散文中文 / 代码标识符英文 / 英文技术术语保原文。
>
> **不可跳过的批次序**：批次1（项1，独立先行）→ 批次2-step0【KV-cache 实测硬门 AC-GATE-1】→ 批次2-step1（项3 read-back + store task 隔离）→ 批次2-step2（项2 tail eviction，BLOCKED 直到 step0 PASS）→ 批次2-step3（项3 per-turn 预算 P2）→ 收尾（双评审 + living-docs + 交付）。
>
> **标记说明**：`[P]` 可并行（不同文件、无依赖）；`[硬前置门]` 阻塞下游任务；每任务标 SD 依赖（SD-1 硬门 / SD-2 占位↔readback / SD-3 项1独立 / SD-4 预算统一）。

---

## 任务 0 — baseline 抓取（开工前置，所有任务的隐式依赖）

- [ ] **T000 [SD-0]** — 抓取 cd9a56c3 全量回归 baseline passed 数（防假 0）
  - **落点**: 无代码改动。在主仓 cd9a56c3 干净 checkout 跑 `python -m pytest -q`，记录 `N passed / 0 failed / 0 error`，作为后续 0 regression 判别基线。
  - **PYTHONPATH 锁定 worktree（禁 `uv sync`）**：引 project memory `project_worktree_venv_symlink` —— worktree `.venv` 是主仓 symlink，裸 pytest 跑 master src 会假 0。判别命令：
    ```bash
    WT=/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F126-cap-eff/octoagent
    export PYTHONPATH="$WT/packages/core/src:$WT/packages/tooling/src:$WT/packages/memory/src:$WT/packages/provider/src:$WT/packages/protocol/src:$WT/packages/sdk/src:$WT/packages/skills/src:$WT/packages/policy/src:$WT/apps/gateway/src"
    uv run --no-sync python -c "import octoagent.core.store as s; print(s.__file__)"  # 须含 .claude/worktrees/F126-cap-eff
    ```
  - **依赖**: 无（任务 0）
  - **FR-AC**: spec §10 / plan §5
  - **验证**: 记录 baseline passed 数到 completion-report；后续每批次结束 `passed ≥ baseline + 新增测试数`、0 failed/0 error

---

## 批次 1 — 项 1 schema 校验 BeforeHook（独立先行，SD-3 弱耦合）

> SD-3：项 1 与项 2/3 触碰面几乎不重叠（broker BeforeHook 执行前 / `packages/tooling`），独立先合建立信心。低风险。

- [ ] **T101 [SD-3]** — 新建 `SchemaValidationBeforeHook` 类（C1 宽松校验）
  - **落点**: 新文件 `octoagent/packages/tooling/src/octoagent/tooling/schema_validation_hook.py`（与 `hooks_legacy.py` 同层）。实现 BeforeHook 协议（`protocols.py:80`，`before_execute` `protocols.py:101`）。
  - **改动**: 对照 `meta.parameters_json_schema`（`models.py:144`）做 C1 宽松校验——仅校验 ① `required` 字段齐全；② 顶层属性 `type`（含 enum 顶层）。**不做** `additionalProperties:false` / 深层 nested 递归 / format/pattern。校验失败返回 `BeforeHookResult(proceed=False, rejection_reason=<结构化 JSON>)`，复用 `broker.py:399-417` 既有拒绝路径，**broker 主干零改动**。fail_mode = `CLOSED`。schema 直接读 `meta.parameters_json_schema`（与 `provider_model_client.py:254` LLM 收到的同源，FR-1.3/#3）。
  - **依赖**: T000
  - **FR-AC**: FR-1.1 / FR-1.3 / FR-1.4 / FR-1.5；AC-1.1 / AC-1.4
  - **验证**: T106（`test_schema_validation_hook.py::test_missing_required_rejected_before_handler` + `::test_validation_uses_same_schema_source`）

- [ ] **T102 [SD-3]** — `ToolMeta` 加 `skip_arg_validation` opt-out 标记（C1 衍生 / FR-1.6）
  - **落点**: `octoagent/packages/tooling/src/octoagent/tooling/models.py:144` 附近可选字段区，加 `skip_arg_validation: bool = Field(default=False)`。
  - **改动**: T101 BeforeHook 内 `if meta.skip_arg_validation: return BeforeHookResult(proceed=True)` 直接放行（回退到 broker except 兜底 `broker.py:478-493`）。注册期声明。
  - **依赖**: T101
  - **FR-AC**: FR-1.6（MAY）；AC-1.5
  - **验证**: T106（`::test_optout_tool_skips_validation`）

- [ ] **T103 [SD-3]** — 扩 `ToolFeedbackMessage.validation_errors` 字段（结构化反馈承载，FR-1.2）
  - **落点**: `octoagent/packages/skills/src/octoagent/skills/models.py:347`，加 `validation_errors: list[dict[str, Any]] = Field(default_factory=list)`（每条 `{"loc": [...], "msg": str, "type": str}`，对齐 pydantic `ValidationError.errors()` 结构，默认空保向后兼容）。
  - **依赖**: T101
  - **FR-AC**: FR-1.2；AC-1.2
  - **验证**: T107（`test_structured_validation_feedback.py`）

- [ ] **T104 [SD-3]** — runner `_build_tool_feedback` 透传结构化 errors + 渲染回灌
  - **落点**: ① `runner.py:672/695` `_build_tool_feedback` 解析 broker 拒绝结果 `ToolResult.error`（`broker.py:412` 已写入 `rejection_reason` JSON 字符串）还原到 `validation_errors` 字段（**不引入新 broker 出参**，最小触碰面）；② `provider_model_client.py:165` `_append_feedback_to_history` 渲染时把 `validation_errors` 拼进 LLM 可读文本，回灌 `runner.py:406` `feedback.extend`。
  - **依赖**: T103
  - **FR-AC**: FR-1.2；AC-1.2
  - **验证**: T107（`::test_field_level_errors_in_feedback`）

- [ ] **T105 [SD-3]** — 注册 `SchemaValidationBeforeHook` 到 broker before-hook 链
  - **落点**: broker 构造期把 T101 hook 注册进 `self._before_hooks`（`broker.py:396`），确认执行顺序在 `check_permission`（`broker.py:370`）之后、`_invoke_handler`（`broker.py:567`）之前。
  - **改动**: emit 路径复用既有 `_emit_failed_event`（`broker.py:402`）发 `TOOL_CALL_FAILED`，**零新增 EventType**（见 emit 任务 T140）。
  - **依赖**: T101, T102
  - **FR-AC**: FR-1.1 / FR-1.4
  - **验证**: T106（handler 零调用断言）+ 批次 1 回归

- [ ] **T106 [P] [SD-3]** — 测试 `test_schema_validation_hook.py`（项 1 校验主体）
  - **落点**: 新文件 `octoagent/packages/tooling/tests/test_schema_validation_hook.py`。
  - **用例**: `test_missing_required_rejected_before_handler`（缺 required → `_invoke_handler` 前拒、handler 零调用、emit `TOOL_CALL_FAILED`，AC-1.1）；`test_lenient_valid_call_not_rejected`（工具体内 coerce 合法调用不误拒，AC-1.3）；`test_validation_uses_same_schema_source`（schema 同源，AC-1.4）；`test_optout_tool_skips_validation`（`skip_arg_validation=True` 跳过，AC-1.5）。
  - **依赖**: T101, T102, T105
  - **FR-AC**: AC-1.1 / AC-1.3 / AC-1.4 / AC-1.5
  - **验证**: `pytest -k` 全 PASS

- [ ] **T107 [P] [SD-3]** — 测试 `test_structured_validation_feedback.py`（项 1 反馈回灌）
  - **落点**: 新文件 `octoagent/packages/skills/tests/test_structured_validation_feedback.py`。
  - **用例**: `test_field_level_errors_in_feedback`（`validation_errors` 字段级 `loc`/`msg`/`type` 回灌 retry loop，下一轮 generate 能消费）。
  - **依赖**: T103, T104
  - **FR-AC**: AC-1.2
  - **验证**: `pytest -k test_field_level_errors_in_feedback` PASS

- [ ] **T108 [SD-3]** — 批次 1 回归 + 双评审（项 1 节点）
  - **落点**: 无代码改动。PYTHONPATH 锁 worktree 跑 `packages/tooling` + `packages/skills` focused 回归 + 全量回归（≥ baseline + 新增）。
  - **评审**: 动 tooling 层 BeforeHook + `ToolFeedbackMessage` schema → 命中"重大架构变更"，走 Codex review（可 foreground）。处理到 0 HIGH。
  - **依赖**: T106, T107
  - **FR-AC**: spec §10
  - **验证**: 0 regression + e2e_smoke 必过 + Codex 0 HIGH

---

## 批次 2-step0 — KV-cache 实测【硬前置门 AC-GATE-1 / SD-1】

> **决策 B / SD-1 / AC-GATE-1**：`_maybe_compact_history` no-op 起点零既有验证。**本任务标 [硬前置门]：实测通过前，批次 2-step2 项 2 实现任务（T130–T135）一律 BLOCKED。**

- [ ] **T120 [硬前置门 AC-GATE-1] [SD-1]** — 逐 transport KV-cache 实测 → 产 `kv-cache-probe.md`
  - **落点产物**: `.specify/features/126-capability-efficiency/kv-cache-probe.md`（含 chat / responses / anthropic 三 transport 各自结论 + 复现方法）。
  - **实测内容**: 逐 transport（chat/responses/anthropic）实测确定性占位改写（把 history 前段 `role:tool` 消息 content 改写为 C4 占位串）**是否触发 KV-cache 整段重算**。证明前缀 = [合并 system]（`provider_client.py:113-149`）+ [history 前段直到首个被折叠块前] 字节级不变时，transport 侧不触发整段重算。
  - **门控语义**: **实测未通过 → 批次 2-step2（T130–T135）BLOCKED**。若实测 fail，进入 plan §7 fallback 决策树（① 项 2 降级为"仅 emit 告警不实际折叠"；② 推迟项 2，AC-LOOP-1 标记 deferred），并在 completion-report 显式归档"项 2 跳过/降级，理由 X，影响 Y"。
  - **依赖**: T000（与批次 1 可并行，不依赖 T101–T108）
  - **FR-AC**: AC-GATE-1（P0 前置门）/ FR-2.5 / SD-1
  - **验证**: probe.md 三 transport 结论齐全；结论转化为确定性回归断言 → T136 `test_placeholder_does_not_break_prefix`（CI 可重跑，非一次性 probe）

---

## 批次 2-step1 — 项 3 read-back + store task 隔离（先于项 2，SD-2）

> **关键排序**：read-back 先做，让项 2 占位"卸载-占位-读回"天然可验闭环（AC-LOOP-1）。占位指向的 artifact 须能被读回才不是信息单向丢失（SD-2）。本 step 不依赖 T120 硬门（store/工具层与 history 层不同层），可与 step0 并行推进。

- [ ] **T130 [SD-2]** — store 层 `get_artifact`/`get_artifact_content` 加 `Optional` task 参数（C5 纵深防御，GATE_DESIGN 拍板纳入）
  - **落点**: ① Protocol 签名 `octoagent/packages/core/src/octoagent/core/store/protocols.py:91`（`get_artifact`）+ `:99`（`get_artifact_content`）加 `task: str | None = None`；② 实现 `artifact_store.py:385`（`get_artifact`）+ `:405`（`get_artifact_content`）：`task=None` = 内部信任跳过过滤（保 `context_compaction.py:839` 零变更）、传 task → SQL `WHERE task_id=?` 物理隔离。
  - **依赖**: T000
  - **FR-AC**: FR-3.2（store 纵深，C5）
  - **验证**: T133（store 层 task 隔离单测）

- [ ] **T131 [SD-2]** — 新建 builtin 工具 `artifact.read_content`（C2 独立工具）
  - **落点**: 新文件 `octoagent/apps/gateway/.../builtin_tools/artifact_tools.py`。工具 `artifact.read_content(artifact_ref, offset?, limit?)`，走 broker 注入的 `artifact_store`（`broker.py:126/149`），后端复用 `get_artifact_content`（`store/protocols.py:99`）。**不扩展** filesystem 工具识别 `artifact:` 前缀（避免 #9 前缀嗅探分支）。offset/limit 按**字节**语义与 `get_artifact_content -> bytes` 对齐（UTF-8 安全边界截断）。read-back 调用**必传 task**（T130 SQL WHERE 隔离）。
  - **依赖**: T130
  - **FR-AC**: FR-3.1；AC-3.1
  - **验证**: T134（`test_artifact_read_back_tool.py::test_read_back_returns_content`）

- [ ] **T132 [SD-2]** — read-back task 隔离三道防护（C5）
  - **落点**: ① 权限走中央 `check_permission`（`broker.py:370`，#10 单一入口，工具走 broker.execute 标准路径自动覆盖）；② T131 工具层取回后比对 `artifact.task_id == 当前 task_id`，不匹配拒绝 + emit 失败事件（走 broker `TOOL_CALL_FAILED` 标准路径）；③ store 层 SQL `WHERE task_id`（T130）作纵深第二道。
  - **依赖**: T130, T131
  - **FR-AC**: FR-3.2；AC-3.2
  - **验证**: T134（`::test_cross_task_read_denied`）

- [ ] **T133 [P] [SD-2]** — store 层 task 隔离单测（新增，C5 纵深）
  - **落点**: 建议 `octoagent/packages/core/tests/`（store 测试既有目录）新增用例。
  - **用例**: `get_artifact(task=...)` SQL `WHERE` 隔离（传 task 只查到本 task artifact）+ `task=None` 内部 caller 零变更（跨 task 也能查，保 `context_compaction.py:839` 行为）。
  - **依赖**: T130
  - **FR-AC**: FR-3.2 store 纵深
  - **验证**: `pytest -k` 两路径均 PASS

- [ ] **T134 [P] [SD-2]** — 测试 `test_artifact_read_back_tool.py`（read-back 工具主体）
  - **落点**: 新文件 `octoagent/apps/gateway/tests/test_artifact_read_back_tool.py`。
  - **用例**: `test_read_back_returns_content`（含 offset/limit 分页，AC-3.1）；`test_cross_task_read_denied`（跨 task 读被中央权限/工具层 task 比对/store WHERE 拒绝，AC-3.2）。
  - **依赖**: T131, T132
  - **FR-AC**: AC-3.1 / AC-3.2
  - **验证**: `pytest -k` 全 PASS

---

## 批次 2-step2 — 项 2 tail eviction【依赖 T120 PASS + step1，SD-1/SD-2】

> **BLOCKED 直到 T120（AC-GATE-1）PASS**。占位 read-back 已就位（step1）→ 端到端可验证（AC-LOOP-1）。最高风险项。

- [ ] **T130-GATE — 前置确认**：开工前确认 T120 probe.md 三 transport 实测 PASS。若 fail，本 step 全部任务（T135 起）转 plan §7 fallback（降级/推迟），不进入实现。

- [ ] **T135 [SD-1] [SD-2]** — `_maybe_compact_history` 落地确定性 tail eviction
  - **落点**: `octoagent/packages/skills/src/octoagent/skills/provider_model_client.py:267`（当前 no-op `:288-294`）。复用 `_append_feedback_to_history` 的 `tool_call_id` 去重/寻址逻辑（`provider_model_client.py:170`）。
  - **改动**: ① **触发判定**：history token 估算接近 `compaction_threshold_ratio × max_context_tokens`（`:275-282` 已有阈值读取）时触发；② **确定性选块**：从 history 前段（最旧）选**已卸载为 artifact** 的 `role:tool` 消息折叠（同一 history 状态选同一批，禁"每轮重算哪些该折叠"）；③ 该 tool 结果尚未卸载（无 artifact_ref）则**不折叠**（保守，避免信息丢失）；④ `tool_call_id` 不变、位置不动、配对 assistant tool_call 不动（FR-2.3）。
  - **边界声明**: 只动 `self._histories[key]` 的 `role:tool` 消息 content；**不触及** `_merge_system_messages_to_front`（`provider_client.py:113-149`）system 合并折叠；**不触及** F108 W8 AmbientRuntime（Block 2 尾，`harness-and-context.md:173`）。
  - **依赖**: T120（PASS），T131（占位 artifact_ref 须可被 read-back）
  - **FR-AC**: FR-2.1 / FR-2.3；AC-2.2
  - **验证**: T136（`::test_no_mid_history_rewrite` + `::test_placeholder_does_not_break_prefix`）

- [ ] **T135b [SD-2]** — C4 占位串构造一次 + 永久冻结
  - **落点**: 同 `provider_model_client.py` `_maybe_compact_history` 折叠路径内。
  - **改动**: ① 占位串 = `[已折叠，见 artifact:<artifact_id>（工具 <tool_name>，原始 <N> 字节）]`，三插值元素折叠时刻冻结（`artifact_id`=卸载 ULID `hooks_legacy.py:257`、`tool_name`=tool_call 固有属性、`<N>`=卸载时刻测定原始字节数）；② **首折叠构造一次写入 history**（成为该 tool 消息新 content），后续 compaction 检测 content 已是占位形态（正则/前缀匹配 `[已折叠，见 artifact:`）则跳过不重构（**禁止每轮重拼**）；③ 占位 `artifact_ref` 格式与项 3 read-back 统一（FR-2.6/SD-2）；④ 禁含任何轮次/时间戳/计数/剩余预算可变内容。
  - **依赖**: T135
  - **FR-AC**: FR-2.2 / FR-2.6（C4）；AC-2.1 / AC-LOOP-1
  - **验证**: T136（`::test_deterministic_frozen_placeholder` 字节级 `==`）

- [ ] **T135c [SD-1]** — resume 配对完整性（折叠后 checkpoint 重建）
  - **落点**: `provider_model_client.py` eviction 改写后 history 进 checkpoint 路径；resume 重建已折叠版本，保证 tool_call/tool_result 配对不错位，无 `conversation_state_lost`（`provider_model_client.py:378`）。
  - **依赖**: T135, T135b
  - **FR-AC**: FR-2.4；AC-2.3
  - **验证**: T136（`::test_resume_pairing_intact`）

- [ ] **T136 [P] [SD-1] [SD-2]** — 测试 `test_provider_model_client_tail_eviction.py`（项 2 主体）
  - **落点**: 新文件 `octoagent/packages/skills/tests/test_provider_model_client_tail_eviction.py`。
  - **用例**: `test_placeholder_does_not_break_prefix`（**KV-cache 实测结论转化为确定性回归断言**：占位改写后前缀 [合并 system]+[history 前段直到首折叠块前] 字节级不变，AC-GATE-1）；`test_deterministic_frozen_placeholder`（同 `tool_call_id` 多轮 compaction 占位字节级一致 `==`、位置不动、无可变内容，AC-2.1）；`test_no_mid_history_rewrite`（只折旧块、中段字节不变、配对 assistant 不动，AC-2.2）；`test_resume_pairing_intact`（折叠后 checkpoint+resume 配对完整、无 `conversation_state_lost`，AC-2.3）。
  - **依赖**: T135, T135b, T135c
  - **FR-AC**: AC-GATE-1 / AC-2.1 / AC-2.2 / AC-2.3
  - **验证**: `pytest -k` 全 PASS

- [ ] **T137 [SD-2]** — e2e 闭环测试 `test_offload_placeholder_readback_loop.py`（AC-LOOP-1）
  - **落点**: 新文件 `octoagent/apps/gateway/tests/e2e/test_offload_placeholder_readback_loop.py`。
  - **用例**: `test_evicted_placeholder_readable`（项 2 折叠占位 `artifact_ref` → 项 3 `artifact.read_content` read-back 成功读回，"卸载-占位-读回"端到端闭环）。
  - **依赖**: T135b（占位），T131/T132（read-back）
  - **FR-AC**: AC-LOOP-1（P1，FR-2.6/SD-2）
  - **验证**: `pytest -k test_evicted_placeholder_readable` PASS

---

## 批次 2-step3 — 项 3 per-turn 预算 hook（SHOULD/P2，SD-4）

> P2 末位，与 step2 共享 artifact 占位语义（SD-4），复杂度超预期可降级为"仅 emit 告警不自动卸载"最小版（AC-3.4 相应调整）。

- [ ] **T138 [SD-4]** — runner `_call_hook` per-turn 预算聚合 + 卸载
  - **落点**: `octoagent/packages/skills/.../runner.py:653` `_call_hook`，在该轮所有 tool call 执行完之后聚合（`runner.py:642` `_execute_tool_calls` 之后），**非** AfterHook（per-tool 粒度做不到跨工具加总）。
  - **改动**: 单轮所有 tool 输出 token 加总 > `per_turn_tool_output_budget`（默认 ≈ 8000 token，env/config 可覆盖）时 → ① emit 告警事件（T141）；② 对超额部分复用 `LargeOutputHandler._store_as_artifact`（`hooks_legacy.py:241`）卸载为 artifact，占位格式与项 2 统一（SD-4/FR-3.5，项 2 折旧块 / 项 3 拦新输出，作用窗口不重叠）。
  - **降级兜底**: 复杂度超预期 → 降为"仅 emit 告警不自动卸载"，AC-3.4 相应调整，completion-report 归档降级理由。
  - **依赖**: T135b（占位语义统一）
  - **FR-AC**: FR-3.4 / FR-3.5（SHOULD）；AC-3.4
  - **验证**: T139（`test_per_turn_budget_hook.py`）

- [ ] **T139 [P] [SD-4]** — 测试 `test_per_turn_budget_hook.py`（P2）
  - **落点**: 新文件 `octoagent/packages/skills/tests/test_per_turn_budget_hook.py`。
  - **用例**: `test_aggregate_overflow_offloaded`（单轮加总超阈值 → 告警 + 聚合卸载、下轮不携超额原文）。
  - **依赖**: T138
  - **FR-AC**: AC-3.4
  - **验证**: `pytest -k test_aggregate_overflow_offloaded` PASS

---

## emit 事件任务（Constitution #2，穿插实现）

> 实施前先 grep EventType 枚举确认可复用项（已确认：`TOOL_RESULT_THREAT_FLAGGED` 为 F124 既有，两个 F126 候选均需新增）。新增遵循 `TOOL_*` / `*_EXCEEDED` 命名 convention，落 EventStore，payload JSON-native 抗 replay。

- [ ] **T140 [SD-3]** — 项 1 校验拒绝 emit（复用，零新增 EventType）
  - **落点**: BeforeHook `proceed=False` 走 broker `_emit_failed_event`（`broker.py:402`）发 `TOOL_CALL_FAILED`（含 `error_type="rejection"` + 结构化 reason）。
  - **依赖**: T105
  - **FR-AC**: FR-1.4（#2）
  - **验证**: T106（emit `TOOL_CALL_FAILED` 断言）

- [ ] **T141 [SD-1] [SD-4]** — 新增 EventType `TOOL_RESULT_EVICTED` + `PER_TURN_BUDGET_EXCEEDED`
  - **落点**: `octoagent/packages/core/src/octoagent/core/models/enums.py`（EventType 枚举）。
  - **改动**: ① **新增** `TOOL_RESULT_EVICTED`（项 2 每折叠一条，payload 含 `tool_call_id` / `artifact_id` / `tool_name` / `原始字节数` / `step` → 可审计前缀演进），emit 落点 T135b 折叠路径；② **新增** `PER_TURN_BUDGET_EXCEEDED`（项 3 per-turn 超阈值，payload 含 `turn_total_tokens` / `budget` / `offloaded_count`），emit 落点 T138 预算 hook；③ 项 3 read-back 成功/越权走 broker 标准 `TOOL_CALL_COMPLETED` / `TOOL_CALL_FAILED`（复用，零新增）。
  - **依赖**: T135b（EVICTED emit 点），T138（BUDGET emit 点）
  - **FR-AC**: FR-2.1 emit / FR-3.4 emit（#2）；plan §3
  - **验证**: T136（折叠 emit `TOOL_RESULT_EVICTED`）+ T139（超阈 emit `PER_TURN_BUDGET_EXCEEDED`）

---

## 收尾任务 — 双评审 + living-docs + 交付

- [ ] **T150 [SD-1] [SD-2] [SD-4]** — 批次 2 Codex + Opus 双评审 panel（0 HIGH 门）
  - **落点**: 无代码改动。命中 **prefix-cache 不变量节点 + capability/tooling/context 层 + 行为变更** → Codex + Opus 双评审。
  - **评审重点（三确定性论证）**: ① 确定性占位生成规则（C4，T135b）；② 折叠单调收敛论证（plan §6）；③ 与 F108 W8 system 折叠边界（FR-2.3，T135 边界声明）。LLM judge 配确定性打底（测试/类型/probe 实测证据），分歧显式列"必须人裁"清单。**含 re-review**（大改 commit 后再 review，参照 F099 三轮收敛，至少 2 轮收敛到 0 HIGH）。**KV-cache 实测（T120 probe.md）作为评审前置证据输入**。
  - **依赖**: T136, T137, T139, T141（批次 2 全部实现 + 测试）
  - **FR-AC**: spec §10
  - **验证**: 0 HIGH 残留 + 分歧人裁清单闭环

- [ ] **T151 [SD-2]** — living-docs 同步（漂移闸）
  - **落点**: ① `octoagent/.../hooks_legacy.py:148` 注释——改写「ArtifactStore 仅审计、不作为 LLM 恢复完整内容途径」→ 反映 read-back 已暴露（FR-3.3，`grep` 旧约束文案须消失）；② `docs/codebase-architecture/harness-and-context.md`——推翻 read-back 旧约束 + 新增 `artifact.read_content` 工具 + per-turn 预算 hook + store `Optional` task 参数描述 + tail eviction history 层不变量与 `:172-178` context-assembly 侧 prefix-cache 不变量**合并表述**。
  - **依赖**: T131（read-back 落地），T135b（eviction 占位），T150（评审定稿）
  - **FR-AC**: FR-3.3；AC-3.3
  - **验证**: `grep` 旧约束文案消失 + completion-report living-docs 比对表

- [ ] **T152 [SD-0]** — 全量 0 regression 终验（PYTHONPATH 锁 worktree）
  - **落点**: 无代码改动。PYTHONPATH 锁 worktree 跑全量 + e2e_smoke。
  - **依赖**: T150, T151
  - **FR-AC**: spec §10
  - **验证**: `passed ≥ T000 baseline + 新增测试数`、0 failed/0 error、deselect 情况说明；e2e_smoke 必过（worktree 手动 PYTHONPATH 跑，commit 时 `SKIP_E2E=1` bypass 记录理由）

- [ ] **T153 [SD-0]** — 交付物：completion-report + handoff
  - **落点**: `.specify/features/126-capability-efficiency/completion-report.md` + `handoff.md`。
  - **内容**: completion-report 含——① living-docs 比对表（触碰模块 code↔doc drift 入"已知 limitations"）；② Phase 列表"实际做 vs 计划"；③ Phase 跳过/降级显式归档（尤其 T120 fail 时项 2 降级/推迟 + per-turn P2 若降级）；④ opt-out 工具清单（`skip_arg_validation=True` 的工具）；⑤ 双评审闭环结果（N HIGH / M MED 处理 / K LOW）；⑥ baseline vs 终态 passed 数对比。**不主动 push，等用户拍板**。
  - **依赖**: T152
  - **FR-AC**: plan §10 / spec §10
  - **验证**: 制品齐全 + 给出"建议合入 / 先 review"明确建议

---

## 依赖序总览（不可跳过）

```
T000 baseline
  ├─ 批次1（项1，SD-3 独立）: T101→T102→T103→T104→T105 + [P]T106/T107 → T108(回归+Codex)
  │                          T140(emit 复用) 挂 T105
  └─ 批次2:
       step0【硬门 AC-GATE-1/SD-1】: T120 → kv-cache-probe.md
       step1（项3 read-back/store, SD-2，可与 step0 并行）: T130→T131→T132 + [P]T133/T134
       step2（项2 eviction，BLOCKED 直到 T120 PASS + step1）:
            T135→T135b→T135c + [P]T136 → T137(e2e 闭环 AC-LOOP-1)
            T141(新增 2 EventType) 挂 T135b/T138
       step3（项3 per-turn 预算 P2, SD-4）: T138 + [P]T139
       收尾: T150(双评审 0 HIGH)→T151(living-docs)→T152(0 regression 终验)→T153(交付)
```

**硬门语义重申**：T120（AC-GATE-1）未 PASS → T135/T135b/T135c/T136/T137（项 2 实现 + 闭环 e2e）全部 BLOCKED，转 plan §7 fallback 决策树（降级"仅告警不折叠" / 推迟项 2，AC-LOOP-1 标 deferred），completion-report 显式归档。
