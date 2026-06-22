# F110 语音 v0.1 — 技术实现计划（plan.md）

- **Feature**: F110 voice-v01
- **基线 commit**: `1cd2083f`（F109 STT only 已合入）
- **GATE_DESIGN 裁决（2026-06-22）**: D1=Piper/GPL，D2/D3=C 混合+显式关不自动重开，D4=PyAV 优先，D5=单一 env 可配置模型 zh_CN，scope=异步多轮
- **代码根**: worktree `octoagent/` 子目录；所有路径相对仓库根（`octoagent/` 下）

> 散文中文 / 代码标识符英文 / 英文技术术语保原文。

---

## 执行摘要

F110 在 F109 STT 地基上补全语音往返闭环：TTS 出站（Piper 本地合成 + WAV→OGG/Opus + send_voice）、voice session 状态机（ConversationBinding.metadata 三态标记）。所有改动严格约束在渠道层，不触碰 AgentSession、决策环、AgentSessionKind。

**Phase 数**: Phase 0（de-risk 实测）+ Phase A（TTS 服务层）+ Phase B（send_voice）+ Phase C（voice_mode 状态机）+ Phase D（出站接入 + e2e）+ Phase E（观测 + 收尾）= **6 Phase**。

**最大技术风险**: Phase 0 的 D4 PyAV libopus 实测——若 `libopus` 在 venv 不可用，需切到 PyOgg，影响 piper_backend.py 的实现路径和 pyproject.toml 依赖。

**触碰文件数**: 新增 4 个文件（tts.py / piper_backend.py / voice/__init__.py 更新 / test_tts_service.py）+ 修改 5 个文件（telegram.py / telegram_client.py / octo_harness.py / test_telegram_voice.py / pyproject.toml）= **共 9 个文件**。

---

## Phase 0：De-risk（实施第一步，规划在此，implement 阶段真跑）

> **Phase 0 不写生产代码**。只做侦察与验证，记录实测数字。

### 0-1 基线对账

```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F110-voice-v01/octoagent

WORKTREE=$(pwd)
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
uv run --no-sync python -m pytest \
  packages/core/tests packages/provider/tests packages/protocol/tests \
  packages/tooling/tests packages/skills/tests packages/policy/tests \
  packages/memory/tests apps/gateway/tests tests \
  -p no:cacheprovider --tb=short -q 2>&1 | tail -5
```

记录结果为 `N_baseline passed`（预期约 4135+，以实测为准）。这是 0 regression 的基准。

### 0-2 D4 PyAV libopus 实测

```bash
# 步骤 1：确认 PyAV 版本
uv run --no-sync python -c "import av; print(av.__version__)"

# 步骤 2：运行 WAV→OGG/Opus 骨架（用最小合法 WAV：44 字节 RIFF/WAV 骨架）
uv run --no-sync python - <<'EOF'
import io, wave, struct, av

# 最小合法 WAV（无音频数据，但结构合法）
def minimal_wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x00\x00" * 22050)  # 1 秒静音
    return buf.getvalue()

wav_bytes = minimal_wav_bytes()
in_buf = io.BytesIO(wav_bytes)
out_buf = io.BytesIO()
try:
    with av.open(in_buf, "r") as in_c, av.open(out_buf, "w", format="ogg") as out_c:
        in_s = in_c.streams.audio[0]
        out_s = out_c.add_stream("libopus", rate=in_s.rate)
        for pkt in in_c.demux(in_s):
            for frm in pkt.decode():
                frm.pts = None
                for op in out_s.encode(frm):
                    out_c.mux(op)
        for op in out_s.encode(None):
            out_c.mux(op)
    ogg_bytes = out_buf.getvalue()
    magic = ogg_bytes[:4]
    print(f"SUCCESS: {len(ogg_bytes)} bytes, magic={magic!r}")
    assert magic == b"OggS", f"magic 不对: {magic!r}"
    print("OGG magic check PASS")
except Exception as e:
    print(f"FAIL: {e}")
EOF
```

**决策树**：

| 结果 | 决策 |
|------|------|
| `magic=b'OggS'` + `PASS` | Phase A 用 PyAV（`libopus` 可用），pyproject.toml 不加额外依赖 |
| 抛 `ValueError: Codec not found: libopus` 或类似 | 切 PyOgg：pyproject.toml 加 `pyogg>=0.8,<1.0`，piper_backend.py 用 `OpusBufferedEncoder` 路径 |
| av 本身不可导入 | PyAV 未安装（fast-whisper 传递链断），先 `uv pip install av`，再重跑步骤 2 |

**AC-C4 CI 策略**（由此实测决定）：
- **PyAV 可用** → `test_wav_to_ogg_opus_produces_valid_ogg` 使用真实编解码，无需 skip
- **PyAV 不可用且需 PyOgg** → CI 需 `pip install pyogg`；若 pyogg 在 CI 也不可用，则用 `pytest.mark.skipif` 在无 codec 时跳过，并在 plan 注明

### 0-3 piper-tts 安装态侦察

```bash
uv run --no-sync python -c "import importlib.util; print(importlib.util.find_spec('piper'))"
```

**结论**：piper-tts 在 worktree venv 是否已装。**单测策略**：

- **全程使用 `FakeTtsBackend`（零 piper 依赖）** 跑所有单测，镜像 F109 `_FakeBackend` 范式
- **真实 PiperTtsBackend** 仅当 `is_available()` 探测返回 True 时才走真实合成路径（e2e 场景）
- **禁止 worktree 内 `uv sync`**——`piper-tts` 只在「用户手动 `uv pip install -e '.[voice]'`」的 production 实例有

---

## Codebase Reality Check

### 目标文件清单（实证读取）

