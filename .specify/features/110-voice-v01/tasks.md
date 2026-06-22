# F110 语音 v0.1 — 任务清单（tasks.md）

- **Feature**: F110 voice-v01
- **基线 commit**: `1cd2083f`（F109 STT only 已合入）
- **User Stories**: US-1（P1）TTS 出站 / US-2（P1）voice session 自动标记 / US-3（P1）降级 / US-4（P2）显式控制 / US-5（P2）可观测性
- **任务总数**: 47 个（Phase 0: 3 / Phase A: 9 / Phase B: 5 / Phase C: 6 / Phase D: 14 / Phase E: 10）
- **触碰文件数**: 9（新增 3 + 扩展 1 + 修改 5）

> 每条任务格式：`- [ ] T<phase><seq> [US?] 动词开头一句话 | 文件 | 验收 | 依赖`  
> `[P]` = 可与同 Phase 其他 `[P]` 标记任务并行（文件不冲突，无顺序依赖）。  
> 「blockedBy」列出前置 Task ID，空白表示仅依赖前一 Phase 全部完成。

---

## Phase 0：De-risk 侦察（不写生产代码）

**目标**：锁定 N_baseline、确认 PyAV libopus 可用性（决定 piper_backend.py 实现路径）、确认 piper-tts 安装态（决定测试策略），为后续 Phase 提供确定性事实。

**这两个 task 是所有后续 task 的前置。**

---

- [x] T0.1 — 基线对账：PYTHONPATH 锁 worktree 跑全量测试，记录 N_baseline（预期约 4135+）  
  **文件**: 无（只跑命令）  
  **验收**: 命令输出含 `N passed`，0 failed，记入 Phase 0 笔记  
  **blockedBy**: 无  
  **命令**（锁 PYTHONPATH，防 worktree 逃逸）:
  ```bash
  WORKTREE=/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F110-voice-v01/octoagent
  PYTHONPATH=${WORKTREE}/apps/gateway/src:${WORKTREE}/packages/core/src:${WORKTREE}/packages/provider/src:${WORKTREE}/packages/policy/src:${WORKTREE}/packages/memory/src:${WORKTREE}/packages/protocol/src:${WORKTREE}/packages/sdk/src:${WORKTREE}/packages/skills/src:${WORKTREE}/packages/tooling/src \
  uv run --no-sync python -m pytest \
    packages/core/tests packages/provider/tests packages/protocol/tests \
    packages/tooling/tests packages/skills/tests packages/policy/tests \
    packages/memory/tests apps/gateway/tests tests \
    -p no:cacheprovider --tb=short -q 2>&1 | tail -5
  ```

- [x] T0.2 — D4 PyAV libopus 实测：执行 plan §0-2 脚本，确认 WAV→OGG/Opus 路径可用性  
  **文件**: 无（只跑脚本）  
  **验收**: 根据结果做决策树判断（见 plan §0-2）——  
    - `magic=b'OggS' PASS` → Phase A 用 PyAV，pyproject.toml 不加 pyogg  
    - `ValueError: Codec not found` → Phase A 用 PyOgg，pyproject.toml 加 `pyogg>=0.8,<1.0`  
    - `av 不可导入` → `uv pip install av` 再重跑  
  结果写入 Phase 0 笔记（影响 TA.2/TA.3 的实现路径）  
  **blockedBy**: T0.1

- [x] T0.3 — piper-tts 安装态侦察：确认 worktree venv 是否有 piper（决定测试 skip 策略）  
  **文件**: 无（只跑命令）  
  **验收**: `importlib.util.find_spec('piper')` 输出 None 或路径，记入 Phase 0 笔记  
    - 无 piper → 单测全用 `FakeTtsBackend`，跳过真实合成（AC-C4 用 skipif 或 PyAV 真跑）  
    - 有 piper → 可视情况在 test_tts_service.py 中加真实 backend 的集成测  
  **blockedBy**: T0.1

---

## Phase A：TTS 服务层（新建）

**User Story**: US-1（P1）+ US-3（P1）+ US-5（P2）  
**目标**: 建立 TTS 抽象层——`TtsResult` / `TtsBackend` Protocol / `TextToSpeechService` / `PiperTtsBackend`，镜像 F109 STT 架构，单测零真实 piper 依赖。  
**独立测试**: `pytest apps/gateway/tests/test_tts_service.py` 全 PASS，无 piper 安装也通。

---

### 实现 task

- [x] TA.1 [US1,US3] 新建 `tts.py`：定义 `TtsResult`（Pydantic）+ `TtsBackend`（Protocol @runtime_checkable）+ `TextToSpeechService`  
  **文件**: `apps/gateway/src/octoagent/gateway/voice/tts.py`（新建，~80 行）  
  **满足 FR/AC**: FR-A1（TtsResult 字段） / FR-A2（TtsBackend Protocol） / FR-A3（TextToSpeechService 永不抛异常） / AC-A2 / AC-A3 / AC-A4  
  **关键实现要求**:  
    - `TtsResult`: `ok: bool` / `audio: bytes = b""` / `reason: str = ""` / `backend: str = ""` / `duration_ms: int = 0`，字段语义对称 `stt.py:21`  
    - `TtsBackend`: `name: str` / `is_available() -> bool` / `async synthesize(text, *, language) -> TtsResult`，对称 `stt.py:36`  
    - `TextToSpeechService.synthesize`: 捕获所有异常，空音频归一为 `reason="empty_audio"`，绝不把异常抛给调用方  
  **验收**: 文件创建，类定义无 import 错误（`python -c "from octoagent.gateway.voice.tts import TextToSpeechService"`）  
  **blockedBy**: T0.1（N_baseline 已记）

- [x] TA.2 [US1,US3] 新建 `piper_backend.py`：实现 `wav_to_ogg_opus`（D4 路径由 T0.2 决定）+ `PiperTtsBackend` + `build_default_tts_service`  
  **文件**: `apps/gateway/src/octoagent/gateway/voice/piper_backend.py`（新建，~130 行）  
  **满足 FR/AC**: FR-A4（lazy load + asyncio.to_thread + 30s 超时） / FR-A6（wav_to_ogg_opus 失败返回空 bytes） / FR-A7（env 配置读取） / AC-A1 / AC-A4 / AC-C4 / AC-C5  
  **关键实现要求**:  
    - `PiperTtsBackend.is_available()`: 检查 `importlib.util.find_spec("piper") is not None` + 模型文件存在（AC-A1 独立路径：安装了但模型缺失）  
    - `PiperTtsBackend._synthesize_sync`: `PiperVoice.synthesize_wav` → `io.BytesIO` → WAV bytes → `wav_to_ogg_opus`  
    - `PiperTtsBackend.synthesize`: `asyncio.to_thread(_synthesize_sync)` + `asyncio.wait_for(30s)` 超时 → `TtsResult(ok=False, reason="tts_timeout")`  
    - `wav_to_ogg_opus`: 按 T0.2 结果选路径（PyAV `libopus` / PyOgg），失败返回 `b""`  
    - env 变量: `OCTOAGENT_TTS_BACKEND` / `OCTOAGENT_TTS_VOICE_MODEL`（默认 `zh_CN-huayan-medium`）/ `OCTOAGENT_TTS_LANGUAGE`（默认 `zh_CN`）/ `OCTOAGENT_TTS_ENABLED`（默认 True）  
    - `build_default_tts_service()`: 工厂函数，镜像 `faster_whisper_backend.py:95`  
  **验收**: `python -c "from octoagent.gateway.voice.piper_backend import PiperTtsBackend"` 无 import 期崩溃  
  **blockedBy**: T0.2（PyAV libopus 可用性决定实现路径）, TA.1（依赖 TtsResult/TtsBackend）

