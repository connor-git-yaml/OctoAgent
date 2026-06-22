# F110 语音 v0.1 — Handoff to v0.2

- **Feature**: F110 voice-v01（STT+TTS+voice session，Telegram 仅入出站）
- **实施完成**: Phase A-E 主体（pending 双 panel review + 用户拍板 + push）
- **接力目标**: F110 v0.2 或 F111

---

## v0.2 必须做清单

### 1. Web 渠道 TTS 接入（outbound）
- `WebGatewayService.notify_task_result` 加 TTS 分支（对称 telegram.py Phase D）
- Web UI 需能播放 audio/ogg 音频 blob
- `ConversationBinding` 在 Web 渠道同样需要 `voice_mode` metadata 支持

### 2. Piper 模型下载 + 环境配置文档
- `octo setup` 或独立 CLI 命令下载 piper 模型（`zh_CN-huayan-medium.onnx`）
- `OCTOAGENT_TTS_VOICE_MODEL` / `OCTOAGENT_TTS_ENABLED` env 配置文档

### 3. PyAV libopus 可用性 — ✅ v0.1 已用 ephemeral venv 真验证
- F110 v0.1 的 `wav_to_ogg_opus` 依赖 PyAV 的 `libopus` codec。
- **已验证（2026-06-22，主节点 ephemeral venv 真 piper 冒烟）**：`av 17.1.0` + `libopus in codecs: True`；真 piper `synthesize_wav` → 合法 WAV（RIFF）→ `wav_to_ogg_opus` → 合法 OGG/Opus（`OggS` magic）端到端 PASS。
- CI 仍 `skipif`（dev/worktree venv 无 av，禁 uv sync）；`test_wav_to_ogg_opus_produces_valid_ogg` 在装了 `[voice]` extra 的环境自动跑。
- **v0.2 待办**：把这条真验证固化进 CI（专门的 voice-installed CI job 或 nightly），而非靠 ephemeral 手动跑。

### 4. tts_timeout 测试 — ✅ v0.1 已 hermetic 覆盖
- backend 层 `asyncio.wait_for` 超时机制：`test_tts_service.py::test_piper_backend_synthesize_times_out`（慢 `_synthesize_sync` + `OCTOAGENT_TTS_TIMEOUT_S=0.05` → `reason="tts_timeout"`）。
- 服务层「超时结果 → 文字降级」：`test_telegram_voice.py::test_notify_task_result_degrades_on_tts_timeout`（注入 `ok=False reason=tts_timeout` → 断言 send_message 文字降级 + 无 send_voice）。
- **v0.2 待办**：真 piper + 真慢合成的端到端超时（当前 hermetic 已覆盖语义，真延迟链留 voice-installed CI）。

### 5. voice session 连续性 — ✅ AC-D4 已 hermetic 覆盖，跨重启留 v0.2
- AC-D4 多轮连续性：`test_voice_session_continuous_rounds`（第 1 轮 voice → voice_mode=True → 第 2 轮**文字** update → notify_task_result 走 send_voice）已 PASS。
- AC-Z2 往返链：`test_voice_roundtrip_e2e` 已 PASS。
- **v0.2 待办**：voice_mode 跨 bot 重启持久验证（binding 落 SQLite 已有机制，需 e2e_live 覆盖）；F119 e2e_live 域可纳入。

### 6. Slack / Discord TTS 出站
- F105 v0.2 已有 Slack/Discord ChannelAdapter 基础
- 需要评估 Slack/Discord 语音消息 API（Slack 无 sendVoice，可能需要 audio/ogg 文件上传）

---

## F110 v0.1 地基供 v0.2 直接复用

### TtsBackend Protocol（`voice/tts.py`）
```python
@runtime_checkable
class TtsBackend(Protocol):
    name: str
    def is_available(self) -> bool: ...
    async def synthesize(self, text: str, *, language: str = "") -> TtsResult: ...
```
v0.2 可替换后端：ElevenLabs / OpenAI TTS / 其他本地引擎（只需实现 Protocol）。

### voice_mode ConversationBinding.metadata KV
- Key: `"voice_mode"` → `bool | None`（三态）
- 读写 API：`binding_store.get(...)` + `binding_store.upsert_runtime_binding(..., metadata=merged)`
- 扩展点：可加 `tts_backend`（str，per-chat 指定后端）/ `tts_language`（str，语言偏好）

