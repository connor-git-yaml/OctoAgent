# F126 Capability 效率改进 — 技术调研（纯代码库扫描）

- **research_mode**: codebase-scan（仅只读扫描，未改任何生产代码）
- **基线**: master cd9a56c3 / worktree `F126-cap-eff`
- **范围**: F108 spin out 的 3 项设计输入立项（行为变更，非零变更重构）
  - 项 1：执行前 schema 校验 + 结构化 retry feedback（竞品 Pydantic AI F1）
  - 项 2：tool_call_id 确定性 tail eviction（竞品 Claude Code F3）
  - 项 3：artifact read-back + per-turn 预算（竞品 Hermes F3）
- **代码根**: 实际代码在 `octoagent/` 子目录（`packages/` `apps/` 均在其下）

> 锚点约定：所有 `file:line` 均相对仓库根，文件位于 `octoagent/` 子目录下。

---

## 项 1：执行前 schema 校验 + 结构化 retry feedback

### 现状（带锚点）

工具执行统一入口是 `ToolBroker.execute`（`packages/tooling/src/octoagent/tooling/broker.py:314`）。完整链路（broker.py:320-335 docstring + 实现）：

1. 步骤 1 查找工具 `self._registry.get(tool_name)`（broker.py:343）
2. 步骤 2 emit `TOOL_CALL_STARTED`（broker.py:356）
3. 步骤 3 `check_permission(...)` 中央权限决策（broker.py:370）
4. 步骤 4 before hook 链 `for hook in self._before_hooks`（broker.py:396），支持 `hook_result.modified_args` 与 `proceed=False` 拒绝（broker.py:399-417）
5. 步骤 5 `_invoke_handler(handler, current_args, ...)` 真正执行（broker.py:445 → broker.py:549）。`_invoke_handler` 直接 `handler(**args)`（broker.py:567/569），**无任何参数校验**
6. except 兜底：业务方法 raise 被捕获为 `ToolResult(is_error=True, error=str(e))`（broker.py:478-493）

**当前 args 在执行前没有任何 schema 校验**。唯一的"校验"是工具内部代码自己 raise（如 Pydantic 在工具体内 validate），由步骤 5 的 except 兜底包装成 `is_error`——这是 `harness-and-context.md:161` 记录的"错误包装契约：capability 层业务方法直接 raise，由 broker.execute except 兜底包装"隐式契约。

**自愈 retry loop 已存在**，位于 `packages/skills/src/octoagent/skills/runner.py` 的 Free Loop（runner.py:139 `while tracker.check_limits(...)`）：
- 每步 `_model_client.generate(..., feedback=feedback, ...)`（runner.py:148）
- tool 调用经 `_execute_tool_calls`（runner.py:374）→ `_execute_single_tool`（runner.py:642）→ `self._tool_broker.execute(call.tool_name, call.arguments, ...)`（runner.py:668）
- 结果经 `_build_tool_feedback`（runner.py:672/695）转成 `ToolFeedbackMessage`，**`is_error` 与 `error` 原样透传**（runner.py:715-725）
- feedback 回灌下一轮 generate（runner.py:406 `feedback.extend(tool_feedbacks)`），由 `_append_feedback_to_history`（`packages/skills/src/octoagent/skills/provider_model_client.py:165`）写回 LLM 对话历史

**`ToolFeedbackMessage` 定义**：`packages/skills/src/octoagent/skills/models.py:347`。字段：`tool_name` / `is_error` / `output` / `error: str|None` / `duration_ms` / `artifact_ref` / `parts: list[dict]` / `tool_call_id` / `kind: FeedbackKind` / `security_findings`（models.py:356-380）。`FeedbackKind` 三态：`TOOL_RESULT` / `LOOP_GUARD` / `SYSTEM_NOTICE`（models.py:335-337）。消费点是 `_append_feedback_to_history`（provider_model_client.py:165），按 kind 分派写入 `role:tool` / `role:user`（provider_model_client.py:176-214）。

