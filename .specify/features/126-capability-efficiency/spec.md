# F126 Capability 效率改进 — 功能规范（spec.md）

- **Feature ID**: F126
- **Slug**: capability-efficiency
- **基线**: master cd9a56c3 / 分支 `feature/126-capability-efficiency` / worktree `F126-cap-eff`
- **性质**: **行为变更（非零变更重构）**——F108 spin out 的 3 项设计输入立项
- **调研依据**: `.specify/features/126-capability-efficiency/research/tech-research.md`（纯代码库扫描，含全部 file:line 锚点）
- **代码根**: 实际代码在仓库根的 `octoagent/` 子目录（`packages/` `apps/` 均在其下）；本 spec 内 `file:line` 锚点与调研一致，相对仓库根、文件位于 `octoagent/` 下

> 散文中文 / 代码标识符英文 / 英文技术术语保原文。

---

## 1. 概述

F126 把 F108 在 M6 竞品源码深读阶段沉淀的 **3 项 capability/context 层效率改进** 一次性落地。三项分别来自三个竞品的真实抽象：

1. **项 1 — 执行前 schema 校验 + 结构化 retry feedback**（竞品 Pydantic AI F1）：在工具真正执行前对照其 `parameters_json_schema` 校验 args，校验失败把字段级 `errors()`（`loc`/`msg`/`type`）结构化回灌自愈 retry loop，而非让工具体内迟到 raise 出非结构化自由文本。
2. **项 2 — tool_call_id 确定性 tail eviction**（竞品 Claude Code F3，**最高风险**）：上下文 truncate 前，按 `tool_call_id` 把旧 tool 结果**确定性**替换为稳定占位（指向 artifact），**只折叠不改写中段**，占位一旦生成对同一 `tool_call_id` 永久冻结，使前缀单调收敛，避免现状下 history 无限增长直到 provider 溢出即失败。
3. **项 3 — artifact read-back + per-turn 预算**（竞品 Hermes F3）：新增 LLM 可调用的 artifact 读回能力（被项 2/卸载机制折叠的工具输出可恢复），并新增"单轮内所有 tool 输出加总超阈值即触发聚合卸载/截断"的 per-turn 跨工具聚合预算 hook。

**两项已拍板的总约束（必须贯穿全 spec）**：

- **决策 A — 单一交付**：3 项由**一个 spec 覆盖、一次性验收 + 合入**。内部可分实现批次（建议批次 1 = 项 1 独立低风险；批次 2 = 项 2+项 3 强耦合一起做），但**不拆成多个独立 Feature**。
- **决策 B — KV-cache 实测硬前置门**：项 2 tail eviction 的"逐 transport（chat/responses/anthropic）KV-cache 实测"是**动 tail-eviction 代码前的硬前置门**。必须先实测占位改写是否触发 KV-cache 整段重算，**实测通过才允许写实现代码**。本约束作为显式 acceptance gate（AC-GATE-1）+ 前置结构化依赖（SD-1）写入。

---

## 2. 问题陈述（3 项的缝，引调研锚点）

### 项 1 的缝

- **缺执行前预校验**：`ToolBroker.execute`（broker.py:314）链路在步骤 5 `_invoke_handler` 直接 `handler(**args)`（broker.py:567/569），**执行前从不对照 `parameters_json_schema`（models.py:144）校验 args**。`Field required` / 类型错误要么被工具体内迟到 raise（broker.py:478-493 except 兜底包装成 `is_error`），要么静默传入造成运行时异常。
- **反馈非字段级**：`error=str(e)`（broker.py:490）是自由文本，LLM 拿不到精确 `loc` / `Field required` / 期望 `type`。竞品 Pydantic AI F1 的价值正是回灌 `ValidationError.errors()`。
- **`_coerce_input`（runner.py:734）不覆盖**：它只校验 skill 顶层 input（`manifest.input_model.model_validate`），不校验每个 tool call 的 args，是不同层。

### 项 2 的缝

- **model_client 历史层无 tail eviction**：`_maybe_compact_history`（provider_model_client.py:267）**当前 no-op**（provider_model_client.py:288-294），旧 tool 结果永久留在 `self._histories[key]` 直到 provider 溢出。兜底是 runner `context_overflow` → `_terminate_with_failure`（runner.py:210-219），溢出即失败、不优雅。
- **占位需"改写"而非"追加"**：现有 `[artifact:xxx]` 占位（runner.py:707）是 feedback 构造时一次性决定；tail eviction 需要的是**事后改写历史中已有的 tool 消息**为占位。`_append_feedback_to_history`（provider_model_client.py:165）已有 `tool_call_id` 去重（provider_model_client.py:170），`tool_call_id` 稳定可寻址。