| 文件（相对仓库根 `octoagent/`） | LOC（估算/实测） | 公开接口数 | 已知 debt | 操作 |
|------------------------------|-----------------|-----------|-----------|------|
| `apps/gateway/src/octoagent/gateway/voice/stt.py` | 79 行 | 3（SttResult/SttBackend/SpeechToTextService） | 无 | 镜像范本，只读 |
| `apps/gateway/src/octoagent/gateway/voice/faster_whisper_backend.py` | 102 行 | 2（FasterWhisperBackend/build_default_stt_service） | 无 | 镜像范本，只读 |
| `apps/gateway/src/octoagent/gateway/voice/__init__.py` | 16 行 | 5 re-export | 无 | 修改：加 TTS 导出 |
| `apps/gateway/src/octoagent/gateway/services/telegram.py` | ~1150 行 | 含 `notify_task_result`/`_handle_control_command`/`_record_conversation_binding` | 无 TODO | 修改：3 处（voice_mode 标记/命令扩展/TTS 出站） |
| `apps/gateway/src/octoagent/gateway/services/telegram_client.py` | ~330 行 | 含 `_request`/`send_message`/`download_file_bytes` | 无 | 修改：新增 `send_voice` |
| `apps/gateway/src/octoagent/gateway/harness/octo_harness.py` | ~600 行 | STT wiring at line 510 | 无 | 修改：加 TTS wiring（镜像 STT） |
| `apps/gateway/pyproject.toml` | ~51 行 | `[voice]` optional-deps | 无 | 修改：加 piper-tts（条件加 pyogg） |
| `apps/gateway/tests/test_telegram_voice.py` | 已存在（F109） | F109 入站测试 | 无 | 扩展：加 F110 出站 + voice_mode 测试 |

**前置清理规则评估**：无文件 LOC > 500 且将新增 > 50 行（telegram.py 1150 行新增约 40 行，低于 50 行阈值）；无相关 TODO/FIXME；无重复逻辑 > 30 行。**无需前置 CLEANUP task**。

### 新增文件

| 文件 | LOC 预估 | 说明 |
|------|---------|------|
| `apps/gateway/src/octoagent/gateway/voice/tts.py` | ~80 行 | TtsResult/TtsBackend Protocol/TextToSpeechService |
| `apps/gateway/src/octoagent/gateway/voice/piper_backend.py` | ~130 行 | PiperTtsBackend + wav_to_ogg_opus + build_default_tts_service |
| `apps/gateway/tests/test_tts_service.py` | ~120 行 | TTS 单测（FakeTtsBackend，镜像 test_stt_service.py） |

---

## Impact Assessment

| 维度 | 详情 |
|------|------|
| **直接修改文件** | 5 个（telegram.py / telegram_client.py / octo_harness.py / pyproject.toml / voice/__init__.py） |
| **新增文件** | 4 个（tts.py / piper_backend.py / test_tts_service.py / test_telegram_voice.py 扩展） |
| **总影响文件** | 9 个 |
| **跨包影响** | 零——所有变更在 `apps/gateway` 内；`packages/core` 中的 `ConversationBinding` 模型/`SqliteConversationBindingStore.get` 读取零修改 |
| **数据迁移** | 无——`ConversationBinding.metadata` 是 dict KV bag，无 schema 变更；voice_mode 键懒入 |
| **API/契约变更** | `TelegramGatewayService.__init__` 新增 `tts_service` 参数（向后兼容，默认 None）；`telegram_client.py` 新增 `send_voice` 方法 |
| **风险等级** | **LOW**（影响文件 9 < 10，零跨包影响，零数据迁移） |

HIGH 风险强制分阶段：**不触发**（LOW 级别）。

---

## Constitution Check

| 原则 | 适用性 | 评估 | 说明 |
|------|--------|------|------|
| #1 Durability First | 不适用 | ✅ | voice_mode 通过 ConversationBinding SQLite 持久化，跨重启保留 |
| #2 Everything is an Event | 不适用 | ✅ | TTS 合成不需要新 EventType（出站后处理层），结构化日志足够观测 |
| #3 Tools are Contracts | 不适用 | ✅ | TtsBackend Protocol + TextToSpeechService 契约在 tts.py 单一事实源 |
| #4 Two-Phase | 不适用 | ✅ | TTS 合成是可逆操作（失败退文字），不需要 Two-Phase |
| #5 Least Privilege | ✅ 适用 | ✅ | Piper 本地，无 API key；FR-E2 若换云 API 走 CredentialStore/env |
| #6 Degrade Gracefully | ✅ 适用 | ✅ | 降级矩阵 8 类，TextToSpeechService 永不抛给调用方，notify_task_result 全路径 try/except |
| #7 User-in-Control | ✅ 适用 | ✅ | `/voice on|off` 让用户显式控制；入站 voice 自动标记只在 unset 时触发 |
| #8 Observability | ✅ 适用 | ✅ | 结构化日志记 {backend/duration_ms/text_len}，不含音频内容 |
| #9 Agent Autonomy | ✅ 适用 | ✅ | `/voice on|off` 是渠道层控制命令（`_handle_control_command` 路径），**不经 Agent LLM 决策环**，不违反 #9 |
| #10 Policy-Driven Access | 不适用 | ✅ | TTS 无权限决策需求；voice_mode 是渠道状态标记 |
| **H1 铁律** | ✅ 核心 | ✅ | TTS 在 `notify_task_result` 出站后处理；AgentSession/AgentSessionKind/决策环零修改；voice session 仅靠 binding metadata 标记 |

**Constitution Check 结论**：全部原则通过，无 VIOLATION。

---

## 架构图

```mermaid
graph TD
    subgraph "渠道层（F110 触碰范围）"
        A[用户发 Telegram voice] --> B[_handle_voice_message<br/>STT 转写 F109]
        B --> C[_record_conversation_binding<br/>voice_mode=True 当 unset]
        C --> D[_ingest_update → 主路径]

        E[用户发 /voice on|off] --> F[_handle_control_command<br/>F110 扩展]
        F --> G[ConversationBinding.metadata<br/>voice_mode = True/False]

        H[主 Agent 回复完成] --> I[notify_task_result<br/>F110 插入 TTS 分支]
        I --> J{voice_mode == True<br/>AND tts available?}
        J -- yes --> K[tts_service.synthesize text]
        K --> L{ok?}
        L -- yes --> M[bot_client.send_voice<br/>OGG/Opus]
        L -- no --> N[降级 send_message 文字]
        M -- 失败 --> N
        J -- no --> N
    end

    subgraph "TTS 服务层（新建）"
        K --> O[TextToSpeechService]
        O --> P[PiperTtsBackend<br/>asyncio.to_thread]
        P --> Q[PiperVoice.synthesize_wav<br/>CPU-bound]
        Q --> R[wav_to_ogg_opus<br/>PyAV libopus]
    end

    subgraph "F093 AgentSession（零修改）"
        D --> S[AgentSession rolling_summary<br/>memory_cursor_seq<br/>recent_transcript]
    end

    subgraph "ConversationBinding Store（只读写 metadata KV）"
        G --> T[SqliteConversationBindingStore.get<br/>upsert_runtime_binding]
    end
```

