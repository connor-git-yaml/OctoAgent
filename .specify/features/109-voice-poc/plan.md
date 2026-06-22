# F109 语音 PoC — 实施计划（plan.md）

- **Feature ID**: F109 / Slug: voice-poc
- **基线**: master d6f0ec54 / 分支 `feature/109-voice-poc`
- **D1 决策（GATE_DESIGN 已拍板）**: **本地 faster-whisper**（用户选定）。STT 后端做成可替换薄抽象（`SttBackend`），API 后端留缝不实现。
- **代码根**: 仓库根 `octoagent/` 子目录。

> 散文中文 / 代码标识符英文 / 英文术语保原文。

---

## 0. 测试与验证范式（关键，避免假 0 regression）

- **禁 worktree 内 `uv sync`**：worktree `.venv` 是主仓 symlink，裸 pytest 跑 **master src**。验证 F109 worktree 代码必须 PYTHONPATH 锁 worktree：
  ```
  cd octoagent
  PYTHONPATH=<worktree>/octoagent/apps/gateway/src:<worktree>/octoagent/packages/core/src:... \
  uv run --no-sync python -m pytest <tests> -p no:cacheprovider
  ```
  实施时按 `project_worktree_venv_symlink` + `project_pytest_invocation_env_pollution` memory 锁定。
- **faster-whisper 不需真装即可全测**：所有单测/e2e 用 **Fake backend**；真实 `FasterWhisperBackend.is_available()` 在测试 venv（无 faster-whisper）天然返回 False，正好覆盖 AC-5"lib missing"路径。**faster-whisper 是纯 optional 运行时依赖，测试零依赖它**——避开 uv sync 难题。
- **0 regression 基线**：先在 worktree 跑一次全量（master 等价）记 baseline passed 数，改完对比。

---

## 1. 架构总览

```
Telegram voice message (OGG/Opus)
  → routes/telegram.py (webhook) | _polling_loop (polling)
  → _ingest_update
      → _extract_context: 检测 message.voice → context.voice (新字段)
      → [voice 分支] _handle_voice_message(context):
            ① 幂等预检 (event_store.check_idempotency_key) → 命中则 duplicate, 不转写
            ② STT 可用性 / 时长大小守卫 → 不过则降级回复
            ③ get_file + download_file_bytes → 音频字节 (失败降级)
            ④ stt_service.transcribe(bytes) → SttResult (失败/空降级)
            ⑤ 成功 → dataclasses.replace(context, text=转写文本)
      → (text 已填) → 原有空文本检查 / 控制命令 / create_task / enqueue 全不变 ← H1
  → 主 Agent 推理 (与文字消息完全同路)
```

**H1 不变量**：voice 分支只把 `context.text` 从空填成转写文本，之后**一行下游代码都不改**。

---

## 2. 新增文件

### 2.1 `apps/gateway/src/octoagent/gateway/voice/__init__.py`
导出 `SpeechToTextService` / `SttResult` / `SttBackend` / `FasterWhisperBackend` / `build_default_stt_service`。

### 2.2 `apps/gateway/src/octoagent/gateway/voice/stt.py`
- `SttResult(BaseModel)`：`ok: bool`、`text: str=""`、`reason: str=""`、`backend: str=""`、`duration_ms: int=0`。reason 码：`lib_missing`/`model_error`/`download_error`(上层用)/`transcribe_error`/`empty`/`too_large`。
- `SttBackend(Protocol)`：`name: str` 属性；`is_available() -> bool`；`async transcribe(audio: bytes, *, mime: str, filename: str) -> SttResult`。
- `SpeechToTextService`：
  - `__init__(backend: SttBackend)`
  - `is_available() -> bool` → 委托 backend
  - `async transcribe(audio, *, mime, filename) -> SttResult`：try 调 backend.transcribe；**捕获一切异常** → `SttResult(ok=False, reason="transcribe_error")`；backend 返回 ok 但 `text.strip()==""` → 归一化为 `ok=False, reason="empty"`（service 层统一判空，backend 不各自判）。