### 项 3 的缝

- **缺 LLM 可调用的 read-back 工具**：artifact 已存（`LargeOutputHandler._store_as_artifact`，hooks_legacy.py:241）、`get_artifact_content`（store/protocols.py:99）已能取，但**无工具暴露给 LLM**。`filesystem_read_text`（filesystem_tools.py:94）按路径读、不接受 `artifact:` 前缀。
- **旧约束须推翻**：hooks_legacy.py:148 明确写「ArtifactStore 仅用于审计存档，不作为 LLM 恢复完整内容的途径」——**本 Feature 显式推翻该约束**，并要求同步该处注释 + `harness-and-context.md`。
- **越权读风险**：`get_artifact`（artifact_store.py:385）仅按 id 查、**无 task 隔离**，read-back 工具层必须校验 artifact 归属 task/scope。
- **缺 per-turn 跨工具聚合预算**：现有预算均不是 per-turn 跨工具聚合——`LargeOutputHandler` 是单工具单次（hooks_legacy.py:186）、`UsageTracker` 是任务级累计（runner.py:105/139）、`ContextBudgetPlanner` 是构建期静态（context_budget.py:53）。

---

## 3. User Stories（按 3 项分组）

### 项 1：执行前 schema 校验

- **US-1.1（P1）** — 作为依赖工具自愈的 LLM Agent，当我用缺字段/错类型的 args 调工具时，我应在工具执行前就收到**字段级结构化的校验失败反馈**（哪个字段缺、期望什么类型），以便下一轮精确修正。
  - 优先级理由：直接降低工具调用失败的 retry 轮数与 token 消耗，是 3 项里 ROI 最直接、风险最低的。
  - 独立测试：构造一个缺 required 字段的 tool call → 断言 broker 在调 handler 前返回 `is_error`、feedback 含结构化 `loc`/`type`，且 handler 未被调用。
  - 验收场景（Given-When-Then）：
    - Given 一个 `parameters_json_schema` 含 required 字段 `path` 的工具；When LLM 以 `{}` 调用；Then broker 在 `_invoke_handler` 前以 `proceed=False` 拒绝，emit `TOOL_CALL_FAILED`，feedback `kind` 携结构化字段错误，handler 零调用。
    - Given 一个合法 args（含工具体内做 coerce 的字段）；When 调用；Then **不得**被预校验误拒（合法调用照常执行）。

### 项 2：tool_call_id 确定性 tail eviction

- **US-2.1（P1）** — 作为长会话中的 LLM Agent，当 history 接近预算压力时，系统应把最旧的 tool 结果折叠为指向 artifact 的**稳定占位**，使我能继续推进而非溢出失败，且不破坏 KV-cache 前缀。
  - 优先级理由：解决现状"history 无限增长 → 溢出即失败"的不优雅终态；是闭环核心。
  - 独立测试：多轮注入大 tool 结果触发 compaction → 断言被折叠的 tool 消息内容被替换为确定性占位、同一 `tool_call_id` 多轮重复 compaction 后占位**字节级不变**、位置不动。
  - 验收场景：
    - Given history 中存在已卸载为 artifact 的旧 tool 结果；When compaction 触发；Then 该 tool 消息 content 替换为 `[已折叠，见 artifact:<ref>]` 形态的确定性占位，`tool_call_id` 不变，配对的 assistant tool_call 不动。
    - Given 同一 session 连续 N 轮 compaction；When 每轮执行；Then 同一 `tool_call_id` 的占位串**完全一致**（无可变计数/时间戳），前缀单调收敛。
- **US-2.2（P1）** — 作为系统维护者，eviction 改写后的 history 进 checkpoint 并 resume 时，tool_call/tool_result 配对不得错位。
  - 验收场景：Given eviction 后 history 进 checkpoint；When resume 重建；Then resume 重建的是已折叠版本，无 `conversation_state_lost`（provider_model_client.py:378），配对完整。

### 项 3：artifact read-back + per-turn 预算

