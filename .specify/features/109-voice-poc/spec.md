# F109 语音 PoC — 功能规范（spec.md）

- **Feature ID**: F109
- **Slug**: voice-poc
- **基线**: master d6f0ec54 / 分支 `feature/109-voice-poc` / worktree `F109-voice`
- **性质**: **新能力（行为新增）**——M6 最后一块大功能，语音第一步（PoC 验证可行性）
- **调研依据**: `.specify/features/109-voice-poc/research/tech-research.md`（块 A：STT 选型 Web 调研 + F105 telegram inbound 路径代码侦察，含 file:line 锚点）
- **代码根**: 实际代码在仓库根 `octoagent/` 子目录；本 spec 内 `file:line` 锚点相对仓库根、文件位于 `octoagent/` 下

> 散文中文 / 代码标识符英文 / 英文技术术语保原文。

---

## 1. 概述

F109 是 OctoAgent 语音能力的**第一步 PoC**：**单向语音输入 → STT（语音转文字）→ text → 现有 chat 主路径**。目标是用最小改动**证明可行性**——验证"用户发一条 Telegram 语音消息，系统转写成文字后，像普通文字消息一样被主 Agent 处理"这条端到端管线打通。

**核心哲学 H1（语音是输入预处理，不改 Agent 模型）**：语音转成 text 后，**仍走主 Agent 的现有 chat 主路径**。F109 只在消息入站处增加一道"voice → text"的预处理，**不新增 Agent 模式、不改决策环、不改主 Agent 接收/回复语义**。转写文本 `text` 一旦填好，下游 `create_task` → `enqueue` → 主 Agent 推理与文字消息**完全同路**。

**范围收窄（PoC 最小化，别过度工程）**：

- ✅ **只做 STT**（语音 → 文字）。入口为 **Telegram voice message**（复用 F105 telegram inbound 路径，最省事验证）。
- ❌ **不做 TTS**（文字转语音）→ F110。
- ❌ **不做 voice session**（完整语音会话/连续语音态）→ F110（依赖 F093 Worker Full Session Parity）。
- ❌ **不做 Web 端音频上传 UI** → 后续。

---

## 2. 决策点（GATE_DESIGN 回用户拍板）

### D1 — STT 后端选型（关键决策）

调研推荐 **本地 faster-whisper**，理由见 `research/tech-research.md §2.3`。核心权衡：

| 维度 | 本地 faster-whisper（推荐） | 云 API（OpenAI Whisper / Deepgram） |
|------|------------------------------|--------------------------------------|
| 隐私 | ✅ 音频不出设备 | ❌ 音频上传第三方 |
| 成本 | ✅ 零边际成本 | ~$0.006/min，经常性 + 需 key |
| 安装 | 首次下模型 ~150MB，无系统 ffmpeg；optional 依赖 + 优雅降级 | 零本地足迹，需配 key + 网络 |
| 资源 | 转写时占本地 CPU/RAM 几秒 | 零本地计算 |
| 与哲学契合 | ✅ 单用户隐私导向（Constitution #5 / Blueprint §0） | ❌ 与隐私导向冲突 |

**默认假设**：本 spec 以 faster-whisper 为默认后端书写。STT 后端做成**可替换薄抽象**（`SttBackend`），后端切换对 telegram 接入路径透明——若用户在 GATE_DESIGN 反选 API，仅需替换 backend 实现 + 接 CredentialStore，上层 AC/FR 不变。

> **GATE_DESIGN 输出**：用户拍板 D1（本地 / API / 二者都做缝但 PoC 先本地）后，plan/tasks 据此细化 STT 服务实现与依赖。

---

## 3. 问题陈述（引调研锚点）

- **voice message 当前被静默丢弃**：`services/telegram.py:_extract_context`（607-671）仅提取 `message.get("text")`（line 632）。voice message（`message.voice`，OGG/Opus）走到 `_ingest_update` line 367 `if not context.text.strip(): return ignored`，**既不报错也不回复**——用户发语音如石沉大海，违反 #6（优雅降级应给明确反馈）与 #8（可观测）。
- **无文件下载能力**：`TelegramBotClient`（telegram_client.py 96-271）只有 send/get_updates/answer_callback/edit，**无 getFile/download**，拿不到语音音频。
- **无 STT 能力**：项目无任何语音转写组件。
- **H1 风险**：若不慎把语音处理塞进 Agent 决策层或新增语音专用 Agent 路径，会违反 H1（语音应是入站预处理）。

