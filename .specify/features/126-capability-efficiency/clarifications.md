# F126 Capability 效率改进 — 澄清决议（clarifications.md）

- **Feature ID**: F126 / Slug: capability-efficiency
- **澄清范围**: spec.md §7 待澄清点 C1–C5（5 个**实现级设计决策**）
- **处理策略**: 不单独打断用户；本文件给出**推荐解 + 依据 + 备选与取舍**，默认选最稳妥项；随后在 **GATE_DESIGN 硬门**一次性呈给用户确认/微调（用户已在 pre-spec 拍板宏观决策：3 项一把做 + KV-cache 实测硬前置门）。
- **依据来源**: spec.md / research/tech-research.md（含全部 file:line 锚点）/ Constitution #3/#9/#10。

> 散文中文 / 代码标识符英文 / 英文技术术语保原文。

---

## C1 — 项 1 校验严格度策略

| 维度 | 内容 |
|------|------|
| **推荐解** | **宽松校验 + 逐工具白名单豁免机制**。BeforeHook 默认仅校验：① `required` 字段是否齐全；② 顶层属性的 `type`（含 enum 顶层校验）。**不做** `additionalProperties:false` 拒多余字段、**不递归校验深层 nested**、**不强制 format/pattern**。再提供一个可选的 per-tool opt-out 标记（如 `ToolMeta` 上的 `skip_arg_validation: bool = False` 或注册期声明），让"工具体内自己做 coerce/默认值/复杂 validate"的工具能整体跳过预校验。 |
| **依据** | tech-research.md:54 明确：JSON Schema 校验若比工具体内实际宽容度更严（工具体内做 coerce/默认值填充），会误拒合法调用——FR-1.5/AC-1.3 的硬约束就是"不得误拒"。宽松校验只拦最高频、最确定的失败（缺 required + 顶层 type 错），这两类在工具体内几乎不可能被"宽容掉"，误拒风险趋零。Constitution #3：校验源必须是 LLM 看到的同一份 `parameters_json_schema`（schema.py:122 / provider_model_client.py:254），宽松校验仍从该 schema 提取 required+顶层 type，不另建事实源（满足 FR-1.3/AC-1.4）。白名单豁免是**纵深保险**：即使某工具的 required 声明与体内实际宽容度不一致，可显式标记跳过，避免 hardcode 例外逻辑（符合 #9 机制非规则）。 |
| **备选** | (B) **严格完整 JSON Schema validate**（jsonschema.validate 全量）：能拦更多错误、反馈更全，但直接踩 FR-1.5 误拒风险，且 `additionalProperties` 默认行为对 coerce 类工具杀伤大。**取舍**：项 1 是 3 项里 ROI 最直接、风险最低的（US-1.1 P1），用严格校验把它变成高误拒风险得不偿失。(C) 宽松但**无豁免机制**：实现更简，但一旦出现 required 声明与体内宽容度不一致的工具，只能改 schema 或硬编码例外，破坏统一性。 |
| **风险** | 宽松校验**漏拦**深层 nested 字段错误（这类仍走原 except 兜底路径 broker.py:478-493，回退到现状非结构化反馈，不劣化）。白名单若被滥用（大量工具 opt-out）会架空项 1 价值——缓解：opt-out 默认 False，仅在实测确认误拒的工具上开，且 completion-report 列出 opt-out 清单。 |
| **关联 FR-AC** | FR-1.1 / FR-1.3 / FR-1.5；AC-1.1 / AC-1.3 / AC-1.4。需新增覆盖"豁免工具不被预校验"的 test（建议挂 `test_schema_validation_hook.py::test_optout_tool_skips_validation`）。 |

---

## C2 — 项 3 read-back 工具形态