- **US-3.1（P1）** — 作为 LLM Agent，当我看到指向 artifact 的折叠占位时，我应能通过一个工具读回该 artifact 的内容（可分页），以恢复被卸载的工具输出。
  - 优先级理由：是项 2 占位的"另一半"——占位不可读回 = 信息单向丢失。
  - 独立测试：卸载一个工具输出为 artifact → LLM 调 read-back 工具 → 断言取回原内容、且越权读其它 task 的 artifact 被中央权限拒绝。
  - 验收场景：
    - Given 当前 task 拥有 artifact `A`；When LLM 调 read-back 工具读 `A`（含 offset/limit）；Then 返回 `A` 的对应内容分片。
    - Given artifact `B` 属于另一 task；When LLM 调 read-back 工具读 `B`；Then 中央权限（broker.py:370）/ 工具层 task 隔离校验拒绝，emit 失败事件。
- **US-3.2（P2）** — 作为系统，当单轮内所有 tool 输出加总超阈值时，应触发聚合卸载/截断，避免单轮工具输出膨胀撑爆上下文。
  - 优先级理由：per-turn 聚合预算是新增治理维度，但单工具 `LargeOutputHandler` 已提供基础保护，故 P2。
  - 验收场景：Given 单轮多个 tool 输出加总超阈值；When 该轮结束聚合 hook 触发；Then 超额部分被卸载为 artifact 占位或截断，下一轮 generate 不携带超额原文。

---

## 4. Functional Requirements（FR）

> MUST = 必须；SHOULD = 应当；MAY = 可选。每条标注 YAGNI 必要性。AC↔test 显式绑定见 §5。

### 项 1

- **FR-1.1 [必须] MUST** — 系统 MUST 在 `ToolBroker` before-hook 链中以一个 **fail-closed BeforeHook**（复用 broker.py:396 现有 before-hook 机制 + broker.py:399-417 拒绝路径）对照工具的 `parameters_json_schema`（models.py:144）校验 args；校验失败时返回 `proceed=False`，**broker 主干不改**。
- **FR-1.2 [必须] MUST** — 校验失败 MUST 把校验器（pydantic/jsonschema）的 `errors()`（含 `loc`/`msg`/`type`/`Field required`）**结构化**写入反馈，承载于扩展或复用后的 `ToolFeedbackMessage`（skills/models.py:347），回灌 runner 的自愈 retry loop（runner.py:406 `feedback.extend`）。
- **FR-1.3 [必须] MUST** — 校验源 MUST 是 LLM 看到的同一份 `parameters_json_schema`（schema.py:122 / provider_model_client.py:254），不得另建事实源（Constitution #3 Tools are Contracts）。
- **FR-1.4 [必须] MUST** — 校验拒绝 MUST 走中央权限决策（broker.py:370）与事件溯源（emit `TOOL_CALL_FAILED`，Constitution #2/#10）；handler MUST 在拒绝时零调用。
- **FR-1.5 [必须] MUST** — 校验严格度策略 MUST 避免比工具体内实际宽容度更严而误拒合法调用（如工具体内做 coerce / 默认值填充的场景）。**【C1 已决议】采用「宽松校验 + 逐工具白名单豁免」**：BeforeHook 默认仅校验 `required` 字段齐全 + 顶层属性 `type`（含 enum 顶层），**不**做 `additionalProperties:false` / 深层 nested 递归 / format/pattern 强制；并提供 per-tool opt-out 标记（如 `ToolMeta.skip_arg_validation: bool = False`）让"工具体内自做 coerce/默认值/复杂 validate"的工具整体跳过。依据：直接满足 FR-1.5「不得误拒」（缺 required + 顶层 type 错几乎不可能被体内宽容掉，误拒风险趋零），仍从同一份 schema 提取（满足 FR-1.3），豁免为机制非硬编码例外（#9）。详见 clarifications.md C1。
- **FR-1.6 [可选] MAY**（C1 衍生）— 系统 MAY 在 `ToolMeta` 上提供 `skip_arg_validation` 标记，注册期声明，标记为 True 的工具跳过 FR-1.1 预校验，回退到 broker except 兜底路径（broker.py:478-493）。

### 项 2

