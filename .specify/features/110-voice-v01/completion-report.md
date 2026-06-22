# F110 语音 v0.1 — Completion Report

- **Feature**: F110 voice-v01
- **基线 commit**: `1cd2083f`（F109 STT only 已合入）
- **完成 commit**: 待用户拍板后 push（当前在 worktree 分支 `F110-voice-v01`）
- **实施 Agent**: spec-driver:implement（Claude Sonnet 4.6）
- **报告日期**: 2026-06-22

---

## Phase 执行概览

| Phase | 计划 Task 数 | 实际完成 | 偏离 | 状态 |
|-------|-------------|---------|------|------|
| Phase 0（侦察） | 3 | 3 | 无 | ✅ |
| Phase A（TTS 服务层） | 9 | 9 | 见下 | ✅ |
| Phase B（send_voice） | 5 | 5 | 无 | ✅ |
| Phase C（voice_mode 状态机） | 6 | 6 | 无 | ✅ |
| Phase D（出站 TTS 接入） | 11（TD.11 review 主节点） | 11 | 见下 | ✅ 实施完成 |
| Phase E（观测/文档） | 5（review 主节点） | 5 | 见下 | ✅（pending review） |

---

## Phase 0 实测结论

- **N_baseline**: 主节点权威基线（identical 命令，1cd2083f 干净树）= **4341 passed / 1 failed / 13 skipped**。1 failed = F106 race `test_start_degrades_without_watchdog`（pre-existing flake，非 F110）。
- **D4 PyAV 实测**：`importlib.util.find_spec("av") is None` → **av 不可用**（worktree venv 无 PyAV）。
  决策：`wav_to_ogg_opus` 保留 lazy `import av`（引入时 ImportError），`is_available()` 同时检查 piper AND av，两者都需要才能完成合成→编码链。`test_wav_to_ogg_opus_produces_valid_ogg` 使用 `pytest.mark.skipif(not _AV_AVAILABLE, ...)` 跳过。
- **piper 安装态**：`importlib.util.find_spec("piper") is None` → **piper 未安装**（worktree venv 无 piper）。
  决策：所有测试使用 `FakeTtsBackend`，零真实 piper 依赖。`PiperTtsBackend.is_available()` 返回 False，优雅降级。

---

## Phase A 实施记录

**新建文件**：
- `apps/gateway/src/octoagent/gateway/voice/tts.py`（新建，~90 行）
  - `TtsResult` Pydantic / `TtsBackend` Protocol @runtime_checkable / `TextToSpeechService`
  - synthesize 永不抛，空音频归 `reason="empty_audio"`，异常归 `reason="synthesize_error"`
- `apps/gateway/src/octoagent/gateway/voice/piper_backend.py`（新建，~192 行）
  - `wav_to_ogg_opus` lazy `import av`（函数内）
  - `PiperTtsBackend` lazy load + `asyncio.to_thread` + `asyncio.wait_for(30s)` 超时
  - `build_default_tts_service()` 工厂函数
- `apps/gateway/tests/test_tts_service.py`（新建，12 test，1 skipif）

**修改文件**：
- `apps/gateway/src/octoagent/gateway/voice/__init__.py`：加入 TTS 5 个导出
- `apps/gateway/pyproject.toml`：`voice` extras 加 `piper-tts>=1.4,<2.0`

**spec/plan 偏离**：
- `wav_to_ogg_opus` 失败原来 spec 说"返回空 bytes"，实际实现为 raise（让调用方 `_synthesize_sync` 捕获，避免 silent fail，Constitution #3 更合规）。`test_wav_to_ogg_opus_failure_handled` 对应测试调整为 `pytest.raises(Exception)`。

---

## Phase B 实施记录

**修改文件**：
- `apps/gateway/src/octoagent/gateway/services/telegram_client.py`：新增 `send_voice` 方法
  - multipart/form-data POST（不走 `_request` JSON 路径）
  - 失败抛 `TelegramBotApiError`
  - 成功返回 `TelegramMessage.model_validate(data.get("result"))`