- [x] TA.3 [US1] 修改 `voice/__init__.py`：加入 TTS 相关导出  
  **文件**: `apps/gateway/src/octoagent/gateway/voice/__init__.py`（修改，当前 16 行）  
  **满足 FR/AC**: FR-A8（wiring 依赖此 re-export）  
  **关键实现要求**:  
    - 新增导出: `TextToSpeechService`, `TtsBackend`, `TtsResult`, `PiperTtsBackend`, `build_default_tts_service`  
    - 加入 `__all__`  
  **验收**: `from octoagent.gateway.voice import build_default_tts_service` 成功  
  **blockedBy**: TA.1, TA.2

- [x] TA.4 [US1] 修改 `pyproject.toml`：在 `[voice]` extra 加入 `piper-tts>=1.4,<2.0`（以及视 T0.2 结果条件加 pyogg）  
  **文件**: `apps/gateway/pyproject.toml`（修改，当前约 51 行）  
  **满足 FR/AC**: FR-A5（piper-tts 必须作为 optional 依赖）  
  **关键实现要求**:  
    - `[project.optional-dependencies].voice` 加 `"piper-tts>=1.4,<2.0"`  
    - 如果 T0.2 确认 PyAV 不可用，同时加 `"pyogg>=0.8,<1.0"`  
    - pyogg 的注释行保留（`# D4 备选：Phase 0 实测 PyAV 不可用时启用`）  
  **验收**: `grep piper-tts apps/gateway/pyproject.toml` 有匹配  
  **blockedBy**: T0.2

### 测试 task

- [x] TA.5 [US3] 新建 `test_tts_service.py`：定义 `FakeTtsBackend` + 基础测试骨架  
  **文件**: `apps/gateway/tests/test_tts_service.py`（新建，~120 行）  
  **满足 AC（绑定测试函数）**:  
    - AC-A3: `test_tts_result_schema`（TtsResult 字段存在性 + 默认值）  
    - AC-A4: `test_piper_backend_protocol_conformance`（isinstance check with @runtime_checkable Protocol）  
  **FakeTtsBackend 接口**:  
    ```python
    class FakeTtsBackend:
        name = "fake"
        def __init__(self, *, available=True, result=None, raises=False): ...
        def is_available(self) -> bool: ...
        async def synthesize(self, text, *, language) -> TtsResult: ...
    ```  
  **blockedBy**: TA.1, TA.2（被测对象需先存在）

- [x] TA.6 [US3] 补全 `test_tts_service.py`：AC-A1 / AC-A2 / AC-E1 测试函数  
  **文件**: `apps/gateway/tests/test_tts_service.py`（扩展 TA.5）  
  **满足 AC（绑定测试函数）**:  
    - AC-A1: `test_tts_unavailable_when_lib_missing`（monkeypatch `importlib.util.find_spec` 返回 None → `is_available()=False`，不崩）  
    - AC-A1 扩展: `test_piper_model_missing_is_unavailable`（piper 可 import 但模型文件路径不存在 → `is_available()=False`）  
    - AC-A2: `test_synthesize_ok`（FakeTtsBackend 返回正常 → TtsResult.ok=True）  
    - AC-A2: `test_synthesize_error_returns_false`（FakeTtsBackend.synthesize 抛异常 → TtsResult(ok=False, reason="synthesize_error")）  
    - AC-A2: `test_synthesize_empty_audio_returns_false`（FakeTtsBackend 返回 audio=b"" → TtsResult(ok=False, reason="empty_audio")）  
    - AC-E1: `test_tts_synthesis_observable`（FakeTtsBackend + logging 捕获 → 断言日志含 backend/duration_ms/text_len，不含完整文本）  
  **blockedBy**: TA.5

- [x] TA.7 [US3] 补全 `test_tts_service.py`：AC-C4 / AC-C5 WAV→OGG 格式转换测试  
  **文件**: `apps/gateway/tests/test_tts_service.py`（扩展 TA.5）  
  **满足 AC（绑定测试函数）**:  
    - AC-C4: `test_wav_to_ogg_opus_produces_valid_ogg`（生成 1 秒静音 WAV，转换后断言 magic=b'OggS'）  
      skip 策略：若 T0.3 确认 PyAV libopus 不可用且 PyOgg 不可用，用 `pytest.mark.skipif` + 明确注释  
    - AC-C5: `test_wav_to_ogg_opus_failure_handled`（传入非法 bytes → 返回空 bytes 或捕获异常，调用方不崩）  
  **blockedBy**: TA.2, TA.5

- [x] TA.8 — Phase A 单测验证（PYTHONPATH 锁定）  
  **文件**: 无（只跑命令）  
  **验收**: 以下命令全 PASS，0 failed
  ```bash
  PYTHONPATH=... uv run --no-sync python -m pytest \
    apps/gateway/tests/test_tts_service.py -p no:cacheprovider -v
  ```  
  **blockedBy**: TA.5, TA.6, TA.7

- [x] TA.9 — Phase A Codex per-Phase review  
  **文件**: 无（review 钩子）  
  **触发**: `/codex:adversarial-review`  
  **范围**: `tts.py` + `piper_backend.py` + `test_tts_service.py`  
  **重点**: Protocol 对称性 / 降级路径覆盖 / asyncio.to_thread 线程安全 / lazy import 路径稳定性  
  **验收**: 0 HIGH 残留；medium/low finding 处理决策记入 commit message  
  **blockedBy**: TA.8

---

## Phase B：send_voice bot client 方法

**User Story**: US-1（P1）+ US-3（P1）  
**目标**: `TelegramBotClient` 新增 `send_voice`（multipart/form-data），为 Phase D 出站接入提供 client 层支撑。  
**独立测试**: `pytest apps/gateway/tests/test_telegram_voice.py -k "send_voice"` 全 PASS。

---

### 实现 task

