# F110 语音 v0.1 — 技术调研

- **Feature**: F110 语音 v0.1（STT + TTS + voice session）
- **基线**: master HEAD `1cd2083f`（F109 语音 PoC STT only 已合入）
- **分支/worktree**: `claude/hungry-franklin-e7a9c8` / `F110-voice-v01`
- **调研方式**: ① 代码库 read-only 侦察（4 块，含 file:line 锚点）；② Web 调研（TTS 选型部分由主节点 Perplexity 调研后传入，本文整理并补充验证）
- **代码根**: 仓库根 `octoagent/` 子目录；本文所有 `file:line` 锚点相对仓库根

> 散文中文 / 代码标识符英文 / 英文技术术语保原文。

---

## 1. 调研结论速览

| 维度 | 结论 |
|------|------|
| **TTS 选型推荐** | **本地 Piper（OHF-Voice/piper1-gpl）**：⚠️ **GPL-3.0 许可**（因内嵌 espeak-ng，repo 名即 `piper1-gpl`）+ 仅 onnxruntime 依赖（无 PyTorch）+ espeak-ng 已 bundle + CPU RTF ~0.008 + 中文模型 ~63MB。**许可证是 GATE_DESIGN 头号决策**（见 §2.2 + D1），因 F109 STT 栈（faster-whisper/CTranslate2）是 MIT，引入 GPL optional 依赖改变项目许可画像 |
| **格式转换** | Piper → 16-bit PCM WAV（22050Hz）；Telegram `sendVoice` 需 OGG/Opus。**PyAV 可编码**（`libopus` 编解码器，已是 faster-whisper 传递依赖，但编码路径需验证实验标志），备选 PyOgg |
| **出站 TTS 接入点** | `notify_task_result`（`telegram.py:872`）——主 Agent 回复之后，在此处把 text 转语音再发，H1 出站后处理最自然 |
| **voice session 标记落点** | `ConversationBinding.metadata`（chat 级，`telegram.py:584` 写入）——天然承载每 chat 的"voice 模式"开关，无需新建数据结构 |
| **v0.1 scope 推荐** | **异步多轮**（每轮 voice→STT→Agent→TTS→voice，依赖现有 polling/webhook + session 连续性）；实时双工推 v0.2 |
| **降级范式** | 沿用 `_reply_voice_degrade`（`telegram.py:565`）——TTS 失败退回文字回复，永不崩、永不静默 |

---

## 2. TTS 选型（采纳主节点结论 + 本次验证整理）

### 2.1 候选对比

| 方案 | 类型 | 许可证 | 依赖足迹 | CPU RTF | Python API | 中文支持 |
|------|------|--------|----------|---------|-----------|---------|
| **Piper（OHF-Voice/piper1-gpl）** ✅ **推荐（技术）** | 本地 ONNX | ⚠️ **GPL-3.0** | onnxruntime + espeak-ng(bundle) | ~0.008 | `pip install piper-tts`（v1.4.2，2026-04-02）；`PiperVoice.load()` + `synthesize_wav(text, wav_file)` | ✅ zh_CN-huayan-medium ~63MB |
| Kokoro-82M | 本地 | ✅ Apache 2.0 | **PyTorch**（~2GB+）+ 常需系统 espeak-ng | 中等 | `pip install kokoro` | 英文为主，中文有限 |
| Coqui idiap fork `coqui-tts` | 本地训练框架 | ✅ Apache 2.0 | **PyTorch 重栈**（>2GB）| 慢 | 过重 | 需单独训练 |
| rhasspy/piper（旧）| 本地 ONNX | ✅ MIT | onnxruntime（但 espeak-ng 音素化路径仍有 GPL 牵连）| ~0.008 | 已 archived 停维护 | ✅ |
| OpenAI TTS / 云 API | 云 | 商业 | 零本地 + 需 key | 网络主导 | `openai.audio.speech` | ✅（需 key） |

### 2.2 Piper 选型理由

