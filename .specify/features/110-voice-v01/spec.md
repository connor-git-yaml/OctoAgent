# F110 语音 v0.1 — 功能规范（spec.md）

- **Feature ID**: F110
- **Slug**: voice-v01
- **基线**: master HEAD `1cd2083f`（F109 语音 PoC STT only 已合入）
- **分支**: `feature/110-voice-v01` / worktree `F110-voice-v01`
- **性质**: **新能力（行为新增）**——M6 收官 Feature，STT + TTS + voice session 三合一
- **调研依据**: `.specify/features/110-voice-v01/research/tech-research.md`（TTS 选型 + 4 块代码侦察 + 降级矩阵 + D1-D5 决策点，含 file:line 锚点）
- **地基依赖**: F109（STT 地基）+ F093（Worker Full Session Parity，voice session 连续性）
- **代码根**: 实际代码在仓库根 `octoagent/` 子目录；本 spec 内 `file:line` 锚点相对仓库根、文件位于 `octoagent/` 下

> 散文中文 / 代码标识符英文 / 英文技术术语保原文。

---

## 1. 概述

F110 在 F109 语音 PoC 的基础上完成语音能力的另外两块：**TTS（文字转语音出站）**与 **voice session（多轮连续语音会话）**，形成"用户发语音 → 主 Agent 推理 → bot 回语音"的完整往返闭环。

**核心哲学 H1（语音是渠道层预/后处理，不改 Agent 模型）**：

- **入站**（F109 已建）：`voice → STT → context.text → 主 Agent chat 主路径`——语音只是输入预处理，转写文字后下游与文字消息完全同路。
- **出站**（F110 新建）：`主 Agent 回复 text → TTS → send_voice`——语音是出站后处理，挂在 `notify_task_result`（`telegram.py:872`）渠道出站层，**在主 Agent 回复之后**。
- **语音会话连续性**（F110 新建）：voice session 多轮连续性复用 F093 的 `AgentSession`，渠道层通过 `ConversationBinding.metadata["voice_mode"]` 标记该 chat 是否处于语音模式。**绝不为 voice session 新建 Agent 模式、决策环或新主路径**。

**v0.1 范围**：异步多轮（每轮 voice → STT → Agent → TTS → voice，复用现有 polling/webhook + F093 session 连续性）。实时双工明确留 v0.2。

---

## 2. 决策点（GATE_DESIGN 回用户拍板）

> **✅ GATE_DESIGN 裁决（2026-06-22，用户拍板）**
> - **D1 = Piper（接受 GPL-3.0）**。技术最优；GPL 作 optional 依赖在单用户非分发场景负担低 + `TtsBackend` 抽象可换。
> - **D2/D3 = C 混合**，且明确「**显式关闭后不自动重开**」：入站 voice 在 `voice_mode` **未设置**时自动置 True；用户 `/voice off`（显式 False）后，再发 voice **不**自动重开，须 `/voice on`。→ `voice_mode` 元数据必须区分「未设置（key 缺失）」与「显式 False」（消解 clarify HIGH-1）。
> - **D4 = PyAV 优先**（plan Phase 0 实测 `libopus`，不可用切 PyOgg）。**D5 = 单一 env 可配置模型，默认 zh_CN**。
> - **scope = 异步语音多轮**；实时双工留 v0.2。

### D1 — TTS 后端选型 + 许可证取舍（头号决策）

调研推荐 **本地 Piper（OHF-Voice/piper1-gpl，GPL-3.0）**，理由见 `research/tech-research.md §2.2`。核心权衡：

| 维度 | 本地 Piper（推荐）| 本地 Kokoro（Apache-2.0）| 云 API |
|------|-------------------|--------------------------|--------|
| **许可证** | ⚠️ **GPL-3.0**（含 espeak-ng）| ✅ Apache-2.0 | 商业 |
| 隐私 | ✅ 音频不出设备（Constitution #5）| ✅ | ❌ 音频上传第三方 |
| 依赖足迹 | ✅ onnxruntime 轻量（~60MB 模型）| ❌ PyTorch ~2GB+ | 零本地 + 需 key |
| CPU RTF | ~0.008（极快）| 中等 | 网络主导 |
| 中文支持 | ✅ zh_CN-huayan-medium | 有限 | ✅ |
| 与 F109 哲学契合 | ✅ 轻量 optional 依赖 | ❌ PyTorch 重栈相悖 | ❌ |

**⚠️ GPL-3.0 取舍说明（GATE_DESIGN 必决）**：F109 STT 栈（faster-whisper/CTranslate2）均 MIT。引入 Piper 会让 `[voice]` optional 依赖组首次带 GPL-3.0。缓解项：①Piper 是 optional 依赖，core 安装不含它；②`TtsBackend` 抽象使后端随时可替换（许可若成问题换 Kokoro 或云 API 只改 backend 层，上层 AC/FR 不变）；③OctoAgent Blueprint §0 锁定单用户深度非分发场景，GPL copyleft 义务主要在分发组合时触发，个人自用基本无负担。