- [x] TB.1 [US1] 修改 `telegram_client.py`：新增 `async send_voice(chat_id, voice, *, duration=None, reply_to_message_id=None, message_thread_id=None, disable_notification=True) -> TelegramMessage`  
  **文件**: `apps/gateway/src/octoagent/gateway/services/telegram_client.py`（修改，~330 行）  
  **满足 FR/AC**: FR-C1（send_voice 签名） / FR-C2（multipart/form-data，不走 _request JSON POST）/ FR-C3（失败抛 TelegramBotApiError）/ AC-C1 / AC-C2 / AC-C3  
  **关键实现要求**:  
    - 不走 `_request`：单独构造 multipart httpx 请求，复用 `_load_bot_token()` + `_base_url`  
    - URL: `f"{self._base_url}/bot{bot_token}/sendVoice"`  
    - files 字段: `{"voice": ("voice.ogg", voice, "audio/ogg")}`  
    - 失败时抛 `TelegramBotApiError(description, status_code=response.status_code)`  
    - 成功时返回 `TelegramMessage.model_validate(response.json()["result"])`  
  **验收**: 方法存在，签名正确，`grep send_voice apps/gateway/src/octoagent/gateway/services/telegram_client.py` 有匹配  
  **blockedBy**: Phase A 全部完成

### 测试 task

- [x] TB.2 [US1] 扩展 `test_telegram_voice.py`：AC-C1 multipart 结构测试  
  **文件**: `apps/gateway/tests/test_telegram_voice.py`（扩展）  
  **满足 AC（绑定测试函数）**:  
    - AC-C1: `test_bot_client_send_voice_multipart`（mock httpx → 断言 POST、URL 含 sendVoice、body 是 multipart 含 voice 字段）  
  **blockedBy**: TB.1

- [x] TB.3 [US1] 扩展 `test_telegram_voice.py`：AC-C2 + AC-C3 可选参数和失败处理测试  
  **文件**: `apps/gateway/tests/test_telegram_voice.py`（扩展）  
  **满足 AC（绑定测试函数）**:  
    - AC-C2: `test_bot_client_send_voice_optional_params`（传 duration/reply_to_message_id/message_thread_id → 断言 form 字段存在）  
    - AC-C3: `test_bot_client_send_voice_raises_on_failure`（mock 返回 400 → 断言抛 TelegramBotApiError）  
  **blockedBy**: TB.1

- [x] TB.4 — Phase B 单测验证  
  **文件**: 无（只跑命令）  
  **验收**: 以下命令全 PASS
  ```bash
  PYTHONPATH=... uv run --no-sync python -m pytest \
    apps/gateway/tests/test_telegram_voice.py -k "send_voice" -p no:cacheprovider -v
  ```  
  **blockedBy**: TB.2, TB.3

- [x] TB.5 — Phase B Codex per-Phase review  
  **文件**: 无（review 钩子）  
  **触发**: `/codex:adversarial-review`  
  **范围**: `telegram_client.py` send_voice 新增部分  
  **重点**: multipart 结构正确性 / 错误处理完整性 / bot token 不泄漏日志  
  **验收**: 0 HIGH 残留  
  **blockedBy**: TB.4

---

## Phase C：voice_mode 状态机

**User Story**: US-2（P1）+ US-4（P2）  
**目标**: `ConversationBinding.metadata` 三态 helper + `/voice on|off` 命令 + 入站 voice 自动标记（GATE D2-C 裁决落地）。最大风险：read-modify-write 必须完整执行，防止 upsert 全量替换清掉其他 metadata 字段。  
**独立测试**: `pytest apps/gateway/tests/test_telegram_voice.py -k "voice_mode or voice_command"` 全 PASS。

---

### 实现 task

- [x] TC.1 [US2,US4] 修改 `telegram.py`：添加 `_get_voice_mode` + `_is_voice_mode_explicitly_disabled` 私有 helper 方法  
  **文件**: `apps/gateway/src/octoagent/gateway/services/telegram.py`（修改，~1150 行）  
  **满足 FR/AC**: FR-D1（三态语义） / FR-D3（voice_mode 读取）/ AC-D7  
  **关键实现要求**:  
    - `_get_voice_mode(binding) -> bool`: key 缺失→False；True→True；False→False  
    - `_is_voice_mode_explicitly_disabled(binding) -> bool`: 区分「key 缺失（unset）」vs「key=False（显式 False）」  
    - 两个方法都不依赖其他新方法，可先实现  
  **验收**: grep 确认两个方法存在于 telegram.py  
  **blockedBy**: Phase B 全部完成

- [x] TC.2 [US4] 修改 `telegram.py`：扩展 `_handle_control_command` 支持 `/voice on|off`，新增 `_handle_voice_command`  
  **文件**: `apps/gateway/src/octoagent/gateway/services/telegram.py`（修改，在 `_handle_control_command` ~635 行附近）  
  **满足 FR/AC**: FR-D2（voice 控制命令不经 LLM 决策环，Constitution #9）/ AC-D2 / AC-D3  
  **关键实现要求**:  
    - `_handle_control_command` 在 `build_telegram_action_request` 前检测 `/voice on` / `/voice off`（case-insensitive，strip）  
    - `_handle_voice_command(context, *, enable: bool)`: 执行 read-modify-write（先 get→merge existing metadata→再 upsert）+ `send_message` 确认文字（Constitution #9：控制命令回复**不**走 TTS，FR-B3）  
    - merge metadata 标准流程: `existing = await binding_store.get(...)` → `merged = dict(existing.metadata) if existing else {}` → `merged["voice_mode"] = enable` → `upsert_runtime_binding(..., metadata=merged)`  
    - 确认回复文字: `/voice on` → "语音模式已开启 🔊"；`/voice off` → "语音模式已关闭 💬"  
  **验收**: grep 确认 `_handle_voice_command` 存在，且 `_handle_control_command` 内有 voice 分支  
  **blockedBy**: TC.1

- [x] TC.3 [US2] 修改 `telegram.py`：扩展 `_record_conversation_binding` 支持 `set_voice_mode_if_unset` 参数（MEDIUM-3 read-modify-write 最大风险）  
  **文件**: `apps/gateway/src/octoagent/gateway/services/telegram.py`（修改，`_record_conversation_binding` ~584 行）  
  **满足 FR/AC**: FR-D1（入站 voice 自动标记，GATE D2-C 裁决） / FR-D4（voice_mode 落 SQLite 跨重启保留） / AC-D1 / AC-D1b  
  **关键实现要求（最大风险，必须 read-modify-write）**:  
    - 新增参数 `set_voice_mode_if_unset: bool = False`（向后兼容，默认 False）  
    - 当 `set_voice_mode_if_unset=True` 时: `existing = await binding_store.get(...)` → 检查 `_is_voice_mode_explicitly_disabled(existing)` → 若否则 `merged["voice_mode"] = True`；若是则保留原 False 值  
    - **关键**：写入前必须先 get 现有 metadata，merge（`merged = dict(existing.metadata) if existing else {}`），再写所有字段（last_message_thread_id / last_reply_thread_root_id / voice_mode），**防止 upsert 全量替换清掉其他字段**  
    - `_handle_voice_message` 成功后调用时传 `set_voice_mode_if_unset=True`  
  **验收**: `grep set_voice_mode_if_unset apps/gateway/src/octoagent/gateway/services/telegram.py` 有匹配  
  **blockedBy**: TC.1