1. **无 PyTorch**：仅 `onnxruntime`（CPU），这是相对 Kokoro/Coqui 的决定性优势——与 F109 `faster-whisper` 走轻量 CTranslate2 的哲学完全一致。
2. **espeak-ng 已 bundle**：`piper-tts` 包内嵌 espeak-ng 数据，`pip install piper-tts` 全部完成，**无需系统级 `apt/brew install espeak-ng`**，镜像 F109「PyAV bundle ffmpeg、免系统 ffmpeg」的范式（Constitution #6 优雅降级）。
3. **模型轻**：每个 voice 约 60-63MB（如 `zh_CN-huayan-medium`），首次下载友好。
4. **Python API 简洁**（验证确认，见 2.3）：两行即可完成合成。

#### 2.2.1 ⚠️ 许可证（GATE_DESIGN 头号决策，修正先前误述）

调研初稿误称 Piper 为 MIT——**实测纠正：当前维护版 `OHF-Voice/piper1-gpl` 是 GPL-3.0**（repo 名即含 `gpl`）。GPL 来源：它内嵌 espeak-ng（espeak-ng 本身 GPL）做音素化，这也正是它能「纯 pip 免系统依赖」的原因——**便利性与 GPL 是同一枚硬币的两面**。

- **旧 `rhasspy/piper`**：MIT，但已 archived 停维护，且其音素化路径同样牵连 GPL espeak-ng（不是干净的纯 MIT 方案）。
- **许可画像影响**：F109 的 STT 栈（faster-whisper + CTranslate2）均 MIT。引入 Piper 会让 OctoAgent 的 `[voice]` optional 依赖组首次带 GPL-3.0。OctoAgent 仓库公开（connor-git-yaml/OctoAgent），但 Blueprint §0 锁定**单用户深度、非分发产品**——GPL 的 copyleft 义务主要在**分发组合/衍生作品**时触发，个人自用基本无负担。
- **缓解项（供 plan/GATE 参考）**：①Piper 是 **optional 依赖**（`[voice]` extra），core 安装不含它，不污染主体；②`TtsBackend` 抽象使后端可随时替换（许可若成问题可换 Kokoro/云 API）；③若要进一步隔离 copyleft，可考虑 subprocess 调用 piper CLI（聚合边界，而非 import 链接）——但增加实现复杂度，v0.1 不建议。
- **替代（若用户要求许可洁净）**：Kokoro-82M（Apache-2.0），但代价是 **PyTorch ~2GB+**（与 F109 轻量哲学相悖）且常仍需系统 espeak-ng。云 API 违隐私 #5。

**结论**：技术上 Piper 最优；**许可证取舍交 GATE_DESIGN（D1）由用户拍板**——主节点推荐「接受 GPL-3.0 作为 optional 依赖」，理由是单用户非分发场景负担低 + 抽象可换 + 技术优势显著，但这是用户的决定。

### 2.3 Piper Python API 调用方式（验证确认）

```python
from piper import PiperVoice

# 加载模型（需 .onnx + .onnx.json 两文件）
voice = PiperVoice.load("/path/to/zh_CN-huayan-medium.onnx")

# 同步合成到 WAV file-like 对象
import io, wave
wav_buf = io.BytesIO()
with wave.open(wav_buf, "wb") as wav_file:
    voice.synthesize_wav("你好，这是 OctoAgent。", wav_file)
wav_bytes = wav_buf.getvalue()

# 流式合成（每 chunk 含 sample_rate / sample_width / sample_channels / audio_int16_bytes）
for chunk in voice.synthesize("你好"):
    ...  # 可用于实时播放（v0.1 不做）
```

模型下载：`python -m piper --download-voice zh_CN-huayan-medium`（或 `piper-tts` CLI）。

**[推断]** `synthesize_wav` 是同步 CPU-bound，与 faster-whisper 同理，应用 `asyncio.to_thread` 卸载，避免阻塞 async event loop。

### 2.4 格式转换：WAV → OGG/Opus（关键复用机会）

Piper 输出 16-bit PCM WAV（22050Hz）。Telegram `sendVoice` API 要求 OGG/Opus 格式才能在 chat 中呈现为「语音消息」气泡（圆形 waveform 样式）。

**PyAV 编码路径**：

PyAV 是 faster-whisper 的传递依赖（解码路径已验证可用），其 PyPI wheel 内置 FFmpeg 库包含 `libopus` 编解码器。PyAV 支持 OGG/Opus **编码**，但 native Opus 编码需要启用 experimental flag（`stream.codec_context.options["strict"] = "-2"`）；使用 `libopus` 编解码器则无需该标志。