| 维度 | 内容 |
|------|------|
| **推荐解** | **新增独立工具 `artifact.read_content(artifact_ref, offset?, limit?)`**，后端复用 `get_artifact_content`（store/protocols.py:99），落在 `apps/gateway/.../builtin_tools/`（新文件 `artifact_tools.py` 或并入既有 builtin_tools 目录），走 broker 注入的 `artifact_store`（broker.py:126/149）。 |
| **依据** | ① **语义清晰（Constitution #3 工具即契约）**：独立工具有自己干净的 `parameters_json_schema`——`artifact_ref` / `offset` / `limit` 三参数语义明确，LLM 一眼知道这是"读回被折叠的 artifact"。② **关注点分离**：artifact 是审计/卸载域的对象，与 filesystem 路径读（filesystem_tools.py:94，按文件系统路径语义）是两个领域；混进 filesystem 工具会让 `filesystem_read_text` 的 schema/语义变脏（需在 docstring 解释"也能读 artifact: 前缀"），违反单一职责。③ **#9 机制非规则**：独立工具不需要在 filesystem 工具里加 `if path.startswith("artifact:")` 这类前缀嗅探分支（硬编码字符串识别正是 #9 要避免的规则式 dispatch）。④ 占位串与 read-back 共享 `artifact_ref` 格式（SD-2/FR-2.6），独立工具命名 `artifact.read_content` 与占位 `artifact:<ref>` 语义自然对齐。 |
| **备选** | (B) **扩展 filesystem 工具识别 `artifact:` 前缀**：复用现有读路径、少一个工具。**取舍**：复用收益小（后端都是 `get_artifact_content` 一行调用），代价是 filesystem 工具 schema 语义污染 + 前缀字符串嗅探分支（踩 #9）+ filesystem 的 offset/limit 是按"字符/行"还是"字节"语义与 artifact 分页语义可能不一致。综合判 (A) 优。 |
| **风险** | 新增工具增加工具集大小（影响 prefix-cache 工具侧不变量 harness-and-context.md:168——但工具集稳定排序+静态注入，新增 1 个工具是一次性前缀变化，非每轮 volatile，可接受）。read-back 工具的 offset/limit 分页语义须明确（建议按**字节** offset/limit，与 `get_artifact_content -> bytes` 对齐，避免编码歧义；返回时按 UTF-8 安全边界截断或返回 bytes 解码结果）。 |
| **关联 FR-AC** | FR-3.1 / FR-3.2；AC-3.1 / AC-3.2 / AC-LOOP-1（占位读回闭环）。test 落点 `apps/gateway/tests/test_artifact_read_back_tool.py`。 |

---

## C3 — 项 3 per-turn 预算（SHOULD / P2）

| 维度 | 内容 |
|------|------|
| **推荐解** | **保守默认：阈值可配 + 默认动作 = 先告警 + 聚合卸载为单/多 artifact 占位，落点选 runner `_call_hook`（runner.py:653）**。具体：① 阈值 = 单轮内所有 tool 输出 token 加总，默认阈值取一个保守大值（建议 `per_turn_tool_output_budget`，默认 ≈ 单轮 8000 token，可配，env/config 覆盖）；② 触发动作分两级——首先 emit 告警事件（#2 Everything is an Event），然后对**超额部分**复用 `LargeOutputHandler._store_as_artifact`（hooks_legacy.py:241）卸载为 artifact 并替换为 `artifact:<ref>` 占位（与项 2 占位格式统一，SD-4/FR-3.5）；③ 落点 runner `_call_hook` 而非 AfterHook，因为 AfterHook（protocols.py:112）是**单工具单次**粒度（per-tool），而 per-turn 是**跨工具聚合**——必须在"该轮所有 tool call 都执行完"的 runner 层（runner.py:642 `_execute_tool_calls` 之后）才能加总。 |
| **依据** | ① FR-3.4 是 **SHOULD/P2**（US-3.2 P2），不应过度设计——保守默认（高阈值 + 告警优先 + 卸载而非粗暴截断）让它默认几乎不误伤，仅在真异常膨胀时兜底。② **避免双重截断（SD-4/FR-3.5）**：per-turn 预算与项 2 tail eviction 治理同一压力源，统一走 `artifact:<ref>` 卸载占位语义、统一 `LargeOutputHandler` 卸载后端，不引入第二套截断；项 2 是 history 层（跨轮，旧块折叠），per-turn 是单轮入口（新输出膨胀），两者作用窗口不重叠（项 2 折旧、项 3 拦新），治理同一语义不打架。③ runner `_call_hook` 落点：research.py:126/139 已确认 runner 有通用 `_call_hook` 钩子点且 UsageTracker（任务级累计）也在 runner 层，per-turn 聚合放 runner 与现有预算治理同层、可读到该轮全部 tool 结果。 |
| **备选** | (B) 落 **AfterHook**：单工具粒度，做不到跨工具加总（除非 AfterHook 内自持累加器，但那是把 per-turn 状态塞进 per-tool hook，职责错位）。**取舍**：runner 层天然有"轮"边界，更自然。(C) 触发动作=**逐工具截断**：粗暴、丢信息且不可读回（违背项 2/3 "卸载可读回"哲学）。(D) **仅告警不卸载**：最保守但不解决膨胀。综合：卸载为 artifact 既兜底又保留可读回（read-back），优于截断。 |
| **风险** | per-turn 阈值与项 2 history 阈值若各自独立配置，可能出现"单轮没超 per-turn、但跨轮累计触发项 2"或反之的边界——缓解：两者共用同一 `artifact:<ref>` 占位与卸载后端，且文档（harness-and-context.md）统一说明两者作用窗口。因是 P2，若实现复杂度超预期，可在 GATE_DESIGN 决定降级为"仅 emit 告警事件 + 不自动卸载"的最小版（AC-3.4 相应调整）。 |
| **关联 FR-AC** | FR-3.4 / FR-3.5（均 SHOULD）；AC-3.4（P2）。test 落点 `packages/skills/tests/test_per_turn_budget_hook.py`。 |