---

## Phase 分解

### Phase A — TTS 服务层（新建）

**目标**：建立 TTS 抽象层（Protocol + Service + PiperBackend），镜像 F109 STT 架构，单测零真实 piper 依赖。

**触碰文件**：
- 新建 `apps/gateway/src/octoagent/gateway/voice/tts.py`
- 新建 `apps/gateway/src/octoagent/gateway/voice/piper_backend.py`
- 修改 `apps/gateway/src/octoagent/gateway/voice/__init__.py`（加 TTS 导出）
- 修改 `apps/gateway/pyproject.toml`（加 `piper-tts>=1.4,<2.0`；视 Phase 0 结果条件加 `pyogg`）
- 新建 `apps/gateway/tests/test_tts_service.py`

**详细实现**：

#### `tts.py`（镜像 stt.py）

```python
class TtsResult(BaseModel):
    ok: bool
    audio: bytes = b""          # 成功时含 OGG/Opus bytes
    reason: str = ""            # 失败原因码
    backend: str = ""
    duration_ms: int = 0

@runtime_checkable
class TtsBackend(Protocol):
    name: str
    def is_available(self) -> bool: ...
    async def synthesize(self, text: str, *, language: str) -> TtsResult: ...

class TextToSpeechService:
    def __init__(self, backend: TtsBackend) -> None: ...
    def is_available(self) -> bool: ...   # 委托 backend，捕获异常
    async def synthesize(self, text: str) -> TtsResult: ...
    # 永不把异常抛给调用方；空音频归一为 reason="empty_audio"
```

#### `piper_backend.py`（镜像 faster_whisper_backend.py）

```python
class PiperTtsBackend:
    name = "piper"
    # env 配置读取（镜像 FasterWhisperBackend）：
    #   OCTOAGENT_TTS_BACKEND（piper/none）
    #   OCTOAGENT_TTS_VOICE_MODEL（默认 zh_CN-huayan-medium）
    #   OCTOAGENT_TTS_LANGUAGE（默认 zh_CN）
    #   OCTOAGENT_TTS_ENABLED（默认 True）
    
    def is_available(self) -> bool:
        # importlib.util.find_spec("piper") is not None
        # 模型文件存在检查（AC-A1 独立路径：安装但模型缺失）
    
    def _ensure_model(self) -> Any:
        # double-checked locking + lazy import piper
        # 模型缺失 raise → 上层 is_available 返回 False
    
    def _synthesize_sync(self, text: str, language: str) -> TtsResult:
        # piper voice.synthesize_wav → io.BytesIO → wav_bytes
        # wav_to_ogg_opus(wav_bytes) → ogg_bytes
        # 返回 TtsResult(ok=True, audio=ogg_bytes)
    
    async def synthesize(self, text: str, *, language: str) -> TtsResult:
        # asyncio.to_thread(_synthesize_sync) + asyncio.wait_for(30s)
        # 超时 → TtsResult(ok=False, reason="tts_timeout")

def wav_to_ogg_opus(wav_bytes: bytes) -> bytes:
    # Phase 0 实测决定路径：PyAV libopus / PyOgg
    # 失败 → 返回 b""（调用方归一为 reason="encode_error"）

def build_default_tts_service() -> TextToSpeechService:
    # 镜像 build_default_stt_service，返回 TextToSpeechService(PiperTtsBackend())
```

**`wav_to_ogg_opus` 双路备选**（Phase 0 实测决定）：

| 路径 | 条件 | 代码 |
|------|------|------|
| **PyAV（优先）** | `libopus` 实测可用 | `av.open(..., format="ogg")` + `add_stream("libopus")` |
| **PyOgg（备选）** | PyAV libopus 不可用 | `from pyogg import OpusBufferedEncoder; from pyogg.ogg_opus_writer import OggOpusWriter` |

#### `voice/__init__.py` 更新

```python
from .tts import TextToSpeechService, TtsBackend, TtsResult
from .piper_backend import PiperTtsBackend, build_default_tts_service
# 加入 __all__
```

#### `test_tts_service.py`（单测，零真实 piper）

```python
class FakeTtsBackend:
    name = "fake"
    def __init__(self, *, available=True, result=None, raises=False): ...
    def is_available(self) -> bool: ...
    async def synthesize(self, text, *, language) -> TtsResult: ...

# 覆盖 AC 列表：
# AC-A1: test_tts_unavailable_when_lib_missing（monkeypatch find_spec）
# AC-A1 扩展: test_piper_model_missing_is_unavailable（模型文件缺失独立路径）
# AC-A2: test_synthesize_ok / test_synthesize_error_returns_false / test_synthesize_empty_audio_returns_false
# AC-A3: test_tts_result_schema
# AC-A4: test_piper_backend_protocol_conformance
# AC-C4: test_wav_to_ogg_opus_produces_valid_ogg
#         — Phase 0 实测决定：PyAV 可用则真跑；否则 pytest.mark.skipif 或用 PyOgg
#         — 注释标明 skipif 原因，CI 需装 [voice] extras
# AC-C5: test_wav_to_ogg_opus_failure_handled
# AC-E1: test_tts_synthesis_observable（FakeTtsBackend + logging 捕获）
```

**FR-AC 映射**：FR-A1 → AC-A3；FR-A2 → AC-A4；FR-A3 → AC-A2；FR-A4 → AC-A1 + AC-A4；FR-A5 → pyproject.toml 变更；FR-A6 → AC-C4 + AC-C5；FR-A7 → env 读取逻辑。

**Phase A 结束验证**：

