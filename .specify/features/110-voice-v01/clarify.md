# F110 语音 v0.1 — 需求澄清记录

- **Feature**: F110 voice-v01
- **基线 spec**: spec.md（2026-06-22 初版）
- **澄清日期**: 2026-06-22

---

## 总结

**无阻塞性架构歧义。** spec 整体清晰，H1 边界定义明确，AC↔test 绑定完整，降级矩阵覆盖 8 类失败面。检测到 **1 个 high 歧义**（D2/D3 自动标记的再触发语义未定义）、**3 个 medium 歧义**（voice_mode 持久化期望、/voice 命令的 `_record_conversation_binding` 写入路径、`notify_task_result` 如何通过 task_id 拿到 chat binding）、**1 个 low**（DR-D6 `input_kind=voice` 实现门槛未量化）。

D1/D4/D5 已在 spec §2 作为 GATE_DESIGN 标记，待用户拍板，不在此列。

---

## 歧义清单

### [HIGH-1] D2/D3 自动标记的「再触发」语义未定义

**描述**：spec D2-C（混合模式）规定「用户发 voice 自动标记 voice_mode=True，`/voice off` 关闭，`/voice on` 重开」。但以下场景未定义：

> 用户先发 voice（voice_mode=True）→ 发 `/voice off`（voice_mode=False）→ 再次发 voice message

此时 `_handle_voice_message` 成功后，D3 规则要求「入站 voice 追加写 voice_mode=True」，但用户刚刚显式 `/voice off`——这会**静默覆盖用户的显式关闭**，行为反直觉。

同样地：用户先 `/voice off`，后发 voice，是否自动重开？

**影响**：决定 `_handle_voice_message` → `_record_conversation_binding` 路径中是否要加「voice_mode 已被显式关闭时跳过自动标记」的守卫逻辑，影响 AC-D1 的实现与测试设计。

**建议消解**：
- **选项 A（推荐，最直觉）**：自动标记仅在 `voice_mode` 未被显式设置（`None`/key 缺失）时生效；一旦用户发过 `/voice off`，入站 voice 不再自动重开，需用户显式 `/voice on`。实现：binding 区分「未设置」vs「显式 False」。
- **选项 B（简单）**：每次 voice 入站无条件写 `voice_mode=True`，`/voice off` 只能临时关到下次 voice 入站。用户发 voice 永远意味着「我想要语音回复」。
- **选项 C**：取消自动标记（降级为 D2-A 纯命令），`/voice on` 是唯一开关——最保守但 UX 摩擦最大。

**需 GATE_DESIGN 拍板**（非自动解决）：涉及用户意图语义，选 A/B 决定是否加额外 boolean 状态字段，影响数据模型与测试用例。

---

### [MEDIUM-1] `notify_task_result` 通过 task_id 查 ConversationBinding 的路径未明确

**描述**：spec FR-B1 和 FR-D3 要求 `notify_task_result` 通过 `_resolve_reply_target(task_id)` 拿 `chat_id`，再查 binding store 读 `voice_mode`。但 `_resolve_reply_target` 返回 `dict{chat_id, reply_to_message_id, message_thread_id}`（research §1.1），而 binding store 的查询 key 是 `(platform, conversation_id)`——spec 未说明 `conversation_id` 是否等于 `chat_id`，以及用哪个 `platform` 字符串（`"telegram"` 硬编码？）。

**影响**：plan 阶段需要实际读 `binding_store.get_binding(platform, conversation_id)` 签名确认，若 conversation_id ≠ chat_id 则需额外 mapping。

**建议消解**：[AUTO-CLARIFIED: conversation_id == chat_id（Telegram 语境），platform == "telegram" 硬编码——与 `_record_conversation_binding` 写入时完全一致，plan 阶段读 `telegram.py:584` 附近代码确认即可] 此处不需 GATE，plan 侦察可闭环。

---

### [MEDIUM-2] voice_mode 持久化跨重启是「期望」还是「非期望」未拍板

**描述**：spec §9 已知约束提到「voice_mode 标记持久化在 binding（跨 bot restart 保留），不新建幂等机制」，research §7 也将此列为「待 spec 定义的边界问题」，但 spec 正文未明确说明这是设计选择还是实现副产品。

ConversationBinding 落 SQLite（持久），因此 voice_mode 自然跨重启保留——但用户可能期望「重启后语音模式重置」（避免「忘了关 voice 结果一直在语音模式」）。

**影响**：若期望重启重置，则需要在 bot 启动时清除所有 binding 的 voice_mode 字段，或改存 in-memory。若期望持久则无需额外代码。

**建议消解**：[AUTO-CLARIFIED: 持久（跨重启保留）是正确语义——voice session 连续性是 F110 核心价值，与 F093 AgentSession 持久化哲学一致；用户若不需要语音可显式 /voice off，这是 US-2 验收场景的隐含期望] 记入 plan 的 FR 实现说明即可，无需 GATE。

