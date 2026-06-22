# F109 语音 PoC — 技术调研（块 A）

- **Feature**: F109 语音 PoC（STT only，单向语音输入 → text）
- **基线**: master d6f0ec54 / 分支 `feature/109-voice-poc` / worktree `F109-voice`
- **调研方式**: ① 代码库 read-only 侦察（F105 telegram adapter inbound 路径，含 file:line 锚点）；② Web 调研（Perplexity，STT 选型 2025-2026 现状）
- **代码根**: 实际代码在仓库根 `octoagent/` 子目录（`packages/` `apps/` 均在其下）

> 散文中文 / 代码标识符英文 / 英文技术术语保原文。

---

## 1. 调研结论速览（决策输入）

| 维度 | 结论 |
|------|------|
| **STT 选型推荐** | **本地 faster-whisper**（隐私 + 零边际成本 + 干净安装 + 离线可用），作为 GATE_DESIGN 决策点 D1 回用户拍板 |
| **接入点** | 复用 F105 telegram inbound 路径：`services/telegram.py` `_ingest_update` / `_extract_context`，STT 文本回填 `context.text` 后走**完全相同**的 chat 主路径（满足 H1） |
| **依赖足迹** | faster-whisper → 传递依赖 PyAV（pip wheel 内置 ffmpeg 库，**无需系统 ffmpeg**），可直接解码 Telegram OGG/Opus；作为 **optional 依赖 + lazy import + 优雅降级**（沿用 F106 watchdog 先例） |
| **优雅降级** | lib 未装 / 模型缺失 / 转写失败 / 空结果 → 给用户明确回复"语音转写不可用"，不静默丢弃、不崩（Constitution #6） |
| **隐私** | 本地路径音频不出设备，零第三方上传（Constitution #5 + OctoAgent 单用户隐私导向） |

---

## 2. STT 选型调研（Web，2025-2026 现状）

### 2.1 四个候选对比

| 方案 | 类型 | 隐私 | 成本 | 安装/运维 | 延迟（短语音 CPU） | Python 集成 |
|------|------|------|------|-----------|---------------------|-------------|
| **faster-whisper** | 本地（CTranslate2 重实现 Whisper） | ✅ 全本地，音频不出设备 | ✅ 零边际成本 | 中：pip 装 + 首次下模型（base ~150MB / small ~500MB），**无系统 ffmpeg** | small/base 量化后近实时 | ✅ 官方 `faster-whisper` 包，几行调用 |
| **whisper.cpp** | 本地（C/C++ ggml） | ✅ 全本地 | ✅ 零边际成本 | 中：超小足迹，适合树莓派/嵌入；但 Python 绑定较薄，常走 CLI/HTTP 包装 | 近实时 | ⚠️ Python 绑定低层，需手工 wire subprocess/HTTP |
| **OpenAI Whisper API** | 云 API | ❌ 音频上传 OpenAI | ~$0.006/min（单用户绝对值小但经常性 + 需 key） | ✅ 零基础设施 | 网络往返主导，亚秒-数秒 | ✅ 官方 client，发字节收 JSON |
| **Deepgram** | 云 API（可企业 on-prem） | ❌ 默认云（on-prem 需合同） | batch ~$0.0043/min / streaming ~$0.0077/min | 企业向，单用户偏重 | 低延迟优化 | ✅ 官方 Python SDK |

来源：Perplexity 综合多篇 2026 对比文（faster-whisper CTranslate2 2-4× 加速 / OpenAI Whisper $0.006/min / Deepgram batch $0.0043/min / faster-whisper 无需系统 ffmpeg 依赖 PyAV）。

### 2.2 关键技术事实（影响安装与降级故事）