```bash
PYTHONPATH=... uv run --no-sync python -m pytest \
  apps/gateway/tests/test_tts_service.py -p no:cacheprovider -v
```

期望：全 PASS。无真实 piper 安装也全通（FakeTtsBackend 零外部依赖）。

**Phase A Codex per-Phase review 钩子**：`/codex:adversarial-review`，范围：`tts.py` + `piper_backend.py` + `test_tts_service.py`。重点：Protocol 对称性、降级覆盖、线程安全。

---

### Phase B — send_voice（bot client 新增方法）

**目标**：`TelegramBotClient` 新增 `send_voice` 方法（multipart/form-data），为 Phase D 出站接入提供 client 层支撑。

**触碰文件**：
- 修改 `apps/gateway/src/octoagent/gateway/services/telegram_client.py`（新增 `send_voice`）
- 扩展 `apps/gateway/tests/test_telegram_voice.py`（新增 AC-C1/C2/C3 测试）

**实现设计**：

`send_voice` **不走 `_request` JSON POST 路径**，原因：`sendVoice` 是 multipart/form-data 文件上传。需复用 `_load_bot_token()` + `base_url` 但单独构造 multipart httpx 请求：

```python
async def send_voice(
    self,
    chat_id: str | int,
    voice: bytes,
    *,
    duration: int | None = None,
    reply_to_message_id: str | int | None = None,
    message_thread_id: str | int | None = None,
    disable_notification: bool = True,
) -> TelegramMessage:
    """sendVoice：multipart/form-data 上传 OGG/Opus，在 chat 呈现为语音消息气泡。"""
    bot_token = self._load_bot_token()
    url = f"{self._base_url}/bot{bot_token}/sendVoice"
    
    form_data: dict[str, str] = {
        "chat_id": str(chat_id),
        "disable_notification": str(disable_notification).lower(),
    }
    if duration is not None:
        form_data["duration"] = str(duration)
    if reply_to_message_id is not None:
        form_data["reply_to_message_id"] = str(int(reply_to_message_id))
    if message_thread_id is not None:
        form_data["message_thread_id"] = str(int(message_thread_id))
    
    files = {"voice": ("voice.ogg", voice, "audio/ogg")}
    
    async with httpx.AsyncClient(
        timeout=self._timeout,
        transport=self._transport,
    ) as client:
        response = await client.post(url, data=form_data, files=files)
    
    # 错误处理：抛 TelegramBotApiError（现有异常类型，AC-C3 可捕获）
    if response.status_code != 200 or not response.json().get("ok", False):
        description = str(response.json().get("description") or response.text[:200])
        raise TelegramBotApiError(
            description,
            status_code=response.status_code,
        )
    return TelegramMessage.model_validate(response.json().get("result"))
```

**测试**（扩展 `test_telegram_voice.py`，mock httpx 不真实调 Telegram API）：
- `test_bot_client_send_voice_multipart`（AC-C1）：断言请求方法 POST、URL 含 sendVoice、body 是 multipart 含 voice 文件字段
- `test_bot_client_send_voice_optional_params`（AC-C2）：传 duration/reply_to_message_id/message_thread_id → 断言 form 字段存在
- `test_bot_client_send_voice_raises_on_failure`（AC-C3）：mock 返回 400 → 断言抛 `TelegramBotApiError`

**FR-AC 映射**：FR-C1 → AC-C1；FR-C2 → AC-C1（multipart 验证）；FR-C3 → AC-C3。

**Phase B 结束验证**：

```bash
PYTHONPATH=... uv run --no-sync python -m pytest \
  apps/gateway/tests/test_telegram_voice.py -k "send_voice" -p no:cacheprovider -v
```

**Phase B Codex per-Phase review 钩子**：范围 `telegram_client.py` send_voice 新增部分。重点：multipart 结构正确性、错误处理、token 不泄漏。

---

### Phase C — voice_mode 状态机

**目标**：`ConversationBinding.metadata` 三态读写 helper + `_handle_control_command` 扩展 `/voice on|off` + `_record_conversation_binding` 入站自动标记（GATE D2-C 语义）。

**触碰文件**：
- 修改 `apps/gateway/src/octoagent/gateway/services/telegram.py`（3 处）
- 扩展 `apps/gateway/tests/test_telegram_voice.py`（AC-D1/D1b/D2/D3）

**关键设计决策（GATE_DESIGN D2/D3 裁决落地）**：

`voice_mode` 三态语义（由 `ConversationBinding.metadata` KV 承载）：
- **unset**（metadata 无 `voice_mode` 键）→ 等价 False，入站 voice 可自动设为 True
- **True**（显式 True）→ voice 模式开启，出站走 TTS
- **False**（显式 False，用户 `/voice off` 过）→ voice 模式关闭，入站 voice 不自动重开

**Helper 函数**（在 telegram.py 内，私有方法）：

```python
def _get_voice_mode(self, binding: ConversationBinding | None) -> bool:
    """三态读取：key 缺失 → False；True → True；False → False。"""
    if binding is None:
        return False
    return bool(binding.metadata.get("voice_mode", False))

def _is_voice_mode_explicitly_disabled(self, binding: ConversationBinding | None) -> bool:
    """区分「unset」vs「显式 False」。"""
    if binding is None:
        return False
    return "voice_mode" in binding.metadata and binding.metadata["voice_mode"] is False
```

**`_record_conversation_binding` 修改**（FR-D1）：

在 `_handle_voice_message` 成功后，由调用方传入 `set_voice_mode=True`；`_record_conversation_binding` 内部判断：