**本 spec 默认以 Piper 书写**。GATE_DESIGN 反选 Kokoro 或云 API 时，仅影响 backend 实现层，不改 FR/AC 结构。

> **GATE_DESIGN 输出**：用户拍板 D1 后，plan 据此细化 TTS backend 实现与依赖。

### D2 — voice session 模式切换机制

| 选项 | 行为 |
|------|------|
| A — 显式命令 | `/voice on` / `/voice off` 控制命令切换；未命令则文字回复 |
| B — 入站自动标记 | 用户发一条 voice message 自动将该 chat 标记为 voice 模式 |
| **C — 混合（推荐）** | **入站 voice 自动标记 voice_mode=True（零配置） + `/voice off` 关闭 + `/voice on` 显式重开** |

**本 spec 以 C（混合）为默认书写**，标注「GATE_DESIGN 可改为 A 或 B」。C 的优点：用户发语音自然得到语音回，无需额外配置；同时保留显式命令作为覆盖手段。

### D3 — 入站 voice 自动标记（关联 D2）

采用 D2-C 默认时：每次 `_handle_voice_message` 成功后，在 `_record_conversation_binding`（`telegram.py:584`）写入时追加 `metadata["voice_mode"] = True`；`/voice off` 写入 `False`；`/voice on` 写入 `True`。

**本 spec 以此为默认**。GATE_DESIGN 若选 D2-A（纯命令），则入站 voice 不自动标记。

### D4 — WAV→OGG/Opus 格式转换实现

Piper 输出 16-bit PCM WAV（22050Hz）；Telegram `sendVoice` 需 OGG/Opus。

**推荐 PyAV**（`libopus` 编解码器）：已是 faster-whisper 传递依赖，无需新增系统依赖。plan 阶段必须在真实 venv 中实测验证 `libopus` 可用性（`research/tech-research.md §2.4`）。备选 PyOgg（新 pip 依赖，更专门），如 PyAV 编码路径不稳定则切换。

### D5 — TTS 语言与模型配置

v0.1 默认**单一可配置 voice 模型**（env 配置：`OCTOAGENT_TTS_BACKEND`/`OCTOAGENT_TTS_VOICE_MODEL`/`OCTOAGENT_TTS_LANGUAGE`/`OCTOAGENT_TTS_ENABLED`，默认 `zh_CN-huayan-medium`），**不做按 STT 语言自动多模型选择**——留后续。沿用 F109 STT 配置的 env 路径策略，不动 `octoagent.yaml` schema（F110 后若 voice 成稳定功能可 promote 到 yaml）。

---

## 3. 问题陈述（引调研锚点）

- **回复永远是文字，语音体验不完整**：F109 已打通"用户发语音 → 主 Agent 处理"入站链路；但 `notify_task_result`（`telegram.py:872`）目前只能 `send_message`（文字），bot 的回复对语音用户而言格格不入。
- **voice session 无连续性标记**：`ConversationBinding.metadata`（`telegram.py:584`）已有 KV bag 结构（含 `last_message_thread_id` 等字段），但无 `voice_mode` 标记，导致 bot 无法区分「这个 chat 用户期望语音回」还是「偶发发了一条 voice」。
- **无 TTS 服务层**：项目无任何 text→audio 组件；`gateway/voice/` 目录现有 `stt.py`（`SttBackend` Protocol + `SpeechToTextService`，`stt.py:36/46`）和 `faster_whisper_backend.py`，TTS 侧完全空白。
- **`TelegramBotClient` 缺 `send_voice`**：现有方法清单（`telegram_client.py:112-329`）含 `send_message`（JSON POST），但无 `sendVoice`（multipart/form-data 上传），无法发送语音消息气泡。
- **H1 风险**：若为 voice session 新建 Agent 路径或 Agent 模式，会违反 H1 铁律——本 spec 明确 voice session 只是渠道层标记，不侵入 Agent 模型。

---

## 4. User Stories

### US-1（P1）— 发语音得到语音回复（TTS 出站）

作为 OctoAgent 的单用户 owner，当我处于语音模式的 Telegram chat 中，主 Agent 处理完我的输入（无论语音还是文字）后，我应收到一条**语音消息气泡**作为回复，而不是文字——让语音对话体验完整自然。

- **优先级理由**：这是 F110 最核心的体验差距——F109 已完成入站，TTS 出站是让完整往返闭环成立的必要条件。
- **独立测试**：构造一个 voice_mode=True 的 chat，mock TTS 返回固定 OGG bytes → 断言 `notify_task_result` 调用 `bot_client.send_voice` 而非 `send_message`；voice_mode=False 时断言仍走 `send_message`。
- **验收场景（Given-When-Then）**：
  - Given 一个 chat 的 `ConversationBinding.metadata["voice_mode"] == True`，主 Agent 推理完成，`notify_task_result` 被调用；When TTS 服务可用，合成成功返回 OGG/Opus bytes；Then `bot_client.send_voice(chat_id, ogg_bytes, ...)` 被调用，用户收到语音消息气泡。
  - Given 同一场景但 voice_mode=False（或 binding 查不到）；When `notify_task_result`；Then 走原有 `send_message`（文字），行为与 F109 基线完全一致。