### 测试 task（voice_mode 边界测试，最重要单列）

- [x] TC.4 [US2] 扩展 `test_telegram_voice.py`：AC-D1 + AC-D1b voice_mode 自动标记和「显式关闭后不重开」边界测试  
  **文件**: `apps/gateway/tests/test_telegram_voice.py`（扩展）  
  **满足 AC（绑定测试函数）**:  
    - AC-D1: `test_voice_message_sets_voice_mode`（voice update 处理后 binding.metadata["voice_mode"]=True）  
    - AC-D1b: `test_voice_off_then_voice_message_stays_off`（先 /voice off → voice_mode=False → 再发 voice update → voice_mode 仍为 False，不重开）  
  **注意**: AC-D1b 是 GATE 裁决的核心边界，必须显式测试 `_is_voice_mode_explicitly_disabled` 区分 unset vs False 的逻辑  
  **blockedBy**: TC.2, TC.3

- [x] TC.5 [US4] 扩展 `test_telegram_voice.py`：AC-D2 + AC-D3 显式控制命令测试  
  **文件**: `apps/gateway/tests/test_telegram_voice.py`（扩展）  
  **满足 AC（绑定测试函数）**:  
    - AC-D2: `test_voice_off_command_clears_voice_mode`（`/voice off` → metadata["voice_mode"]=False，bot 回复确认文字）  
    - AC-D3: `test_voice_on_command_sets_voice_mode`（`/voice on` → voice_mode=True，bot 回复确认文字）  
  **blockedBy**: TC.2, TC.3

- [x] TC.6 — Phase C 单测验证 + Codex per-Phase review  
  **文件**: 无  
  **验收（两步）**:  
    1. `pytest apps/gateway/tests/test_telegram_voice.py -k "voice_mode or voice_command" -p no:cacheprovider -v` 全 PASS  
    2. Codex `/codex:adversarial-review`（范围：telegram.py Phase C 改动）  
       重点：三态语义正确性 / read-modify-write 是否在所有写 voice_mode 路径都完整执行 / Constitution #9 边界（控制命令不经 LLM）/ AC-D1b 显式关闭后不重开逻辑  
       验收：0 HIGH 残留  
  **blockedBy**: TC.4, TC.5

---

## Phase D：出站 TTS 接入 + e2e

**User Story**: US-1（P1）+ US-2（P1）+ US-3（P1）+ US-4（P2）  
**目标**: `notify_task_result` 插入 TTS 分支 + `TelegramGatewayService.__init__` 注入 tts_service + `octo_harness.py` wiring + e2e voice 往返链验证。  
**独立测试**: `pytest apps/gateway/tests/test_telegram_voice.py` 全 PASS（含 e2e AC-Z2）。

---

### 实现 task

- [x] TD.1 [US1] 修改 `telegram.py`：`TelegramGatewayService.__init__` 新增 `tts_service: TextToSpeechService | None = None` 参数  
  **文件**: `apps/gateway/src/octoagent/gateway/services/telegram.py`（修改，`__init__` 方法签名）  
  **满足 FR/AC**: FR-A8（tts_service 参数，向后兼容 None 默认值）  
  **关键实现要求**: 在现有 `stt_service: SpeechToTextService | None = None` 之后加，`self._tts_service = tts_service`  
  **验收**: grep 确认 `__init__` 签名含 `tts_service`  
  **blockedBy**: Phase C 全部完成，TA.3（TTS 类型需要 import）

- [x] TD.2 [US1,US3] 修改 `telegram.py`：新增 `_try_send_voice_reply` 私有方法（TTS 出站核心，含完整降级链）  
  **文件**: `apps/gateway/src/octoagent/gateway/services/telegram.py`（修改，~30 行新增方法）  
  **满足 FR/AC**: FR-B1（voice_mode 查询 + TTS 合成 + send_voice）/ FR-B3（控制命令回复不走 TTS）/ FR-B4（降级链完整）/ FR-B5（异常不逃逸）/ AC-B1 / AC-B3 / AC-B4 / AC-D7  
  **关键实现要求**:  
    - 签名：`async _try_send_voice_reply(self, target: dict, text: str) -> bool`  
    - 顺序：tts_service 是否可用 → 查 binding voice_mode → TTS 合成 → send_voice  
    - 任何一步 False/失败 → log + return False（降级到调用方的 send_message）  
    - 所有异常用顶层 `except Exception` 捕获，log + return False，绝不逃逸  
    - voice_mode 读取：`binding = await binding_store.get("telegram", target["chat_id"])` + `_get_voice_mode(binding)`  
    - 成功日志: `"tts_success backend=%s duration_ms=%d text_len=%d"` (FR-E1)  
    - 降级日志: `"tts_degrade reason=%s chat_id=%s"` (FR-E2 / AC-E2)  
  **验收**: grep 确认 `_try_send_voice_reply` 存在  
  **blockedBy**: TD.1

- [x] TD.3 [US1,US3] 修改 `telegram.py`：在 `notify_task_result` 插入 TTS 分支（调用 `_try_send_voice_reply`）  
  **文件**: `apps/gateway/src/octoagent/gateway/services/telegram.py`（修改，`notify_task_result` ~872 行）  
  **满足 FR/AC**: FR-B1（插入点）/ FR-B2（`_build_result_text` 和 `_resolve_reply_target` 零修改）/ FR-B5（异常不逃逸）/ AC-B1 / AC-B2  
  **关键实现要求**:  
    - 在 `_build_result_text` 结果之后、原有 `send_message` 之前：  
      ```python
      if await self._try_send_voice_reply(target, text):
          return  # 语音发送成功，不再发文字
      ```  
    - `_build_result_text` 和 `_resolve_reply_target` MUST NOT 被修改（FR-B2 硬约束）  
    - `notify_approval_event` / 控制命令回复 / 降级路径的 `send_message` MUST NOT 加 TTS（FR-B3）  
  **验收**: `grep -A5 "_try_send_voice_reply" telegram.py` 显示 `if await ... : return` 模式  
  **blockedBy**: TD.2

- [x] TD.4 [US1] 修改 `octo_harness.py`：在构造 `TelegramGatewayService` 时注入 `tts_service=build_default_tts_service()`  
  **文件**: `apps/gateway/src/octoagent/gateway/harness/octo_harness.py`（修改，STT wiring ~510 行附近）  
  **满足 FR/AC**: FR-A8（wiring 镜像 STT）  
  **关键实现要求**:  
    - import: `from ..voice import build_default_stt_service, build_default_tts_service`（更新现有 STT import）  
    - `TelegramGatewayService(...)` 构造调用加 `tts_service=build_default_tts_service()`  
  **验收**: `grep build_default_tts_service apps/gateway/src/octoagent/gateway/harness/octo_harness.py` 有匹配  
  **blockedBy**: TD.1, TA.3