**实际代码骨架**（待 plan 阶段实测验证）：

```python
import av, io

def wav_to_ogg_opus(wav_bytes: bytes) -> bytes:
    in_buf = io.BytesIO(wav_bytes)
    out_buf = io.BytesIO()
    with av.open(in_buf, "r") as in_container, \
         av.open(out_buf, "w", format="ogg") as out_container:
        in_stream = in_container.streams.audio[0]
        out_stream = out_container.add_stream("libopus", rate=in_stream.rate)
        for packet in in_container.demux(in_stream):
            for frame in packet.decode():
                frame.pts = None
                for out_packet in out_stream.encode(frame):
                    out_container.mux(out_packet)
        for out_packet in out_stream.encode(None):  # flush
            out_container.mux(out_packet)
    return out_buf.getvalue()
```

**[推断]** 若 PyAV 编码路径在 plan 实测不稳定（codec 不可用或实验标志不够），备选方案为 `PyOgg`（`OpusBufferedEncoder` + `OggOpusWriter`），需额外 pip 依赖（`pyogg`），也无系统依赖。另一备选：使用 `sendAudio`（Telegram 接受更多格式如 MP3/M4A）但 UX 呈现为「文件」而非「语音消息气泡」，降级体验较差。

**plan 阶段必做验证**：在真实 venv（含 faster-whisper 传递安装的 PyAV）里运行上述骨架，确认 `libopus` 可用且无需系统依赖。

---

## 3. 代码侦察（4 块，含 file:line 锚点）

### 侦察块 1：出站 TTS 接入点

#### 1.1 `notify_task_result`（主接入点）

**文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/telegram.py:872-891`

当前逻辑（已读验证）：

```
notify_task_result(task_id)
  → get_task(task_id)
  → _resolve_reply_target(task_id) → dict{chat_id, reply_to_message_id, message_thread_id}
  → _build_result_text(status, events) → text: str
  → bot_client.send_message(chat_id, text, ...)   ← TTS 接入点：在此处 text→音频后改调 send_voice
  → _remember_outbound_reply_thread(target, sent_message)
```

**TTS 接入设计**：在 `send_message` 调用之前插入：若该 task 的 chat 处于「voice 模式」→ `tts_service.synthesize(text)` → `bot_client.send_voice(chat_id, ogg_bytes, ...)`；TTS 失败降级回 `send_message`（文字）。全程不修改 `_build_result_text`，不改 `_resolve_reply_target`，H1 出站后处理边界清晰。

#### 1.2 `TelegramBotClient` 现有方法清单（`telegram_client.py`，已读验证）

| 方法 | 行（约） | 用途 |
|------|----------|------|
| `_request(method, *, payload, timeout)` | 148 | 通用 Bot API POST |
| `get_me()` | 188 | getMe |
| `send_message(chat_id, text, ...)` | 192 | sendMessage |
| `answer_callback_query(...)` | 223 | answerCallbackQuery |
| `edit_message_text(...)` | 239 | editMessageText |
| `get_updates(*, offset, timeout_s, limit)` | 261 | getUpdates（polling） |
| `get_file(file_id)` | 288 | getFile（F109 新增） |
| `download_file_bytes(file_path, *, max_bytes)` | 295 | 流式下载（F109 新增） |

**F110 需新增 `send_voice`**：

```python
async def send_voice(
    self,
    chat_id: str | int,
    voice: bytes,            # OGG/Opus 二进制
    *,
    duration: int | None = None,  # 秒，可选（Telegram 用于进度条）
    reply_to_message_id: str | int | None = None,
    message_thread_id: str | int | None = None,
    disable_notification: bool = True,
) -> TelegramMessage:
    """sendVoice：multipart/form-data 上传 OGG/Opus，在 chat 呈现为语音消息气泡。"""