### US-2（P1）— 发一条语音即进入语音模式（voice session 自动标记）

作为单用户 owner，当我向 bot 发第一条 Telegram voice message 时，bot 应自动记住「这个 chat 我想要语音互动」——此后我的每条消息（包括文字消息）都会得到语音回复，直到我显式关闭（`/voice off`）。这种零配置体验比手动开命令更自然。

- **优先级理由**：voice session 的连续性体验是 F110 相对 F109 最核心的用户价值差距，决定 voice mode 是否实用。
- **独立测试**：构造一条 voice update → 处理后查 `ConversationBinding.metadata["voice_mode"]` 为 True；再发一条文字 update → 断言 notify_task_result 时 voice_mode 仍为 True；发 `/voice off` → voice_mode 变 False。
- **验收场景**：
  - Given 一个 chat 没有任何 voice 历史（voice_mode 不存在或 False）；When 用户发一条 voice message 并转写成功；Then `ConversationBinding.metadata["voice_mode"]` 置 True，该 chat 进入语音模式。
  - Given 同一 chat 已在语音模式；When 用户发一条**文字**消息并被主 Agent 处理；Then `notify_task_result` 也走语音回复路径（TTS + send_voice）。
  - Given 同一 chat 在语音模式；When 用户发 `/voice off`；Then `voice_mode` 置 False，后续回复恢复文字。

### US-3（P1）— TTS 任何失败都能安全降级到文字回复

作为单用户 owner，当 TTS 因任何原因失败（未装依赖/模型缺失/合成异常/编码失败/`send_voice` API 失败/超时），我仍应收到**文字回复**而不是沉默——bot 绝不能因为语音发不出去就把 Agent 的回复也丢了。

- **优先级理由**：Constitution #6（优雅降级）是硬约束；TTS 是新链路且 CPU-bound，失败面多，没有可靠降级则 voice_mode 成为不稳定因素。
- **独立测试**：注入 §7 降级矩阵的全部 8 类失败 → 断言每类都回退到 `send_message`（文字），不抛异常逃逸，gateway 不崩。
- **验收场景**：
  - Given TTS 服务 `is_available()=False`（piper-tts 未安装）；When `notify_task_result` 在 voice_mode chat；Then 仍走 `send_message` 文字回复，结构化日志记 `tts_unavailable`。
  - Given TTS 可用但 `synthesize` 在 30s 内未完成；When 超时；Then `asyncio.wait_for` 捕获，降级文字回复，日志记 `tts_timeout`。

### US-4（P2）— `/voice on|off` 显式控制语音模式

作为单用户 owner，我可以通过 `/voice on` 和 `/voice off` 指令随时显式切换某个 chat 的语音模式，不依赖「发语音才能触发」的自动逻辑，以便在不想发语音时也能进入或退出语音模式。

- **优先级理由**：P2，D2-C 组合方案的显式控制侧；增强控制感，不阻塞 US-1/US-2。
- **验收场景**：
  - Given voice_mode=False；When 用户发 `/voice on`；Then voice_mode 置 True，bot 回复确认（「语音模式已开启」）。
  - Given voice_mode=True；When 用户发 `/voice off`；Then voice_mode 置 False，bot 回复确认（「语音模式已关闭」）。

### US-5（P2）— 语音往返全程可观测

作为关注可观测性（Constitution #8）的 owner，TTS 合成成功或降级在结构化日志中有迹可查（backend/duration_ms/text_len/reason），不包含音频内容或完整回复文本（隐私）。

---

## 5. 验收标准（Acceptance Criteria，含 AC↔test 绑定）

> **AC↔test 绑定（SDD 工作流强化）**：每条 P1 AC 紧邻标注对应 test 文件路径 + 测试函数名。verify 阶段机械校验该 test 存在且 PASS（`grep` + `pytest -k`）。
>
> 新测试文件：
> - `apps/gateway/tests/test_tts_service.py`（TTS 服务单测 + `FakeTtsBackend`，镜像 `test_stt_service.py`）
> - `apps/gateway/tests/test_telegram_voice.py`（已存在，F110 扩展：出站 TTS + voice_mode + send_voice）

### TTS 服务层（FR-A 组）