```python
async def _record_conversation_binding(
    self,
    context: TelegramInboundContext,
    scope_id: str,
    reply_thread_root_id: str,
    *,
    set_voice_mode_if_unset: bool = False,  # F110 新增参数
) -> None:
    ...
    metadata: dict[str, Any] = {}
    if context.message_thread_id:
        metadata["last_message_thread_id"] = context.message_thread_id
    if reply_thread_root_id:
        metadata["last_reply_thread_root_id"] = reply_thread_root_id
    
    # F110 D2-C：入站 voice 自动标记（仅 unset 时，不覆盖显式 False）
    if set_voice_mode_if_unset:
        binding_store = getattr(self._stores, "conversation_binding_store", None)
        if binding_store is not None:
            existing = await binding_store.get("telegram", str(context.chat_id))
            if not self._is_voice_mode_explicitly_disabled(existing):
                metadata["voice_mode"] = True
            else:
                # 显式 False，保留原值（D2-C GATE 裁决：不重开）
                if existing is not None and "voice_mode" in existing.metadata:
                    metadata["voice_mode"] = existing.metadata["voice_mode"]
    
    try:
        await binding_store.upsert_runtime_binding(
            "telegram", context.chat_id, scope_id=scope_id, project_id="", metadata=metadata
        )
    except Exception:
        ...
```

> **注意**：`upsert_runtime_binding` 的 metadata 参数是**全量替换**（ON CONFLICT DO UPDATE SET metadata = excluded.metadata），不是 partial update。因此写入前需先 get→merge existing metadata，再 upsert。这是 MEDIUM-3 的闭环。

**merge metadata 模式**（所有写 voice_mode 的路径统一遵循）：

```python
# 标准 read-modify-write 流程
existing = await binding_store.get("telegram", str(chat_id))
merged = dict(existing.metadata) if existing is not None else {}
merged.update(new_kv)  # 只覆盖需要改的 key
await binding_store.upsert_runtime_binding("telegram", chat_id, ..., metadata=merged)
```

**`_handle_control_command` 扩展**（FR-D2）：

在 `build_telegram_action_request` 之前加 voice 命令检测：

```python
# F110 FR-D2：渠道层 voice 控制命令（不经 Agent LLM 决策环，Constitution #9）
text_stripped = context.text.strip().lower()
if text_stripped in ("/voice on", "/voice off"):
    return await self._handle_voice_command(context, enable=(text_stripped == "/voice on"))
```

```python
async def _handle_voice_command(
    self, context: TelegramInboundContext, *, enable: bool
) -> TelegramIngestResult | None:
    binding_store = getattr(self._stores, "conversation_binding_store", None)
    if binding_store is not None:
        existing = await binding_store.get("telegram", str(context.chat_id))
        merged = dict(existing.metadata) if existing is not None else {}
        merged["voice_mode"] = enable
        with contextlib.suppress(Exception):
            await binding_store.upsert_runtime_binding(
                "telegram", str(context.chat_id),
                scope_id=existing.scope_id if existing else "",
                project_id="",
                metadata=merged,
            )
    if self._bot_client is not None:
        reply_text = "语音模式已开启 🔊" if enable else "语音模式已关闭 💬"
        with contextlib.suppress(Exception):
            await self._bot_client.send_message(
                context.chat_id,
                reply_text,
                reply_to_message_id=context.message_id,
            )
    return TelegramIngestResult(status="control_action", detail="voice_command", created=False)
```

> `_handle_voice_command` 的确认回复文字只用文字，不加 TTS（FR-B3 明确：控制命令回复不走 TTS）。

**测试**（扩展 `test_telegram_voice.py`）：
- `test_voice_message_sets_voice_mode`（AC-D1）：voice update → binding.metadata["voice_mode"] == True
- `test_voice_off_then_voice_message_stays_off`（AC-D1b）：先 /voice off → 再发 voice → voice_mode 仍为 False
- `test_voice_off_command_clears_voice_mode`（AC-D2）
- `test_voice_on_command_sets_voice_mode`（AC-D3）

**FR-AC 映射**：FR-D1 → AC-D1 + AC-D1b；FR-D2 → AC-D2 + AC-D3；FR-D4 → AC-D5；FR-D5 → AC-D6；FR-D6（LOW-1 SHOULD）→ 评估：`_handle_voice_message` 结束处标注 `input_kind=voice` ≤ 3 行且不新增依赖，**实现**，在 AC-D5 测试 setup 附加 assertion。

**Phase C 结束验证**：

```bash
PYTHONPATH=... uv run --no-sync python -m pytest \
  apps/gateway/tests/test_telegram_voice.py \
  -k "voice_mode or voice_command" -p no:cacheprovider -v
```

**Phase C Codex per-Phase review 钩子**：重点：三态语义正确性、read-modify-write 是否完整、Constitution #9 边界（控制命令不经 LLM）。

---

### Phase D — 出站 TTS 接入 + e2e

**目标**：`notify_task_result` 插入 TTS 分支（读 voice_mode → 合成 → send_voice → 降级链）；`TelegramGatewayService.__init__` 加 `tts_service` 参数；`octo_harness.py` 注入 `build_default_tts_service()`；e2e voice 往返链。

**触碰文件**：
- 修改 `apps/gateway/src/octoagent/gateway/services/telegram.py`（`notify_task_result` + `__init__`）
- 修改 `apps/gateway/src/octoagent/gateway/harness/octo_harness.py`（TTS wiring）
- 扩展 `apps/gateway/tests/test_telegram_voice.py`（AC-B1~B6 + AC-D4/D5/D7 + AC-Z2 e2e + AC-E2/E3）

**`notify_task_result` 修改**（FR-B1，核心）：

```python
async def notify_task_result(self, task_id: str) -> None:
    if self._bot_client is None:
        return
    task = await self._stores.task_store.get_task(task_id)
    if task is None or task.requester.channel != "telegram":
        return

    target = await self._resolve_reply_target(task_id)
    if target is None:
        return

    events = await self._stores.event_store.get_events_for_task(task_id)
    text = self._build_result_text(self._status_value(task.status), events)

    # F110 FR-B1：TTS 出站分支（H1：在主 Agent 回复之后，渠道层后处理）
    if await self._try_send_voice_reply(target, text):
        return  # 语音发送成功，直接返回（不再发文字）

    # 原有文字路径（voice_mode=False / TTS 不可用 / 所有失败降级后到此）
    sent_message = await self._bot_client.send_message(
        target["chat_id"],
        text,
        reply_to_message_id=target.get("reply_to_message_id") or None,
        message_thread_id=target.get("message_thread_id") or None,
    )
    self._remember_outbound_reply_thread(target, sent_message)
```