---

## C4 — 项 2 占位文本确定性格式

| 维度 | 内容 |
|------|------|
| **推荐解** | **占位串 = `[已折叠，见 artifact:<artifact_id>（工具 <tool_name>，原始 <N> 字节）]`，其中所有插值元素均为折叠时刻即冻结的稳定值**：① `<artifact_id>` = 卸载时生成的 ULID（hooks_legacy.py:257，一旦生成永不变）；② `<tool_name>` = 该 tool_call 的工具名（该 tool_call_id 的固有属性，不变）；③ `<N>` = artifact 原始内容**字节数**（卸载时刻测定的静态值，不变）。**禁止**任何含"当前轮次序号 / 当前时间戳 / 已折叠计数 / 剩余预算 / 每轮重算结果"的内容。占位与 `tool_call_id` 一一绑定，首次折叠时构造一次并随历史持久化，后续轮次直接复用同一字符串、不重新格式化。 |
| **依据** | FR-2.2/AC-2.1 硬约束：同一 `tool_call_id` 在所有后续轮次占位串**字节级一致**、位置不动、不含可变计数/时间戳。推荐格式三个插值元素（artifact_id / tool_name / 字节数）全部是"折叠时刻冻结的稳定值"——artifact_id 是 ULID 生成即定，tool_name 是 tool_call 固有属性，字节数是卸载时的静态测量。**关键工程纪律**：占位串必须在首次折叠时**构造一次并写入历史**（成为该 tool 消息的新 content），后续 compaction 检测到该 content 已是占位形态则跳过不重构——而非每轮按当前状态重新拼字符串（重拼即埋下引入可变内容的风险，tech-research.md:99/173 警告"每轮重算+占位含可变内容比不做更糟"）。包含 tool_name + 字节数是为给 LLM 足够语义判断是否需 read-back（vs 纯 `[artifact:<ref>]` 信息太少），同时这两者都稳定，不破坏冻结。 |
| **备选** | (B) **最小占位 `[已折叠，见 artifact:<id>]`**（不含 tool_name/字节数）：最不可能出错（插值元素最少），但 LLM 看不到"这是什么工具的多大输出"，read-back 决策信息不足。**取舍**：tool_name+字节数都是稳定值，加上它们不破坏确定性却提升可用性，推荐 (A)。(C) 含**原始内容 head 预览**（如现状 runner.py:707 `[artifact:xxx] prefix...`）：prefix 截取本身确定（取卸载时固定的前 K 字节），但增加占位长度且需确保 prefix 也在折叠时冻结——可作为 (A) 的可选增强，若 GATE_DESIGN 认为需要预览再加，默认不含以最小化可变面。 |
| **风险** | 字节数 `<N>` 须确保取的是**原始内容字节数**而非 artifact 存储后大小或截断后大小（须在卸载时刻一次性记录到 artifact 元数据或占位构造点，避免后续重测得到不同值）。中文占位串本身是固定字面量，无 i18n 切换风险（系统单语言）。须有测试断言"同一 tool_call_id 连续 N 轮 compaction 后占位 `==` 字节级相等"。 |
| **关联 FR-AC** | FR-2.2 / FR-2.6（artifact_ref 格式与项 3 read-back 统一）；AC-2.1（确定性冻结占位）/ AC-LOOP-1。test 落点 `test_provider_model_client_tail_eviction.py::test_deterministic_frozen_placeholder`。 |

---

## C5 — artifact task 隔离落点

