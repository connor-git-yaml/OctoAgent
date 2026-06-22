# F109 语音 PoC — 任务分解（tasks.md）

> 顺序 = 实施顺序。每任务标注 FR/AC 覆盖。GATE_TASKS：non-hard，按用户单会话偏好 auto-continue（不暂停），实施后双评审 + push 前 拍板。

## 批次 1 — STT 服务层（FR-A）
- **T1.1** 新建 `voice/stt.py`：`SttResult` / `SttBackend` Protocol / `SpeechToTextService`（捕获异常 + 判空归一）。→ FR-A1/A2/A3
- **T1.2** 新建 `voice/faster_whisper_backend.py`：`FasterWhisperBackend`（find_spec 探测 + 函数内 lazy import + 模型单例 + to_thread 转写 + env config）+ `build_default_stt_service`。→ FR-A3/A4/A5
- **T1.3** 新建 `voice/__init__.py` 导出。
- **T1.4** 新建 `tests/test_stt_service.py`（5 用例）→ AC-5/AC-6，自测绿。

## 批次 2 — Telegram voice 接入（FR-B/FR-C）
- **T2.1** `telegram_client.py`：`TelegramVoice` model + `TelegramMessage.voice` 字段 + `get_file` + `download_file_bytes`（独立 GET + size 守卫）。→ FR-C1/C2/C3/C4
- **T2.2** `telegram.py`：`TelegramBotClientProtocol` 加 get_file/download_file_bytes；`TelegramVoiceRef` dataclass；`TelegramInboundContext.voice` 字段。→ FR-B1
- **T2.3** `telegram.py`：`_extract_context` 检测 `message.voice`。→ FR-B1/AC-1
- **T2.4** `telegram.py`：`_ingest_update` 插入 voice 分支 + `_handle_voice_message`（幂等预检 / 可用性 / 守卫 / 下载 / 转写 / replace）+ `_reply_voice_degrade` + `__init__` 加 `stt_service`。→ FR-B2/B3/B4/B5/D1/D3/AC-2/AC-3/AC-4
- **T2.5** `octo_harness.py` wiring：注入 `stt_service=build_default_stt_service()`。
- **T2.6** `apps/gateway/pyproject.toml`：optional-dependencies `voice = ["faster-whisper>=1.0,<2.0"]`。→ FR-A4

## 批次 3 — telegram voice 测试 + 回归
- **T3.1** 新建 `tests/test_telegram_voice.py`（11 用例：extract/transcribed-enqueued/idempotent/5 降级/download/observable）→ AC-1..4/7/8/10。
- **T3.2** 全量回归（PYTHONPATH 锁 worktree）对比 baseline 0 regression → AC-9。
- **T3.3** `pytest -m e2e_smoke` 8/8 → AC-9。

## 批次 4 — 评审 + 收尾
- **T4.1** 双评审 panel（Codex GPT-5.4 high + 第二模型 Opus spec-对齐）→ 0 HIGH。→ spec §10 Gate
- **T4.2** completion-report.md + handoff.md（F110）+ living-docs（blueprint/milestones）。
- **T4.3** 归总报告 → 等用户 push 拍板（不主动 push）。