**工具 schema 单一事实源**（Constitution #3）：`ToolMeta.parameters_json_schema`（`packages/tooling/src/octoagent/tooling/models.py:144`），由"函数签名 + type hints + docstring 反射生成"（models.py:138 注释）。生成在 `packages/tooling/src/octoagent/tooling/schema.py:122`（`parameters_json_schema=dict(fs.json_schema)`，用 `pydantic.json_schema.GenerateJsonSchema`，schema.py:15）。该 JSON Schema 即是送给 LLM 的工具参数定义（provider_model_client.py:254 `"parameters": tool_meta.parameters_json_schema`）——**校验源与 LLM 看到的 schema 是同一份**，符合"Tools are Contracts"。

### 缝 / gap

1. **缺执行前预校验**：args 在 broker 步骤 5 之前从不被对照 `parameters_json_schema` 校验。`Field required` / 类型错误 / 多余字段要么被工具内部 raise（晚、错误信息非结构化），要么静默传入造成运行时异常。
2. **反馈非字段级**：`is_error` + `error: str` 是自由文本（broker.py:490 `error=str(e)`），LLM 拿不到精确的 `loc` 路径 / `Field required` / `type` 期望。竞品 Pydantic AI F1 的价值正是把 `ValidationError.errors()`（含 `loc`/`msg`/`type`）结构化回灌。
3. **`_coerce_input` 不覆盖**：runner.py:734 的 `_coerce_input` 只校验 *skill 顶层 input*（`manifest.input_model.model_validate`），**不校验每个 tool call 的 args**——两者是不同层。

### 可复用的现有抽象

- **BeforeHook 协议**（`packages/tooling/src/octoagent/tooling/protocols.py:80`，`before_execute` 在 protocols.py:101）+ `BeforeHookResult`（models.py:287，含 `proceed` / `modified_args` / `rejection_reason`）：schema 校验天然实现为一个 **fail-closed BeforeHook**，校验失败时 `proceed=False` + 把 `errors()` 写进 `rejection_reason`，broker.py:399-415 已有拒绝路径（emit `TOOL_CALL_FAILED` + 返回 `is_error` ToolResult），**自动走中央权限决策 + 事件溯源**，无需改 broker 主干。
- **`ToolFeedbackMessage`**（models.py:347）已是结构化反馈容器；可新增一个字段承载结构化 validation errors，或复用 `error` 字段 + 约定 JSON 编码。
- **`parameters_json_schema`**（models.py:144）即校验所需 schema，无需新建事实源。

### 实现风险

- 低-中。最大风险是**校验严格度**：JSON Schema 校验若比工具体内实际接受的更严（如工具体内做了 coerce / 默认值填充），会误拒合法调用。需对照每个工具的实际宽容度，或采用"宽松校验"（仅查 required + 顶层 type，不查深层）。
- prefix-cache 无影响（不触碰上下文组装）。

---

## 项 2：tool_call_id 确定性 tail eviction

### 现状（带锚点）

**两套独立的"上下文瘦身"机制，落点不同：**

**(A) model_client 会话历史层**（项 2 的真正落点）：`ProviderModelClient`（provider_model_client.py:57）用 `self._histories[key]`（OpenDict）维护 OpenAI Chat 格式消息历史。
- tool 结果以 `{"role":"tool", "tool_call_id": call_id, "content": ...}` **只追加**写入（provider_model_client.py:181-191）；`_append_feedback_to_history`（provider_model_client.py:165）按 `tool_call_id` 去重（provider_model_client.py:170-178）。**tool_call_id 稳定可寻址**。
- `_maybe_compact_history`（provider_model_client.py:267）**当前是 no-op**：Phase 3 留注释"本 phase 不做 compaction（threshold 默认 1.0 不触发）"（provider_model_client.py:288-294）。即 **model_client 历史层目前完全无 tail eviction / 无压缩**，历史无限增长。
- 兜底是 runner 的 `context_overflow` 异常 → `_terminate_with_failure`（runner.py:210-219）——溢出即失败，不优雅。
- 历史以独立参数送 provider：`resolved.client.call(instructions=instructions, history=history, tools=tools, ...)`（provider_model_client.py:453-460）。