- **FR-2.1 [必须] MUST** — 系统 MUST 在 model_client 历史层（`_maybe_compact_history` provider_model_client.py:267，当前 no-op；和/或 `_append_feedback_to_history` provider_model_client.py:165）实现按 `tool_call_id` 的确定性 tail eviction：上下文 truncate 前把旧 tool 结果替换为指向 artifact 的稳定占位。
- **FR-2.2 [必须] MUST** — 占位 MUST 满足确定性冻结：一旦某 `tool_call_id` 被折叠，它在所有后续轮次 MUST 保持**同一占位串**（字节级一致）、不再变回原文、不再二次改写、**位置不动**；占位文本 **MUST NOT** 含可变计数/时间戳/每轮重算结果。
- **FR-2.3 [必须] MUST** — eviction MUST 只折叠历史中旧 tool 结果、**不得改写历史中段非折叠内容**；MUST NOT 触及 system prompt 折叠假设（provider_client.py:113 `_merge_system_messages_to_front`）与 F108 W8 的 system 组装层。
- **FR-2.4 [必须] MUST** — eviction 改写后的 history 进 checkpoint 后，resume MUST 重建已折叠版本，保证 tool_call/tool_result 配对不错位（provider_model_client.py:378）。
- **FR-2.5 [必须] MUST**（前置门）— 在写任何 tail-eviction 实现代码前，MUST 完成逐 transport（chat/responses/anthropic）KV-cache 实测，证明占位改写不触发 KV-cache 整段重算（决策 B / SD-1 / AC-GATE-1）。
- **FR-2.6 [必须] MUST** — 占位指向的 `artifact_ref` 格式 MUST 与项 3 read-back 统一（共享 `get_artifact_content` 后端 store/protocols.py:99），构成"卸载-占位-读回"闭环（SD-2）。**【C4 已决议】占位串 = `[已折叠，见 artifact:<artifact_id>（工具 <tool_name>，原始 <N> 字节）]`**，三个插值元素（`artifact_id` = 卸载时 ULID、`tool_name` = tool_call 固有属性、`<N>` = 卸载时刻测定的原始字节数）**全部为折叠时刻即冻结的稳定值**；占位首次折叠时构造一次并写入历史，后续 compaction 检测到 content 已是占位形态则跳过不重构（**禁止每轮重拼**）；**禁止**任何当前轮次/时间戳/计数/剩余预算等可变内容。详见 clarifications.md C4。

### 项 3

- **FR-3.1 [必须] MUST** — 系统 MUST 提供 LLM 可调用的 artifact read-back 能力，后端复用 `get_artifact_content`（store/protocols.py:99）。**【C2 已决议】采用「新增独立工具 `artifact.read_content(artifact_ref, offset?, limit?)`」**（落 `apps/gateway/.../builtin_tools/`，走 broker 注入的 `artifact_store`），**不**扩展 filesystem 工具识别 `artifact:` 前缀。依据：独立工具 schema 语义清晰（Constitution #3），避免 filesystem 工具语义污染 + `if path.startswith("artifact:")` 前缀嗅探分支（违 #9 机制非规则）；offset/limit 建议按**字节**语义与 `get_artifact_content -> bytes` 对齐。详见 clarifications.md C2。
- **FR-3.2 [必须] MUST** — read-back 工具 MUST 校验 artifact 归属当前 task/scope，**不得越权读其它 task 的 artifact**；权限 MUST 走中央权限决策（broker.py:370）。**【C5 已决议 + GATE_DESIGN 拍板】采用「工具层主隔离（走中央权限 #10）+ store 层 task 参数纵深防御（纳入本批次）」**：①权限是否允许读走中央 `check_permission`（broker.py:370，满足 Constitution #10 权限单一入口）；②工具层取回后比对 `artifact.task_id == 当前 task_id`，不匹配则拒绝 + emit 失败事件；③**store 层 `get_artifact`/`get_artifact_content` 加 `Optional` task 参数（artifact_store.py:385/405）作为纵深防御第二道——GATE_DESIGN 已拍板纳入本批次**。`task=None` = 内部信任调用、跳过 task 过滤（保内部 caller 如 context_compaction.py:839 行为零变更）；read-back 工具调用必传 task → SQL 层 `WHERE task_id=?` 物理隔离。依据：权限语义不下沉 store 层（合 #10，能否读走中央），store 层 task 过滤是纵深防御非权限事实源。详见 clarifications.md C5。
- **FR-3.3 [必须] MUST** — 系统 MUST 显式推翻 hooks_legacy.py:148「artifact 不作为 LLM 恢复完整内容途径」的旧约束，并同步该处注释与 `docs/codebase-architecture/harness-and-context.md`（living-docs）。
- **FR-3.4 [可选] SHOULD** — 系统 SHOULD 新增 per-turn 跨工具聚合预算 hook：单轮内所有 tool 输出加总超阈值即触发聚合卸载/截断。**【C3 已决议】保守默认：阈值可配（`per_turn_tool_output_budget`，默认 ≈ 单轮 8000 token，env/config 可覆盖）+ 触发动作分两级（先 emit 告警事件 → 对超额部分复用 `LargeOutputHandler._store_as_artifact` 卸载为 artifact 占位，占位格式与项 2 统一）+ 落点 runner `_call_hook`（runner.py:653，在该轮所有 tool call 执行完之后聚合）**，而非 AfterHook（per-tool 粒度做不到跨工具加总）。降级兜底：若复杂度超预期，GATE_DESIGN 可决定降为"仅 emit 告警事件不自动卸载"最小版（AC-3.4 相应调整）。详见 clarifications.md C3。
- **FR-3.5 [可选] SHOULD** — per-turn 预算与项 2 tail eviction SHOULD 统一治理同一压力源（工具输出膨胀），避免双重截断（共用 `artifact:<ref>` 占位语义 + `LargeOutputHandler` 卸载后端；项 2 折旧块、项 3 拦新输出，作用窗口不重叠）。