### 测试 task

- [x] TD.5 [US1] 扩展 `test_telegram_voice.py`：定义 `FakeTtsService` + `FakeVoiceBotClient`（加 send_voice 记录）  
  **文件**: `apps/gateway/tests/test_telegram_voice.py`（在文件顶部扩展）  
  **内容**:
  ```python
  class FakeTtsService:
      def __init__(self, *, available=True, result_ok=True, audio=b"OggS\x00\x00"): ...
      def is_available(self) -> bool: ...
      async def synthesize(self, text: str) -> TtsResult: ...
  
  # FakeVoiceBotClient 扩展（F109 已有），加:
  #   send_voice_calls: list[dict]  # 记录每次 send_voice 调用参数
  ```  
  **blockedBy**: Phase C 全部完成

- [x] TD.6 [US1,US3] 扩展 `test_telegram_voice.py`：AC-B1 / AC-B2 / AC-B3 / AC-B4 / AC-B5 出站 TTS 核心测试  
  **文件**: `apps/gateway/tests/test_telegram_voice.py`（扩展）  
  **满足 AC（绑定测试函数）**:  
    - AC-B1: `test_notify_task_result_sends_voice_when_voice_mode`（voice_mode=True → send_voice 调用，不调 send_message）  
    - AC-B2: `test_notify_task_result_sends_text_when_voice_mode_off`（voice_mode=False → 仍走 send_message，行为与 F109 基线一致）  
    - AC-B3: `test_notify_task_result_degrades_to_text_on_tts_failure`（FakeTtsService ok=False → 降级 send_message，不丢 Agent 回复）  
    - AC-B4: `test_notify_task_result_degrades_to_text_on_send_voice_failure`（FakeVoiceBotClient.send_voice 抛 TelegramBotApiError → 降级 send_message）  
    - AC-B5: `test_build_result_text_unchanged`（直接调用 `_build_result_text`，断言返回等价 F109 基线）  
  **blockedBy**: TD.3, TD.5

- [x] TD.7 [US2,US1] 扩展 `test_telegram_voice.py`：AC-D4 / AC-D5 / AC-D6 / AC-D7 voice session 连续性测试  
  **文件**: `apps/gateway/tests/test_telegram_voice.py`（扩展）  
  **满足 AC（绑定测试函数）**:  
    - AC-D4: `test_voice_session_continuous_rounds`（第 1 轮 voice → voice_mode=True；第 2 轮**文字** update → notify_task_result 仍走 TTS，binding 跨轮 SQLite 持久）  
    - AC-D5: `test_voice_session_idempotent_replay`（同一 voice update 重投 → voice_mode 幂等，不重复写 True，TTS 不重复触发）  
    - AC-D6: `test_voice_session_reuses_existing_agent_session`（AgentSessionKind 枚举无新 voice 值 → 断言 voice session 复用主 Agent 现有 session 类型）  
    - AC-D7: `test_voice_mode_defaults_to_false_if_missing`（binding 不存在 / voice_mode key 缺失 → `_get_voice_mode` 返回 False → notify_task_result 走 send_message，不崩）  
  **blockedBy**: TD.3, TD.5

- [x] TD.8 [US3] 扩展 `test_telegram_voice.py`：AC-B6 超时降级 + AC-E2 降级可观测性 + AC-E3 异常不逃逸  
  **文件**: `apps/gateway/tests/test_telegram_voice.py`（扩展）  
  **满足 AC（绑定测试函数）**:  
    - AC-B6: `test_notify_task_result_degrades_on_tts_timeout`（FakeTtsService.synthesize 内 `asyncio.sleep(31)` 触发 wait_for 超时 → 降级文字，日志含 reason=tts_timeout）  
    - AC-E2: `test_tts_degrade_observable`（各 reason 码的日志断言：tts_unavailable / synthesize_error / send_voice_failed）  
    - AC-E3: `test_no_exception_escapes_notify_task_result`（注入多类失败 → 断言 notify_task_result 正常返回，不抛任何异常）  
  **blockedBy**: TD.3, TD.5

- [x] TD.9 [US1,US2] 扩展 `test_telegram_voice.py`：AC-Z2 端到端 voice 往返链  
  **文件**: `apps/gateway/tests/test_telegram_voice.py`（扩展）  
  **满足 AC（绑定测试函数）**:  
    - AC-Z2: `test_voice_roundtrip_e2e`  
      ```
      # 用 FakeSttService + FakeTtsService + FakeVoiceBotClient 组装 TelegramGatewayService
      # 步骤 1: 构造 fake voice update → service._ingest_update
      # 步骤 2: 断言 binding.metadata["voice_mode"]=True
      # 步骤 3: service.notify_task_result(fake_task) → 断言 bot_client.send_voice_calls 非空
      # 步骤 4: 构造文字 update（同 chat）→ notify_task_result → 仍 send_voice（voice_mode 持久）
      # 步骤 5: 构造 voice_mode=False 的 chat → notify_task_result → 断言 send_message（文字）
      ```  
  **blockedBy**: TD.3, TD.5, TD.6, TD.7

- [x] TD.10 — Phase D 单测验证（全 test_telegram_voice.py）  
  **文件**: 无（只跑命令）  
  **验收**: 以下命令全 PASS
  ```bash
  PYTHONPATH=... uv run --no-sync python -m pytest \
    apps/gateway/tests/test_telegram_voice.py -p no:cacheprovider -v
  ```  
  **blockedBy**: TD.6, TD.7, TD.8, TD.9

- [x] TD.11 — Phase D Codex per-Phase review  
  **文件**: 无（review 钩子）  
  **触发**: `/codex:adversarial-review`  
  **范围**: telegram.py Phase D 改动（_try_send_voice_reply / notify_task_result 分支 / __init__）+ octo_harness.py wiring  
  **重点**: 降级链完整性（8 类降级全覆盖）/ 异常不逃逸 / AC-Z2 e2e 覆盖面 / H1 边界守住（AgentSession 零修改）/ FR-B2 `_build_result_text`/`_resolve_reply_target` 零改  
  **验收**: 0 HIGH 残留  
  **blockedBy**: TD.10

---

## Phase E：观测 + 降级矩阵收尾 + 文档

**User Story**: US-3（P1）+ US-5（P2）  
**目标**: 降级矩阵 8 类逐一确认测试覆盖；living-docs 漂移闸；全量回归 0 regression；e2e_smoke 8/8；双 panel review；completion-report + handoff。

---

### 验证 task