---

## 4. User Stories

### US-1（P1）— 发语音消息得到回复
作为 OctoAgent 的单用户 owner，当我向 Telegram bot 发一条语音消息时，我应得到与发文字消息相同的处理——系统把我的语音转成文字、当作我的输入交给主 Agent、并正常回复我。

- **优先级理由**：这是 F109 的核心可行性验证，没有它 PoC 不成立。
- **独立测试**：构造一条含 `message.voice` 的 Telegram update（mock STT 返回固定文本）→ 断言 `_ingest_update` 把转写文本填入 `NormalizedMessage.text`、创建 Task、`enqueue` 收到转写文本（即进入主 Agent 路径）。
- **验收场景（Given-When-Then）**：
  - Given 一条授权用户的 Telegram voice message（OGG/Opus，无 text），STT 后端可用且把音频转写为 `"明天提醒我开会"`；When `_ingest_update` 处理该 update；Then 下载音频 → 转写 → `context.text == "明天提醒我开会"` → 创建 Task → `task_runner.enqueue(task_id, "明天提醒我开会")`，返回 `accepted`，与等价文字消息**走完全相同的主路径**。
  - Given 同一条 voice update 重投（相同 update_id/chat_id/message_id）；When 再次 `_ingest_update`；Then 幂等键命中，返回 `duplicate`，**不重复转写、不重复 enqueue**。

### US-2（P1）— 语音不可用时得到清楚反馈而非石沉大海
作为单用户 owner，当语音转写因任何原因不可用（未装依赖/模型缺失/下载失败/转写失败/空结果）时，我应收到一条**清楚的文字回复**告诉我怎么回事（并提示改发文字），而不是消息被默默吞掉，系统也不能崩。

- **优先级理由**：优雅降级（#6）是硬约束；语音是新链路、失败面多，没有清晰降级 PoC 不可用。
- **独立测试**：分别注入 5 类失败（lib 未装 / 模型初始化 raise / 下载 raise / transcribe raise / 空文本）→ 断言每类都给用户发一条对应的降级回复、return `ignored`、不抛异常出 `_ingest_update`、不创建 Task。
- **验收场景**：
  - Given STT 后端不可用（`faster-whisper` 未安装）；When 收到 voice message；Then 给用户回复"🎙️ 语音转写未启用…请发送文字"，return `ignored`，不创建 Task，gateway 不崩。
  - Given STT 后端可用但转写返回空文本（静音）；When 处理；Then 回复"未能识别语音内容…"，不创建 Task。
  - Given 音频下载失败；When 处理；Then 回复"语音下载失败…"，不创建 Task。

### US-3（P2）— 语音处理全程可观测
作为关注系统可观测性（#8）的 owner，语音消息的接收、转写、降级应在事件流/日志中留痕，便于诊断"我的语音去哪了"。

- **优先级理由**：P2，可观测性增强；不阻塞核心 PoC，但符合 Constitution #2/#8。
- **独立测试**：处理一条 voice message → 断言至少有一条结构化日志/事件记录转写发生（含 duration、转写字符数、后端名）；降级路径记录失败原因。
- **验收场景**：
  - Given 一条成功转写的 voice message；When 处理完成；Then 存在一条结构化日志记录 `{channel=telegram, kind=voice, backend, duration_s, transcript_len}`（不含音频原文、不含敏感内容）。

---

## 5. 验收标准（Acceptance Criteria，含 AC↔test 绑定）

> AC↔test 绑定（SDD 工作流强化）：每条 P1 AC 紧邻标注对应 test 文件；verify 阶段机械校验该 test 存在且 PASS。新测试文件：`apps/gateway/tests/test_telegram_voice.py`（telegram 接入 + 降级）、`apps/gateway/tests/test_stt_service.py`（STT 服务单元）。

