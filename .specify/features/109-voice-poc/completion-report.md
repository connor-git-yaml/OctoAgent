# F109 语音 PoC — 完成报告(completion-report.md)

- **Feature**: F109 语音 PoC(STT only,单向语音输入 → text)
- **分支**: `feature/109-voice-poc` / worktree `F109-voice`
- **基线**: master d6f0ec54(回归基线);origin/master 期间前进到 3208f728(合入需 rebase,见 §6)
- **状态**: 实现完成 + 双评审 0 HIGH;**未 push,等用户拍板**

> 散文中文 / 代码标识符英文 / 英文术语保原文。

---

## 1. 做了什么(用户视角)

用户现在可以**给 Telegram bot 发一条语音消息**,系统把语音转成文字后,**像普通文字消息一样**交给主 Agent 处理并正常回复。语音转写**全本地**(faster-whisper),音频不出设备(隐私)。语音转写不可用时(没装库/转写失败等)给清楚的文字提示,不会石沉大海也不会崩。

**哲学 H1 守住**:语音只是"入站预处理"——转成文字后走与文字消息**完全相同**的 chat 主路径,没有新增 Agent 模式、没碰决策环。

---

## 2. 决策点闭环

| 决策 | 结果 |
|------|------|
| **D1 STT 后端选型**(GATE_DESIGN 硬门禁回用户) | 用户拍板 **本地 faster-whisper**(隐私 + 零成本 + 干净安装,贴合单用户隐私导向)。STT 后端做成可替换薄抽象 `SttBackend`,API 后端留缝不实现。 |

---

## 3. 计划 vs 实际(Phase/批次)

| 批次 | 计划 | 实际 |
|------|------|------|
| 块 A 调研 | STT 选型 + 接入点侦察 | ✅ tech-research.md(Web 调研 4 候选 + F105 telegram inbound 代码侦察 file:line) |
| spec + GATE_DESIGN | spec + STT 决策回用户 | ✅ spec.md(10 AC + AC↔test 绑定)+ 用户拍板本地 |
| 批次 1 STT 服务层 | stt.py + faster_whisper_backend + 单测 | ✅ `voice/{stt,faster_whisper_backend,__init__}.py` + test_stt_service.py(8 用例) |
| 批次 2 telegram 接入 | client 下载 + telegram voice 分支 + wiring + dep | ✅ telegram_client(get_file/download 流式)+ telegram(voice 分支/降级)+ octo_harness wiring + pyproject optional dep |
| 批次 3 测试 + 回归 | telegram voice 测试 + 0 regression + e2e_smoke | ✅ test_telegram_voice.py(14 用例)+ 2095 passed 0 regression + e2e_smoke 8/8 |
| 批次 4 评审 + 收尾 | 双评审 0 HIGH + 文档 | ✅ Codex + Opus 双评审 0 HIGH;本报告 + handoff + living-docs |

**无 Phase 跳过。**

---

## 4. 改动清单

**新增**(`apps/gateway/`):
- `src/octoagent/gateway/voice/__init__.py` / `stt.py` / `faster_whisper_backend.py`(STT 服务层 + 本地后端)
- `tests/test_stt_service.py`(8)/ `tests/test_telegram_voice.py`(14)

**修改**(`apps/gateway/`):
- `src/octoagent/gateway/services/telegram.py`(+~190):`_extract_voice_ref` / `_handle_voice_message` / `_reply_voice_degrade` + `_ingest_update` voice 分支 + `TelegramVoiceRef` + `TelegramInboundContext.voice` + `__init__` stt_service + 降级文案常量
- `src/octoagent/gateway/services/telegram_client.py`(+~70):`TelegramVoice` model + `TelegramMessage.voice` 字段 + `get_file` + `download_file_bytes`(流式超限即断)
- `src/octoagent/gateway/harness/octo_harness.py`(+5):wiring 注入 `build_default_stt_service()`
- `pyproject.toml`(+6):`[project.optional-dependencies].voice = ["faster-whisper>=1.0,<2.0"]`

净增约 +1000 行(含测试)。**0 行改动溢出 `apps/gateway` 之外**(core/provider 等零触碰)。

---

## 5. 验收(AC + 不变量)