```

注意：`sendVoice` 是 multipart form-data 上传（`voice` 字段为文件），**不走** `_request` 的 JSON POST 路径，需单独写一个 multipart httpx 请求（仍复用 `_load_bot_token()` + `base_url`）。这是与 `send_message` 最大的实现差异。

#### 1.3 其他出站 send 点分析（scope 判断）

- `notify_approval_event`（`telegram.py:893-939`）：审批请求/结果通知，含 inline keyboard。**不建议加 TTS**：审批通知需要用户看按钮操作，语音无法承载 inline keyboard 语义；加 TTS 增加噪声。
- `_reply_voice_degrade`（`telegram.py:565-582`）：降级文字回复。**不加 TTS**：降级路径本身就是 TTS 失败的兜底，再走 TTS 逻辑矛盾。
- `_handle_control_command` 的回复（`telegram.py:635-648`）：控制命令响应（如 `/status`）。**不加 TTS**：控制命令用户预期是文字，语音化无收益。

**推荐 v0.1 出站 TTS 范围**：**仅 `notify_task_result`**。理由：这是主 Agent 实质回复的唯一出口；审批/通知/控制命令保持文字，避免噪声 + 范围爆炸。voice session 模式下用户发一条 voice → 得到一条 voice 回复，语义自洽。

### 侦察块 2：voice session 状态（出站如何知道「该用语音回」）

#### 2.1 核心问题

`notify_task_result` 收到 `task_id`，如何知道「这条 task 应该用语音回复」？需要追踪「voice 模式」状态，有以下候选落点：

#### 2.2 候选落点分析

**候选 A：`ConversationBinding.metadata`（`telegram.py:584`）**

- **文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/telegram.py:584-618`
- **现有结构**（已读验证）：`_record_conversation_binding` 写入 `metadata = {"last_message_thread_id": ..., "last_reply_thread_root_id": ...}`，通过 `binding_store.upsert_runtime_binding` 落 SQLite。
- **`ConversationBinding` 模型**（`octoagent/packages/core/src/octoagent/core/models/conversation_binding.py:36-72`）：含 `metadata: dict[str, Any]`，天然 KV bag。
- **评估**：`ConversationBinding` 以 `(platform, conversation_id)` 为 key（telegram chat_id），是 chat 级持久化状态——**voice 模式是 chat 级决策（一个 chat 整体开/关语音），落这里语义最自然**。`notify_task_result` 通过 `_resolve_reply_target(task_id)` 得到 `chat_id`，再从 binding store 查 `metadata["voice_mode"]` 即可。无需新建任何数据结构。

**候选 B：`NormalizedMessage.metadata`**

- **文件**: `octoagent/packages/core/src/octoagent/core/models/message.py`
- **评估**：只能传递入站单条消息的元数据，不能跨轮持久化「这个 chat 处于 voice 模式」。**不适合**：每次都要入站 voice 消息才能感知，无法支持「用户开启 voice 模式后发文字也得语音回」等未来语义。

**候选 C：`AgentSession.metadata`**

- **文件**: `octoagent/packages/core/src/octoagent/core/models/agent_context.py:335-368`
- **`AgentSession` 字段**（已读验证）：`surface: str`、`thread_id: str`、`rolling_summary: str`、`memory_cursor_seq: int`、`metadata: dict[str, Any]`、`recent_transcript: list[...]`
- **评估**：AgentSession 是 Agent 内部会话持久化，与渠道出站寻址（`notify_task_result` 走 `chat_id`）是不同维度。从 `notify_task_result` 路径查 AgentSession 需要额外的 session→task→chat 关联查询，比 ConversationBinding 复杂。**可选但非首选**。若未来 voice session 要影响 Agent 行为（如提示词里注入「当前是语音会话」），再扩展此路径更合适。

**推荐落点：`ConversationBinding.metadata["voice_mode"]`**

写入时机：用户通过控制命令 `/voice on` 或 `/voice off` 切换（`_handle_control_command` 路径），更新 `ConversationBinding.metadata`。入站 voice message 时也可自动将 chat 标记为 voice 模式（策略由 GATE_DESIGN 决定）。

#### 2.3 `AgentSession` 连续性（F093 依赖）

- **锚点**：`octoagent/packages/core/src/octoagent/core/models/agent_context.py:335`
- F093（Worker Full Session Parity）已完成，`AgentSession` 含 `rolling_summary` + `memory_cursor_seq` + `recent_transcript`，会话连续性底座已就位。
- **voice session 设计原则**：voice session **无需新建 AgentSession 子类型**，只需「每轮 voice message 走 `_ingest_update` → 与文字消息**完全同路** → 复用现有 session 连续性」。「voice 模式」是渠道层标记（binding metadata），不侵入 Agent 会话模型。这符合 H1 铁律（不新建 Agent 模式）。
- `AgentSessionKind`（`agent_context.py:130`）：现有 `MAIN_BOOTSTRAP`/`WORKER_INLINE` 等枚举值无需扩展——voice 会话仍是 `MAIN_BOOTSTRAP`（或同等主 Agent 会话类型）。

