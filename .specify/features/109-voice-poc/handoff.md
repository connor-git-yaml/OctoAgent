# F109 → F110 Handoff(语音 v0.1:STT+TTS+voice session)

- **F109 状态**:语音 PoC(STT only)实现完成,双评审 0 HIGH(详见 completion-report.md)。
- **基线**:F109 基于 master d6f0ec54;合入时需 rebase 到当前 origin/master(F109 期间已前进到 3208f728,改动文件不与 F109 冲突,见下)。
- **F110 依赖**:F093(Worker Full Session Parity)——voice session 的连续会话态依赖它,F109 不触碰。

---

## 1. F109 建立的地基(F110 直接复用)

### 1.1 STT 服务层(可替换薄抽象)
- `apps/gateway/src/octoagent/gateway/voice/`:
  - `stt.py`:`SttResult`(pydantic)/ `SttBackend`(Protocol:`name` + `is_available()` + `async transcribe(audio,*,mime,filename)->SttResult`)/ `SpeechToTextService`(异常兜底 + 判空归一)。
  - `faster_whisper_backend.py`:`FasterWhisperBackend`(本地,懒加载单例 + double-checked locking + `asyncio.to_thread` + env 配置)+ `build_default_stt_service()`。
  - **F110 TTS 对称设计**:TTS 是反向(text→audio)。建议平行新增 `TtsBackend` Protocol + `TextToSpeechService` + 默认本地后端(如 piper / coqui,F110 调研)。`SttBackend` 的抽象形状可直接镜像。
- **模型单例可复用**:F110 voice session 若用同一 faster-whisper 模型,复用 `FasterWhisperBackend` 实例即可(已线程安全)。

### 1.2 H1 入站预处理范式(关键)
- F109 的核心范式:**入站消息预处理 → 回填 `context.text` → 走与文字消息完全相同的 chat 主路径**。位置:`services/telegram.py` `_ingest_update` 在空文本检查前插入 voice 分支(`_handle_voice_message`)。
- **F110 voice session 必须沿用 H1**:即便是连续语音会话,每一轮"语音→text"仍是入站预处理,转写文本走主 Agent;TTS"text→语音"是**出站后处理**(在主 Agent 回复之后)。不要为 voice session 新建 Agent 模式 / 决策环。
- **出站 TTS 接入点**:F109 未做出站。F110 的 TTS 应挂在**主 Agent 回复 → 渠道出站**的路径上(telegram `notify_task_result` / send 出站处),把回复文本转语音再发,而非在 Agent 内部。

### 1.3 Telegram 媒体下载能力
- `TelegramBotClient.get_file(file_id)` + `download_file_bytes(file_path,*,max_bytes)`(**流式 + 超限即断**)已就绪。F110 若需下载更多媒体(如用户发的音频文件)可直接复用。
- `TelegramVoice` model + `TelegramMessage.voice` 字段(polling 路径存活必需)+ `TelegramInboundContext.voice` + `_extract_voice_ref` 已就绪。

### 1.4 优雅降级范式(#6)
- `_reply_voice_degrade(context, text, *, reason)` 统一降级回复 + 观测日志。F110 新增失败面(TTS 合成失败 / voice session 中断)应沿用:**永不崩、永不静默丢弃、给用户可理解回复**。

---

## 2. F110 需要新建/扩展(F109 明确不做)

| 能力 | F109 | F110 |
|------|------|------|
| STT(语音→文字) | ✅ 本地 faster-whisper | 复用 |
| TTS(文字→语音) | ❌ | **新建** `TtsBackend` + 出站接入 |
| voice session(连续语音态) | ❌ | **新建**(依赖 F093) |
| Web 端音频上传 UI | ❌(仅 telegram voice) | 可选扩展 |
| 多语言精调 / 说话人分离 / 流式 | ❌ | 视需求 |
| 音频原文持久化 artifact | ❌(PoC 不存) | 若 voice session 需回放则评估 |

---

## 3. F109 已知约束 / 给 F110 的注意点

1. **STT 服务位置**:F109 放在 `gateway/voice/`(它消费 telegram bytes、对渠道无感知,但属 gateway 入站预处理)。若 F110 让 voice 能力跨渠道/被 core 多处用,可评估 promote 到 `packages/core`;F109 不预先抽象(PoC 纪律)。
2. **STT 配置**:F109 用 env(`OCTOAGENT_STT_MODEL`/`_DEVICE`/`_COMPUTE_TYPE`/`_LANGUAGE`/`_MAX_DURATION_S`/`_MAX_BYTES`),**未动 `octoagent.yaml` schema**。F110 若做用户可视的语音设置,应 promote 到 yaml(参考 `config_schema.py` + wizard),并在 USER.md/设置 UI 暴露。
3. **faster-whisper optional 依赖**:在 `apps/gateway/pyproject.toml` 的 `[project.optional-dependencies].voice`。真实启用需 `uv pip install -e '.[voice]'` + 首次下模型。未装则优雅降级(语音未启用回复)。F110 文档应明确安装步骤。
4. **`mime` 参数预留**:`SttBackend.transcribe` 带 `mime` 但 faster-whisper 不用(PyAV 靠 `buf.name` 扩展名嗅探)。这是为 F110/云 API 后端留的缝(云 API 常需 content-type)。
5. **幂等**:voice 走现有 `_build_idempotency_key`(telegram:update_id:chat_id:message_id),F109 在转写前做幂等预检避免重复转写。F110 voice session 多轮交互的幂等边界需重新设计(单轮幂等 ≠ 会话幂等)。
6. **合入 rebase**:F109 基线 d6f0ec54,origin/master 已前进到 3208f728(改了 `test_f105v02_ingress.py` / `test_telegram_service.py`,**不与 F109 改动文件冲突**——F109 改 telegram.py/telegram_client.py + 新增文件)。合入前 rebase onto origin/master 并重跑回归。

---

## 4. 测试资产(F110 可复用模板)
- `apps/gateway/tests/test_stt_service.py`:STT 服务单测 + Fake backend 模板(零依赖真 faster-whisper)。
- `apps/gateway/tests/test_telegram_voice.py`:`FakeVoiceBotClient`(含 get_file/download)+ `FakeSttService` + `_voice_update` helper + 全链 e2e + 降级矩阵模板。F110 TTS/voice session 测试可镜像。