**新增测试**：test_telegram_voice.py 扩展 3 个 Phase B 测试（AC-C1/C2/C3）

---

## Phase C 实施记录

**修改文件**：`apps/gateway/src/octoagent/gateway/services/telegram.py`（3 处）

1. **`_get_voice_mode` + `_is_voice_mode_explicitly_disabled`** @staticmethod helper（GATE D2-C 三态区分）
2. **`_handle_control_command`**：新增 `/voice on|off` 检测（Constitution #9 确定性渠道开关，优先于 control_plane）
3. **`_handle_voice_command`**：read-modify-write 写 voice_mode + 发确认文字（FR-B3：确认回复不走 TTS）
4. **`_record_conversation_binding`** 重构：新增 `set_voice_mode_if_unset: bool = False` 参数，引入 read-modify-write 防全量替换（MEDIUM-3 最大风险，已妥善处理）

**关键设计决策**：
- `_handle_voice_command` 先 get 现有 binding，merge existing metadata，再 upsert（防清掉 last_message_thread_id 等字段）
- AC-D1b 边界：`_is_voice_mode_explicitly_disabled` 区分「key 缺失」vs「key=False（显式 False）」—— 显式 False 后入站 voice 不重开

**新增测试**：4 个 Phase C 测试（AC-D1/D1b/D2/D3）全 PASS

---

## Phase D 实施记录

**修改文件**：
- `telegram.py`（新增 `tts_service` 参数 + `_try_send_voice_reply` 方法 + `notify_task_result` TTS 分支）
- `octo_harness.py`（注入 `build_default_tts_service()`）

**关键实现细节**：
- `_try_send_voice_reply` 完整降级链：tts_service 可用 → 查 binding voice_mode → TTS 合成 → send_voice → 成功返回 True
- 任何一步失败 log + return False，调用方降级到原有 `send_message`
- `if await self._try_send_voice_reply(target, text): return` — 成功则不再发文字，保持互斥

**测试策略**：`_create_text_task_and_get_id` 辅助函数通过 `handle_webhook_update` 文字 update 建立 task + event，使 `_resolve_reply_target` 能找到 reply target。

**新增测试**：FakeTtsService + FakeVoiceBotClient.send_voice_calls + 6 个 Phase D TTS 测试全 PASS

---

## 降级矩阵 8 类覆盖核查

| 失败场景 | reason 码 | 覆盖测试 | 状态 |
|----------|----------|---------|------|
| piper-tts 未安装 / is_available()=False | tts_unavailable | test_tts_unavailable_when_lib_missing + test_tts_falls_back_to_text_when_tts_unavailable | ✅ |
| 模型文件缺失 | tts_unavailable | test_piper_model_missing_is_unavailable | ✅ |
| synthesize 抛异常 | synthesize_error | test_synthesize_error_returns_false + test_tts_falls_back_to_text_when_synthesize_fails | ✅ |
| 合成返回空音频 | empty_audio | test_synthesize_empty_audio_returns_false | ✅ |
| WAV→OGG 格式转换失败 | encode_error | test_wav_to_ogg_opus_failure_handled（raises 路径） | ✅ |
| send_voice Telegram API 失败 | send_voice_failed | test_tts_falls_back_to_text_when_send_voice_raises | ✅ |
| ConversationBinding 查不到 / voice_mode 缺失 | — | test_tts_falls_back_to_text_when_voice_mode_off（unset） + AC-D7 静默 | ✅ |
| TTS 合成超时 | tts_timeout | backend 机制 `test_piper_backend_synthesize_times_out`（慢 _synthesize_sync + TIMEOUT_S=0.05 → wait_for 超时）+ 服务层降级 `test_notify_task_result_degrades_on_tts_timeout`（注入 ok=False → send_message 文字降级 + 无 send_voice）| ✅（双评审 M1/AC-B6 补全，不再 deferred）|

**8 类降级全 hermetic 覆盖**（双评审后补全 tts_timeout）。

---

## 回归结果（0 regression，多轮证据）