### 2.3 `apps/gateway/src/octoagent/gateway/voice/faster_whisper_backend.py`
- `FasterWhisperBackend`：
  - config 从 env 读（硬默认）：`OCTOAGENT_STT_MODEL=base` / `OCTOAGENT_STT_DEVICE=cpu` / `OCTOAGENT_STT_COMPUTE_TYPE=int8` / `OCTOAGENT_STT_LANGUAGE=`(空=auto)。（env 配置范式沿用 F115 `OCTOAGENT_USER_TIMEZONE`，PoC 不动 octoagent.yaml schema；promote 到 yaml 留 F110。）
  - `name = "faster-whisper"`
  - `is_available()`：`importlib.util.find_spec("faster_whisper") is not None`（**不 import、不加载模型**，cheap）。
  - `_ensure_model()`：lazy `import faster_whisper`（**函数内 import**，模块顶层零 import，AC-5）→ 构造 `WhisperModel` 单例（缓存到 `self._model`），失败 raise（上层 service 捕获 → model_error... 实际归一到 transcribe_error，见下）。
  - `async transcribe(audio, *, mime, filename)`：把 blocking 转写丢 `asyncio.to_thread`（faster-whisper 同步 CPU-bound，沿用 F125 to_thread 先例）。线程内：`buf = io.BytesIO(audio); buf.name = filename or "voice.ogg"`；`model = self._ensure_model()`；`segments, info = model.transcribe(buf, language=... or None)`；`text = "".join(s.text for s in segments).strip()`。返回 `SttResult(ok=bool(text), text=text, backend=self.name, reason="" if text else "empty", duration_ms=...)`。**model_error 区分**：`_ensure_model` 失败的异常在 service 层被捕获归 `transcribe_error`（reason 粒度对 PoC 足够；如需细分在 backend try 内 catch 模型加载异常单独返回 reason=model_error——采用此细分以满足 AC-6 措辞）。
- `build_default_stt_service() -> SpeechToTextService`：`SpeechToTextService(FasterWhisperBackend())`。

---

## 3. 修改文件

### 3.1 `apps/gateway/src/octoagent/gateway/services/telegram_client.py`
- 新增 `TelegramVoice(BaseModel)`：`file_id: str`、`file_unique_id: str=""`、`duration: int=0`、`mime_type: str="audio/ogg"`、`file_size: int=0`。
- `TelegramMessage` 加字段 `voice: TelegramVoice | None = None`（**polling 路径经 `model_validate` → `model_dump`，不加此字段 voice 会被 pydantic 丢弃**；webhook 路径走原始 dict 不受影响，但加了对两路都安全）。
- `TelegramBotClient` 新增：
  - `async get_file(file_id) -> dict`：`return await self._request("getFile", payload={"file_id": file_id})`（result 含 `file_path`）。
  - `async download_file_bytes(file_path, *, max_bytes: int) -> bytes`：**独立 httpx GET**（非 `_request`，因下载走 `{base_url}/file/bot{token}/{file_path}` 而非 bot API method）。加载 token；`async with httpx.AsyncClient(...) as c: r = await c.get(url)`；非 200 raise `TelegramBotApiError`；`len(content) > max_bytes` raise；返回 `r.content`。

### 3.2 `apps/gateway/src/octoagent/gateway/services/telegram.py`
- `TelegramBotClientProtocol`（109）加 `get_file` + `download_file_bytes` 方法签名（service 通过 protocol 调）。
- `TelegramVoiceRef` 新 dataclass（slots）：`file_id: str`、`mime_type: str`、`duration: int`、`file_size: int`。
- `TelegramInboundContext`（150）末尾加 `voice: TelegramVoiceRef | None = None`。
- `_extract_context`（607）：text 提取后加 voice 提取——`raw_voice = message.get("voice")`；若是 Mapping → 构 `TelegramVoiceRef`，赋给返回的 context。
- `_ingest_update`（353）：在 `is_callback` 分支后、空文本检查（367）前插入：
  ```python
  if context.voice is not None and not context.text.strip():
      outcome = await self._handle_voice_message(context)
      if isinstance(outcome, TelegramIngestResult):
          return outcome
      context = outcome  # text 已填，继续原流程
  ```
- 新增 `async _handle_voice_message(context) -> TelegramInboundContext | TelegramIngestResult`（设计见 §1 流程①-⑤）：
  - 幂等预检：`existing = await self._stores.event_store.check_idempotency_key(self._build_idempotency_key(context))`；命中 → `return TelegramIngestResult(status="duplicate", task_id=existing, created=False)`（**不转写**，AC-3）。
  - STT 不可用（`self._stt_service is None or not self._stt_service.is_available()`）→ `_reply_voice_degrade` + `return ignored(detail="voice_stt_unavailable")`。
  - 守卫：`context.voice.duration > max_duration` 或 `file_size > max_bytes` → 降级 `too_large`。
  - 下载：try `file_path = (await bot_client.get_file(voice.file_id))["file_path"]; audio = await bot_client.download_file_bytes(file_path, max_bytes=...)`；except → 降级 `download`。
  - 转写：`result = await self._stt_service.transcribe(audio, mime=voice.mime_type, filename=...)`；`not result.ok` → 降级（reason→用户文案映射：empty→"未能识别"、其他→"语音转写失败"）。
  - 成功：`logger.info("telegram_voice_transcribed backend=%s duration_s=%s transcript_len=%s", ...)`（FR-D1，不记原文）；`return dataclasses.replace(context, text=result.text)`。