```python
async def _try_send_voice_reply(
    self, target: dict[str, str], text: str
) -> bool:
    """尝试 TTS + send_voice；任何失败记日志后返回 False（调用方降级文字）。
    
    Constitution #6 + FR-B4/B5：所有异常在此捕获，永不逃逸到 notify_task_result 调用方。
    """
    try:
        if self._tts_service is None or not self._tts_service.is_available():
            logger.info("tts_skip reason=tts_unavailable chat_id=%s", target["chat_id"])
            return False
        
        # 查 voice_mode
        binding_store = getattr(self._stores, "conversation_binding_store", None)
        if binding_store is None:
            return False
        binding = await binding_store.get("telegram", target["chat_id"])
        if not self._get_voice_mode(binding):
            return False  # voice_mode=False，静默走文字（无需日志噪声）
        
        # TTS 合成（TextToSpeechService 内已有全面兜底，不会抛）
        tts_result = await self._tts_service.synthesize(text)
        if not tts_result.ok:
            logger.warning(
                "tts_degrade reason=%s chat_id=%s text_len=%d",
                tts_result.reason, target["chat_id"], len(text),
            )
            return False
        
        logger.info(
            "tts_success backend=%s duration_ms=%d text_len=%d",
            tts_result.backend, tts_result.duration_ms, len(text),
        )
        
        # send_voice（失败抛 TelegramBotApiError，此处捕获）
        await self._bot_client.send_voice(
            target["chat_id"],
            tts_result.audio,
            reply_to_message_id=target.get("reply_to_message_id") or None,
            message_thread_id=target.get("message_thread_id") or None,
        )
        # _remember_outbound_reply_thread：send_voice 无法拿到 TelegramMessage 的方式
        # 与 send_message 相同（两者均返回 TelegramMessage），可沿用
        return True
    
    except Exception:
        logger.warning(
            "tts_degrade reason=send_voice_failed chat_id=%s", target["chat_id"], exc_info=True
        )
        return False
```

**`TelegramGatewayService.__init__` 修改**：

在现有 `stt_service: SpeechToTextService | None = None` 之后加：
```python
tts_service: TextToSpeechService | None = None,
```
并 `self._tts_service = tts_service`（FR-A8）。

**`octo_harness.py` 修改**（镜像 STT wiring，line 510 附近）：

```python
from ..voice import build_default_stt_service, build_default_tts_service

telegram_service = TelegramGatewayService(
    ...,
    stt_service=build_default_stt_service(),
    tts_service=build_default_tts_service(),  # F110 新增
)
```

**测试**（扩展 `test_telegram_voice.py`，均用 FakeTtsService）：

```
# FakeTtsService（文件顶部定义，供本 Phase 所有测试共用）
class FakeTtsService:
    def __init__(self, *, available=True, result_ok=True, audio=b"OggS\x00\x00"): ...
    def is_available(self) -> bool: ...
    async def synthesize(self, text: str) -> TtsResult: ...

# FakeVoiceBotClient 扩展（F109 已有，加 send_voice mock）
class FakeVoiceBotClient:
    send_voice_calls: list[...]  # 记录 send_voice 调用

# 覆盖 AC 列表：
# AC-B1: test_notify_task_result_sends_voice_when_voice_mode
# AC-B2: test_notify_task_result_sends_text_when_voice_mode_off
# AC-B3: test_notify_task_result_degrades_to_text_on_tts_failure
# AC-B4: test_notify_task_result_degrades_to_text_on_send_voice_failure
# AC-B5: test_build_result_text_unchanged（纯函数，直接调用对比 F109 基线）
# AC-B6: test_notify_task_result_degrades_on_tts_timeout（FakeTtsService asyncio.sleep 触发超时）
# AC-D4: test_voice_session_continuous_rounds（第 1 轮 voice→voice_mode=True，第 2 轮文字→仍 TTS）
# AC-D5: test_voice_session_idempotent_replay（同一 update 重投，voice_mode 幂等）
# AC-D7: test_voice_mode_defaults_to_false_if_missing（binding 不存在 → 文字回复）
# AC-E2: test_tts_degrade_observable（捕获日志 reason 码）
# AC-E3: test_no_exception_escapes_notify_task_result（多类失败，断言无异常逃逸）
# AC-Z2: test_voice_roundtrip_e2e（端到端：_ingest_update→notify_task_result→send_voice）
```

**e2e voice 往返链（AC-Z2）设计**：

```
# 用 FakeSttService + FakeTtsService + FakeVoiceBotClient 组装 TelegramGatewayService
# 1. 调 service._ingest_update（构造 fake voice update）
# 2. 验证 binding.metadata["voice_mode"] == True
# 3. 调 service.notify_task_result（构造 fake task）
# 4. 断言 bot_client.send_voice_calls 非空（语音 chat 发语音）
# 5. 构造文字 update（同 chat）→ 不改 voice_mode → notify_task_result → 仍 send_voice
# 6. 构造 voice_mode=False 的 chat → notify_task_result → 断言 send_message（文字）
```

**FR-AC 映射**：FR-B1 → AC-B1；FR-B2 → AC-B5；FR-B3 → AC-B3；FR-B4 → AC-B3+B4；FR-B5 → AC-E3；FR-A8 → octo_harness wiring；FR-D3 → AC-B1（voice_mode 读取）；FR-D4 → AC-D4；FR-D6（LOW-1）→ AC-D5 附加 assertion。

**Phase D 结束验证**：

```bash
PYTHONPATH=... uv run --no-sync python -m pytest \
  apps/gateway/tests/test_telegram_voice.py -p no:cacheprovider -v
```

**Phase D Codex per-Phase review 钩子**：重点：降级链完整性、异常不逃逸、AC-Z2 e2e 覆盖面、H1 边界守住（AgentSession 零修改）。

---

### Phase E — 观测 + 降级矩阵收尾 + 文档

**目标**：补全 FR-E 结构化日志；对照降级矩阵 8 类逐一验证测试覆盖；living-docs 漂移闸；completion-report + handoff。