#### 2.4 幂等边界确认

- **`_build_idempotency_key`**（`telegram.py:750-752`，已读验证）：`return f"telegram:{context.update_id}:{context.chat_id}:{context.message_id}"`
- **多轮 voice session 幂等分析**：每条入站 voice message 仍是独立 Telegram update（独立 update_id），单轮幂等键 `telegram:<update_id>:<chat_id>:<message_id>` 天然覆盖每一轮。「voice session」是多轮的**逻辑串联**，不是新的幂等单元——不需要新的幂等机制。F109 handoff §5 的理解正确，本次侦察确认。

#### 2.5 voice session scope：异步多轮 vs 实时双工

| 维度 | 异步多轮（推荐 v0.1） | 实时双工（v0.2）|
|------|-----------------------|-----------------|
| 工作模式 | 用户发语音 → 异步处理 → bot 回语音（同 text 消息时序） | WebRTC/WebSocket 持续连接，实时 STT+TTS 流 |
| 依赖 | 现有 polling/webhook + session 连续性（已就绪）| 新建 WebRTC/长连接通道，F105 v2+ |
| 延迟 | 数秒（STT + Agent 推理 + TTS，可接受） | 亚秒级打断/响应（复杂） |
| 实施规模 | **M**（TTS backend + send_voice + voice_mode 标记） | **XL**（新协议层）|
| Telegram 限制 | Telegram 原生 voice message 是异步的，无 WebRTC | Telegram 不支持实时音频流，须走 Web 端独立页面 |
| 与 H1 契合度 | ✅ 完全兼容（每轮仍走主 Agent 同路） | ⚠️ 需要流式 Agent 输出，与现有 event-sourcing 模式有摩擦 |

**推荐**：v0.1 只做异步多轮。实时双工复杂度 XL、Telegram 平台本身不支持实时音频流、与现有架构摩擦大——留 v0.2。

### 侦察块 3：配置与依赖

#### 3.1 config_schema.py 位置与结构

- **文件**: `octoagent/apps/gateway/src/octoagent/gateway/services/config/config_schema.py`
- **根模型**: `OctoAgentConfig`（line 641）：`providers` / `model_aliases` / `runtime: RuntimeConfig` / `memory: MemoryConfig` / `front_door: FrontDoorConfig` / `channels: ChannelsConfig` / `security: SecurityConfig`
- **F109 STT 配置策略**（handoff §2 确认）：F109 全走 env（`OCTOAGENT_STT_MODEL`/`_DEVICE`/`_COMPUTE_TYPE`/`_LANGUAGE`/`_MAX_DURATION_S`/`_MAX_BYTES`），**未动 octoagent.yaml schema**。
- **F110 TTS 配置推荐**：
  - **v0.1 沿用 env 路径**（镜像 STT）：`OCTOAGENT_TTS_BACKEND`（piper/none）/ `OCTOAGENT_TTS_VOICE_MODEL`（模型 onnx 路径或名称）/ `OCTOAGENT_TTS_LANGUAGE`（default: zh_CN）/ `OCTOAGENT_TTS_ENABLED`（bool）
  - **理由**：config_schema.py 改动需过 wizard + schema 版本管理，v0.1 快速迭代阶段 env 更灵活；若 F110 之后 voice 成为稳定功能，可在后续 Feature 将 voice 配置块 promote 到 yaml（`channels.telegram.voice_reply: bool` + `voice.tts_backend: piper|api`）。

#### 3.2 `[project.optional-dependencies].voice` 组（`apps/gateway/pyproject.toml`）

**当前内容**（line 45-47，已读验证）：

```toml
[project.optional-dependencies]
voice = [
    "faster-whisper>=1.0,<2.0",
]
```

**F110 新增**：`piper-tts>=1.4,<2.0`（当前版本 1.4.2，2026-04-02 发布）。