- **faster-whisper 不需系统 ffmpeg**：官方文档明确 "FFmpeg does not need to be installed on the system"，音频经 **PyAV** 解码，PyAV 的 PyPI wheel 已内置 FFmpeg 库（libavformat/libavcodec/Opus 解码器）。常见 `pip install faster-whisper` 路径零系统依赖。
- **可直接解码 in-memory OGG/Opus（BytesIO）**：`model.transcribe(buffer)` 接受文件路径或 file-like 对象（实现 `read()`/`seek()`，`BytesIO` 满足）。Telegram voice message 正是 OGG/Opus，可不落盘直接转写（给 `BytesIO` 设 `.name="voice.ogg"` 辅助扩展名识别更稳）。
- **模型单例**：在进程内复用一个 `WhisperModel` 实例，避免每次加载，短语音近实时。
- **CPU-only 可行**：small/base + INT8 量化在现代桌面 CPU 上短片段近实时；后续若 CPU 延迟不够再上 GPU 或换 whisper.cpp 小模型。

### 2.3 推荐与理由（D1 决策点）

**推荐：faster-whisper（本地）。**

1. **隐私是关键权衡因素**：OctoAgent 是隐私导向单用户个人 OS（Constitution #5 secrets 不进 LLM 上下文 / Blueprint §0 锁单用户深度）。云 API 把音频上传第三方与该哲学正面冲突。
2. **PoC 目标是"验证可行性"**：本地路径无需账号/key/网络，端到端证明"语音 → STT → text → chat"管线打通，是最干净的可行性验证。
3. **零边际成本 + 零运维账号**：单用户长期使用免费、无 vendor lock-in。
4. **安装故事干净**：无系统 ffmpeg，optional 依赖 + lazy import + 优雅降级（F106 watchdog 先例），未装时降级不阻塞 gateway。

**代价（须如实告知用户）**：① 首次需下模型（base ~150MB），② 转写时占本地 CPU/RAM 几秒（短语音），③ 模型文件占盘。

**何时反选 API**：若用户更看重"零本地足迹/零模型下载"且接受音频上传第三方 + 经常性小额成本 + 配 key，则选 OpenAI Whisper API。F109 设计将 STT 后端做成可替换缝（薄 backend 抽象），后端切换对上层 telegram 接入路径透明。

---

## 3. 接入点侦察（F105 Telegram inbound，含 file:line）

### 3.1 入站主链路

| 文件 | 行 | 职责 |
|------|----|------|
| `apps/gateway/src/octoagent/gateway/routes/telegram.py` | 15-51 | webhook 端点 `/api/telegram/webhook`，调 `service.handle_webhook_update` |
| `apps/gateway/src/octoagent/gateway/services/telegram.py` | 301-322 | `handle_webhook_update` webhook 入口 → `_ingest_update` |
| `apps/gateway/src/octoagent/gateway/services/telegram.py` | 324-351 | `_polling_loop` 也调 `_ingest_update`（**两路共用，改一处两路都覆盖**） |
| `apps/gateway/src/octoagent/gateway/services/telegram.py` | **353-409** | **`_ingest_update` 核心处理器** |
| `apps/gateway/src/octoagent/gateway/services/telegram.py` | **607-671** | **`_extract_context` 字段提取（静态方法）** |

### 3.2 当前消息类型支持

- **text**：✅（`_extract_context` line 632 `message.get("text")`）
- **voice / photo / document**：❌ 完全无处理。voice message（`message.voice`，OGG/Opus）当前在 line 367 `if not context.text.strip(): return ignored` 被**静默丢弃**（不报错也不回复）。

### 3.3 最小改动接入点（H1-clean）

核心洞察：`_ingest_update` 在 line 393 用 `context.text` 构造 `NormalizedMessage`，line 404 用 `context.text` 调 `task_runner.enqueue`。**只要在 line 367（空文本检查）之前把 STT 转写文本填进 `context.text`，下游 create_task + enqueue + 主 Agent 推理完全不变** —— 这正是 H1（语音是输入预处理，转 text 后仍走主 Agent，不改 Agent 模型）。