- 新增 `async _reply_voice_degrade(context, text) -> None`：`if self._bot_client is None: return`；`with contextlib.suppress(Exception): await self._bot_client.send_message(context.chat_id, text, reply_to_message_id=context.message_id)`；`logger.warning("telegram_voice_degraded ...")`。
- `__init__` 加参数 `stt_service: SpeechToTextService | None = None` → `self._stt_service = stt_service`。
- 降级文案常量（模块级或方法内）：`未启用`/`下载失败`/`转写失败`/`未能识别`/`语音过长`。

### 3.3 `apps/gateway/src/octoagent/gateway/harness/octo_harness.py`（wiring，510）
- 构造 `TelegramGatewayService(...)` 时加 `stt_service=build_default_stt_service()`（lazy，模型首次转写才加载，不影响启动）。import from `..voice`。

### 3.4 `apps/gateway/pyproject.toml`
- 加：
  ```toml
  [project.optional-dependencies]
  voice = ["faster-whisper>=1.0,<2.0"]
  ```
  （faster-whisper 传递依赖 `av`/PyAV 自带 ffmpeg 库，无系统依赖。）

---

## 4. 测试计划（AC↔test 绑定）

### 4.1 `apps/gateway/tests/test_stt_service.py`
- `FakeBackend`（available 可控、transcribe 返回可控/raise）。
- `test_stt_unavailable_when_lib_missing`（AC-5）：真 `FasterWhisperBackend().is_available()` 在测试 venv = False（faster-whisper 未装）。
- `test_transcribe_ok`（AC-6）：FakeBackend ok → service 透传。
- `test_transcribe_empty_normalized`（AC-6）：FakeBackend ok 但空文本 → service 归一 ok=False reason=empty。
- `test_transcribe_exception_caught`（AC-6）：FakeBackend.transcribe raise → service ok=False reason=transcribe_error，不抛。
- `test_faster_whisper_backend_lazy_import`（AC-5）：import 模块不崩（顶层无 faster_whisper import）。

### 4.2 `apps/gateway/tests/test_telegram_voice.py`
- 复用/扩展 `FakeTelegramBotClient`（加 `get_file`/`download_file_bytes` + 记录 sent_messages）；`FakeSttService`（is_available + transcribe 可控）。
- `test_extract_context_detects_voice`（AC-1）。
- `test_voice_message_transcribed_and_enqueued`（AC-2/AC-10 e2e）：经 `handle_webhook_update` 全链 → enqueue 收到转写文本 + Task 创建。
- `test_voice_message_idempotent_replay`（AC-3）：两次同 update → 第二次 duplicate，`FakeSttService.transcribe` 仅调 1 次。
- `test_voice_degrade_unavailable`（AC-4）：stt 不可用 → 回复 + ignored + 无 Task。
- `test_voice_degrade_download_fail`（AC-4）：download raise → 回复 + ignored。
- `test_voice_degrade_transcribe_fail`（AC-4）：transcribe ok=False → 回复 + ignored。
- `test_voice_degrade_empty`（AC-4）：空转写 → "未能识别" 回复 + ignored。
- `test_voice_degrade_too_large`（AC-4）：duration 超限 → 回复 + ignored，**不下载不转写**。
- `test_bot_client_download`（AC-7）：mock httpx transport → get_file/download_file_bytes URL/参数断言。
- `test_voice_transcription_observable`（AC-8）：caplog 断言成功日志含 backend/duration/transcript_len，不含转写原文。

### 4.3 回归
- 全量 `pytest`（PYTHONPATH 锁 worktree）对比 baseline，0 regression（AC-9）。
- `pytest -m e2e_smoke` 8/8（AC-9）。

---

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| polling 路径 voice 被 pydantic 丢弃 | TelegramMessage 加 `voice` 字段（§3.1） |
| 重投触发昂贵重复转写 | 幂等预检前置于转写（§3.2，event_store.check_idempotency_key） |
| 异常逃逸 polling loop / webhook 500 | `_handle_voice_message` 全 try/降级；`_reply_voice_degrade` suppress（FR-D3） |
| 模块顶层 import faster_whisper 崩启动 | 函数内 lazy import + find_spec 探测（AC-5） |
| worktree uv sync 污染 / 假 0 regression | PYTHONPATH 锁 + Fake backend，零真 faster-whisper 依赖（§0） |
| 隐私泄漏（日志记转写原文/音频） | 日志只记 len/duration/backend（FR-D1/D2） |

---

## 6. 实施批次

- **批次 1**：voice 模块（stt.py + faster_whisper_backend.py + __init__）+ test_stt_service.py → 自测绿。
- **批次 2**：telegram_client.py（TelegramVoice + get_file + download）+ 协议 + telegram.py（context/extract/ingest/voice handler/degrade）+ wiring + pyproject。
- **批次 3**：test_telegram_voice.py 全用例 + 全量回归 + e2e_smoke。
- **批次 4**：双评审 panel（Codex + 第二模型）→ 0 HIGH → completion-report + handoff + living-docs。