若 PyAV 编码 WAV→Opus 实测不可行，则额外加 `pyogg>=0.8,<1.0`。PyAV 本身是 faster-whisper 的传递依赖，不需单独声明。

安装命令（含 TTS）：`uv pip install -e '.[voice]'`（voice 组同时含 STT+TTS，保持一组安装）。

#### 3.3 STT service wiring 路径（TTS 要镜像的参照）

- **构造点**（`octo_harness.py:510-522`，已读验证）：
  ```python
  from ..voice import build_default_stt_service
  telegram_service = TelegramGatewayService(
      ...,
      stt_service=build_default_stt_service(),
  )
  ```
- **`TelegramGatewayService.__init__`** 接收 `stt_service: SpeechToTextService | None = None`（`telegram.py:232`），`None` = 语音转写未启用，优雅降级。
- **TTS wiring 设计**（镜像 STT）：
  1. `gateway/voice/tts.py`：`TtsResult` + `TtsBackend` Protocol（对称 `SttBackend`）+ `TextToSpeechService`
  2. `gateway/voice/piper_backend.py`：`PiperTtsBackend`（懒加载单例，`asyncio.to_thread`）+ `build_default_tts_service()`
  3. `octo_harness.py`：在 telegram_service 构造时注入 `tts_service=build_default_tts_service()`
  4. `telegram.py`：`__init__` 新增 `tts_service: TextToSpeechService | None = None`，`notify_task_result` 按 voice_mode 条件调用

### 侦察块 4：验证基线（PYTHONPATH 锁定）

#### 4.1 worktree .venv symlink 陷阱

- **背景**：此 worktree（`F110-voice-v01`）的 `.venv` 是 symlink 指向主仓 `octoagent/.venv`。裸 `pytest` 或 `uv run pytest` 会跑**主仓 master 的 src**（editable 安装指向 master worktree），而非 F110 worktree 的代码 → 假 0 regression。
- **F109 先例**（`plan.md:14-20` 确认）：F109 明确记录了此问题，精确命令为 PYTHONPATH 锁定。

#### 4.2 精确 pytest 调用命令

```bash
# 在 worktree 内的 octoagent/ 目录执行：
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F110-voice-v01/octoagent

WORKTREE=/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F110-voice-v01/octoagent

PYTHONPATH=\
${WORKTREE}/apps/gateway/src:\
${WORKTREE}/packages/core/src:\
${WORKTREE}/packages/provider/src:\
${WORKTREE}/packages/policy/src:\
${WORKTREE}/packages/memory/src:\
${WORKTREE}/packages/protocol/src:\
${WORKTREE}/packages/sdk/src:\
${WORKTREE}/packages/skills/src:\
${WORKTREE}/packages/tooling/src \
uv run --no-sync python -m pytest <tests> -p no:cacheprovider
```

#### 4.3 各 package 的 src 路径

| Package | src 路径（相对 worktree `octoagent/`）|
|---------|--------------------------------------|
| gateway | `apps/gateway/src` |
| core | `packages/core/src` |
| provider | `packages/provider/src` |
| policy | `packages/policy/src` |
| memory | `packages/memory/src` |
| protocol | `packages/protocol/src` |
| sdk | `packages/sdk/src` |
| skills | `packages/skills/src` |
| tooling | `packages/tooling/src` |

#### 4.4 e2e_smoke 跑法

```bash
PYTHONPATH=... uv run --no-sync python -m pytest -m e2e_smoke \
  apps/gateway/tests/ -p no:cacheprovider
```

**e2e_smoke marker** 定义（`pyproject.toml:70`）：`"e2e_smoke: F087 smoke 5 域真实 LLM e2e（pre-commit hook 自动跑，≤ 180s）"`。目标 8/8 PASS（F087 含 5 域 smoke，3 个 full 场景跑 stub 路径）。

#### 4.5 基线对账

- 基线 commit：`1cd2083f`
- 对账方式：先在 worktree 空跑（不改任何代码）得 N_baseline passed；改完后跑同命令得 N_after passed；要求 `N_after >= N_baseline` 且 `failed == 0`。
- **F109 合入后当前全量约 4135+ passed**（F108b 后数据，F109 +32 新增）——以实测 worktree 空跑数字为准。
- **不要真跑全量测试**（此文档阶段）；plan 开始时跑一次确认基线数字。

---