### YAGNI 检验小结

- 被标 **[可选]** 的 FR-3.4 / FR-3.5：去掉 per-turn 预算后，项 2 tail eviction + 项 3 read-back 闭环仍成立（核心需求可实现），但单轮工具输出膨胀的即时防护减弱。保留为 SHOULD（US-3.2 为 P2）。
- FR-1.6（`skip_arg_validation` 标记）判 **[可选] MAY**：C1 宽松校验下豁免是纵深保险，非默认必需；若实测无误拒工具可不暴露此标记。
- 无 FR 被判 **[YAGNI-移除]**——三项均为已拍板范围内的核心能力。
- read-back 工具的 `offset`/`limit` 分页（FR-3.1）判 [必须]：折叠的工具输出通常大，无分页则 read-back 自身会再次撑爆上下文。

---

## 5. Acceptance Criteria（AC，含 AC↔test 显式绑定）

> 遵守 CLAUDE.local.md「AC↔test 显式绑定」SDD 强化：每条 P1 AC / 关键 FR 紧邻标注对应 test 文件路径；verify 阶段机械校验该 test 存在且 PASS（`pytest -k`）。test 路径为**预期落点**，实现时建立。

### Gate（决策 B 前置硬门）

- **AC-GATE-1（P0 前置门，对应 FR-2.5 / SD-1）** — 逐 transport（chat/responses/anthropic）KV-cache 实测完成并记录结论，证明确定性占位改写不触发 KV-cache 整段重算；**实测未通过则项 2 实现代码不得开工**。
  - 实测证据落点：`.specify/features/126-capability-efficiency/kv-cache-probe.md`（含三 transport 各自结论 + 复现方法）。
  - 绑定：`octoagent/packages/skills/tests/test_provider_model_client_tail_eviction.py::test_placeholder_does_not_break_prefix`（实测结论转化为确定性回归断言）。

### 项 1

- **AC-1.1（P1，对应 FR-1.1/FR-1.4）** — 缺 required 字段的 tool call 在 `_invoke_handler` 前被拒，handler 零调用，emit `TOOL_CALL_FAILED`。
  - test: `octoagent/packages/tooling/tests/test_schema_validation_hook.py::test_missing_required_rejected_before_handler`
- **AC-1.2（P1，对应 FR-1.2）** — 拒绝反馈含结构化字段错误（`loc`/`msg`/`type`），回灌 retry loop 后下一轮 generate 能消费。
  - test: `octoagent/packages/skills/tests/test_structured_validation_feedback.py::test_field_level_errors_in_feedback`
- **AC-1.3（P1，对应 FR-1.5）** — 工具体内做 coerce/默认值的合法调用**不被误拒**（宽松校验只查 required + 顶层 type）。
  - test: `octoagent/packages/tooling/tests/test_schema_validation_hook.py::test_lenient_valid_call_not_rejected`
- **AC-1.4（对应 FR-1.3）** — 校验所用 schema 与 LLM 收到的 `parameters_json_schema` 为同一份（断言取自同一来源）。
  - test: `octoagent/packages/tooling/tests/test_schema_validation_hook.py::test_validation_uses_same_schema_source`
- **AC-1.5（对应 FR-1.6 / C1 豁免）** — 标记 `skip_arg_validation=True` 的工具跳过预校验，回退 except 兜底。
  - test: `octoagent/packages/tooling/tests/test_schema_validation_hook.py::test_optout_tool_skips_validation`