---

### [MEDIUM-3] `/voice on|off` 写 binding 的实现路径与 `_handle_voice_message` 路径的 binding store 访问方式未对齐

**描述**：`_record_conversation_binding`（`telegram.py:584`）在 voice 入站时被调用，写入 metadata 含 `last_message_thread_id` 等字段。`/voice on|off` 控制命令（FR-D2）需要「查写 `ConversationBinding.metadata["voice_mode"]`」——但当前 `_handle_control_command`（`telegram.py:635`）路径上是否有现成的 binding store 访问点，spec 未说明。

具体疑问：`/voice on|off` 是否需要先 `get_binding` 再 merge 已有 metadata 字段（避免覆盖 `last_message_thread_id` 等）再 `upsert_runtime_binding`？还是 binding store 的 upsert 支持 partial metadata 更新（只更新指定 key）？

**影响**：决定 FR-D2 实现是否需要额外的 read-modify-write 流程，影响测试 setup（`test_voice_off_command_clears_voice_mode` 需要正确初始化 binding）。

**建议消解**：[AUTO-CLARIFIED: plan 阶段读 `binding_store.upsert_runtime_binding` 签名；若不支持 partial update，则实现为 get→merge metadata→upsert 三步；若支持 partial update 则直接调用。此为实现细节，不影响 AC 语义，plan 侦察可闭环] 无需 GATE。

---

### [LOW-1] FR-D6「`input_kind=voice` turn metadata 标注」的实现门槛未量化

**描述**：FR-D6 用 `SHOULD` 级别说「若成本低（一行 dict 写入）则实现，否则留后续」，但 spec/AC 未给出可操作的决策标准，也没有对应 AC 绑定 test。plan 阶段实施者需自行判断。

**影响**：若实现，需确认 turn metadata 写入点（`_handle_voice_message` 路径中何处），以及是否需要新的字段约定。若不实现，则 FR-D6 成为无法追踪的空 FR。

**建议消解**：[AUTO-CLARIFIED: 评估标准定为「改动范围 ≤ 3 行且不新增外部依赖」；满足则实现并在 `test_telegram_voice.py` 补一条 assertion（归入 AC-D5 的幂等测试或单独 low-priority case）；不满足则在 completion-report 的 deferred 区记录] plan 阶段可按此标准执行。

---

## 已确认清晰的关键点（plan 可直接依赖）

1. **H1 边界完全正确**：TTS 是 `notify_task_result` 出站后处理，voice session 仅是渠道层 ConversationBinding.metadata 标记，`AgentSession`/`AgentSessionKind` 零修改，无新 Agent 模式。
2. **AC↔test 绑定完整**：所有 P1 AC（A1-A4、B1-B5、C1-C5、D1-D7、E1-E2、Z1-Z2）均有明确 test 文件路径 + 函数名，verify 阶段可机械校验。
3. **降级矩阵完整**：8 类失败面全覆盖，reason 码清晰（`tts_unavailable`/`synthesize_error`/`empty_audio`/`encode_error`/`send_voice_failed`/`tts_timeout`），与 AC-E2 绑定。
4. **幂等边界清晰**：每轮独立 `_build_idempotency_key`（update_id 粒度），voice session 连续性依赖 binding 持久化而非新幂等机制，AC-D5 定义到位。
5. **范围边界明确**：实时双工、其他渠道、多模型选择、音频原文持久化全部显式排除。
6. **optional 依赖策略清晰**：`piper-tts` 进 `[voice]` extra，lazy import，core 不污染，与 F109 faster-whisper 同范式。
7. **D4 PyAV 路径**：plan Phase 0 必实测验证，备选 PyOgg，判断标准明确（`libopus` 可用性）。
8. **`FR-B3` 降级语义**：TTS 失败时 Agent 回复内容不丢弃，仅换成文字形式发出，spec 表述明确。

---

## 需 GATE_DESIGN 拍板的问题（汇总给编排器）

### GATE-1（源自 HIGH-1）：D2-C 再触发语义

> **当用户显式 `/voice off` 后，再次发 voice message，是否自动重开 voice 模式？**
>
> - 选项 A（推荐）：不重开，需显式 `/voice on`。实现上区分「binding key 缺失/None（从未设置）」vs「显式 False（用户关闭）」。
> - 选项 B：每次 voice 入站无条件开。简单但可能违反用户意图。
>
> **此决策直接影响 AC-D1 的实现逻辑与测试用例，以及是否需要扩展 binding metadata schema。**

这是 spec 中唯一的真实歧义决策点，D1/D4/D5 已在 spec §2 标记为 GATE_DESIGN 待用户拍板（TTS 选型、格式转换、语言模型），此处不重复。