**触碰文件**：
- 审查 `telegram.py` 日志（FR-E1：成功含 backend/duration_ms/text_len，降级含 reason）
- 审查 `piper_backend.py` 日志（FR-E1 同规范）
- `test_tts_service.py` AC-E1（可观测性）
- `test_telegram_voice.py` AC-E2（降级可观测）
- 生成 `completion-report.md` + `handoff.md`（F110 → v0.2 接力）

**降级矩阵 8 类逐一确认**（Phase E 强制核查）：

| 失败场景 | reason 码 | 覆盖测试 | Phase |
|----------|----------|---------|-------|
| piper-tts 未安装 / is_available()=False | `tts_unavailable` | AC-A1 + AC-B1（tts_service 不可用） | A + D |
| 模型文件缺失 / 初始化 raise | `tts_unavailable` | AC-A1 扩展 test_piper_model_missing_is_unavailable | A |
| synthesize 抛异常 | `synthesize_error` | AC-A2 test_synthesize_error_returns_false | A |
| 合成返回空音频 | `empty_audio` | AC-A2 test_synthesize_empty_audio_returns_false | A |
| WAV→OGG 格式转换失败 | `encode_error` | AC-C5 test_wav_to_ogg_opus_failure_handled | A |
| send_voice Telegram API 失败 | `send_voice_failed` | AC-B4 + AC-E3 | D |
| ConversationBinding 查不到 / voice_mode 缺失 | — | AC-D7 | D |
| TTS 合成超时（30s） | `tts_timeout` | AC-B6 | D |

**living-docs 漂移闸**：对照本 Feature 改动，确认以下文档是否需要同步更新：
- `docs/blueprint/milestones.md`：标记 F110 ✅ 完成
- `docs/codebase-architecture/platform-gateway.md`：是否提及 TTS 出站路径（若有，同步）
- `CLAUDE.md` `##里程碑 M6` 表格：F110 状态更新

**Phase E 验证（最终全量回归）**：

```bash
# 1. 全量回归（0 regression）
PYTHONPATH=... uv run --no-sync python -m pytest \
  packages/core/tests packages/provider/tests packages/protocol/tests \
  packages/tooling/tests packages/skills/tests packages/policy/tests \
  packages/memory/tests apps/gateway/tests tests \
  -p no:cacheprovider --tb=short -q

# 2. e2e_smoke 8/8
PYTHONPATH=... uv run --no-sync python -m pytest -m e2e_smoke \
  apps/gateway/tests/ -p no:cacheprovider

# 3. AC↔test 机械校验（逐函数）
PYTHONPATH=... uv run --no-sync python -m pytest \
  apps/gateway/tests/test_tts_service.py \
  apps/gateway/tests/test_telegram_voice.py \
  -v -p no:cacheprovider
```

**Phase E Codex 双 panel review**（命中「新能力 + 外部 GPL 依赖」）：
- Codex：全面对抗 review（全部 5 Phase 改动）
- 第二 panel（Claude Opus 或另一 provider）：spec 对齐专项（AC/FR 是否全落实，H1 是否真守住）
- 两者分歧项必须人裁

---

## 文件结构变更

```
octoagent/apps/gateway/src/octoagent/gateway/voice/
├── __init__.py              [修改] 加 TtsResult/TtsBackend/TextToSpeechService/PiperTtsBackend/build_default_tts_service 导出
├── stt.py                   [不变]
├── faster_whisper_backend.py [不变]
├── tts.py                   [新增] TtsResult / TtsBackend Protocol / TextToSpeechService
└── piper_backend.py          [新增] PiperTtsBackend + wav_to_ogg_opus + build_default_tts_service

octoagent/apps/gateway/src/octoagent/gateway/services/
├── telegram.py              [修改] __init__(+tts_service) / _record_conversation_binding(+set_voice_mode_if_unset) / _handle_control_command(+/voice 命令) / notify_task_result(+TTS 分支) / 新增 _try_send_voice_reply / _handle_voice_command / _get_voice_mode / _is_voice_mode_explicitly_disabled
└── telegram_client.py       [修改] 新增 send_voice

octoagent/apps/gateway/src/octoagent/gateway/harness/
└── octo_harness.py          [修改] 加 build_default_tts_service import + telegram_service 构造注入

octoagent/apps/gateway/
└── pyproject.toml           [修改] [voice] 加 piper-tts>=1.4,<2.0（条件加 pyogg>=0.8,<1.0）

octoagent/apps/gateway/tests/
├── test_tts_service.py      [新增] TTS 单测（FakeTtsBackend，AC-A1~A4 + AC-C4/C5 + AC-E1）
└── test_telegram_voice.py   [扩展] 加 F110 出站 + voice_mode 测试（AC-B1~B6 + AC-C1~C3 + AC-D1~D7 + AC-E2/E3 + AC-Z2）
```

**不触碰的文件**（明确）：
- `packages/core/src/octoagent/core/models/conversation_binding.py`（零 schema 变更）
- `packages/core/src/octoagent/core/store/conversation_binding_store.py`（只调用现有 API）
- `packages/core/src/octoagent/core/models/agent_context.py`（AgentSession 零修改）
- F109 的 `stt.py` / `faster_whisper_backend.py`（只读不改）
- `config_schema.py`（沿用 env 策略，不动 yaml schema）

---

## 风险与缓解

| 风险 | 可能性 | 影响 | 缓解方案 |
|------|--------|------|---------|
| **D4 PyAV libopus 不可用**（Phase 0 核心 de-risk） | 中 | 中（需切 PyOgg，改 piper_backend.py + 加依赖） | Phase 0 必跑实测，备选路径在 plan 明确；PyOgg API 也已熟悉 |
| **piper-tts lazy import 路径不稳定**（模型下载/路径） | 低 | 低（`is_available()` 返回 False，优雅降级） | lazy import 范式已在 F109 FasterWhisperBackend 验证，直接复用 |
| **upsert_runtime_binding 全量覆盖 metadata 导致字段丢失** | 中 | 中（last_message_thread_id 等被清掉） | read-modify-write 标准流程强制：先 get → merge → upsert；Phase C 测试验证 |
| **asyncio.to_thread 在 piper 合成期间 event loop 卡顿** | 低 | 低（to_thread 本就是为此设计，F109 STT 同模式） | asyncio.wait_for 30s 超时守卫，到期自动降级 |
| **send_voice multipart 与 Telegram API 不兼容**（参数格式） | 低 | 中（voice 消息发不出，降级文字） | mock httpx 单测断言请求结构；production 实测首次发一条静音验证 |
| **test_wav_to_ogg_opus CI 无 codec**（AC-C4） | 低 | 低（skipif，不影响其他 AC） | Phase 0 决定 skip 策略，注释清晰 |