- [x] TE.1 — 降级矩阵 8 类逐一核查（Phase E 强制）  
  **文件**: 无（Review 步骤）  
  **验收**: 逐行对照以下表格，确认每类失败场景均有对应测试函数且测试 PASS：

  | 失败场景 | reason 码 | 覆盖测试（具名函数） | Phase |
  |----------|----------|-------------------|-------|
  | piper-tts 未安装 / is_available()=False | `tts_unavailable` | `test_tts_unavailable_when_lib_missing` + `test_notify_task_result_sends_voice_when_voice_mode`（tts=unavailable variant） | A + D |
  | 模型文件缺失 | `tts_unavailable` | `test_piper_model_missing_is_unavailable` | A |
  | synthesize 抛异常 | `synthesize_error` | `test_synthesize_error_returns_false` + `test_notify_task_result_degrades_to_text_on_tts_failure` | A + D |
  | 合成返回空音频 | `empty_audio` | `test_synthesize_empty_audio_returns_false` | A |
  | WAV→OGG 格式转换失败 | `encode_error` | `test_wav_to_ogg_opus_failure_handled` | A |
  | send_voice Telegram API 失败 | `send_voice_failed` | `test_notify_task_result_degrades_to_text_on_send_voice_failure` + `test_tts_degrade_observable` | D |
  | ConversationBinding 查不到 / voice_mode 缺失 | — | `test_voice_mode_defaults_to_false_if_missing` | D |
  | TTS 合成超时（30s） | `tts_timeout` | `test_notify_task_result_degrades_on_tts_timeout` | D |

  **命令**（机械校验每个具名测试函数存在且 PASS）:
  ```bash
  PYTHONPATH=... uv run --no-sync python -m pytest \
    apps/gateway/tests/test_tts_service.py \
    apps/gateway/tests/test_telegram_voice.py \
    -v -p no:cacheprovider | grep -E "PASSED|FAILED|ERROR"
  ```  
  **blockedBy**: Phase D 全部完成

- [x] TE.2 — 全量回归 0 regression 对账（N_after ≥ N_baseline，failed=0）  
  **文件**: 无（只跑命令）  
  **验收**: 命令输出 `N_after passed`（N_after ≥ Phase 0 记录的 N_baseline），0 failed，0 errors
  ```bash
  PYTHONPATH=... uv run --no-sync python -m pytest \
    packages/core/tests packages/provider/tests packages/protocol/tests \
    packages/tooling/tests packages/skills/tests packages/policy/tests \
    packages/memory/tests apps/gateway/tests tests \
    -p no:cacheprovider --tb=short -q 2>&1 | tail -5
  ```  
  **满足 AC**: AC-Z1（0 regression）  
  **blockedBy**: TE.1

- [x] TE.3 — e2e_smoke 8/8 PASS  
  **文件**: 无（只跑命令）  
  **验收**: `pytest -m e2e_smoke` 8/8 PASS
  ```bash
  PYTHONPATH=... uv run --no-sync python -m pytest -m e2e_smoke \
    apps/gateway/tests/ -p no:cacheprovider
  ```  
  **满足 AC**: AC-Z1（e2e_smoke 门）  
  **blockedBy**: TE.2

- [x] TE.4 — AC↔test 机械校验（spec §5 SDD 强化：所有 P1 AC 函数存在且 PASS）  
  **文件**: 无（grep + pytest -k）  
  **验收**: 以下每条 `pytest -k <func>` 均返回 PASSED（无 FAILED）：

  | AC | 具名测试函数 |
  |----|-------------|
  | AC-A1 | test_tts_unavailable_when_lib_missing |
  | AC-A1（扩展）| test_piper_model_missing_is_unavailable |
  | AC-A2 | test_synthesize_ok, test_synthesize_error_returns_false, test_synthesize_empty_audio_returns_false |
  | AC-A3 | test_tts_result_schema |
  | AC-A4 | test_piper_backend_protocol_conformance |
  | AC-B1 | test_notify_task_result_sends_voice_when_voice_mode |
  | AC-B2 | test_notify_task_result_sends_text_when_voice_mode_off |
  | AC-B3 | test_notify_task_result_degrades_to_text_on_tts_failure |
  | AC-B4 | test_notify_task_result_degrades_to_text_on_send_voice_failure |
  | AC-B5 | test_build_result_text_unchanged |
  | AC-C1 | test_bot_client_send_voice_multipart |
  | AC-C2 | test_bot_client_send_voice_optional_params |
  | AC-C3 | test_bot_client_send_voice_raises_on_failure |
  | AC-C4 | test_wav_to_ogg_opus_produces_valid_ogg |
  | AC-C5 | test_wav_to_ogg_opus_failure_handled |
  | AC-D1 | test_voice_message_sets_voice_mode |
  | AC-D1b | test_voice_off_then_voice_message_stays_off |
  | AC-D2 | test_voice_off_command_clears_voice_mode |
  | AC-D3 | test_voice_on_command_sets_voice_mode |
  | AC-D4 | test_voice_session_continuous_rounds |
  | AC-D5 | test_voice_session_idempotent_replay |
  | AC-D6 | test_voice_session_reuses_existing_agent_session |
  | AC-D7 | test_voice_mode_defaults_to_false_if_missing |
  | AC-E1 | test_tts_synthesis_observable |
  | AC-E2 | test_tts_degrade_observable |
  | AC-E3 | test_no_exception_escapes_notify_task_result |
  | AC-Z2 | test_voice_roundtrip_e2e |

  **blockedBy**: TE.1

### 文档 task

- [x] TE.5 — Living-docs 漂移闸：确认触碰文档范围并同步  
  **文件**: 视检查结果修改（docs/blueprint/milestones.md / docs/codebase-architecture/platform-gateway.md）  
  **检查清单**:  
    - `docs/blueprint/milestones.md`：F110 行状态标记为 ✅ 完成（含 commit hash + 关键产出）  
    - `docs/codebase-architecture/platform-gateway.md`：若有 TTS 出站路径相关章节，同步 `_try_send_voice_reply` + `send_voice` 描述  
    - `CLAUDE.md` `§M6` 表格 F110 状态更新  
  **验收**: 三处文档更新完成（或确认不需要更新，需写明原因）  
  **blockedBy**: TE.3

- [x] TE.6 — 生成 `completion-report.md`：按 spec §11 验收门逐项标注实际做了 vs 计划  
  **文件**: `.specify/features/110-voice-v01/completion-report.md`（新建）  
  **内容要求**:  
    - Phase 0-E 每个 Phase 的「计划 vs 实际」  
    - D4 PyAV 实测结论（PyAV 可用 / 切 PyOgg）+ 原因  
    - 降级矩阵 8 类覆盖确认表  
    - 任何 spec/plan 偏离（含接受原因）  
    - deferred 事项（给 v0.2 handoff 的接力点）  
    - Codex review 闭环摘要（N high / M medium 处理 / K low ignored）  
  **blockedBy**: TE.4