**(B) ContextCompactionService**（gateway 层，不同关注点）：`apps/gateway/.../context_compaction.py:201`。三层压缩（cheap_truncation → Recent/Compressed/Archive）。**但它只从 `USER_MESSAGE` + `MODEL_CALL_COMPLETED` 事件重建 turn 序列**（`_load_conversation_turns`，context_compaction.py:807-837），**tool 结果完全不在它的 turn 序列里**——所以它不是项 2 的落点。`_cheap_truncation_phase`（context_compaction.py:1175）会改写单条超大消息内容（context_compaction.py:1209-1211），会改写中段（见风险）。

**prefix-cache 不变量权威描述**：`docs/codebase-architecture/harness-and-context.md:172-178`（F108b W8-C2 定调）：
- volatile 内容（秒级时间戳）不得置于 system prompt 冻结前缀 **Block 1 core_sections** 中段；
- **AmbientRuntime 块自 W8-C2 起归属 Block 2（context_sections）尾部**；
- Block 1 + Block 2 前段（session 内稳定内容）全部可缓存；
- 残留核查项：capability_pack bootstrap 模板 `{{current_datetime_local}}`（harness-and-context.md:176-178）。
- 工具侧不变量（harness-and-context.md:168）：工具集稳定排序 + 静态注入，可见性收敛到 Policy（返回 policy-deny 而非删 schema）。

**system 折叠机制**：provider_client `_merge_system_messages_to_front`（`packages/provider/src/octoagent/provider/provider_client.py:113`）把多条 system 合并成一条放最前（provider_client.py:149）。**实际送 provider 的前缀 = [合并后单条 system] + [history 前段稳定消息]**。这印证了 CLAUDE.local.md "provider_client 已折叠 system，压缩打断 prefix cache 线上不复现"的结论——Block 组装与 history 是分层的。

**tool 结果卸载为 artifact 的能力已存在**：`LargeOutputHandler`（AfterHook，`packages/tooling/src/octoagent/tooling/hooks_legacy.py:141`）。超阈值时 head_tail 截断 + `_store_as_artifact`（hooks_legacy.py:219/241），回写 `artifact_ref` + `truncated=True`（hooks_legacy.py:233-238）。runner 的 `_build_tool_feedback` 也已会用 `[artifact:xxx] prefix...` 占位（runner.py:706-707）。**但 hooks_legacy.py:148 明确：「ArtifactStore 仅用于审计存档，不作为 LLM 恢复完整内容的途径」**——这正是项 3 要打破的限制。

### 缝 / gap

1. **model_client 历史层无 tail eviction**：`_maybe_compact_history` 是 no-op（provider_model_client.py:288），旧 tool 结果永久留在 history 直到 provider 溢出。项 2 要在此实现"按 tool_call_id 的确定性 tail eviction，旧 tool 结果替换为稳定占位"。
2. **占位需稳定可缓存**：现有 `[artifact:xxx]` 占位（runner.py:707）是在 feedback 构造时一次性决定的；tail eviction 需要的是**事后改写历史中已有的 tool 消息**为占位——这是"改写"而非"追加"。
3. **ConversationTurn 无 tool_call_id**：`ConversationTurn`（context_compaction.py:120）有 `artifact_ref`（context_compaction.py:126）但**无 `tool_call_id` 字段**——若未来要在 (B) 层也做 id 级 eviction 需扩字段（当前 (B) 层无 tool 结果，暂不需要）。

### 可复用的现有抽象

- `_append_feedback_to_history`（provider_model_client.py:165）的 `tool_call_id` 去重逻辑（provider_model_client.py:170）——eviction 可在同函数或 `_maybe_compact_history`（provider_model_client.py:267，当前空壳）落地。
- `LargeOutputHandler` + `artifact_ref`（hooks_legacy.py:236）：占位文本可指向已存的 artifact（`[已折叠，见 artifact:xxx]`），与项 3 read-back 联动。
- `get_artifact_content`（store/protocols.py:99）：占位指向的 artifact 内容可被 read-back 取回。