| AC | 优先级 | 描述 | 绑定 test |
|----|--------|------|-----------|
| **AC-1** | P1 | voice message（含 `message.voice`）被 `_extract_context` 识别并提取 `{file_id, mime_type, duration, file_size}` 进 context | `test_telegram_voice.py::test_extract_context_detects_voice` |
| **AC-2** | P1 | 可用 STT 后端下，voice message 转写文本回填 `NormalizedMessage.text` 且 `enqueue` 收到该文本（H1：与文字消息同路） | `test_telegram_voice.py::test_voice_message_transcribed_and_enqueued` |
| **AC-3** | P1 | voice update 重投幂等：相同 update 不重复转写/不重复 enqueue，返回 `duplicate` | `test_telegram_voice.py::test_voice_message_idempotent_replay` |
| **AC-4** | P1 | 5 类降级（lib 未装 / 模型 raise / 下载 raise / transcribe raise / 空文本）各给对应用户回复、return `ignored`、不抛异常、不创建 Task | `test_telegram_voice.py::test_voice_degrade_*`（5 个用例） |
| **AC-5** | P1 | `SpeechToTextService` 在 `faster-whisper` 未安装时 lazy import 降级为"不可用"，`is_available()` 返回 False，不在 import 期崩 | `test_stt_service.py::test_stt_unavailable_when_lib_missing` |
| **AC-6** | P1 | `SpeechToTextService.transcribe(audio_bytes)` 在 backend 可用（stub/fake）时返回 `SttResult{ok=True, text=...}`；异常/空各返回对应 `SttResult{ok=False, reason=...}` | `test_stt_service.py::test_transcribe_*` |
| **AC-7** | P1 | `TelegramBotClient.get_file` / `download_file_bytes` 调用 Telegram Bot API 正确（mock httpx 断言 URL/参数），失败抛可捕获异常；下载流式超限即断 | `test_telegram_voice.py::test_bot_client_get_file_and_download` + `::test_bot_client_download_size_guard_streams_early_abort` |
| **AC-8** | P2 | 成功转写产生一条结构化日志/事件（含 backend/duration/transcript_len，不含音频/转写原文敏感泄漏） | `test_telegram_voice.py::test_voice_transcription_observable` |
| **AC-9** | P1（硬不变量） | 全量回归 0 regression vs d6f0ec54；`pytest -m e2e_smoke` 8/8 PASS | verify 阶段 `pytest` |
| **AC-10** | P1 | 新增 e2e（voice → text 链）：mock STT + fake bot client，端到端（经 `handle_webhook_update`）断言一条 voice update 产出一个进入主路径的 Task | `test_telegram_voice.py::test_voice_message_transcribed_and_enqueued`（AC-2 同测试覆盖完整 webhook→enqueue 链） |

---

## 6. 功能需求（Functional Requirements）

### FR-A：STT 服务层
- **FR-A1** 新增 `SpeechToTextService`（gateway 内或 core，plan 定位），构造时**不**强制加载 faster-whisper（lazy）。提供 `is_available() -> bool` 与 `async transcribe(audio: bytes, *, mime: str, filename: str) -> SttResult`。
- **FR-A2** `SttResult`（pydantic）：`ok: bool`、`text: str`、`reason: str`（失败原因码，如 `lib_missing`/`model_error`/`transcribe_error`/`empty`）、`backend: str`、`duration_ms: int`（可选）。
- **FR-A3** **薄后端抽象** `SttBackend`（Protocol）：`FasterWhisperBackend` 为默认实现（模型单例、INT8/CPU、`model.transcribe(BytesIO)`）。后端可替换（D1 反选 API 时只换此层）。
- **FR-A4** faster-whisper 作为 **optional 依赖**（`pyproject` optional-dependencies 组，如 `voice`），**lazy import** 在 backend 内；未装 → `is_available()=False`，不影响 gateway 启动（#6，沿用 F106 watchdog 先例）。
- **FR-A5** 模型大小/语言/计算类型从 config 可调（默认 `base`/auto-detect 语言/`int8`），有合理硬默认；不强制用户配置即可用。