| AC | 优先级 | 描述 | 绑定 test |
|----|--------|------|-----------|
| **AC-A1** | P1 | `TextToSpeechService` 在 `piper-tts` 未安装时 lazy import 降级，`is_available()` 返回 False，不在 import 期或构造期崩溃 | `test_tts_service.py::test_tts_unavailable_when_lib_missing` |
| **AC-A2** | P1 | `TextToSpeechService.synthesize(text)` 在 backend 可用（`FakeTtsBackend` stub）时返回 `TtsResult{ok=True, audio=bytes}`；backend.synthesize 抛异常时返回 `TtsResult{ok=False, reason="synthesize_error"}`；返回空音频时返回 `TtsResult{ok=False, reason="empty_audio"}` | `test_tts_service.py::test_synthesize_ok` / `test_tts_service.py::test_synthesize_error_returns_false` / `test_tts_service.py::test_synthesize_empty_audio_returns_false` |
| **AC-A3** | P1 | `TtsResult` pydantic 模型包含 `ok: bool` / `audio: bytes` / `reason: str` / `backend: str` / `duration_ms: int`，字段语义对称 `SttResult`（`stt.py:21`） | `test_tts_service.py::test_tts_result_schema` |
| **AC-A4** | P1 | `TtsBackend` Protocol 包含 `name: str` / `is_available() -> bool` / `async synthesize(text, *, language) -> TtsResult`，结构对称 `SttBackend`（`stt.py:36`）；`PiperTtsBackend` 实现该 Protocol | `test_tts_service.py::test_piper_backend_protocol_conformance` |

### 出站 TTS 接入（FR-B 组）

| AC | 优先级 | 描述 | 绑定 test |
|----|--------|------|-----------|
| **AC-B1** | P1 | voice_mode=True 的 chat 上，`notify_task_result` 调用 TTS 合成后调 `bot_client.send_voice`，**不调** `send_message` | `test_telegram_voice.py::test_tts_sends_voice_when_voice_mode_on` |
| **AC-B2** | P1 | voice_mode=False（或 binding 无此字段）的 chat 上，`notify_task_result` 仍走原有 `send_message` 路径，行为与 F109 基线完全一致 | `test_telegram_voice.py::test_tts_falls_back_to_text_when_voice_mode_off` |
| **AC-B3** | P1 | TTS 合成失败（`ok=False`）时，`notify_task_result` 降级调 `send_message`（文字回复），不丢 Agent 回复内容，不抛异常逃逸 | `test_telegram_voice.py::test_tts_falls_back_to_text_when_synthesize_fails` |
| **AC-B4** | P1 | `send_voice` Telegram API 调用失败时，`notify_task_result` 降级到 `send_message`，不崩 | `test_telegram_voice.py::test_tts_falls_back_to_text_when_send_voice_raises` |
| **AC-B5** | P1 | `_build_result_text` 和 `_resolve_reply_target` 不被修改（行为零变更）；TTS 未启用时（`tts_service=None`）回复路径与 F109 基线完全一致 | `test_telegram_voice.py::test_tts_falls_back_to_text_when_tts_none` |
| **AC-B6** | P2 | TTS 合成超时 → 超时降级文字，`notify_task_result` 正常返回（不崩） | `test_telegram_voice.py::test_notify_task_result_degrades_on_tts_timeout` |

### `send_voice` bot client 方法（FR-C 组）

| AC | 优先级 | 描述 | 绑定 test |
|----|--------|------|-----------|
| **AC-C1** | P1 | `TelegramBotClient.send_voice(chat_id, voice_bytes, ...)` 发出 multipart/form-data HTTP 请求至 `sendVoice` 端点，包含 `voice` 文件字段（mock httpx 断言请求结构） | `test_telegram_voice.py::test_bot_client_send_voice_multipart` |
| **AC-C2** | P1 | `send_voice` 可传递 `duration` / `reply_to_message_id` / `message_thread_id` / `disable_notification` 可选参数并正确映射到 Telegram API 字段 | `test_telegram_voice.py::test_bot_client_send_voice_optional_params` |
| **AC-C3** | P1 | `send_voice` 失败时抛出明确可捕获异常类型（供 FR-B 降级路径捕获），不静默吞掉错误 | `test_telegram_voice.py::test_bot_client_send_voice_raises_on_failure` |

### WAV→OGG/Opus 格式转换（FR-C 组延续）

| AC | 优先级 | 描述 | 绑定 test |
|----|--------|------|-----------|
| **AC-C4** | P1 | WAV bytes 转换为 OGG/Opus bytes，输出非空且为合法 OGG 容器（magic bytes `OggS`）；`@pytest.mark.skipif`（av 未装），运行时验证项 [E2E_DEFERRED] | `test_tts_service.py::test_wav_to_ogg_opus_produces_valid_ogg`（skipif av 未装） |
| **AC-C5** | P1 | 格式转换失败（codec 不可用/输入非法）时返回空 bytes 或抛可捕获异常，调用方降级文字回复 | `test_tts_service.py::test_wav_to_ogg_opus_failure_handled` |

### voice session（FR-D 组）