## 4. voice session scope 建议（给 GATE_DESIGN）

### D2 — voice session 模式切换机制

两个设计选项：

**选项 A（推荐）— 控制命令显式切换**

用户发 `/voice on` / `/voice off` → `_handle_control_command` → 更新 `ConversationBinding.metadata["voice_mode"]`。
- 优点：用户有意识决策；不打扰不发 voice 的用户；与现有 control_plane 模式匹配。
- 缺点：需要用户知道命令。

**选项 B — 入站 voice 自动标记**

用户发一条 voice message → 自动将该 chat 标记为 voice 模式（bot 后续回复也用语音）；`/voice off` 关闭。
- 优点：零配置，用户自然触发。
- 缺点：用户可能只是偶发发 voice，不期望所有回复都变成语音。

**推荐**：GATE_DESIGN 拍板两者之一，或两者组合（自动标记 + 命令覆盖）。

### D3 — 入站 voice 自动切 voice 模式？

关联 D2 选项 B：若采用"入站 voice 自动标记"，则每次 `_handle_voice_message` 成功后，在 `_record_conversation_binding` 写入时追加 `metadata["voice_mode"] = True`。关闭时需 `/voice off` 命令。

**GATE_DESIGN 必须拍板**：D2 + D3，否则 spec 无法写。

---

## 5. 降级面清单（Constitution #6）

| 失败场景 | 降级行为 |
|----------|----------|
| `piper-tts` 未安装 / `is_available()=False` | TTS service 标记不可用 → `notify_task_result` 退回 `send_message`（文字），日志记 `tts_unavailable` |
| Piper 模型文件缺失 / 初始化 raise | lazy load 捕获 → `is_available()=False` → 同上 |
| `synthesize_wav` 抛异常 | `TextToSpeechService.synthesize` 兜底 → 返回 `TtsResult(ok=False, reason=...)` → 退回文字 |
| WAV→OGG/Opus 格式转换失败 | 捕获 → 退回文字 |
| `send_voice` Telegram API 调用失败 | `contextlib.suppress` 包裹 → 退回 `send_message`（文字） |
| voice session 中断（binding 查不到 voice_mode）| 默认 `voice_mode=False` → 文字回复，不崩 |
| TTS 合成超时（模型加载慢）| `asyncio.to_thread` 加 timeout guard（如 30s）→ 超时退回文字 |

**沿用范式**：所有降级均经 `_reply_voice_degrade` 风格的 suppress + log + 用户通知（或静默退回文字），**永不崩 gateway，永不静默丢弃**。

---

## 6. 验证命令汇总（§0 PYTHONPATH）

```bash
# 1. 新增单测（快速验证，每个 Phase 后）
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F110-voice-v01/octoagent

PYTHONPATH=\
$(pwd)/apps/gateway/src:\
$(pwd)/packages/core/src:\
$(pwd)/packages/provider/src:\
$(pwd)/packages/policy/src:\
$(pwd)/packages/memory/src:\
$(pwd)/packages/protocol/src:\
$(pwd)/packages/sdk/src:\
$(pwd)/packages/skills/src:\
$(pwd)/packages/tooling/src \
uv run --no-sync python -m pytest \
  apps/gateway/tests/test_tts_service.py \
  apps/gateway/tests/test_telegram_voice.py \
  -p no:cacheprovider -v

# 2. e2e_smoke（pre-commit gate，8/8）
PYTHONPATH=... uv run --no-sync python -m pytest -m e2e_smoke \
  apps/gateway/tests/ -p no:cacheprovider

# 3. 全量回归（0 regression 对账，最终验证前）
PYTHONPATH=... uv run --no-sync python -m pytest \
  packages/core/tests packages/provider/tests packages/protocol/tests \
  packages/tooling/tests packages/skills/tests packages/policy/tests \
  packages/memory/tests apps/gateway/tests tests \
  -p no:cacheprovider --tb=short -q
```

---

## 7. 给 spec/plan 的开放问题清单（GATE_DESIGN 必决）

