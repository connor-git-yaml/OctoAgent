# F110 语音 v0.1 — Spec 质量检查表

- **Feature**: F110 voice-v01
- **Spec 版本**: spec.md（基线 `1cd2083f`）
- **检查日期**: 2026-06-22
- **检查人**: 质量检查表子代理

---

## 维度 1：完整性（US/AC/FR 覆盖三大能力 + 降级矩阵）

### 1.1 三大能力覆盖

| 能力块 | US | FR 组 | AC 组 | 结论 |
|--------|-----|-------|-------|------|
| TTS 服务层 | US-1（TTS 出站） | FR-A（A1-A8） | AC-A（A1-A4） | ✅ 覆盖完整：Protocol / Service / Backend / wiring / env / format 全有 FR |
| 出站接入（telegram.py） | US-1、US-3 | FR-B（B1-B5） | AC-B（B1-B6） | ✅ 覆盖完整：接入点 / 零变更约束 / 限范围 / 降级链 / 异常捕获 |
| voice session（连续性标记） | US-2、US-4 | FR-D（D1-D6） | AC-D（D1-D7） | ✅ 覆盖完整：自动标记 / 命令开关 / 多轮连续性 / 幂等 / 枚举不扩展 |

### 1.2 降级矩阵覆盖（§7 对账 §5 AC）

| 失败场景 | §7 有记录 | AC 绑定 | 日志 reason 码 |
|----------|-----------|---------|----------------|
| piper-tts 未安装 / is_available()=False | ✅ | AC-A1、AC-E2 | tts_unavailable |
| 模型文件缺失 / 初始化 raise | ✅ | AC-A1 隐含（lazy load 捕获） | tts_unavailable |
| synthesize 抛异常 | ✅ | AC-A2（synthesize_error） | synthesize_error |
| 合成返回空音频 | ✅ | AC-A2（empty_audio） | empty_audio |
| WAV→OGG/Opus 格式转换失败 | ✅ | AC-C5 | encode_error |
| send_voice Telegram API 失败 | ✅ | AC-B4 | send_voice_failed |
| ConversationBinding 查不到 / voice_mode 缺失 | ✅ | AC-D7 | —（无 reason 码，默认 False，不崩） |
| TTS 合成超时（asyncio.wait_for 30s） | ✅ | AC-B6（P2），FR-A4（MUST） | tts_timeout |

⚠️ **轻微缺口**：「模型文件缺失 / 初始化 raise」在 §7 降级矩阵有记录，但 AC-A1 只写了「piper-tts 未安装」，没有显式覆盖「安装了但模型 .onnx 缺失」这一分支。FR-A4 隐含（`is_available()` 模型缺失时返回 False），但无专属 AC 测试函数覆盖这一具体场景。

**整体判断：✅ 覆盖完整（含上述一处轻微缺口，不阻 GATE_DESIGN）**

---

## 维度 2：Constitution 对齐

### #5 Secrets（云 API key 不进上下文）

✅ spec §5 FR-E2 明确：「若 D1 反选云 API 后端：TTS API key MUST 走 CredentialStore/env，不进 LLM 上下文、不进日志」。默认 Piper 本地无凭证需求，不适用时有预备规则。

### #6 优雅降级（TTS 不可用退文字永不崩）

✅ 降级矩阵完整（8 类场景），FR-A3 / FR-B4 / FR-B5 / FR-E3 均有 MUST 级不崩要求；AC-E3 有显式测试绑定。

### #8 可观测（结构化日志不含音频原文）

✅ AC-E1 明确：「不含完整回复文本或音频 bytes」；FR-E1 明确「MUST NOT 记录完整回复文本或音频 bytes」。US-5 专门覆盖可观测性用户故事。

### #9 Agent 自主（不用硬编码关键词替代决策）

✅ `/voice on|off` 是渠道控制命令（`_handle_control_command` 路径），非 Agent 决策——与 #9「禁止用硬编码关键词替代 LLM 决策」不冲突。spec 明确「voice session 仍用主 Agent 现有 session 类型」，Agent 模型零侵入。FR-D5 明确「MUST NOT 扩展 AgentSessionKind 枚举」。

⚠️ **建议在 GATE_DESIGN 前补一句说明**：spec 可以在 FR-D2 或 §2-D2 段落显式注明「`/voice on|off` 属于渠道层控制命令，不经 Agent 决策环，不违反 Constitution #9」，避免实施者误解。现在只能从整体架构推断，不是显式表述。