### send_voice 路径（`telegram_client.py`）
- `TelegramBotClient.send_voice(chat_id, voice: bytes, ...)` → multipart/form-data sendVoice
- 完整降级链：`_try_send_voice_reply` → False → 原有 send_message

### WAV→OGG/Opus 编码（`piper_backend.py:wav_to_ogg_opus`）
- D4 实测结论：PyAV + libopus 路径（worktree venv 无 av，真实环境 faster-whisper 传递 PyAV 可用）
- 函数内 lazy `import av`（不崩启动）
- v0.2 若需 fallback：可检测 av 不可用时走 pydub/ffmpeg-python

---

## ConversationBinding.metadata 扩展建议

当前 v0.1 使用的 key：

| Key | Type | 语义 |
|-----|------|------|
| `voice_mode` | `bool` | True=语音输出 / False=显式关闭 / 缺失=默认文字 |
| `last_message_thread_id` | `str` | F105 topic 追踪 |
| `last_reply_thread_root_id` | `str` | F105 reply thread 追踪 |

v0.2 建议新增：

| Key | Type | 语义 |
|-----|------|------|
| `tts_backend` | `str` | per-chat 后端选择（"piper"\|"elevenlabs"），覆盖全局默认 |
| `tts_language` | `str` | per-chat 语言（"zh_CN"\|"en_US"等） |

---

## 已知 Limitations（传递给 v0.2）

1. **真 piper 路径 CI 固化**：v0.1 已用 ephemeral venv 真验证（synthesize_wav + libopus 编码端到端 PASS），但 CI 内 `test_wav_to_ogg_opus_produces_valid_ogg` 仍 `skipif`（dev venv 无 av，禁 uv sync）。v0.2 应建 voice-installed CI job 固化。
2. **超时线程不可强杀**（Python 限制）：`asyncio.wait_for` 超时后底层 `to_thread` 合成线程仍跑完；v0.1 已加 `OCTOAGENT_TTS_MAX_CONCURRENCY`（默认 2）semaphore 限并发 await，但孤儿线程清理是 Python 固有限制（单用户低风险，可接受）。
3. **voice session 跨重启持久验证**：AC-D4 多轮连续性已 hermetic 覆盖；voice_mode 跨 bot 重启（binding 落 SQLite）的 e2e_live 验证 deferred（F119 域）。
4. **notify_task_result 出站不去重**（DEFER FINDING-3）：与 F109 `send_message` 同属性（既有行为，非 F110 引入），同一 task notify 被调两次会发两条；归 v0.2 通知幂等域。
5. **`/voice on` 无 binding 时写空 scope_id**（DEFER L1）：下一条真实入站消息的 `_record_conversation_binding` RMW 自愈；出站寻址当前不依赖 binding.scope_id（走 `_resolve_reply_target` event metadata）。F105 v0.2 `resolve_outbound_route` 若按 scope_id 排序需复查。
6. **Web 渠道 voice_mode**：v0.1 仅 Telegram 实现，Web 渠道未接（v0.2 §1）。
7. **TTS 不接通知渠道**：`NotificationService.notify_task_state_change` 不走 TTS——正确设计（通知是 push 不是 reply）。

---

## 测试文件对应关系

| 测试文件 | 覆盖 |
|---------|------|
| `apps/gateway/tests/test_tts_service.py` | TTS 服务层 + FIX-1 API 签名锁 + backend 超时（Phase A + 双评审后补，13 test，含 1 skipif AC-C4） |
| `apps/gateway/tests/test_telegram_voice.py` | STT（F109）+ send_voice + voice_mode 三态 + TTS 出站 + AC-D4 多轮 + AC-Z2 往返 + AC-B6 超时降级（Phase B+C+D + 双评审后补）|

> 两文件合计实测 **43 passed / 1 skipped**（PYTHONPATH 锁 worktree）。skip = AC-C4 真编码（dev venv 无 av，已由 ephemeral venv 真 piper 冒烟旁证）。

---

## 架构不变量备忘

- **H1 不变量**：TTS 是 `notify_task_result` 的出站后处理，`agent_context.py` / AgentSession 零修改
- **Constitution #9**：`/voice on|off` 是确定性渠道开关，不经 LLM 决策
- **Constitution #6**：所有 TTS 失败路径降级到文字，不崩，不丢 Agent 回复
- **Constitution #5**：bot token 仅在 URL 路径，不记日志