| AC | 优先级 | 描述 | 绑定 test |
|----|--------|------|-----------|
| **AC-D1** | P1 | 入站 voice message 转写成功且 `voice_mode` **未设置**时，`ConversationBinding.metadata["voice_mode"]` 置 True（D3：首次自动标记） | `test_telegram_voice.py::test_voice_message_sets_voice_mode` |
| **AC-D1b** | P1 | `voice_mode` 已**显式 False**（用户 `/voice off` 过）时，再发 voice message **不**自动重开（保持 False），须 `/voice on`（GATE 裁决：显式关闭后不自动重开） | `test_telegram_voice.py::test_voice_off_then_voice_message_stays_off` |
| **AC-D2** | P1 | `/voice off` 控制命令将 `ConversationBinding.metadata["voice_mode"]` 置 False，bot 回复「语音模式已关闭」 | `test_telegram_voice.py::test_voice_off_command_clears_voice_mode` |
| **AC-D3** | P1 | `/voice on` 控制命令将 `voice_mode` 置 True，bot 回复「语音模式已开启」 | `test_telegram_voice.py::test_voice_on_command_sets_voice_mode` |
| **AC-D4** | P1 | voice session 多轮连续性：第 1 轮 voice update 触发 voice_mode=True 后，第 2 轮**文字** update 的 `notify_task_result` 也走 TTS 路径（binding 持久存 SQLite，跨 update 读取） | `test_telegram_voice.py::test_voice_session_continuous_rounds` |
| **AC-D5** | P1 | voice session 多轮幂等边界：同一 voice update 重投，转写幂等（不重复调 TTS），voice_mode 状态幂等（不重复写 True） | `test_telegram_voice.py::test_voice_message_idempotent_replay` |
| **AC-D6** | P1 | `AgentSessionKind` 枚举无需扩展；voice session 仍用主 Agent 同等 session 类型，连续性靠 F093 `AgentSession` | `test_telegram_voice.py::test_tts_falls_back_to_text_when_tts_none`（AC-D6 对应：tts_service=None 时走文字，不改 Agent session） |
| **AC-D7** | P1 | `ConversationBinding.metadata["voice_mode"]` 未设置（None 或 key 缺失）时默认 False，不崩，`notify_task_result` 走文字回复 | `test_telegram_voice.py::test_tts_falls_back_to_text_when_voice_mode_off` |

### 观测与安全（FR-E 组）

| AC | 优先级 | 描述 | 绑定 test |
|----|--------|------|-----------|
| **AC-E1** | P1 | TTS 合成成功产生一条结构化日志，含 `{backend, duration_ms, text_len}`，**不含**完整回复文本或音频 bytes | `test_tts_service.py::test_tts_synthesis_observable` |
| **AC-E2** | P1 | TTS 降级产生一条结构化日志，含 `{reason}` 失败原因码，便于诊断 | `test_telegram_voice.py::test_tts_falls_back_to_text_when_synthesize_fails`（验证降级路径被走到，日志通过结构化 warning 产出） |
| **AC-E3** | P2 | 任一失败不得让异常逃逸出 `notify_task_result`（防 bot callback 500 / polling loop 崩） | `test_telegram_voice.py::test_tts_falls_back_to_text_when_send_voice_raises`（send_voice 失败不崩） |

### 硬不变量

| AC | 优先级 | 描述 | 绑定 test |
|----|--------|------|-----------|
| **AC-Z1** | P1（硬不变量）| 全量回归 0 regression vs `1cd2083f`；`pytest -m e2e_smoke` 8/8 PASS | verify 阶段 `pytest`（全量 + e2e_smoke） |
| **AC-Z2** | P1 | e2e voice 往返链：Fake STT + `FakeTtsService`，端到端经 `_ingest_update` → voice_mode 置位 → `notify_task_result`，断言 voice_mode chat 收到 `send_voice` 调用，文字 chat 收到 `send_message` | `test_telegram_voice.py::test_voice_roundtrip_e2e` |

> **FIX-2 注记（双评审修正）**：以上绑定表以实际测试函数名为事实源（FIX-2 要求）。
> 原始 spec 绑定名（如 `test_notify_task_result_sends_voice_when_voice_mode`、`test_build_result_text_unchanged`、`test_tts_degrade_observable` 等）
> 已全部修正为与 `test_tts_service.py` / `test_telegram_voice.py` 实际函数名完全一致。
> FIX-4 新增的三个测试（`test_voice_session_continuous_rounds`、`test_notify_task_result_degrades_on_tts_timeout`、`test_voice_roundtrip_e2e`）
> 已在绑定表 AC-D4 / AC-B6 / AC-Z2 中登记。
> FIX-1 新增的 `test_piper_backend_uses_synthesize_wav_not_synthesize` 为 API 签名锁，已在 `test_tts_service.py` 中新增，无独立 AC 行（归入 FR-A4 范畴）。

---

## 6. 功能需求（Functional Requirements）

> 每条 FR 使用 MUST / SHOULD / MAY 级别；每条 FR 可追踪到至少一个 User Story 或 AC；YAGNI 必要性标注见括号。