| # | 问题 | 选项 | 推荐 |
|---|------|------|------|
| **D1** | TTS 后端选型 **+ 许可证取舍** | A：本地 Piper（GPL-3.0，技术最优，optional 依赖）/ B：本地 Kokoro（Apache-2.0，但 +PyTorch ~2GB）/ C：云 API（留缝不实现，违隐私）| **A（接受 GPL）**——单用户非分发负担低 + 抽象可换；许可洁净优先则选 B |
| **D2** | voice session 模式切换机制 | A：`/voice on` 控制命令 / B：入站 voice 自动标记 / C：A+B 组合 | 待用户拍板 |
| **D3** | 入站 voice 是否自动开启 voice 模式 | 是（每次 voice 入站设 `voice_mode=True`）/ 否（需显式命令）| 取决于 D2 |
| **D4** | WAV→OGG/Opus 格式转换实现 | PyAV（传递依赖复用）/ PyOgg（新依赖，更专门）| PyAV 优先，plan 实测验证 |
| **D5** | 中文还是多语言 TTS？ | v0.1 仅中文（`zh_CN-huayan-medium`）/ 跟随 STT 语言自动选 | v0.1 仅中文，扩展留后续 |

### 其他待 spec 定义的边界问题

- **TTS timeout**：Piper 是 CPU-bound，首次加载模型 + 合成可能数秒。是否加 `asyncio.wait_for` timeout guard？建议是（如 30s），超时退回文字。
- **voice_mode 持久化范围**：ConversationBinding 是 chat 级别的，跨 bot restart 保留。这是期望行为（语音模式持久）还是非期望（每次 restart 重置）？
- **音频原文是否落 artifact**：voice session 连续多轮，是否保存用户的音频原文（作为 artifact）？F109 PoC 不存，F110 是否延续？（推荐 v0.1 不存，节省存储 + 简化实现）
- **`AGENT_SESSION_TURN_PERSISTED` 事件中是否记录「voice 输入」**：目前 turn 记录不区分文字/语音来源。v0.1 是否需要在 turn metadata 中标记 `input_kind=voice`？建议是，供后续 analytics。

---

## 附录：关键锚点速查表

| 组件 | 文件 | 行（约） |
|------|------|----------|
| STT Backend Protocol `SttBackend` | `apps/gateway/src/octoagent/gateway/voice/stt.py` | 35 |
| `SpeechToTextService` | `apps/gateway/src/octoagent/gateway/voice/stt.py` | 46 |
| `FasterWhisperBackend` + `build_default_stt_service` | `apps/gateway/src/octoagent/gateway/voice/faster_whisper_backend.py` | 28 / 95 |
| `TelegramGatewayService.__init__`（含 stt_service 参数） | `apps/gateway/src/octoagent/gateway/services/telegram.py` | 222 |
| `_ingest_update`（H1 voice 分支插入点） | `apps/gateway/src/octoagent/gateway/services/telegram.py` | 353 |
| voice 分支判断（`context.voice is not None and not context.text.strip()`） | `apps/gateway/src/octoagent/gateway/services/telegram.py` | 407 |
| `_handle_voice_message`（STT 主链路） | `apps/gateway/src/octoagent/gateway/services/telegram.py` | 478 |
| `_reply_voice_degrade`（降级范式） | `apps/gateway/src/octoagent/gateway/services/telegram.py` | 565 |
| `_record_conversation_binding`（voice_mode 标记落点） | `apps/gateway/src/octoagent/gateway/services/telegram.py` | 584 |
| `notify_task_result`（TTS 出站接入点） | `apps/gateway/src/octoagent/gateway/services/telegram.py` | 872 |
| `notify_approval_event`（不加 TTS） | `apps/gateway/src/octoagent/gateway/services/telegram.py` | 893 |
| `_build_idempotency_key`（单轮幂等）| `apps/gateway/src/octoagent/gateway/services/telegram.py` | 750 |
| `TelegramBotClient` 全部方法 | `apps/gateway/src/octoagent/gateway/services/telegram_client.py` | 112-329 |
| `ConversationBinding` 模型 | `packages/core/src/octoagent/core/models/conversation_binding.py` | 36 |
| `AgentSession` 模型 | `packages/core/src/octoagent/core/models/agent_context.py` | 335 |
| STT service wiring（octo_harness） | `apps/gateway/src/octoagent/gateway/harness/octo_harness.py` | 510 |
| voice optional-dependencies 组 | `apps/gateway/pyproject.toml` | 45 |
| pytest markers 定义 | `octoagent/pyproject.toml` | 69 |
