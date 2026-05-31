# F103d Phase E 第 2 步 — M5 baseline 真跑报告

> 执行：2026-05-31，由主 session 在 host 上接管执行（用户授权"自己执行"）。
> 控变量 LLM：DeepSeek-V3.2（SiliconFlow，alias=bench；main+cheap 重写到 bench）。
> 数据文件：`/tmp/octobench_s2_out/m5-baseline-tier13-s2-20260531T104858.{json,md}`，
> SQLite `/tmp/octobench_s2.db`（label=m5-baseline-tier13-s2，id=bsl-8b53a38bc8ad4ebd）。

## 1. 执行摘要

**runner 端到端真跑成功**：`benchmarks/runner/octo_runner.py:runner_fn` 起真实 OctoHarness
→ 真 DeepSeek LLM → scorer → SQLite，30 task 全部产出 verdict。Phase E 第 1 步交付的
runner 在 host 上验证可用。

**规模**：Tier 1（25）+ Tier 3（5）= 30 task × 1 iteration，semaphore=2，wall clock 1328s（22.1min）。
（**未跑** Tier 2 τ-bench/GAIA：需额外装 tau-bench/datasets；**未跑** 3-iteration：见 §6 性能。）

## 2. M5 baseline 数据（DeepSeek-V3.2 / Tier1+3 / 1-iter）

| 结果 | 数量 | 占比（分母=29，TIMEOUT 不计 AC3-4） |
|------|------|------|
| PASS | 8 | 27.6% |
| PARTIAL | 5 | 17.2% |
| FAIL | 16 | 55.2% |
| TIMEOUT | 1 | （SKILL-001 卡 300s） |

**强能力域（PASS）**：TOOL-CALL ×3 全过 / USER-MD-001 / SNAPSHOT-002 / CONNOR-2,3,4（真实场景 3/4）。
**弱能力域（全 FAIL）**：DELEGATION ×4 / THREAT-SCANNER ×2 / **Tier3 哲学 ×5（H1/H2/H3-WW/H3A/H3B）**。
**单 task 耗时**：11s（THREAT-SCANNER-001）~ 300s（SKILL-001 TIMEOUT），中位 ~70s。

## 3. RCA：委托类 task 全 FAIL 根因（baseline 有效性确认）

Tier3 + DELEGATION 全 FAIL，必须排除"scorer bug / 环境缺工具"才能确认 baseline 有效。

**证据链**：
1. **scorer 正常**：同一 scorer 下 Tier1 基础 task（TOOL-CALL/USER-MD/SNAPSHOT/CONNOR）正常 PASS
   → `event_store_assert` 工作正常，不是全局 scorer bug。
2. **委托工具完整暴露**：dump tmp instance tool registry = 63 工具，含完整 8 个委托工具
   （`delegate_task` / `subagents.spawn/list/kill/steer` / `worker.ask_back/escalate_permission/
   request_input`）→ 不是环境缺工具。
3. **委托 events not_found**：Tier3 FAIL 全部 `SUBAGENT_SPAWNED event_not_found` /
   `CONTROL_METADATA_UPDATED event_not_found` → 委托动作根本没发生。

**结论**：DeepSeek-V3.2 控变量**工具可见但不主动委托**——倾向自己直接处理，不调 delegate_task。
这是**真实 model 行为画像**，baseline 有效。pass rate 27.6% 偏低是真实的（30 task 里 11 个
= 37% 是委托/安全类，DeepSeek 在这些域接近 0）。

**对 benchmark 设计的洞察**：Tier3 哲学 task 全依赖委托/audit chain，对"不委托"的弱 model 有
**地板效应**（全 0，区分度差）。M6 若改进委托引导（prompt/工具描述/decision loop），用同
model 同 task 应能看到 Tier3 pass 从 0 抬升——这正是控变量纵向对比的价值。

## 4. 真跑修复的 3 个配置坑（已写入 README_BENCH_ALIAS.md）

| 坑 | 根因 | 处理 | 侵入 production？ |
|----|------|------|------|
| `octo-bench` command not found | entry point 脚本 shebang 写死创建时的 worktree venv 路径 | 改用 `python -m benchmarks.runner.cli` | 否 |
| 所有 LLM 调用 404 Not Found | octoagent.yaml `api_base` 含 `/v1` + provider_client.py:715 chat 路径硬加 `/v1` → double `/v1/v1/` | instance 配置 api_base 去 `/v1` | 否（instance 配置） |
| semaphore=8 资源爆炸（5min 0 done） | 每 task 起完整 OctoHarness（含 watchdog/daily routine/observation/MCP/capability 全套后台服务），8 套并发竞争 CPU/IO/SQLite + DeepSeek 并发限流 | 降到 semaphore=2 | 否（运行参数） |

## 5. 发现的 3 个 production 既有缺陷（benchmark 首次大规模真跑 siliconflow chat 才暴露）