改动点：
1. **`_extract_context`（607-671）**：检测 `message.get("voice")`，提取 `{file_id, mime_type, duration, file_size}` 注入 `TelegramInboundContext`（新增 `voice` 字段）。
2. **`_ingest_update`（353-409）**：`_extract_context` 之后、空文本检查（367）之前，若 context 含 voice 且无 text → 调 STT（下载音频 → 转写）→ 得到文本，用其重建/回填 context.text。失败 → 优雅降级回复 + return ignored。
3. **`TelegramBotClient`（services/telegram_client.py 96-271）**：新增 `get_file(file_id)`（调 Telegram `getFile`）+ `download_file_bytes(file_path)`（下载二进制到内存）。当前**无任何文件下载能力**，需新增。
4. **新 STT 服务模块**：`SpeechToTextService`（faster-whisper backend，lazy import + 优雅降级），输入音频字节 → 输出 `SttResult{text, ok, reason}`。

### 3.4 NormalizedMessage 现状

`packages/core/src/octoagent/core/models/message.py:22-50`：已有 `attachments: list[MessageAttachment]` 字段（id/mime/filename/size/storage_ref）。F109 PoC **不强制**用 attachments 存音频——转写文本回填 `text` 即可走主路径；音频原文是否落 artifact 作为可选审计项（见 spec 决策）。

### 3.5 凭证管理（API STT 才需要）

- `CredentialStore`：`packages/provider/src/octoagent/provider/auth/store.py`（`~/.octoagent/auth-profiles.json`，0o600，filelock，原子写）。
- provider 配置在 `~/.octoagent/octoagent.yaml` providers 段。
- bot token：`services/telegram_client.py` `_load_bot_token` 从 `config.channels.telegram.bot_token_env` 指向的环境变量取。
- 若选 API STT，key 走 CredentialStore / env（Constitution #5，不进 LLM 上下文）。本地 faster-whisper **无凭证需求**。

### 3.6 依赖现状

- **httpx**：✅ 已在 `apps/gateway/pyproject.toml:40` 生产依赖（下载音频可用）。
- **aiogram**：❌ 实测 telegram_client 用 httpx 手工调 Bot API，**非 aiogram**（CLAUDE.md "Telegram (aiogram)" 表述与实现不符，实现是 httpx 手写）。
- **音频/STT 库**：❌ 无 whisper / faster-whisper / openai / pydub。F109 新增 `faster-whisper`（optional）。

### 3.7 测试参考模板

- `apps/gateway/tests/test_telegram_service.py`：`FakeTelegramBotClient`（mock client 捕获 sent_messages），`test_authorized_dm_creates_task_and_dedupes_update` 标准 inbound flow 测试模板。
- `apps/gateway/tests/test_f105_channel_adapter.py`：ChannelAdapter registry 测试。

---

## 4. 优雅降级矩阵（Constitution #6）

| 失败场景 | 表现 | 降级行为 |
|----------|------|----------|
| `faster-whisper` 未安装 | import 失败 | lazy import 捕获 → STT 服务标记不可用 → voice message 回复"🎙️ 语音转写未启用" + 提示发文字 |
| 模型文件缺失/下载失败 | WhisperModel 初始化 raise | 捕获 → 同上不可用回复 |
| 音频下载失败（Telegram getFile/download 出错） | httpx 异常 | 捕获 → 回复"语音下载失败，请重试或发文字" |
| 转写抛异常 | transcribe raise | 捕获 → 回复"语音转写失败" |
| 转写空结果（静音/噪声） | text 为空 | 回复"未能识别语音内容，请重试或发文字" |

降级原则：**永不崩 gateway，永不静默丢弃**，给用户可理解的回复，主链路其他消息不受影响。

---

## 5. 范围边界（明确不做）

- ❌ TTS（文字转语音）→ F110
- ❌ voice session（完整语音会话、连续对话语音态）→ F110（依赖 F093 Worker Full Session Parity）
- ❌ Web 端音频上传 UI（PoC 仅 telegram voice message 入口，最省事验证）
- ❌ 多语言/方言精调、说话人分离、实时流式转写（PoC 用整段短语音）
- ❌ 音频长度无限制（PoC 设合理上限，超限降级提示）