### H1（TTS 出站后处理不侵入 Agent）

✅ spec §1 核心哲学段明确：「出站：主 Agent 回复 text → TTS → send_voice——语音是出站后处理，挂在 notify_task_result 渠道出站层，在主 Agent 回复之后」。FR-B2 MUST NOT 修改 `_build_result_text` 和 `_resolve_reply_target`。

**整体判断：✅ Constitution 对齐（含一处建议显式补注）**

---

## 维度 3：AC↔test 绑定

### P1 AC 绑定完整性检查

| AC | 优先级 | test 文件 | 测试函数 | 绑定完整？ |
|----|--------|----------|---------|-----------|
| AC-A1 | P1 | test_tts_service.py | test_tts_unavailable_when_lib_missing | ✅ |
| AC-A2 | P1 | test_tts_service.py | test_synthesize_ok / test_synthesize_error_returns_false / test_synthesize_empty_audio_returns_false | ✅（3 函数分别绑定 3 子场景） |
| AC-A3 | P1 | test_tts_service.py | test_tts_result_schema | ✅ |
| AC-A4 | P1 | test_tts_service.py | test_piper_backend_protocol_conformance | ✅ |
| AC-B1 | P1 | test_telegram_voice.py | test_notify_task_result_sends_voice_when_voice_mode | ✅ |
| AC-B2 | P1 | test_telegram_voice.py | test_notify_task_result_sends_text_when_voice_mode_off | ✅ |
| AC-B3 | P1 | test_telegram_voice.py | test_notify_task_result_degrades_to_text_on_tts_failure | ✅ |
| AC-B4 | P1 | test_telegram_voice.py | test_notify_task_result_degrades_to_text_on_send_voice_failure | ✅ |
| AC-B5 | P1 | test_telegram_voice.py | test_build_result_text_unchanged | ✅ |
| AC-C1 | P1 | test_telegram_voice.py | test_bot_client_send_voice_multipart | ✅ |
| AC-C2 | P1 | test_telegram_voice.py | test_bot_client_send_voice_optional_params | ✅ |
| AC-C3 | P1 | test_telegram_voice.py | test_bot_client_send_voice_raises_on_failure | ✅ |
| AC-C4 | P1 | test_tts_service.py | test_wav_to_ogg_opus_produces_valid_ogg | ✅ |
| AC-C5 | P1 | test_tts_service.py | test_wav_to_ogg_opus_failure_handled | ✅ |
| AC-D1 | P1 | test_telegram_voice.py | test_voice_message_sets_voice_mode | ✅ |
| AC-D2 | P1 | test_telegram_voice.py | test_voice_off_command_clears_voice_mode | ✅ |
| AC-D3 | P1 | test_telegram_voice.py | test_voice_on_command_sets_voice_mode | ✅ |
| AC-D4 | P1 | test_telegram_voice.py | test_voice_session_continuous_rounds | ✅ |
| AC-D5 | P1 | test_telegram_voice.py | test_voice_session_idempotent_replay | ✅ |
| AC-D6 | P1 | test_telegram_voice.py | test_voice_session_reuses_existing_agent_session | ✅ |
| AC-D7 | P1 | test_telegram_voice.py | test_voice_mode_defaults_to_false_if_missing | ✅ |
| AC-E1 | P1 | test_tts_service.py | test_tts_synthesis_observable | ✅ |
| AC-E2 | P1 | test_telegram_voice.py | test_tts_degrade_observable | ✅ |
| AC-Z1 | P1 硬不变量 | verify 阶段全量 pytest + e2e_smoke | — | ✅（verify 阶段机械校验，不绑具体函数） |
| AC-Z2 | P1 | test_telegram_voice.py | test_voice_roundtrip_e2e | ✅ |

### 测试文件命名一致性（F109 范式对照）

- F109 有 `test_stt_service.py`（STT 单测）和 `test_telegram_voice.py`（入站 voice 测试）。
- F110 新建 `test_tts_service.py`（TTS 单测，镜像 `test_stt_service.py`），扩展 `test_telegram_voice.py`（出站 TTS + voice_mode）。
- ✅ 命名范式一致，文件职责边界清晰。

**整体判断：✅ AC↔test 绑定完整，所有 P1 AC 均有具名测试函数**

---

## 维度 4：可测试性

### Given-When-Then 具体性