| 维度 | 内容 |
|------|------|
| **推荐解** | **主隔离在工具层（read-back 工具内 + 中央权限决策 broker.py:370）；store 层 `get_artifact` 加 task 参数作为可选纵深防御**。即：① read-back 工具执行时，权限检查走 broker 步骤 3 的 `check_permission`（broker.py:370），由中央权限决策判定调用方 task/scope 是否有权读该 artifact（Constitution #10 权限统一入口）；② 工具层在取回 artifact 后比对 `artifact.task_id == 当前 task_id`，不匹配则拒绝并 emit 失败事件（AC-3.2）；③ store 层**可选**为 `get_artifact`/`get_artifact_content` 增加 task 参数（artifact_store.py:385/405），在 SQL 查询层加 `WHERE task_id = ?` 作为**纵深防御第二道**——但这是次要项，不是隔离的主入口。 |
| **依据** | **Constitution #10 Policy-Driven Access 明确要求权限判断收敛到单一入口（中央 `check_permission`），工具层不得自行做路径/权限拦截**。因此**主隔离逻辑必须走中央权限决策（broker.py:370）**，不能让 store 层成为权限语义的事实源（否则权限决策散落两处，违反 #10）。工具层 task_id 比对是"业务正确性校验"（确认 artifact 归属），与中央权限"是否允许读"是两层：权限决策（能不能读 task X 的 artifact）走中央，归属确认（这个 id 是不是 task X 的）可在工具层。store 层加 task 参数的价值是**纵深防御**（即使工具层漏判，SQL 层也查不到跨 task 的 artifact），但 tech-research.md:143/183 指出 `get_artifact` 当前仅按 id 查、无 task 隔离——补 task 参数会**触及 store Protocol 接口**（protocols.py:91/99 签名变更，影响所有 caller 含 ContextCompactionService context_compaction.py:839），触碰面大。 |
| **备选** | (B) **纯 store 层隔离**（`get_artifact` 强制要求 task 参数，无 task 不给查）：最彻底、跨 task 物理隔离，但①违反 #10（把权限语义下沉到 store 层，权限不再单一入口）；②接口破坏性变更影响所有 caller（含系统内部非 LLM 调用如 ContextCompactionService 重建 assistant 内容 context_compaction.py:839-845，这些内部调用未必有 task 上下文）。**取舍**：彻底性诱人但代价是违宪 + 大面积接口改。(C) **纯工具层比对**（store 不动）：最小触碰面，但缺纵深防御——工具层一旦漏判即越权。综合：(A) 折中——权限走中央（合宪）+ 工具层归属比对（最小可行）+ store 层 task 参数作为**可选**纵深增强（GATE_DESIGN 决定是否纳入本批次，因它触接口）。 |
| **风险** | 若只做工具层 + 中央权限不做 store 纵深防御，单点失效面集中在工具层比对逻辑——须有 AC-3.2 强测试覆盖跨 task 读拒绝。store 层加 task 参数若纳入，须处理"内部非 LLM caller 无 task 上下文"的兼容（task 参数设为 `Optional`，None 表示内部信任调用、跳过 task 过滤——但这又削弱了纵深防御的"强制"性，须权衡）。**这是 C1-C5 中接口触碰面争议最大的点，建议 GATE_DESIGN 重点拍板是否动 store 接口。** |
| **关联 FR-AC** | FR-3.2；AC-3.2（跨 task 读拒绝）/ AC-LOOP-1。test 落点 `apps/gateway/tests/test_artifact_read_back_tool.py::test_cross_task_read_denied`。 |

---

## 需用户在 GATE_DESIGN 重点确认的项（分歧最大 / 最该用户拍板）

> 其余 C1/C3/C4 推荐解风险低、默认稳妥，GATE_DESIGN 可快速确认；以下两项涉及**接口触碰面 / 架构语义**，最该用户显式拍板：

1. **C5 — 是否触 store 接口做 task 隔离纵深防御**（最高争议）。推荐"工具层主隔离（走中央权限 #10）+ store 层 task 参数作可选纵深防御"。**需用户拍板：本批次是否纳入 store 接口改造**（纳入=更彻底但触 protocols.py 签名 + 影响内部 caller 兼容；不纳入=最小可行但纵深防御缺位，靠工具层单点 + AC-3.2 强测试兜底）。权衡核心是 Constitution #10 合宪性（已通过"权限走中央"保证）vs 物理隔离彻底性。

2. **C2 — read-back 工具形态**（语义 vs 复用之争）。推荐独立工具 `artifact.read_content`（语义清晰 + 避免 #9 前缀嗅探）。**需用户拍板：是否接受新增一个 builtin 工具**（增工具集大小、一次性前缀变化）vs 复用 filesystem 工具（少一个工具但 schema 语义污染 + 字符串前缀分支）。此点影响工具契约清晰度与 prefix-cache 工具侧前缀。

---

## GATE_DESIGN 拍板结果（用户确认）

- **C5 → 工具层主隔离 + store 层 `Optional` task 参数纵深防御「纳入本批次」**。store `get_artifact`/`get_artifact_content` 加 `Optional` task 参数 + SQL `WHERE task_id`；`task=None` 保内部 caller（context_compaction.py:839）零变更；read-back 工具必传 task。新增 store 层 task 隔离单测。
- **C2 → 新增独立工具 `artifact.read_content`**（确认，不扩展 filesystem 工具）。
- C1/C3/C4 按 clarify 推荐解默认锁定（宽松校验+豁免 / per-turn 8k 默认+告警再卸载+runner _call_hook / 占位三冻结值首折叠构造一次）。
- GATE_DESIGN（硬门）通过，进入 plan/tasks/implement。