- [x] TE.7 — 生成 `handoff.md`（F110 → v0.2 接力）  
  **文件**: `.specify/features/110-voice-v01/handoff.md`（新建）  
  **内容要求**:  
    - v0.2 必须做清单（outbound TTS 接 Web 渠道 / 实时双工 / Slack/Discord / 多模型多语言）  
    - F110 建立的地基供 v0.2 直接复用（TtsBackend Protocol / voice_mode binding / send_voice 路径）  
    - ConversationBinding.metadata["voice_mode"] KV 扩展点建议  
    - D4 编码路径确认结论（供 v0.2 沿用）  
  **blockedBy**: TE.6

### 双 panel review task（命中「新能力 + 外部 GPL 依赖」节点）

- [ ] TE.8 — Codex 全量对抗 review（全部 5 Phase 改动）  
  **文件**: 无（review 钩子）  
  **触发**: `/codex:adversarial-review`  
  **范围**: 全部 5 Phase 的所有改动（tts.py / piper_backend.py / telegram_client.py / telegram.py / octo_harness.py / test_*.py）  
  **重点**: 降级链完整性 / H1 边界 / GPL-3.0 引入合规性 / Constitution #6 全覆盖 / AC/FR 是否全落实  
  **验收**: 0 HIGH 残留；medium/low finding 处理决策写入 completion-report  
  **blockedBy**: TE.4, TE.5

- [ ] TE.9 — 第二 panel review（Claude Opus 或另一 provider，spec 对齐专项）  
  **文件**: 无（review 钩子）  
  **范围**: 与 TE.8 相同，但聚焦「AC/FR 逐条是否真落实 + H1 是否真守住」  
  **验收**: 0 HIGH 残留；两 panel 分歧项必须人裁（记入 completion-report）  
  **blockedBy**: TE.8

- [ ] TE.10 — 等用户拍板（最终 task，不含 push）  
  **文件**: 无  
  **验收**: 向用户呈现：  
    1. 全量回归结果（N_after passed，0 failed）  
    2. e2e_smoke 8/8 PASS  
    3. AC↔test 机械校验全部 PASS 的函数清单  
    4. 双 panel review 闭环表（N high / M medium 处理 / K low ignored）  
    5. completion-report.md + handoff.md 路径  
    6. 建议合入 origin/master 或先 review 再合入的明确建议  
  **注意**: 不主动 push，等用户显式确认后才执行 push  
  **blockedBy**: TE.9

---

## 关键依赖图

```
Phase 0（必须最先，串行）:
  T0.1（基线对账）
    └─→ T0.2（PyAV 实测，决定 TA.2 路径）
    └─→ T0.3（piper 侦察，决定测试 skip 策略）

Phase A（依赖 Phase 0 全完成）:
  TA.1（tts.py）←─ TA.2（piper_backend.py，依赖 T0.2 + TA.1）
  TA.1 + TA.2 ←─ TA.3（voice/__init__.py 导出）
  T0.2 ←─ TA.4（pyproject.toml）
  TA.1 + TA.2 ←─ TA.5（test_tts_service.py 骨架）
  TA.5 ←─ TA.6（AC-A1/A2/E1 测试）
  TA.2 + TA.5 ←─ TA.7（AC-C4/C5 测试）
  TA.5 + TA.6 + TA.7 ←─ TA.8（Phase A 验证）
  TA.8 ←─ TA.9（Phase A Codex review）

Phase B（依赖 Phase A 全完成）:
  TB.1（send_voice）
  TB.1 ←─ TB.2（AC-C1）
  TB.1 ←─ TB.3（AC-C2/C3）
  TB.2 + TB.3 ←─ TB.4（Phase B 验证）
  TB.4 ←─ TB.5（Phase B Codex review）

Phase C（依赖 Phase B 全完成）:
  TC.1（_get_voice_mode helpers）
  TC.1 ←─ TC.2（/voice 命令）
  TC.1 ←─ TC.3（_record_conversation_binding，最大风险 task）
  TC.2 + TC.3 ←─ TC.4（AC-D1/D1b 测试）
  TC.2 + TC.3 ←─ TC.5（AC-D2/D3 测试）
  TC.4 + TC.5 ←─ TC.6（Phase C 验证 + Codex review）

Phase D（依赖 Phase C 全完成 + TA.3）:
  TD.1（__init__ 新参数）
  TD.1 ←─ TD.2（_try_send_voice_reply）
  TD.2 ←─ TD.3（notify_task_result 插入 TTS 分支）
  TD.1 + TA.3 ←─ TD.4（octo_harness.py wiring）
  Phase C + → TD.5（FakeTtsService / FakeVoiceBotClient）
  TD.3 + TD.5 ←─ TD.6（AC-B1~B5）
  TD.3 + TD.5 ←─ TD.7（AC-D4/D5/D6/D7）
  TD.3 + TD.5 ←─ TD.8（AC-B6/E2/E3）
  TD.3 + TD.5 + TD.6 + TD.7 ←─ TD.9（AC-Z2 e2e）
  TD.6 + TD.7 + TD.8 + TD.9 ←─ TD.10（Phase D 验证）
  TD.10 ←─ TD.11（Phase D Codex review）

Phase E（依赖 Phase D 全完成）:
  TE.1（降级矩阵核查）
  TE.1 ←─ TE.2（全量回归）
  TE.2 ←─ TE.3（e2e_smoke）
  TE.1 ←─ TE.4（AC↔test 机械校验）
  TE.3 ←─ TE.5（living-docs 漂移闸）
  TE.4 ←─ TE.6（completion-report）
  TE.6 ←─ TE.7（handoff）
  TE.4 + TE.5 ←─ TE.8（Codex 双 panel #1）
  TE.8 ←─ TE.9（双 panel #2）
  TE.9 ←─ TE.10（等用户拍板，不 push）
```

**Phase 内并行机会**:
- Phase A: TA.1 / TA.4 可并行（文件不冲突）；TA.3 需等 TA.1+TA.2；TA.6 / TA.7 可并行（同测试文件，注意不要同时写避免 merge 冲突，建议串行）
- Phase D: TD.4（octo_harness）和 TD.5~TD.9（tests）可以并行于 TD.3 完成后立即启动；TD.6 / TD.7 / TD.8 涉及同一测试文件，建议按顺序串行避免冲突

---

## FR/AC 覆盖核对表

### FR → Task 映射（确保 100% FR 覆盖）