### FR-A：TTS 服务层（新建 `gateway/voice/tts.py` + `gateway/voice/piper_backend.py`）

- **FR-A1** [必须] 新增 `TtsResult`（pydantic）：`ok: bool` / `audio: bytes`（成功时含 OGG/Opus bytes）/ `reason: str`（失败原因码）/ `backend: str` / `duration_ms: int`（可选）。语义 MUST 对称 `SttResult`（`stt.py:21`）。
- **FR-A2** [必须] 新增 `TtsBackend`（Protocol，`@runtime_checkable`）：`name: str` / `is_available() -> bool` / `async synthesize(text: str, *, language: str) -> TtsResult`。结构 MUST 对称 `SttBackend`（`stt.py:36`）。
- **FR-A3** [必须] 新增 `TextToSpeechService`：构造接收 `TtsBackend`；`is_available()` 委托 backend 并捕获异常；`async synthesize(text: str) -> TtsResult` 捕获所有异常返回 `TtsResult(ok=False, ...)`，空音频归一为 `reason="empty_audio"`——**永不把异常抛给调用方**（Constitution #6）。
- **FR-A4** [必须] 新增 `gateway/voice/piper_backend.py`：`PiperTtsBackend`——懒加载单例（首次 `synthesize` 时 lazy import piper + 加载模型）+ `asyncio.to_thread` 卸载 CPU-bound 合成 + `asyncio.wait_for` 30s 超时守卫；`is_available()` 当 piper-tts 未安装或模型缺失时返回 False 不崩；`build_default_tts_service()` 工厂函数（镜像 `faster_whisper_backend.py:95`）。
- **FR-A5** [必须] piper-tts MUST 作为 optional 依赖加入 `apps/gateway/pyproject.toml` 的 `[project.optional-dependencies].voice` 组（与 faster-whisper 同组：`piper-tts>=1.4,<2.0`）。lazy import 在 backend 内；gateway 启动时不导入 piper（Constitution #6，沿用 F109 STT optional 先例）。
- **FR-A6** [必须] WAV→OGG/Opus 格式转换：`piper_backend.py` 内提供辅助函数 `wav_to_ogg_opus(wav_bytes: bytes) -> bytes`，默认用 PyAV `libopus`（faster-whisper 传递依赖复用）。转换失败 MUST 捕获并向上返回空 bytes，由 `TextToSpeechService` 归一为 `reason="encode_error"`。
- **FR-A7** [必须] TTS 配置走 env（`OCTOAGENT_TTS_BACKEND`/`OCTOAGENT_TTS_VOICE_MODEL`/`OCTOAGENT_TTS_LANGUAGE`/`OCTOAGENT_TTS_ENABLED`），有合理硬默认（Piper / zh_CN-huayan-medium / zh_CN / True），不强制用户配置即可用（镜像 STT env 策略）。
- **FR-A8** [必须] TTS service wiring 镜像 STT wiring（`octo_harness.py:510`）：`octo_harness.py` 在构造 `TelegramGatewayService` 时注入 `tts_service=build_default_tts_service()`；`TelegramGatewayService.__init__` 新增 `tts_service: TextToSpeechService | None = None`，None = TTS 未启用（优雅降级）。

### FR-B：出站 TTS 接入（修改 `telegram.py`）

- **FR-B1** [必须] `notify_task_result`（`telegram.py:872`）在 `_build_result_text` 之后、`send_message` 之前插入 TTS 分支：查询该 task 对应 chat_id 的 `ConversationBinding.metadata["voice_mode"]`；若为 True 且 `tts_service.is_available()` → 调 `tts_service.synthesize(text)` → 若 ok → 调 `bot_client.send_voice`；任一失败降级到 `send_message`（文字）。
- **FR-B2** [必须] `_build_result_text`（`telegram.py:_build_result_text`）和 `_resolve_reply_target`（`telegram.py:_resolve_reply_target`）MUST NOT 被修改（行为零变更，H1 出站后处理边界清晰）。
- **FR-B3** [必须] **仅 `notify_task_result` 加 TTS**：`notify_approval_event`（`telegram.py:893`）、`_reply_voice_degrade`（`telegram.py:565`）、`_handle_control_command` 的回复（`telegram.py:635`）MUST NOT 加 TTS——审批通知需用户看按钮操作/语音无法承载 inline keyboard；降级回复本身是 TTS 失败的兜底逻辑矛盾；控制命令用户期望文字。
- **FR-B4** [必须] 降级链 MUST 完整：TTS 不可用 → 文字；合成失败 → 文字；音频空 → 文字；编码失败 → 文字；`send_voice` 失败 → 文字；任何情况下 Agent 的回复内容 MUST NOT 被丢弃（只是从语音换成文字形式）。
- **FR-B5** [必须] `notify_task_result` 内 TTS 路径的所有异常 MUST 在此函数内捕获，不得逃逸到调用方（Constitution #6）。