### 项 2

- **AC-2.1（P1，对应 FR-2.1/FR-2.2/FR-2.6 占位格式）** — compaction 触发后旧 tool 结果被替换为 C4 决议的确定性占位；同一 `tool_call_id` 多轮 compaction 后占位字节级不变、位置不动、不含可变内容。
  - test: `octoagent/packages/skills/tests/test_provider_model_client_tail_eviction.py::test_deterministic_frozen_placeholder`
- **AC-2.2（P1，对应 FR-2.3）** — eviction 只折叠旧 tool 结果，不改写中段非折叠内容、不触及 system 折叠假设。
  - test: `octoagent/packages/skills/tests/test_provider_model_client_tail_eviction.py::test_no_mid_history_rewrite`
- **AC-2.3（P1，对应 FR-2.4）** — eviction 后 history 进 checkpoint 并 resume，tool_call/tool_result 配对不错位、无 `conversation_state_lost`。
  - test: `octoagent/packages/skills/tests/test_provider_model_client_tail_eviction.py::test_resume_pairing_intact`

### 项 3

- **AC-3.1（P1，对应 FR-3.1）** — LLM 可调独立工具 `artifact.read_content` 取回被卸载的 artifact 内容（含 offset/limit 分页）。
  - test: `octoagent/apps/gateway/tests/test_artifact_read_back_tool.py::test_read_back_returns_content`
- **AC-3.2（P1，对应 FR-3.2）** — read-back 读其它 task 的 artifact 被中央权限/工具层 task 隔离拒绝。
  - test: `octoagent/apps/gateway/tests/test_artifact_read_back_tool.py::test_cross_task_read_denied`
- **AC-3.3（对应 FR-3.3）** — hooks_legacy.py:148 注释与 `harness-and-context.md` 已同步推翻旧约束（living-docs 校验）。
  - 校验: completion-report 的 living-docs 比对表 + `grep` 旧约束文案消失。
- **AC-3.4（P2，对应 FR-3.4/FR-3.5）** — 单轮 tool 输出加总超阈值（`per_turn_tool_output_budget`，默认 8000 token，env `OCTOAGENT_PER_TURN_TOOL_OUTPUT_BUDGET` 可覆盖）触发 **emit `PER_TURN_BUDGET_EXCEEDED` 告警**。**【实施降级，C3 spec 许可 / analyze M3】**：本批次为 **warn-only 最小版**——聚合卸载/截断与 项2 tail eviction 治理同一压力源、共享占位语义（SD-4/FR-3.5），故"自动卸载超额原文"推迟到 项2 落地一并做（避免双重截断 + 占位语义不统一）。AC-3.4 断言相应收窄为"超阈值即 emit 告警事件"。
  - test: `octoagent/packages/skills/tests/test_per_turn_budget_hook.py::test_aggregate_overflow_emits_warning`

### 闭环（项 2 ↔ 项 3）

- **AC-LOOP-1（P1，对应 FR-2.6/SD-2）** — 项 2 折叠出的占位 `artifact_ref` 能被项 3 read-back 工具成功读回，构成"卸载-占位-读回"端到端闭环。
  - test（e2e）: `octoagent/apps/gateway/tests/e2e/test_offload_placeholder_readback_loop.py::test_evicted_placeholder_readable`

---

## 6. Structured Dependencies（SD）

> 显式列出实现前置/耦合依赖，analyze/verify 阶段做确定性结构校验。

- **SD-1（前置门，硬）** — **项 2 实现代码 依赖 AC-GATE-1（KV-cache 逐 transport 实测）PASS**。实测未通过则 FR-2.1~FR-2.4/FR-2.6 实现不得开工（决策 B）。
- **SD-2（强耦合）** — **项 2 占位 依赖 项 3 read-back 闭环**。项 2 的占位 `artifact_ref` 必须可被项 3 read-back 读回（共享 `artifact_ref` 格式 + `get_artifact_content` 后端 + task 隔离策略），否则占位 = 信息单向丢失。两者须在同一批次内统一交付（建议实现批次 2）。
- **SD-3（弱耦合）** — 项 1 与项 2/3 触碰面几乎不重叠（项 1 在 broker BeforeHook 执行前 / `packages/tooling`；项 2/3 在 history、artifact 层执行后 / `packages/skills` + `packages/core` + `apps/gateway`），可独立开发（建议实现批次 1 = 项 1 先行建立信心）。
- **SD-4（治理统一）** — FR-3.4 per-turn 预算 与 项 2 tail eviction 治理同一压力源（工具输出膨胀），须统一 `artifact_ref` 语义与触发策略避免双重截断（FR-3.5）。