> PYTHONPATH 锁 worktree（防假 0）；identical 命令。本会话另一 worktree 有 17 个 runaway F091 pytest 进程长期占 CPU，致 timing-flaky 测试（watchdog race）在重负载下更易 flake、全量重跑变慢——故最终以 **F110 blast radius（gateway+core）+ 基线对照**作权威判定，distant packages 未被 F110 触碰（无 import/schema 改动）。

| 运行 | 范围 | 结果 | 说明 |
|------|------|------|------|
| 基线（1cd2083f 干净树）| full | **4341 passed / 1 failed / 13 skipped** | 1 failed = F106 race `test_start_degrades_without_watchdog`（pre-existing）|
| 实现后（FIX 前）| full | **4365 passed / 1 failed / 14 skipped** | 唯一失败同 F106 race → 0 regression；+24 passed +1 skip(AC-C4) |
| 双评审修复后 | gateway+core（blast radius）| **2647 passed / 1 F106-race**（fix-agent 2646 + 主节点 backend 超时测试 +1）| 含全部 FIX-1~7 + 主节点 AC-B6 重写 |
| 两 voice 测试文件 | gateway | **43 passed / 1 skipped** | skip=AC-C4（dev venv 无 av）|

- **0 regression 判定**：baseline 与各 after 运行的**唯一失败始终是同一个 pre-existing F106 race**，无 F110 引入的新失败。
- **e2e_smoke**：8/8 PASS。
- **F110 净增测试**：TTS 服务层 + send_voice + voice_mode 三态 + 出站 TTS + AC-D4 多轮 + AC-Z2 往返 + AC-B6 超时 + FIX-1 签名锁。

---

## AC↔test 机械校验（P1 AC 完整覆盖）

**权威绑定表 = spec §5**（FIX-2 后以实际函数名为事实源重写，已逐条 `pytest -k <函数名>` 验证 21 条全选中且 PASS）。此处只记关键点 + 双评审后新增：

- **全部 P1 AC 有具名 test 且 PASS**（A1/A1扩展/A2×3/A3/A4 / B1~B6 / C1~C5 / D1/D1b/D2/D3/D4/D5 / E1 / Z1/Z2）。
- **双评审 M1 新增（不再 deferred）**：
  - AC-D4 多轮连续性 → `test_voice_session_continuous_rounds`（voice 轮设 voice_mode → 文字轮也走 send_voice）✅
  - AC-Z2 e2e 往返链 → `test_voice_roundtrip_e2e` ✅
  - AC-B6 超时降级 → `test_notify_task_result_degrades_on_tts_timeout`（服务层）+ `test_piper_backend_synthesize_times_out`（backend wait_for 机制）✅
  - FIX-1 API 签名锁 → `test_piper_backend_uses_synthesize_wav_not_synthesize` ✅
- **AC-C4**（真 WAV→OGG）：CI 内 `test_wav_to_ogg_opus_produces_valid_ogg` ⏭ `skipif`（dev venv 无 av）；**已由主节点 ephemeral venv 真 piper 冒烟旁证**（OggS magic 确认，见下节）。
- 两测试文件实测 **43 passed / 1 skipped**（skip=AC-C4）。

---

## H1 不变量验证（硬约束）

- `agent_context.py`：0 行修改（grep 验证）
- `AgentSession` / `AgentSessionKind`：0 修改
- decision loop：0 修改
- `voice_mode` 是 channel 层（`ConversationBinding.metadata`）状态，不入 Agent 上下文
- TTS 分支在 `notify_task_result`（H1 出站后处理），not in Agent 决策环
- `/voice on|off` 是确定性渠道渲染开关（Constitution #9），不经 LLM

---

## spec/plan 偏离汇总

| 偏离 | 原计划 | 实际 | 接受理由 |
|------|--------|------|---------|
| wav_to_ogg_opus 失败行为 | 返回空 bytes（FR-A6） | 抛 raise | Constitution #3 工具是契约，silent fail 更危险；调用方已有捕获 |
| FakeTtsService.synthesize 签名 | `synthesize(text, *, language)` | `synthesize(text)` | TextToSpeechService 包装层已去掉 language 参数，外部接口统一 |
| TD.7 完整连续性测试（AC-D4） | tasks.md TD.7 | 双评审 M1 已补 `test_voice_session_continuous_rounds`（AC-D4）+ `test_voice_roundtrip_e2e`（AC-Z2）| 不再 deferred，已 hermetic 覆盖 |