### 实现风险（最高项）

- **prefix-cache 打断是本 Feature 最高风险**。前缀 = [合并 system] + [history 前段]（provider_client.py:113-149）。tail eviction 若**只在 history 尾部**替换最旧的几条 tool 结果为占位、且占位串**确定性稳定**（同 tool_call_id 永远生成同一占位），则被改写的是"前段"，**会让其后所有消息的 KV cache 失效**——这正是要避免的中段改写。
- **正确做法约束**（需在 GATE_DESIGN 明确）：eviction 必须保证"一旦某 tool_call_id 被折叠成占位，它在所有后续轮次保持同一占位、不再变回原文、不再二次改写"，使前缀单调收敛。`确定性 tail eviction` 的"tail"应理解为"折叠的是历史尾部新进入预算压力的部分前的旧块，但折叠后位置不动、内容冻结"。⚠️ 若实现成"每轮重算哪些该折叠、占位内容含可变计数/时间戳"，会反复打断前缀，比不做更糟。
- **与 F108 W8 协同**：AmbientRuntime 已挪到 Block 2 尾（harness-and-context.md:173），属 system prompt 组装层，**与 history 层 tail eviction 不在同一层、不直接冲突**；但二者都属"prefix-cache 治理"，GATE_DESIGN 应统一不变量表述，避免 history 层改写无意中触及 system 折叠假设。
- **resume 风险**：history 丢失时 step>1 会 raise `conversation_state_lost`（provider_model_client.py:378）。eviction 改写历史后若进 checkpoint，需保证 resume 重建的是已折叠版本，不能 tool_call/tool_result 配对错位。

---

## 项 3：artifact read-back + per-turn 预算

### 现状（带锚点）

**artifact_store 接口完整**：`ArtifactStore` Protocol（`packages/core/src/octoagent/core/store/protocols.py:80`）：
- `put_artifact(artifact, content)`（protocols.py:83）
- `get_artifact(artifact_id) -> Artifact|None`（protocols.py:91）
- **`get_artifact_content(artifact_id) -> bytes|None`（protocols.py:99）— read-back 底座已就绪**
- `list_artifacts_for_task`（protocols.py:95）

实现 `SqliteArtifactStore`（`packages/core/src/octoagent/core/store/artifact_store.py:64`），`get_artifact_content` 在 artifact_store.py:405（inline + 文件路径读取）。artifact id 是 `str(ULID())`（hooks_legacy.py:257）。

**read-back 现状 = 不存在**：唯一已有的"读 artifact 内容"调用在 ContextCompactionService 内部重建 assistant 内容（`_load_assistant_content`，context_compaction.py:839-845 调 `get_artifact_content`）——这是系统内部用，**不是 LLM 可调用的工具**。hooks_legacy.py:148 明确写「ArtifactStore 不作为 LLM 恢复完整内容的途径」——即设计上 LLM 当前无法读回被卸载的工具输出。

**filesystem 工具按路径读，不接受 artifact_ref**：`filesystem_read_text`（`apps/gateway/.../builtin_tools/filesystem_tools.py:94`）、`filesystem_list_dir`（filesystem_tools.py:39）、`filesystem_write_text`（filesystem_tools.py:136）。无 `artifact.read_content` 类工具。