1. **provider_client `/v1` 拼接不一致**（`octoagent/packages/provider/.../provider_client.py`）：
   `_build_responses_url`(L152) 智能判断 api_base 是否已含 /v1，但 `chat`(L715) /
   `embeddings`(L859) / `messages`(L1075) 都硬编码 `f"{api_base}/v1/..."`。配 api_base 含
   /v1 时 chat 路径 double /v1 → 404。**根治**：让 chat/embeddings/messages 复用
   `_build_responses_url` 的智能 /v1 逻辑。
2. **watchdog RepeatedFailureDetector datetime bug**：多 task 并发时刷
   `watchdog_detector_error "can't compare offset-naive and offset-aware datetimes"`（TypeError）。
   不影响 verdict，但污染日志。
3. **session_memory_extraction shutdown race**：task 完成后 harness shutdown 时
   memory extraction LLM 调用偶发 `no active connection`（DB 已关）。不影响 verdict。

> 3 项均为 production 既有缺陷（非 benchmark runner bug），建议独立 spawn task / Feature 修复。

## 6. 性能发现：runner 不达标 SC-001（≤ 1h），正式全量前需优化

- **每 task 起完整 OctoHarness 太重**：含 watchdog / daily routine / observation routine /
  MCP installer / capability pack refresh 全套后台服务。8 并发 = 8 套后台服务竞争 → 卡死。
- semaphore=2 下 Tier1+3（30 task）单 iteration = 22min。推算全量：
  - Tier1+3 × 3 iter ≈ 66min（已超 SC-001 1h）
  - 全 50 task × 3 iter ≈ 90-120min（远超）
- **正式 M5 baseline（3-iter + 全 50 task）跑通 ≤ 1h 之前，runner 需性能优化**，候选方向：
  - OctoHarness 轻量 bootstrap 模式（benchmark 不需要 watchdog/daily routine/observation 等后台服务）
  - 或受控复用 harness（牺牲部分隔离换吞吐——但违反每 task 独立 data_dir 隔离原则，需权衡）
  - 提高 semaphore 但先解决后台服务竞争

## 7. instance 配置改动（不在版本管理，需告知用户）

改动 `~/.octoagent/octoagent.yaml`（备份 `octoagent.yaml.bak-pre-bench-20260531-174434`）：
1. **新增 bench alias**：`siliconflow / deepseek-ai/DeepSeek-V3.2`（控变量）。
2. **siliconflow api_base 去 /v1**：`https://api.siliconflow.cn/v1` → `https://api.siliconflow.cn`
   （**修复** double /v1 bug；此前 siliconflow chat 从未成功调用过，因 cheap 的
   `Qwen/Qwen3.5-14B` 也已不存在）。

> ⚠️ **SILICONFLOW_API_KEY 在本次对话中明文出现过，benchmark 已跑完，务必去 SiliconFlow
> 控制台轮换该 key**（CLAUDE.local.md 安全规则）。

## 8. 给用户的决策点

1. **是否接受这次为初步 M5 baseline**：runner 验证 + 能力画像已产出（DeepSeek 强基础工具/
   弱委托）。数据在 /tmp（可 copy 到 `benchmarks/baselines/` 作 preliminary 锚点）。
2. **是否正式跑 3-iter + 全 50 task**：需先做 §6 runner 性能优化（否则超 1h）。建议作为
   独立 Feature（runner 轻量 bootstrap）。
3. **3 个 production 缺陷**：是否各开 spawn task 修（provider_client /v1 优先级最高，影响所有
   siliconflow 调用）。

## 9. commit 方案（等用户拍板，不主动 push）

- `benchmarks/README_BENCH_ALIAS.md`（已改：修正 3 坑 + 记录 3 production 缺陷）
- `.specify/features/103d-octobench/phase-e-step2-real-run-report.md`（本文件）
- 可选：copy /tmp baseline json/md 到 `benchmarks/baselines/preliminary-m5-tier13-1iter.{json,md}`
- **不 commit**：/tmp SQLite（临时）；`~/.octoagent/octoagent.yaml`（instance 配置不在 repo）
- octo_runner.py / scorer.py / score_dispatch.py **本次零改动**（Phase E 第 1 步已就位，真跑直接可用）

## 10. 用户决策（2026-05-31 拍板）

1. **baseline 数据**：copy 进 repo 作 preliminary 锚点 →
   `benchmarks/baselines/preliminary-m5-tier13-1iter-deepseek.{json,md}`
2. **commit + push master**：README 修正 + 本报告 + preliminary 数据（fast-forward）。
3. **正式 M5 baseline（3-iter + 全 50 task ≤ 1h）**：**开独立 Feature 做 OctoHarness 轻量
   bootstrap**（benchmark 模式跳过 watchdog / daily routine / observation 等后台服务，
   解决 §6 性能不达标 SC-001）后再正式跑。
4. **production 缺陷**：**spawn task 修 provider_client `/v1` 拼接不一致**（最高优先，影响所有
   siliconflow 调用）；watchdog datetime bug + session_memory race 在同 task 内一并评估。