### FR-C：`send_voice` bot client 方法（修改 `telegram_client.py`）

- **FR-C1** [必须] `TelegramBotClient` 新增 `async send_voice(chat_id, voice: bytes, *, duration=None, reply_to_message_id=None, message_thread_id=None, disable_notification=True) -> TelegramMessage`，调用 Telegram `sendVoice` API。
- **FR-C2** [必须] `sendVoice` 请求 MUST 使用 multipart/form-data（`voice` 字段为文件 bytes），**不走**现有 `_request` 的 JSON POST 路径——需单独写 multipart httpx 请求，复用 `_load_bot_token()` + `base_url`。
- **FR-C3** [必须] `send_voice` 失败 MUST 抛出明确可捕获异常类型供 FR-B 降级路径捕获；失败原因 SHOULD 含 HTTP status code 供日志。

### FR-D：voice session（修改 `telegram.py`）

- **FR-D1** [必须] `_record_conversation_binding`（`telegram.py:584`）在 `_handle_voice_message` 转写成功后，**仅当 `voice_mode` 未设置（key 缺失）时**追加写入 `ConversationBinding.metadata["voice_mode"] = True`（D2-C 默认：入站 voice 自动标记首次开启）；若 `voice_mode` 已**显式为 False**（用户 `/voice off` 过），入站 voice **不重写为 True**（GATE_DESIGN 裁决：显式关闭后不自动重开，须 `/voice on`）。通过 `binding_store.upsert_runtime_binding` 落 SQLite。读取统一用三态语义：`unset`（key 缺失）/ `True` / `False`。
- **FR-D2** [必须] `_handle_control_command`（`telegram.py:635`）扩展支持 `/voice on` → 查写 `ConversationBinding.metadata["voice_mode"] = True`，回复确认；`/voice off` → 置 False，回复确认。
- **FR-D3** [必须] `notify_task_result` 通过 `_resolve_reply_target(task_id)` 得到 `chat_id` 后，MUST 查 `ConversationBinding` binding store 读 `metadata.get("voice_mode", False)`——binding 查不到或字段缺失默认 False，不崩。
- **FR-D4** [必须] voice session 多轮连续性：每轮 voice update 仍是独立幂等单元（沿用 `_build_idempotency_key`，`telegram.py:750`）；voice_mode 标记持久化在 binding（跨 bot restart 保留），不新建幂等机制。
- **FR-D5** [必须] voice session MUST NOT 扩展 `AgentSessionKind` 枚举——voice 会话仍用主 Agent 现有 session 类型，连续性靠 F093 `AgentSession`（`agent_context.py:335`）的 `rolling_summary`/`memory_cursor_seq`/`recent_transcript`；「voice 模式」是渠道层标记，不侵入 Agent 会话模型。
- **FR-D6** [可选] 当 STT 转写成功后，SHOULD 在 turn metadata 中标注 `input_kind=voice`（供后续 analytics），由 plan 阶段评估成本决定。若成本低（一行 dict 写入）则实现，否则留后续。

### FR-E：观测与安全

- **FR-E1** [必须] TTS 合成成功/降级各留一条结构化日志（`structlog` 或 standard `logging`），成功含 `{backend, duration_ms, text_len}`，降级含 `{reason}`；MUST NOT 记录完整回复文本或音频 bytes（Constitution #5 隐私）。
- **FR-E2** [必须] 若 D1 反选云 API 后端：TTS API key MUST 走 CredentialStore/env，不进 LLM 上下文、不进日志。本 spec 默认 Piper 本地，无凭证需求。
- **FR-E3** [必须] 任一 TTS 失败 MUST NOT 让异常逃逸出 `notify_task_result`（Constitution #6）。

---

## 7. 优雅降级矩阵（Constitution #6）

**原则：永不崩 gateway，永不静默丢 Agent 回复，给用户文字兜底，其他消息不受影响。**

| 失败场景 | 降级行为 | 日志 reason 码 |
|----------|----------|----------------|
| `piper-tts` 未安装 / `is_available()=False` | TTS service 标记不可用 → `notify_task_result` 退回 `send_message`（文字） | `tts_unavailable` |
| Piper 模型文件缺失 / 初始化 raise | lazy load 捕获 → `is_available()=False` → 同上 | `tts_unavailable` |
| `synthesize_wav` 抛异常 | `TextToSpeechService.synthesize` 兜底返回 `TtsResult(ok=False, reason="synthesize_error")` → 退回文字 | `synthesize_error` |
| 合成返回空音频 bytes | 归一为 `reason="empty_audio"` → 退回文字 | `empty_audio` |
| WAV→OGG/Opus 格式转换失败 | 捕获 → `reason="encode_error"` → 退回文字 | `encode_error` |
| `send_voice` Telegram API 调用失败 | try/except 包裹 → 退回 `send_message`（文字） | `send_voice_failed` |
| `ConversationBinding` 查不到 / `voice_mode` 字段缺失 | 默认 voice_mode=False → 文字回复，不崩 | — |
| TTS 合成超时（≥30s，asyncio.wait_for）| 超时捕获 → 退回文字 | `tts_timeout` |