---

## 7. 设计决策（C1–C5 已决议）

> 本节原为 [NEEDS CLARIFICATION] 待澄清点，已由澄清阶段在「信任但验证」策略下给出推荐解，并回写进对应 FR（FR-1.5/FR-1.6/FR-3.1/FR-3.2/FR-3.4/FR-2.6）。**完整推荐解 / 依据 / 备选 / 风险 / 关联 FR-AC 见 `clarifications.md`**。GATE_DESIGN 一次性确认/微调。

- **C1 校验严格度 — 已决议：宽松校验 + 逐工具白名单豁免**（仅查 required + 顶层 type，不拒多余字段/不递归深层；per-tool `skip_arg_validation` opt-out）。依据：直接满足 FR-1.5「不得误拒」、仍同一份 schema（#3）、豁免为机制非规则（#9）。落 FR-1.5 / FR-1.6 / AC-1.3 / AC-1.5。
- **C2 read-back 工具形态 — 已决议：新增独立工具 `artifact.read_content(artifact_ref, offset?, limit?)`**（不扩展 filesystem 工具识别 `artifact:` 前缀）。依据：schema 语义清晰（#3）、避免前缀嗅探分支（#9）、offset/limit 按字节与 `get_artifact_content -> bytes` 对齐。落 FR-3.1 / AC-3.1。**（GATE_DESIGN 重点确认项之一）**
- **C3 per-turn 预算 — 已决议：阈值可配（默认 ≈ 8000 token）+ 先告警再聚合卸载为 artifact 占位 + 落点 runner `_call_hook`**（非 AfterHook，因需跨工具加总）。SHOULD/P2，可降级为仅告警最小版。落 FR-3.4 / FR-3.5 / AC-3.4。
- **C4 占位文本确定性格式 — 已决议：`[已折叠，见 artifact:<artifact_id>（工具 <tool_name>，原始 <N> 字节）]`**，三插值元素全部为折叠时刻冻结的稳定值；首次折叠构造一次写入历史、后续跳过不重拼；禁含任何轮次/时间戳/计数。落 FR-2.6 / AC-2.1。
- **C5 artifact task 隔离落点 — 已决议 + GATE_DESIGN 拍板：工具层主隔离（走中央权限 #10）+ store 层 `Optional` task 参数纵深防御（已纳入本批次）**。权限走中央 `check_permission`（合 #10 不下沉），工具层比对 `task_id` 归属，store 层 `get_artifact`/`get_artifact_content` 加 `Optional` task 参数（artifact_store.py:385/405）+ SQL `WHERE task_id`；`task=None` 保内部 caller（context_compaction.py:839）零变更。落 FR-3.2 / AC-3.2 + 新增 store 层 task 隔离单测。**（GATE_DESIGN 已拍板纳入）**

---

## 8. Out-of-scope（显式排除）

- **gateway `ContextCompactionService`（context_compaction.py:201）不碰**：它只从 `USER_MESSAGE` + `MODEL_CALL_COMPLETED` 事件重建 turn 序列（context_compaction.py:807），**tool 结果不在其 turn 序列**，故非项 2 落点。项 2 只动 model_client 历史层。
- **`octoagent-sdk` 独立运行面不纳入**。
- **F108 W8 system 组装层不重做**：AmbientRuntime 已在 Block 2 尾（harness-and-context.md:173），属 system prompt 组装层，与 history 层 tail eviction 不同层；本 Feature 不重做该层，仅声明 history 层改写不得触及其 system 折叠假设（FR-2.3）。
- **`ConversationTurn` 扩 `tool_call_id` 字段不做**：(B) 层（gateway）当前无 tool 结果，本 Feature 不在 (B) 层做 id 级 eviction。

---

## 9. 边界 / 风险