| FR | 描述摘要 | 主要覆盖 Task |
|----|---------|-------------|
| FR-A1 | TtsResult pydantic 字段 | TA.1 |
| FR-A2 | TtsBackend Protocol | TA.1 |
| FR-A3 | TextToSpeechService 永不抛异常 | TA.1 |
| FR-A4 | PiperTtsBackend lazy load + to_thread + 超时 | TA.2 |
| FR-A5 | piper-tts optional 依赖 pyproject.toml | TA.4 |
| FR-A6 | wav_to_ogg_opus 失败返回空 bytes | TA.2 |
| FR-A7 | TTS 配置走 env | TA.2 |
| FR-A8 | tts_service wiring（__init__ 参数 + harness 注入）| TD.1 / TD.4 |
| FR-B1 | notify_task_result 插入 TTS 分支 | TD.2 / TD.3 |
| FR-B2 | _build_result_text / _resolve_reply_target 零修改 | TD.3（约束） |
| FR-B3 | 仅 notify_task_result 加 TTS，控制命令回复不加 | TD.2 / TC.2 |
| FR-B4 | 降级链完整，Agent 回复不丢 | TD.2 |
| FR-B5 | notify_task_result 内异常不逃逸 | TD.2 / TD.3 |
| FR-C1 | send_voice 方法签名 | TB.1 |
| FR-C2 | multipart/form-data，不走 _request JSON POST | TB.1 |
| FR-C3 | send_voice 失败抛明确异常 | TB.1 |
| FR-D1 | _record_conversation_binding voice_mode 自动标记（三态）| TC.3 |
| FR-D2 | /voice on|off 控制命令 | TC.2 |
| FR-D3 | notify_task_result 读 voice_mode | TD.2 |
| FR-D4 | voice session 幂等性，binding 落 SQLite | TC.3 / TD.7 |
| FR-D5 | AgentSessionKind 不扩展 | TC.1（约束）/ TD.7 |
| FR-D6 | input_kind=voice 标注（SHOULD，LOW-1）| TD.3（附加）/ TC.3 |
| FR-E1 | TTS 结构化日志（成功含 backend/duration_ms/text_len）| TD.2 |
| FR-E2 | 降级结构化日志（含 reason 码）| TD.2 |
| FR-E3 | 异常不逃逸 notify_task_result | TD.2 |

### P1 AC → Task 映射（确保零 uncovered P1 AC）

| AC | 优先级 | 绑定测试函数 | 实现 Task | 测试 Task |
|----|--------|-------------|----------|----------|
| AC-A1 | P1 | test_tts_unavailable_when_lib_missing + test_piper_model_missing_is_unavailable | TA.2 | TA.6 |
| AC-A2 | P1 | test_synthesize_ok / _error_returns_false / _empty_audio_returns_false | TA.1 | TA.6 |
| AC-A3 | P1 | test_tts_result_schema | TA.1 | TA.5 |
| AC-A4 | P1 | test_piper_backend_protocol_conformance | TA.1/TA.2 | TA.5 |
| AC-B1 | P1 | test_notify_task_result_sends_voice_when_voice_mode | TD.2/TD.3 | TD.6 |
| AC-B2 | P1 | test_notify_task_result_sends_text_when_voice_mode_off | TD.2/TD.3 | TD.6 |
| AC-B3 | P1 | test_notify_task_result_degrades_to_text_on_tts_failure | TD.2/TD.3 | TD.6 |
| AC-B4 | P1 | test_notify_task_result_degrades_to_text_on_send_voice_failure | TD.2/TD.3 | TD.6 |
| AC-B5 | P1 | test_build_result_text_unchanged | TD.3（约束）| TD.6 |
| AC-C1 | P1 | test_bot_client_send_voice_multipart | TB.1 | TB.2 |
| AC-C2 | P1 | test_bot_client_send_voice_optional_params | TB.1 | TB.3 |
| AC-C3 | P1 | test_bot_client_send_voice_raises_on_failure | TB.1 | TB.3 |
| AC-C4 | P1 | test_wav_to_ogg_opus_produces_valid_ogg | TA.2 | TA.7 |
| AC-C5 | P1 | test_wav_to_ogg_opus_failure_handled | TA.2 | TA.7 |
| AC-D1 | P1 | test_voice_message_sets_voice_mode | TC.3 | TC.4 |
| AC-D1b | P1 | test_voice_off_then_voice_message_stays_off | TC.3（三态判断）| TC.4 |
| AC-D2 | P1 | test_voice_off_command_clears_voice_mode | TC.2 | TC.5 |
| AC-D3 | P1 | test_voice_on_command_sets_voice_mode | TC.2 | TC.5 |
| AC-D4 | P1 | test_voice_session_continuous_rounds | TC.3/TD.3 | TD.7 |
| AC-D5 | P1 | test_voice_session_idempotent_replay | TC.3/TD.3 | TD.7 |
| AC-D6 | P1 | test_voice_session_reuses_existing_agent_session | TC.1（约束）| TD.7 |
| AC-D7 | P1 | test_voice_mode_defaults_to_false_if_missing | TC.1/TD.2 | TD.7 |
| AC-E1 | P1 | test_tts_synthesis_observable | TA.1/TD.2 | TA.6 |
| AC-E2 | P1 | test_tts_degrade_observable | TD.2 | TD.8 |
| AC-Z1 | P1 | 全量回归 + e2e_smoke（非具名函数） | 全 Phase | TE.2/TE.3 |
| AC-Z2 | P1 | test_voice_roundtrip_e2e | TD.3/TC.3 | TD.9 |

**P2 AC（补充，不影响 P1 门控）**:
- AC-B6（tts_timeout）→ TD.8
- AC-E3（异常不逃逸）→ TD.8

**结论：所有 P1 AC（26 条）均有具名测试函数 + 实现 Task + 测试 Task 三方映射，零 orphan FR，零 uncovered P1 AC。**

---

## 触碰文件汇总

| 文件（相对 `octoagent/`） | 操作 | 覆盖 Phase | 涉及 Task |
|--------------------------|------|----------|---------|
| `apps/gateway/src/octoagent/gateway/voice/tts.py` | 新建 | A | TA.1 |
| `apps/gateway/src/octoagent/gateway/voice/piper_backend.py` | 新建 | A | TA.2 |
| `apps/gateway/src/octoagent/gateway/voice/__init__.py` | 修改 | A | TA.3 |
| `apps/gateway/pyproject.toml` | 修改 | A | TA.4 |
| `apps/gateway/tests/test_tts_service.py` | 新建 | A | TA.5/TA.6/TA.7 |
| `apps/gateway/src/octoagent/gateway/services/telegram_client.py` | 修改 | B | TB.1 |
| `apps/gateway/src/octoagent/gateway/services/telegram.py` | 修改（3 处）| C+D | TC.1~TC.3 / TD.1~TD.3 |
| `apps/gateway/src/octoagent/gateway/harness/octo_harness.py` | 修改 | D | TD.4 |
| `apps/gateway/tests/test_telegram_voice.py` | 扩展 | B+C+D | TB.2/TB.3 / TC.4/TC.5 / TD.5~TD.9 |

**明确不触碰**（硬不变量）:
- `packages/core/src/.../conversation_binding.py`（零 schema 变更）
- `packages/core/src/.../conversation_binding_store.py`（只调用现有 API）
- `packages/core/src/.../agent_context.py`（AgentSession 零修改，FR-D5）
- `apps/gateway/src/.../voice/stt.py` / `faster_whisper_backend.py`（F109，只读不改）
- `config_schema.py`（沿用 env 策略，不动 yaml schema）