### FR-B：Telegram voice 接入
- **FR-B1** `_extract_context` 检测 `message.get("voice")`，提取 `{file_id, mime_type, duration, file_size}` 注入 `TelegramInboundContext`（新增 `voice` 字段，默认 None）。photo/document **不在 F109 范围**（仅 voice）。
- **FR-B2** `_ingest_update` 在 `_is_allowed` 通过后、空文本检查（line 367）之前插入 voice 处理分支：若 `context.voice` 存在且 `context.text` 为空 → 下载音频 → STT → 用转写文本得到新 context.text；转写成功后**继续原流程**（控制命令分流、create_task、enqueue 全不变）。
- **FR-B3** voice 处理失败/降级 → 调用统一降级回复 helper 给用户发文字提示 + return `ignored`（不创建 Task）。降级回复复用现有出站发送能力（bot_client）。
- **FR-B4** 幂等：voice message 沿用现有 `_build_idempotency_key`（telegram:update_id:chat_id:message_id），重投不重复转写（转写在创建 Task 之前，但幂等检查需保证重投不触发昂贵转写——见 plan：幂等检查前置 or 转写后 create_task 命中 duplicate 即可，二选一在 plan 定）。
- **FR-B5** voice 处理对 webhook 与 polling 两路统一生效（都经 `_ingest_update`，改一处即可）。

### FR-C：TelegramBotClient 下载能力
- **FR-C1** 新增 `async get_file(file_id: str) -> dict`：调 Telegram `getFile`，返回含 `file_path` 的结果。
- **FR-C2** 新增 `async download_file_bytes(file_path: str) -> bytes`：从 `https://api.telegram.org/file/bot<token>/<file_path>` 下载，返回字节。
- **FR-C3** 复用现有 httpx client + bot token 加载；失败抛明确异常类型供上层捕获降级。
- **FR-C4** 音频大小上限：超过合理上限（如 config 默认 20MB / 或 duration 上限如 300s）拒绝并降级提示（防 DoS / 防超长占用）。

### FR-D：观测与安全
- **FR-D1**（#8）转写成功/降级各留结构化日志，含 backend/duration/transcript_len/reason；**不记录音频原文与完整转写文本**（隐私），转写文本长度可记。
- **FR-D2**（#5）若 D1 选 API 后端：STT API key 走 CredentialStore/env，不进 LLM 上下文、不进日志。本地 faster-whisper 无凭证需求。
- **FR-D3**（#6）任一失败不得让异常逃逸出 `_ingest_update`（防 polling loop 崩 / webhook 500）；降级是默认兜底。

---

## 7. 优雅降级矩阵（Constitution #6）

见 `research/tech-research.md §4`。原则：**永不崩 gateway，永不静默丢弃，给用户可理解回复，其他消息不受影响**。

---

## 8. 范围边界（明确不做）

- ❌ TTS / voice session / Web 音频上传 → F110 或后续
- ❌ 多语言精调 / 说话人分离 / 实时流式 → 后续
- ❌ photo / document 等其他 media type → 后续（F109 仅 voice）
- ❌ 音频原文持久化为 artifact（PoC 默认不存音频；若 plan 评估低成本可作可选项，否则不做）

---

## 9. 已知约束 / 给 F110 的接力点

- F109 建立的"入站预处理 → 回填 text → 主路径"模式是 F110 voice session 的地基；但 F110 需要的连续语音态/会话依赖 F093，F109 不触碰。
- STT 后端抽象 `SttBackend` 为 F110/TTS 对称设计预留（TTS 是反向：text → audio）。
- 若 D1 选本地 faster-whisper：F110 可复用同一模型单例；TTS 需另选（如 piper/coqui，F110 调研）。

---

## 10. 验收门（Gate）

- **0 regression** vs d6f0ec54（全量回归 baseline）。
- `pytest -m e2e_smoke` 8/8 PASS（pre-commit hook）。
- 新增单测（STT 服务 + telegram voice 接入 + 5 类降级）+ e2e（voice → text 链）全 PASS。
- **双评审 panel**（Codex + 第二模型）0 HIGH 残留（新能力 + 引入外部依赖，命中"重大架构变更/外部依赖"节点）。
- completion-report + handoff（F110）+ living-docs（blueprint/milestones）同步。
- **不主动 push，等用户拍板**。