**per-turn / 预算 hook 现状**：
- **per-tool AfterHook** 已有（`LargeOutputHandler`，hooks_legacy.py:186）——但它是**单工具单次**截断，不是 per-turn **跨工具聚合**预算。
- **runner 级 UsageTracker**（runner.py:105 `tracker = UsageTracker(...)`）累加 `request_tokens` / `response_tokens` / `tool_calls`（runner.py:167-171, 407）+ `tracker.check_limits(limits)`（runner.py:139）做循环熔断——这是**任务级**累计预算，非 per-turn 跨工具聚合。
- **ContextBudgetPlanner**（`apps/gateway/.../context_budget.py:53`）做上下文构建期的全局 token 分配（`BudgetAllocation`，context_budget.py:24，含 `conversation_budget` 等）——是**构建期静态规划**，非运行期 per-turn 工具输出聚合预算。
- F102 的 LLM token budget 截断（CLAUDE.local.md 记 `max_input ≤ 2000 字符 / max_output ≤ 512 token`）在 daily_routine，非通用工具路径。
- runner 有 `_call_hook(name, ...)` 通用钩子点（runner.py:144/653/692 等），是 per-turn 聚合 hook 的潜在落点。

### 缝 / gap

1. **缺 LLM 可调用的 read-back 工具**：artifact 已存（hooks_legacy.py:219）、`get_artifact_content` 已能取（protocols.py:99），但无工具暴露给 LLM。要么新增 `artifact.read_content(artifact_ref, offset?, limit?)`，要么让 filesystem 工具识别 `artifact:` 前缀。
2. **缺 per-turn 跨工具聚合预算**：现有预算要么单工具（LargeOutputHandler）要么任务级累计（UsageTracker）要么构建期静态（ContextBudgetPlanner）。无"单轮内所有 tool 输出加总超 N token 就触发聚合卸载/截断"的 hook。
3. **设计哲学冲突待裁**：hooks_legacy.py:148「artifact 仅审计、不供 LLM 恢复」与项 3「artifact read-back」直接矛盾——立项即推翻该约束，需 GATE_DESIGN 显式确认并同步文档。

### 可复用的现有抽象

- `get_artifact_content`（protocols.py:99）+ `SqliteArtifactStore`（artifact_store.py:64）：read-back 后端零新增。
- broker 已注入 `artifact_store`（broker.py:126/149），read-back 工具可走相同注入。
- `LargeOutputHandler._store_as_artifact`（hooks_legacy.py:241）：卸载侧已成熟，read-back 是其逆操作。
- AfterHook 协议（protocols.py:112）/ runner `_call_hook`（runner.py:653）：per-turn 预算 hook 的落点候选（per-tool 用 AfterHook，跨工具聚合用 runner 级 hook）。

### 实现风险

- 中。read-back 工具引入"LLM 可读回任意 artifact"——需走中央权限（broker.execute 步骤 3，broker.py:370）确认 artifact 归属 task/scope，防越权读其它 task 的 artifact（`get_artifact` 仅按 id 查，artifact_store.py:385，无 task 隔离校验，需在工具层加 task_id 比对）。
- per-turn 预算与项 2 tail eviction 可能重复治理同一压力源（工具输出膨胀），需统一策略避免双重截断。

---

## 三项耦合 / 排序依赖

| 关系 | 说明 | 锚点 |
|------|------|------|
| 项 2 ↔ 项 3 **强耦合** | 项 2 折叠占位 `[已折叠，见 artifact:xxx]` 需要 artifact 已存（项 3 卸载侧已由 `LargeOutputHandler` 提供）+ 占位指向的 artifact 能被 LLM read-back（项 3 read-back 工具）。**项 2 的占位若不可读回 = 信息丢失**，二者构成"卸载-占位-读回"闭环 | hooks_legacy.py:219 / runner.py:707 / protocols.py:99 |
| 项 1 ↔ 项 2/3 **弱耦合** | 项 1 在 broker BeforeHook（执行前），项 2/3 在 history 层 / artifact 层（执行后/上下文层），**触碰面几乎不重叠** | broker.py:396 vs provider_model_client.py:165 |
| 项 3 read-back ↔ 项 2 占位 | 共享 `artifact_ref` 语义与 `get_artifact_content` 后端，应统一 artifact_ref 格式与 task 隔离策略 | protocols.py:99 / artifact_store.py:385 |

### 代码触碰面汇总