| AC | Given-When-Then 是否具体 |
|----|------------------------|
| AC-A1 | ✅ US-3 验收场景和 AC 描述均有明确 Given/When/Then |
| AC-B1~B4 | ✅ US-1 验收场景完整，voice_mode=True/False 两路明确 |
| AC-D1~D7 | ✅ US-2 验收场景覆盖 3 个具体路径（入站自动/文字继续/关闭） |

### 是否依赖真实 piper-tts 安装

✅ spec 明确引入 `FakeTtsBackend` stub（§5 开头说明，`test_tts_service.py` 包含 `FakeTtsBackend`），镜像 F109 `FakeSttService` 范式。所有单测可在无 piper-tts 真实安装的环境下运行。

⚠️ **AC-C4（WAV→OGG/Opus 输出 magic bytes `OggS`）**：此测试需要真实的 PyAV `libopus` 编码路径。spec 在 §9 和 research §2.4 已标注「plan 阶段实测验证」，但 AC-C4 描述未说明如何在无真实 PyAV libopus 的环境中处理（是 skip 还是另有 mock）。plan 阶段必须明确此点，否则 CI 环境可能跑不过。

**整体判断：✅ 可测试性良好（含一处 CI 环境依赖需在 plan 阶段明确）**

---

## 维度 5：0 regression 与 e2e_smoke 硬不变量

✅ AC-Z1 明确：「全量回归 0 regression vs `1cd2083f`；`pytest -m e2e_smoke` 8/8 PASS」。

✅ §11 验收门第 1 条就是「0 regression vs `1cd2083f`」，第 2 条「`pytest -m e2e_smoke` 8/8 PASS」。

✅ research §4（侦察块 4）提供了精确的 PYTHONPATH 锁定命令和基线对账方法，防止 worktree symlink 陷阱导致假 0 regression。

✅ AC-Z2 e2e voice 往返链（mock STT + FakeTtsService，端到端 `_ingest_update` → notify_task_result → send_voice）单独绑定测试函数。

**整体判断：✅ 硬不变量明确写入验收门**

---

## 维度 6：范围纪律

### 明确排除项（§8）

| 排除内容 | 是否明确 |
|----------|---------|
| 实时双工语音（WebRTC/WebSocket） | ✅ 明确排除 → v0.2 |
| 其他渠道语音出站（Web/Slack/Discord） | ✅ 明确排除 → v0.2 |
| 按 STT 语言自动多模型 TTS 选择 | ✅ 明确排除 |
| 说话人音色定制 / 个性化 TTS 模型 | ✅ 明确排除 |
| 音频原文持久化为 artifact | ✅ 明确排除（v0.1 不存） |
| Web 端音频上传 UI | ✅ 明确排除 |
| Telegram sendAudio 回退 | ✅ 明确排除（含理由：UX 差） |

### 是否有蔓延风险

✅ FR-D6（`input_kind=voice` turn metadata 标注）标注为「可选」（MAY），由 plan 阶段评估成本决定，不强制，不会拉大范围。

✅ voice session 通过 F093 AgentSession 复用而非新建路径，清楚说明不新建 Agent 模式。

**整体判断：✅ 范围纪律良好，无蔓延风险**

---

## 维度 7：F109 复用诚实度

### 真复用 vs 重造检查