---

## 依赖变更

**pyproject.toml `[voice]` extra 修改**：

```toml
# F109 已有：
voice = [
    "faster-whisper>=1.0,<2.0",
    # F110 新增：
    "piper-tts>=1.4,<2.0",           # 本地 TTS（GPL-3.0 optional，GATE_DESIGN D1 接受）
    # "pyogg>=0.8,<1.0",             # D4 备选：Phase 0 实测 PyAV 不可用时启用
]
```

**安装命令**（含 STT + TTS）：`uv pip install -e '.[voice]'`（voice 组同时含 STT + TTS，一次安装）。

**模型下载**（首次使用，非 CI 步骤）：
```bash
# 方式 A：piper-tts CLI
python -m piper --download-voice zh_CN-huayan-medium

# 方式 B：手动下载（2 个文件）
# zh_CN-huayan-medium.onnx（~63MB）
# zh_CN-huayan-medium.onnx.json（模型 config）
# 放到 OCTOAGENT_TTS_VOICE_MODEL 指定路径
```

---

## 验证命令汇总

```bash
# 变量设置
WORKTREE=/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F110-voice-v01/octoagent
PYPATH=\
${WORKTREE}/apps/gateway/src:\
${WORKTREE}/packages/core/src:\
${WORKTREE}/packages/provider/src:\
${WORKTREE}/packages/policy/src:\
${WORKTREE}/packages/memory/src:\
${WORKTREE}/packages/protocol/src:\
${WORKTREE}/packages/sdk/src:\
${WORKTREE}/packages/skills/src:\
${WORKTREE}/packages/tooling/src

# Phase A 后：TTS 单测
PYTHONPATH=$PYPATH uv run --no-sync python -m pytest \
  ${WORKTREE}/apps/gateway/tests/test_tts_service.py -p no:cacheprovider -v

# Phase B 后：send_voice 单测
PYTHONPATH=$PYPATH uv run --no-sync python -m pytest \
  ${WORKTREE}/apps/gateway/tests/test_telegram_voice.py \
  -k "send_voice" -p no:cacheprovider -v

# Phase C 后：voice_mode 状态机
PYTHONPATH=$PYPATH uv run --no-sync python -m pytest \
  ${WORKTREE}/apps/gateway/tests/test_telegram_voice.py \
  -k "voice_mode or voice_command" -p no:cacheprovider -v

# Phase D 后：全 telegram_voice 测试（含 e2e）
PYTHONPATH=$PYPATH uv run --no-sync python -m pytest \
  ${WORKTREE}/apps/gateway/tests/test_telegram_voice.py -p no:cacheprovider -v

# Phase E：e2e_smoke 门
PYTHONPATH=$PYPATH uv run --no-sync python -m pytest -m e2e_smoke \
  ${WORKTREE}/apps/gateway/tests/ -p no:cacheprovider

# Phase E：全量回归（0 regression 对账）
PYTHONPATH=$PYPATH uv run --no-sync python -m pytest \
  ${WORKTREE}/packages/core/tests \
  ${WORKTREE}/packages/provider/tests \
  ${WORKTREE}/packages/protocol/tests \
  ${WORKTREE}/packages/tooling/tests \
  ${WORKTREE}/packages/skills/tests \
  ${WORKTREE}/packages/policy/tests \
  ${WORKTREE}/packages/memory/tests \
  ${WORKTREE}/apps/gateway/tests \
  ${WORKTREE}/tests \
  -p no:cacheprovider --tb=short -q 2>&1 | tail -5

# AC↔test 机械校验（spec §5 SDD 强化）
PYTHONPATH=$PYPATH uv run --no-sync python -m pytest \
  ${WORKTREE}/apps/gateway/tests/test_tts_service.py \
  ${WORKTREE}/apps/gateway/tests/test_telegram_voice.py \
  -v -p no:cacheprovider | grep -E "PASSED|FAILED|ERROR"
```

---

## Complexity Tracking

| 决策 | 实际选择 | 为何不选更简单方案 |
|------|---------|-----------------|
| read-modify-write vs partial update | read-modify-write（先 get → merge → upsert） | `upsert_runtime_binding` metadata 参数是全量替换，无 partial update API |
| asyncio.to_thread（CPU-bound 卸载） | to_thread + wait_for | Piper synthesize_wav 是同步 CPU-bound，直接 await 会阻塞 event loop（F109 STT 同问题，同解法） |
| 三态 voice_mode（unset/True/False） | dict key 存在性区分 unset vs 显式 False | GATE_DESIGN D2-C 要求区分，bool 字段无法表达「从未设置」 |
| Phase 0 必做 | 是 | AC-C4 依赖 PyAV libopus；若不实测 Phase A 实现路径不确定；代价极低（不改代码） |
| `_try_send_voice_reply` 抽为私有方法 | 是 | notify_task_result 原有结构简洁（~12 行），TTS 分支 ~30 行；抽为私有方法保持可读性，不改 notify_task_result 主脉络 |

---

## 验收门（Gate）总结

- **0 regression** vs `1cd2083f`（全量回归基线 `N_baseline passed`，以 Phase 0 实测为准）
- **`pytest -m e2e_smoke` 8/8 PASS**
- **新增单测全 PASS**：`test_tts_service.py` + `test_telegram_voice.py` F110 扩展部分
- **AC↔test 机械校验**：spec §5 表中所有 P1 AC 函数存在且 `pytest -k <func>` PASS
- **双 panel review**（Phase E）：Codex + 第二模型 0 HIGH 残留
- **completion-report.md** + **handoff.md**（F110 → v0.2）+ living-docs 同步
- **不主动 push，等用户拍板**