- **项 1**：`packages/tooling/`（新增一个 BeforeHook + 可能扩 `ToolFeedbackMessage` 字段于 `packages/skills/models.py`）。broker 主干**不改**（复用现有 before-hook 拒绝路径）。
- **项 2**：`packages/skills/provider_model_client.py`（`_maybe_compact_history` / `_append_feedback_to_history`）。**不触碰** gateway ContextCompactionService（tool 结果不在其 turn 序列）。**不触碰** system prompt Block 组装（F108 W8 领域）。
- **项 3**：新增 read-back 工具于 `apps/gateway/.../builtin_tools/`（或 filesystem_tools.py），per-turn 预算 hook 于 `packages/skills/runner.py` 或 `packages/tooling/` AfterHook。复用 `packages/core/store/artifact_store.py`。

**触碰面重叠度**：项 1 与项 2/3 基本不重叠（不同层、不同包）；项 2 与项 3 在 `artifact_ref` 语义 + 占位/读回闭环上**强重叠**。

---

## 对 GATE_DESIGN 的输入

**1. 3 项代码触碰面是否重叠？**
- 项 1 与项 2/3：**几乎不重叠**。项 1 落在 broker BeforeHook（执行前、`packages/tooling`），项 2/3 落在 history/artifact 层（执行后、`packages/skills` + `packages/core`）。可完全独立开发。
- 项 2 与项 3：**强重叠**。共享"工具输出卸载为 artifact → history 占位 → LLM read-back"同一闭环，共用 `artifact_ref` 语义与 `get_artifact_content` 后端。项 2 的占位若没有项 3 的 read-back = 信息单向丢失。

**2. 哪项风险最高？**
- **项 2（tool_call_id tail eviction）风险最高**，因 prefix-cache 打断。需求本身已点明的护栏（"只追加不改写历史中段"）与"tail eviction 改写旧 tool 结果"存在内在张力：tail eviction 本质是改写历史里已有的 tool 消息。安全实现要求**折叠后位置不动 + 占位内容对同一 tool_call_id 永久确定性冻结**，使前缀单调收敛；任何"每轮重算 + 占位含可变内容"都会反复打断前缀（比不做更糟）。当前 `_maybe_compact_history` 是 no-op（provider_model_client.py:288），是干净的起点但也意味着零既有验证。
- 项 1 风险最低（复用 before-hook 拒绝路径，不碰 prefix-cache，唯一风险是校验严格度误拒）。项 3 中等（read-back 越权读 + 与项 2 双重截断需统一）。

**3. 一把做还是拆 phase？（给依据，不替用户拍板）**
- **建议拆 2 个 phase，依据如下**（最终由用户拍板）：
  - **Phase 1 = 项 1（独立、低风险、可立即闭环）**：与项 2/3 零重叠，BeforeHook 落点清晰，无 prefix-cache 风险，可先合入建立信心。
  - **Phase 2 = 项 2 + 项 3 一起做**：二者强耦合（卸载-占位-读回闭环），分开做会导致"项 2 折叠出无法读回的占位"或"项 3 read-back 无折叠场景可服务"的半成品。一起做能在同一 GATE 内统一 `artifact_ref` 语义、占位格式、task 隔离与 prefix-cache 不变量。
  - **Phase 2 必须前置一个 prefix-cache 不变量设计闸**：在写代码前明确"确定性占位生成规则 + 折叠单调收敛证明 + 与 F108 W8 system 折叠的边界"，并与 `harness-and-context.md:172-178` 的不变量表述合并。建议 Phase 2 命中 CLAUDE.local.md「重大架构变更」节点，走多评审 panel（Codex + Opus）。

**需进一步确认项**：
- ① `get_artifact` 无 task 隔离（artifact_store.py:385 仅按 id 查）——read-back / 占位读回的 task 归属校验落在工具层还是 store 层，需设计期定。
- ② provider_client 各 transport（chat/responses/anthropic）对 history 中 `role:tool` 消息的 prefix-cache 行为是否一致——本次未逐 transport 验证 KV-cache 边界，Phase 2 实施前需对真实 provider 实测确认占位改写不触发整段重算。