- **最高风险：prefix-cache 打断（项 2）**。前缀 = [合并 system]（provider_client.py:113-149）+ [history 前段]。tail eviction 本质是改写 history 里已有的 tool 消息，与"只追加不改写中段"存在内在张力。**唯一安全实现**：折叠后位置不动 + 占位对同一 `tool_call_id` 永久确定性冻结（C4），使前缀单调收敛。任何"每轮重算哪些该折叠 / 占位含可变内容"会反复打断前缀，**比不做更糟**（调研结论 173 行）。`_maybe_compact_history` 是 no-op 干净起点，但意味着零既有验证 → 决策 B 实测前置门由此而来。
- **resume 风险**：history 丢失时 step>1 raise `conversation_state_lost`（provider_model_client.py:378）；eviction 改写后进 checkpoint 须保证配对不错位（FR-2.4 / AC-2.3）。
- **项 1 风险（低）**：唯一风险是校验严格度误拒合法调用——C1 已决议宽松校验 + 豁免兜底（FR-1.5/FR-1.6）将其降至趋零。
- **项 3 风险（中）**：read-back 越权读（FR-3.2 / AC-3.2，C5 决议工具层主隔离 + 可选 store 纵深）；per-turn 预算与项 2 双重截断（FR-3.5，C3 决议统一占位语义规避）。

---

## 10. Non-Functional / 交付约束

- **0 regression vs master cd9a56c3**；`e2e_smoke` 必过；每项新增能力补 unit test + e2e。
- **重大架构变更节点**（动 capability/tooling/context 层，命中 CLAUDE.local.md）：需 **Codex + 第二模型（Opus）双评审 panel**，**0 HIGH 残留**；项 2 命中 prefix-cache 不变量节点，评审须含确定性占位生成规则（C4）+ 折叠单调收敛论证 + 与 F108 W8 system 折叠边界，并与 `harness-and-context.md:172-178` 不变量表述合并。
- **禁 `uv sync`**；验证用 `PYTHONPATH` 锁定 worktree（防假 0 regression，见 project memory `project_worktree_venv_symlink`）。
- **交付物**：`completion-report.md` + `handoff.md` + living-docs 同步（`harness-and-context.md` 推翻 artifact read-back 旧约束 + hooks_legacy.py:148 注释 + 新增工具/hook 描述）。
- **不主动 push，等用户拍板**。
- **Constitution 遵守**：#2 Everything is an Event（校验拒绝/折叠/read-back/预算触发均 emit 事件）；#3 Tools are Contracts（校验源 = LLM 看到的同一份 schema）；#4 Two-Phase（不引入新不可逆副作用）；#9 Agent Autonomy（read-back/预算/豁免是机制非硬编码关键词规则，不替代 LLM 决策）；#10 Policy-Driven Access（权限统一走中央 `check_permission`，C5 隔离不下沉 store 层）。

---

## 11. 复杂度评估（供 GATE_DESIGN 审查）

- **组件总数**：4-5（① schema 校验 BeforeHook；② tail eviction 历史改写逻辑；③ read-back 工具；④ per-turn 预算 hook；⑤ 可能扩 `ToolFeedbackMessage` 字段）。
- **接口数量**：4（read-back 工具新接口 1；`ToolFeedbackMessage` 结构化字段 1；`_maybe_compact_history` 行为契约 1；**store 层 `get_artifact`/`get_artifact_content` 加 `Optional` task 参数 1——GATE_DESIGN 已拍板纳入**）。
- **依赖新引入数**：0（jsonschema/pydantic 已在依赖内，artifact_store/broker/hook 协议均已存在）。
- **跨模块耦合**：是——触及 `packages/tooling` + `packages/skills` + `packages/core/store` + `apps/gateway` 多模块（项 2↔项 3 强耦合）。
- **复杂度信号**：命中 1-2 个——**状态机/前缀单调收敛**（项 2 占位冻结 + resume 配对）属上下文状态演进；**并发/缓存控制**（KV-cache prefix-cache 治理）。无递归结构、无数据迁移。
- **总体复杂度**：**HIGH**（跨 ≥2 模块接口 + prefix-cache 不变量信号 + 行为变更）。建议 GATE_DESIGN 人工审查 + 双评审 panel；项 2 设强前置实测门（AC-GATE-1）。

---

## 12. Success Criteria（成果指标，技术无关）

- **SC-1** — 工具调用因 args 错误失败时，Agent 平均自愈 retry 轮数下降（结构化反馈使一轮即修正成为常态）。
- **SC-2** — 长会话不再因 history 无限增长而"溢出即失败"，被折叠的工具输出仍可按需读回（无信息单向丢失）。
- **SC-3** — tail eviction 启用后 KV-cache 前缀命中不劣化（实测证据 + 确定性占位保证）。
- **SC-4** — 单轮工具输出膨胀被 per-turn 预算约束，上下文不被单轮撑爆。