| 项目 | F109 地基 | F110 是否真复用 |
|------|-----------|----------------|
| FasterWhisperBackend 单例/download/降级范式 | `faster_whisper_backend.py:95` `build_default_stt_service()` | ✅ FR-A4 明确「镜像 `build_default_stt_service()`」工厂函数；FR-A8 镜像 STT wiring（`octo_harness.py:510`） |
| lazy import optional 依赖范式 | F109 lazy import faster-whisper | ✅ FR-A5 「lazy import 在 backend 内；gateway 启动时不导入 piper」，明确沿用 F109 先例 |
| H1 入站预处理范式（不改 Agent） | `_handle_voice_message` → `context.text` → 主路径 | ✅ spec §1 明确「H1 出站后处理边界清晰，在主 Agent 回复之后」，不改入站链路 |
| asyncio.to_thread CPU-bound 卸载 | F109 STT 主路径用 to_thread | ✅ FR-A4 明确 PiperTtsBackend 用 `asyncio.to_thread` 卸载 CPU-bound 合成 |
| `SttBackend` Protocol 对称设计 | `stt.py:36` | ✅ FR-A2 「结构 MUST 对称 `SttBackend`（`stt.py:36`）」；AC-A4 断言 Protocol 对称性 |
| `_reply_voice_degrade` 降级范式 | `telegram.py:565` | ✅ research §5 「沿用 `_reply_voice_degrade` 风格」；spec §7 降级矩阵对齐 |
| STT 单测文件命名范式 | `test_stt_service.py` | ✅ F110 新建 `test_tts_service.py`（显式说明「镜像 test_stt_service.py`」） |
| env 配置路径策略（不动 yaml schema） | F109 全走 env | ✅ FR-A7 / D5 明确沿用 env 路径策略，不动 `octoagent.yaml` |
| `_build_idempotency_key` 单轮幂等 | `telegram.py:750` | ✅ FR-D4 「沿用 `_build_idempotency_key`，不新建幂等机制」；research §2.4 确认 |
| `test_telegram_voice.py` 扩展范式 | F109 已存在此文件 | ✅ spec 明确「F110 扩展」而非新建，复用 F109 入站测试文件 |

⚠️ **一处诚实缺口**：AC-C4（WAV→OGG/Opus `OggS` magic bytes 验证）依赖真实 PyAV libopus，而 F109 的 STT 测试全程可用 FakeSttService 零外部依赖。TTS 的格式转换测试无法纯 mock，这是 F110 相对 F109 的新测试挑战。spec 在 research §2.4 已标注「plan 阶段必做验证」，但 AC 层未说明此测试是否在 CI 中 skip 还是要求真实 codec 可用。

**整体判断：✅ F109 复用诚实，无重造嫌疑（含一处测试挑战需 plan 阶段澄清）**

---

## 关键缺口汇总

以下项在 GATE_DESIGN 前建议补全或注意，均不是阻断性缺陷，但若忽略可能影响实施质量：

### ⚠️ 建议 GATE_DESIGN 前补全的项

| # | 维度 | 缺口描述 | 严重度 | 建议 |
|---|------|---------|--------|------|
| 1 | 完整性（维度 1） | AC-A1 只测「piper-tts 未安装」，未显式覆盖「安装了但模型 .onnx 文件缺失」这一独立失败路径 | LOW | plan 阶段在 `test_piper_backend_protocol_conformance` 或新增 `test_piper_model_missing_is_unavailable` 覆盖此场景 |
| 2 | Constitution #9（维度 2） | spec 未显式注明「`/voice on|off` 是渠道控制命令不经 Agent 决策环，不违反 #9」 | LOW | 在 FR-D2 或 §2-D2 段落加一句「此命令不经 Agent LLM 决策，渠道层直接处理，不违反 Constitution #9」 |
| 3 | 可测试性（维度 4）/ F109 复用（维度 7） | AC-C4（WAV→OGG magic bytes 验证）依赖真实 PyAV libopus，无法纯 mock；CI 环境可能无此 codec | MEDIUM | plan Phase 0 实测 PyAV libopus 可用性后，在 AC-C4 注明「若 CI 无 libopus 则 pytest.skip，否则真跑」；或将 AC-C4 列为 plan 阶段决策点 |

### 不阻 GATE_DESIGN 的说明

上述 3 项中：
- #1 和 #2 是 LOW 级别，不影响 spec 结构正确性，可在 plan 阶段补。
- #3 是 MEDIUM 级别，但 research 已在 §2.4 标注「plan 阶段必做验证」，属于已知风险。

**spec 整体质量：PASS，可进入 GATE_DESIGN。**

---

## 总结

| 维度 | 结论 | 关键发现 |
|------|------|---------|
| 完整性 | ✅ | 三大能力 + 8 类降级场景全覆盖；1 处轻微缺口（模型缺失无专属 AC） |
| Constitution 对齐 | ✅ | #5/#6/#8/#9/H1 全通；建议补 #9 显式注明 |
| AC↔test 绑定 | ✅ | 25 条 P1 AC 全部绑定具名函数，文件命名与 F109 范式一致 |
| 可测试性 | ✅ | FakeTtsBackend 零依赖测，WAV→OGG 测试有 CI codec 依赖问题待 plan 明确 |
| 0 regression + e2e_smoke 硬不变量 | ✅ | AC-Z1 + §11 验收门明确写入 |
| 范围纪律 | ✅ | 7 项明确排除，无蔓延风险 |
| F109 复用诚实度 | ✅ | 10 项逐一确认复用，无重造，WAV→OGG 测试是唯一新挑战 |

**通过率：25/25 P1 AC 绑定 ✅ / 0 项硬阻断 / 3 项 LOW-MEDIUM 建议 plan 阶段补全**