- **AC-1..AC-10 全部有测试且断言到位**(AC↔test 绑定见 spec §5)。22 个 F109 测试全 PASS。
- **AC-9 硬不变量**:全量 gateway 回归 **2095 passed / 0 regression** vs d6f0ec54 基线(baseline 2073 + 22 新测试)。唯一 1 failed = `test_plugin_watcher.py::test_start_degrades_without_watchdog`,**与 F109 无关**——它因本机 venv 装了 watchdog(测试预期未装)而确定性失败,是 F106/symlink-venv 环境敏感 flake,master baseline 同样失败(已逐一核对 F109 未触碰任何 plugin/watcher 文件)。
- `pytest -m e2e_smoke` **8/8 PASS**(pre-commit 硬门)。
- 新增文件 ruff 干净(项目 select E/F/W/I/UP/B/SIM);唯一残留 `telegram_client.py:3` I001 是 **master 既有债**(非本 Feature 引入,scope 纪律未碰)。

---

## 6. 双评审 panel 闭环(Codex + Opus,0 HIGH)

> 新能力 + 引入外部依赖 → 命中"重大架构变更/外部依赖"节点,双评审。两轮独立评审均判 **0 HIGH 可合入**。

| Finding | 提出方 | severity | 处理 |
|---------|--------|----------|------|
| 下载内存守卫名不副实(整包读后才 len 检查) | Opus | MED | ✅ **改流式** `client.stream` + 累计字节超限即断(`download_file_bytes`);新增 L1 早停证明测试 |
| 并发重投绕过转写前幂等预检 → 重复下载/STT | **Codex** | MED | ⚖️ **接受(带原因)**:最终 outcome 已正确(create_task 去重,无双 task/双 enqueue),仅"省 STT CPU"目标在**并发同 update**下失效;单用户 Telegram 投递是**顺序**的(webhook 重试串行 / polling 串行),该窗口现实不触发;per-key asyncio.Lock 需引入**无界 lock 字典 + 清理**,违 PoC 最小化。**列为 F110 并发硬化项**(voice session 引入真并发时处理)。**Opus 同区判可接受 → 此为 panel 分歧,已人裁如上**。 |
| polling 路径无测试覆盖(raw-dict 测试遮蔽 voice 字段回归) | **Codex** | MED | ✅ **补测** `test_voice_survives_polling_model_roundtrip`(经 `TelegramUpdate.model_validate→model_dump` 验证 voice 存活) |
| size_guard 测试未证明"流式早停"(旧整包实现也会过) | Codex | LOW | ✅ **强化** 用 `_CountingByteStream` 断言第 3 chunk 未被拉取 |
| does_not_load_model 测试只查 backend_name | Codex | LOW | ✅ **强化** 断言 `backend._model is None`(证明懒加载未实例化) |
| SttResult reason 注释列了 service 不产出的码 | Opus | LOW | ✅ 注释澄清(`lib_missing`/`download_error` 由调用方层处理) |
| `_voice_update(with_text=)` 死参数 | Opus | LOW | ✅ 删除(Telegram voice 无 caption,边界不真实) |
| AC-7/AC-10 绑定命名 drift(verify grep 会误报 orphan) | Opus | LOW | ✅ spec §5 绑定改为实际测试名 |

**评审过程注**:首轮 Codex 走 background 检索受阻,改 foreground 重跑取回 findings;Opus 走 general-purpose Agent(Opus 视角)spec 对齐专项。两轮独立评审 **0 HIGH 残留**。

---

## 7. 合入须知(rebase)

- F109 基于 d6f0ec54;origin/master 已前进到 **3208f728**(改了 `test_f105v02_ingress.py` / `test_telegram_service.py`)。
- **冲突评估**:F109 改 `telegram.py` / `telegram_client.py` / `octo_harness.py` + 新增文件,**与 3208f728 改的 2 个测试文件不重叠** → rebase 预期零冲突。合入前 `git rebase origin/master` 并重跑全量回归 + e2e_smoke 确认。

---

## 8. 已知 limitations / living-docs 漂移

- **并发幂等窗口**(Codex M1,已接受):见 §6,F110 并发硬化项。
- **STT 配置走 env 未进 octoagent.yaml schema**:PoC 纪律,F110 promote(handoff §3.2)。
- **faster-whisper 真实启用需手动安装 + 下模型**:`uv pip install -e '.[voice]'`;未装则优雅降级。本会话测试零依赖真库(全 Fake backend + find_spec 天然 False)。
- **living-docs 同步**:已更新 `docs/blueprint/milestones.md`(F109 ✅)+ `docs/blueprint.md`(M6 行)+ `docs/codebase-architecture/platform-gateway.md`(voice 入站预处理小节)。无残留 drift。

---

## 9. 给 F110 的接力

见 `handoff.md`(STT 抽象复用 / H1 入站预处理范式 / TTS 出站对称设计 / 媒体下载能力 / 并发幂等硬化)。