---

## 双评审 panel 结果（Codex + Opus，主节点裁决，2026-06-22）

命中"新能力 + 外部 GPL 依赖"节点，强制双 panel。两席独立审，主节点裁决分歧。

| Finding | 席 | 裁决 | 处置 |
|---------|-----|------|------|
| **H1 piper API 错用**（`synthesize(text,buf)` 应为 `synthesize_wav(text, wave.Wave_write)`，运行时必炸，被 Fake 屏蔽）| 两席 consensus HIGH（+ 主节点 web 复核坐实）| 确认 HIGH | **FIX-1**：改 `synthesize_wav` + `wave.open` 包装；补 `test_piper_backend_uses_synthesize_wav_not_synthesize`（monkeypatch 假 piper，断言调 synthesize_wav 且第二参数是 Wave_write，旧 synthesize 被调即 AssertionError）锁签名 |
| **H2 AC↔test 绑定名不符**（spec §5 的 P1 绑定函数名 13/15 与实际不符 → `pytest -k` 机械校验失效）| Opus HIGH / Codex 未报 → 人裁 | 裁 MUST-FIX（项目 SDD 硬规则）| **FIX-2**：以实际函数名为准重写 spec §5 绑定表；逐条 `pytest -k` 验证 21 条全选中且 PASS |
| **F2 首轮竞态**（voice_mode 写在 enqueue 之后，worker 快时首轮误回文字）| Codex MEDIUM | 接受 | **FIX-3**：voice_mode 自动标记前移到 `_handle_voice_message`（enqueue 之前），消除竞态；`_record_conversation_binding` 去掉 voice_mode 逻辑保留 last_* RMW |
| **M1 测试缺口**（AC-D4 多轮/AC-B6 超时/AC-Z2 e2e 无测试）| 两席 consensus MEDIUM | 接受 | **FIX-4**：补 `test_voice_session_continuous_rounds`（AC-D4）/`test_voice_roundtrip_e2e`（AC-Z2）/`test_notify_task_result_degrades_on_tts_timeout`（AC-B6 服务层）+ 主节点再补 `test_piper_backend_synthesize_times_out`（AC-B6 backend wait_for 机制） |
| **F4 超时线程泄漏**（wait_for 超时后 to_thread 线程仍跑）| 两席 MEDIUM | 接受（缓解）| **FIX-5**：加 `OCTOAGENT_TTS_MAX_CONCURRENCY`（默认 2）semaphore 限并发 await；孤儿线程清理是 Python 固有限制（单用户低风险，记 limitation） |
| **F6 Protocol 缺 send_voice** | Codex LOW | 接受 | **FIX-6**：`TelegramBotClientProtocol` 补 `send_voice` 声明 |
| **L2 reason 码不一致**（`lib_missing` 从不产出）| Opus LOW | 接受 | **FIX-7**：删 `tts.py` 中 never-produced 的 `lib_missing` 注释 |
| **F3 notify 重入重复发 voice** | Codex MED / Opus 判既有行为 → 人裁 | **DEFER**（F109 `send_message` 同属性，非 F110 引入）| 注释 + handoff 归档 v0.2 通知幂等域 |
| **L1 `/voice on` 无 binding 写空 scope_id** | Opus LOW | **DEFER**（下条消息 RMW 自愈，出站不依赖 binding.scope_id）| 注释 + handoff 归档 |

**主节点再审（修复后，按 F098/F099"大 fix 后必 re-review"先例）**：抓出 **AC-B6 测试空洞**（原用外层 `wait_for(0.5)` 测取消语义而非降级，且未断言文字 fallback）→ 重写为注入 `ok=False reason=tts_timeout` + 断言 send_message 文字降级 + 无 send_voice。FIX-3 relocation / semaphore / Protocol 复核无新问题。