---

## 8. 范围边界（明确不做）

- ❌ **实时双工语音**（WebRTC/WebSocket 持续连接）→ v0.2，XL 级，Telegram 平台不支持实时音频流
- ❌ **其他渠道语音出站**（Web/Slack/Discord 端 TTS）→ v0.2，需 F105 v0.2 完善出站路由
- ❌ **按 STT 语言自动多模型 TTS 选择** → 留后续
- ❌ **说话人音色定制 / 个性化 TTS 模型** → 留后续
- ❌ **音频原文持久化为 artifact**（v0.1 不存，PoC 延续 F109 决策；若 voice session 回放需求出现则评估）
- ❌ **Web 端音频上传 UI** → F109 已排除，F110 同
- ❌ **Telegram `sendAudio` 回退**（UX 是「文件」气泡而非「语音消息」气泡，体验差；降级统一用 `send_message` 文字）

---

## 9. 已知约束 / 给 v0.2 的接力点

- **v0.2 必须做**：
  1. outbound TTS 接线到 Web 渠道出站路由（`notify_task_result` 渠道无关化，复用 F105 `ChannelAdapter`）
  2. 实时双工语音（F105 v0.2+ WebSocket 通道，重新评估规模）
  3. Slack/Discord 渠道语音支持（F105 v0.2 平台扩展后）
  4. 多模型多语言 TTS 自动选择（按 STT 检测语言路由 Piper voice 模型）

- **F110 建立的地基（v0.2 直接复用）**：
  - `TtsBackend` Protocol + `TextToSpeechService`：v0.2 切换云 API 或 Kokoro 只换 backend
  - `ConversationBinding.metadata["voice_mode"]`：v0.2 其他渠道可扩展同一机制
  - `bot_client.send_voice` multipart 路径：已验证 Telegram sendVoice 完整链路

- **STT 地基（F109，完全保留）**：`stt.py` / `faster_whisper_backend.py` / `_handle_voice_message` / `_reply_voice_degrade` 不被 F110 修改，逻辑零变更。

- **D4 PyAV 编码路径（plan 阶段必验证）**：`research/tech-research.md §2.4` 的 `wav_to_ogg_opus` 骨架在 plan Phase 0 实测；若 `libopus` 不可用则切 PyOgg，plan 阶段决定是否加入 `[voice]` optional 依赖。

---

## 10. 复杂度评估（供 GATE_DESIGN 审查）

| 维度 | 评估 |
|------|------|
| **组件总数** | 3 新增（`tts.py` TTS 服务层 / `piper_backend.py` Piper 实现 / `send_voice` bot client 方法），2 修改（`telegram.py` 出站接入 + voice_mode / `octo_harness.py` wiring） |
| **接口数量** | 新增 3（`TtsBackend` Protocol / `TextToSpeechService.synthesize` / `TelegramBotClient.send_voice`），修改 2（`TelegramGatewayService.__init__` 增 `tts_service` 参数 / `_handle_control_command` 增 voice 命令） |
| **依赖新引入数** | 1（`piper-tts>=1.4` optional 依赖组，lazy import，不污染 core 安装） |
| **跨模块耦合** | 轻度：`telegram.py`（出站接入）→ `tts.py`（服务层）→ `piper_backend.py`（backend）；`octo_harness.py` 构造注入；`ConversationBinding` 模型零修改（只读写 metadata KV）；`AgentSession` 零修改（连续性直接复用） |
| **复杂度信号** | ⚠️ **1 个**：`asyncio.to_thread` CPU-bound 卸载（镜像 F109 STT 已有先例，非新模式）；无递归结构、无新状态机、无并发控制（voice_mode 是 chat 级 SQLite upsert）、无数据迁移 |
| **总体复杂度** | **MEDIUM**（组件 3-5 / 接口 4-8 / 1 个复杂度信号）|

---

## 11. 验收门（Gate）

- **0 regression** vs `1cd2083f`（全量回归基线）。
- `pytest -m e2e_smoke` **8/8 PASS**（pre-commit hook，F087 smoke 5 域）。
- 新增单测（`test_tts_service.py` + `test_telegram_voice.py` F110 扩展）+ e2e voice 往返链全 PASS。
- `§5 AC↔test 绑定表`全部 P1 AC 机械校验：test 文件存在 + `pytest -k <test_func>` PASS。
- **双评审 panel**（Codex + 第二模型，如 Opus）0 HIGH 残留（新能力 + 引入外部 GPL 依赖，命中"重大架构变更/外部依赖"节点）。
- `completion-report.md` + `handoff.md`（F110 → v0.2）+ living-docs（blueprint/milestones）同步（SDD living-docs 漂移闸）。
- **不主动 push，等用户拍板**。