**结论：0 HIGH 残留**（H1/H2 修复 + 主节点 web 复核 + 真 piper 冒烟坐实）。

---

## 真 piper 路径冒烟验证（主节点，闭合双评审 #1 盲区）

两席都强调"真实 piper 环境必须真跑一次"。主节点在 **ephemeral venv**（隔离于项目，`/tmp`，禁污染）真装 `piper-tts 1.4.2 + av 17.1.0 + onnxruntime 1.27.0` + 下载 `en_US-lessac-low` 模型，跑端到端冒烟（脚本 `/tmp/f110_piper_smoke.py`）：

```
av version: 17.1.0
libopus in codecs: True
WAV bytes: 82476  RIFF header: True       ← H1 修复的 synthesize_wav 产出合法 WAV
OGG bytes: 30417  OggS magic: True        ← wav_to_ogg_opus 产出合法 OGG/Opus
SMOKE_RESULT: PASS
```

**意义**：H1 修复（`synthesize_wav`）+ AC-C4 编码链（PyAV libopus）**已对真实库端到端坐实**，不止"文档对齐"。这是双评审点名的头号盲区，现已闭合。（CI 内 `test_wav_to_ogg_opus_produces_valid_ogg` 仍 `skipif`——dev venv 无 av 且禁 uv sync；v0.2 建 voice-installed CI job 固化。）

---

## Known Limitations

- **真 piper 路径 CI 固化**：v0.1 已 ephemeral venv 真验证；CI 内 AC-C4 仍 `skipif`（dev venv 无 av，禁 uv sync）。v0.2 建 voice-installed CI job。
- **超时孤儿线程**（Python 限制）：`wait_for` 超时后 `to_thread` 合成线程跑完才退；semaphore 限并发 await 已缓解，强杀不可（单用户低风险）。
- **F106 race**：`test_start_degrades_without_watchdog` 已知 flake（非 F110 引入，baseline 1cd2083f 同样失败；本会话受另一 worktree 17 个 runaway F091 pytest 进程 CPU 争用加剧）。
- **voice_mode 跨重启 e2e_live**：AC-D4 多轮连续性已 hermetic 覆盖；跨 bot 重启持久验证 deferred（F119 e2e_live 域）。
- **DEFER FINDING-3 / L1**：notify 出站不去重（既有行为）/ `/voice on` 空 scope_id（自愈）——见 handoff。
- **Web/Slack/Discord 出站 TTS**：v0.1 仅 Telegram，其他渠道 v0.2。

---

## 触碰文件汇总

| 文件（相对 `octoagent/`） | 操作 | Phase |
|--------------------------|------|-------|
| `apps/gateway/src/octoagent/gateway/voice/tts.py` | 新建（~90 行）| A |
| `apps/gateway/src/octoagent/gateway/voice/piper_backend.py` | 新建（~192 行）| A |
| `apps/gateway/src/octoagent/gateway/voice/__init__.py` | 修改 | A |
| `apps/gateway/pyproject.toml` | 修改 | A |
| `apps/gateway/tests/test_tts_service.py` | 新建（12 test）| A |
| `apps/gateway/src/octoagent/gateway/services/telegram_client.py` | 修改（+58 行 send_voice）| B |
| `apps/gateway/src/octoagent/gateway/services/telegram.py` | 修改（Phase C+D 多处）| C+D |
| `apps/gateway/src/octoagent/gateway/harness/octo_harness.py` | 修改（+2 行 tts wiring）| D |
| `apps/gateway/tests/test_telegram_voice.py` | 扩展（+26 test）| B+C+D |
| `docs/blueprint/milestones.md` | 修改（F110 状态更新）| E |

**明确未触碰**：
- `agent_context.py` / AgentSession / AgentSessionKind（H1 硬约束 ✅）
- `conversation_binding.py` / `conversation_binding_store.py`（零 schema 变更 ✅）
- `voice/stt.py` / `faster_whisper_backend.py`（F109，只读参考 ✅）
