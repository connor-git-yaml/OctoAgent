# F142 确定性护栏补齐 — completion report

> 分支 `feature/142-deterministic-guards`（worktree `F142-guards`），基线
> origin/master `6972ddc7`。**未 push**，等用户拍板。
> Baseline 实测（PYTHONPATH 锁 worktree 串行）：4975 passed / 14 skipped /
> 1 xfailed / 1 xpassed（378s）。

## 1. 五件交付 vs 计划

| 件 | 计划（spec） | 实际交付 | 偏离 |
|----|--------------|----------|------|
| 1 库语义钉住 | anyio/httpx 真 TLS + APScheduler 真 CronTrigger + piper importorskip + aiosqlite 略过 | `octoagent/tests/lib_semantics/` 4 文件：TLS server（cryptography ephemeral 证书，零外网零新依赖）两中断面确定性复现——RST 面实证 `httpx.ReadError('')` **与 bench 事故空 message 签名一致**、incomplete-close 面实证 `RemoteProtocolError`，断言 family 成员资格 + 事故签名单独一格 + ProviderClient 真栈重试恢复端到端（conn 计数=3）；APScheduler 5 测试（数字 0→周一 off-by-one 本体 / 命名全周对齐 / sun≡6）；piper 3 测试（load + synthesize_wav 剔 self 业务参数 [text, wav_file]，Codex spec P3 姿势）；`__init__.py` 归档 aiosqlite 略过理由 | 无 |
| 2 prompt 预算护栏 | F138 scripted harness 捕获脚本脑 conversation_messages → cap 实测收口 + 短语 + 负向 | `test_prompt_budget_guard.py` 5 测试：真 bootstrap + `POST /api/message` 真 chat 主路径；**捕获点改 `llm_service.call` 入参**（同一份 `compiled_context.messages`，见偏离①）；system 面实测 8938 tokens → cap 10300（+15.2%）/ 工具 schema 面 68 工具 11253 → cap 13000（+15.5%），tokenizer（tiktoken 0.12 cl100k，已锁 uv.lock 本地/CI 一致）校准记录进 docstring；关键短语 ×5（AGENTS 协作/治理、TOOLS 指南、AmbientRuntime、MemoryRuntime 各守一注入层）；负向扫描组装产物+模板库两半边（5 退役标记） | ①捕获点（归档见 §3）②`.env.litellm` 发现为存活文件名化石不能作扫描标记（§4） |
| 3 wire 边界用例族 | malformed JSON ×3 + 粘包/半包 + 行长上限先评估 | `test_provider_client_wire_boundaries.py` 13 用例，httpx.MockTransport + **真 AsyncClient 真 LineDecoder**：malformed JSON `continue` 分支 ×3 transport 首次覆盖；粘包/半包族（全事件一 chunk / `data: ` 前缀切断 / CJK 多字节切断 / tool_call arguments 5 片怪异切断 / \r\n+空 chunk / responses+anthropic 各一条字节级）；**U+2028 行为钉住**（真发现，§4）；2MB 单行现状钉住。行缓冲上限评估结论=**不动生产**（LineDecoder.buffer 无上限属实，但威胁模型=用户显式配置的可信端点 + 修复需弃 aiter_lines 自管 SSE framing 非极小改动，不满足预许可例外） | 无（评估按承诺交付，结论归档） |
| 4 dirty-equals | dev-dep + 2-3 样例 + inline-snapshot 评估可 defer | dirty-equals 0.11（pyproject dev + uv lock + 共享 venv 手动 install 收敛）；3 范式样例：us4 STATE_TRANSITION full-shape+IsStr / control_plane worker_profile.create data 契约 IsStr+IsPartialDict / policy ApprovalOverride IsNow(delta=10) 治时间窗 + model_dump full-shape；importorskip 收窄到样例段（函数级、置于既有断言后——venv 未装窗口只 SKIP 增强段，Codex spec P2 闭环）。**inline-snapshot defer**：需 ①ruff 写回流程 ②xdist 惰性 stub（pydantic-ai `tests/_inline_snapshot.py` 前例）③fix/review 团队约定，三件配套均超本 Feature，F141 lane 稳定后独立评估 | 无 |
| 5 xdist + 欠账 | 分组 + 3 轮全绿翻 CI + 4 欠账处置 | 18 文件 11 组（清单见 §2）；`-n auto --dist=loadgroup`（CI scope）**3 轮全绿** 4894 passed（25.8/27.2/25.8s，串行 378s → ~14x）；**CI backend job 已翻并行**（只动 run 参数区块+注释）；顺手治两类真问题（§4）。4 欠账处置见 §2 | 无 |

## 2. 关键清单

### 4 个 F137 CI-skip 欠账处置表

| 欠账 | 处置 | 方式 |
|------|------|------|
| `test_f131_outbound_spool.py::test_startup_drains_spool` | **治愈，skipif 已移除** | `sleep(0.05)` 赌后台 drain 首轮 → 条件轮询 `ok_bot.sent`（10ms 间隔 / 5s deadline） |
| `test_f009_worker_runtime_flow.py::test_timeout_path` | **治愈，skipif 已移除** | `sleep(0.4)` 赌 watchdog 链 → 轮询任务至终态（10s deadline）再断言；顺手清 `import os` 死引用 |
| `test_finalize_result_offload.py::test_eventloop_not_blocked` | **永久 CI 豁免**（注释升级） | 130ms 阈值按开发机单核校准（卸载 ~54ms vs 同步 200-325ms），共享 runner 裕量被环境噪声吞；卸载机制已有确定性伴测 `TestScanOffloadedToThread`（CI 照跑） |
| `test_threat_scanner_boundary.py::test_long_content_scan_under_1ms` | **永久 CI 豁免**（注释升级） | FR-3 1ms 均值按开发机校准，5000 字符是同族机器敏感度最高一格（F137 首跑实证 rerun 救不了）；短文本/恶意短路两兄弟基准 CI 稳绿续跑 |

**治愈 2 / 永久豁免 2**（诚实豁免优于假治理，两豁免条均有确定性伴测在 CI 覆盖机制本身）。

### xdist_group 分组清单（18 文件 11 组）

| 组 | 文件 |
|----|------|
| task_runner_state_machine | test_task_runner.py |
| integration_timing | test_f009_worker_runtime_flow / test_f013_watchdog / test_sc1_e2e / test_sc8_llm_echo |
| notification_timing | test_f101_phase_b.py |
| pipeline_timing | test_pipeline_tool.py（30 sleep） |
| chat_send_timing | test_chat_send_route.py |
| context_compaction_timing | test_context_compaction.py |
| control_plane_timing | test_control_plane_api.py |
| voice_async_timing | test_f133_voice_async.py |
| us4_echo_timing | test_us4_llm_echo.py |
| oauth_callback_timing | test_callback_server / test_oauth_flow / test_refresh_coordinator / test_oauth_refresh_timeout |
| perf_benchmarks | test_finalize_result_offload / test_threat_scanner_boundary |

### CI 并行翻转结论

**已翻**（`-n auto --dist=loadgroup`，`.github/workflows/feature-007-integration.yml`
backend-deterministic job run 参数区块）。依据：本地同参 3 轮全绿（4894 passed，
~26s/轮）；翻转前迭代抓出并治掉 2 类真问题（见 §4 ①②）。`--reruns 1` 过渡桥保留
（F141 quarantine 后删）；60min 兜底不动；F140 地盘（frontend job/未来 playwright
job）零触碰。**CI 真跑时长待 push 后首轮验证**（GitHub 2-core `-n auto`=2 worker，
预期 6m25s 显著缩短；若首轮抖，按 junit rerun 计数 triage——本地已 3 轮验证，预期
风险低）。

## 3. spec 偏离归档

① **件2 捕获点**：spec 原写「捕获脚本脑收到的 `execution_context.conversation_messages`」，
实施改为在 `llm_service.call` 入参捕获 + canned 应答收尾——同一份
`compiled_context.messages`（task_service.py:748 原样传入）、更上游更简单，且避免
scripted client 被 chat 主路径 tool-selection 辅助调用乱序消费、避免 F137 deny 闸
让 Echo 委托路径 FAILED（探针轮实证）。已写进测试文件 docstring。

② **件2 估算器算法**：spec 侦察时误判 tiktoken 未装；实测 venv 有 tiktoken 0.12
（已锁 uv.lock，本地/CI `uv sync` 一致）→ cap 按 `tokenizer`（cl100k）校准，比
cjk_aware 更精确。docstring 注明降级语义（tiktoken 缺失 → CJK 计数变低，cap 仍
安全但灵敏度下降）。

## 4. 实施中的真发现（超出计划的产出）

1. **set 参数化收集不确定性**（loadgroup 首跑直接 ERROR）：
   `test_state_machine_mappings_f091.py` 两处 `parametrize("status", list(<set>))`——
   PYTHONHASHSEED 随机化下 set 迭代序跨 xdist worker 进程漂移 → 收集不一致 ERROR。
   修 `sorted()`。**这是全仓任何未来 xdist 使用的地雷，已排**。
2. **attach_input「次生窗口」race 实证 + 治根因**（loadgroup 首跑间歇 1 failed）：
   `test_attach_input_live_path` 的 `job.status` 断言在 poll 断开后独立执行——
   task/session 达 WAITING_INPUT 与 job 行更新非原子（正是 F083 归档的 Race #2
   次生窗口）。修=job 状态折进等待条件本身 + 窗口 100→200/20→100 迭代（迭代计数制
   deadline 高负载自动扩张）；SUCCEEDED/事件链两处同款。
3. **U+2028 静默丢 delta**（wire 边界侦察发现，行为已钉住）：httpx `LineDecoder`
   按 `str.splitlines` 全集切行（含 U+2028/U+2029/U+0085）——provider 若在 SSE
   data 行内发未转义 U+2028（合法 JSON；`ensure_ascii=False` 序列化即产生；网页
   抓取文本常见该字符），该行被切两半 → 前半 JSONDecodeError 跳过 + 后半无
   `data: ` 前缀跳过 = **该 delta 静默丢失**（流不中断）。已钉为 documented
   behavior + 对照组（转义形式完整送达）。修复候选=弃 `aiter_lines` 自管 SSE
   framing（与行缓冲上限同源），归 F139 wire 真样本回归或独立 fix Feature 决策。
4. **`.env.litellm` 存活文件名化石**：负向扫描侦察发现 TOOLS.md 模板含
   ".env.litellm"——但它不是退役文案：SecretService/backup_service/path_policy
   **现仍真读写该文件名**（LiteLLM 子系统 F081 退役了，文件名留下了）。扫描标记
   显式排除 bare "litellm"；改名（`.env.litellm` → 中性名 + 迁移）作为独立清理
   候选留档，不在 F142 动。

## 5. 验证记录

| 门 | 结果 |
|----|------|
| baseline（origin/master 6972ddc7，串行） | 4975 passed / 14 skipped / 1 xfailed / 1 xpassed（378s） |
| 终门全量（改后，串行含 e2e_live） | 见 §终值（本报告 commit 前回填） |
| e2e_smoke | 8/8（每次 commit pre-commit hook 实跑 PASS；终门再显式跑一轮） |
| `-n auto --dist=loadgroup` ×3（CI scope） | 3 轮全绿 4894 passed（25.79/27.18/25.84s） |
| ruff 对账 | 全部改动文件 baseline→now 零上涨（新文件 0 违规） |
| 生产代码 | **零改动**（`git diff origin/master --stat` 中 `src/` 路径仅 0 行；wire 上限评估结论=不修） |
| Codex spec review | 0 HIGH / 1 P2 + 1 P3 已闭环（importorskip 收窄 / piper unbound 签名） |
| Codex final + Opus 对抗自审 | 见 §6（双评审结果回填） |

## 6. 双评审闭环（回填）

- Codex final review：（待跑）
- Opus 对抗自审：（待跑）

## 7. 遗留与 handoff

- **CI 首轮并行真跑验证**：push 后看 workflow 时长与稳定性（预期 <6m25s；抖则按
  junit rerun 计数 triage，最坏回退串行只改一行参数）。
- **F141 回收点**：CI `--reruns 1` 过渡桥（quarantine manifest 后删）；
  budget guard / lib_semantics / wire boundaries 目前跑本地全量与（除 e2e_live
  的）CI——budget guard 在 e2e_live 目录，待 F141 lane 显式纳入 CI。
- **inline-snapshot**：defer 评估结论见 §1 件4。
- **独立清理候选**（不阻塞）：`.env.litellm` 文件名化石改名迁移；U+2028/行缓冲
  自管 SSE framing（F139 输入）。
- **剩余 sleep 断言长尾**：F142 治了最热三处（task_runner/f009/f131），全仓
  ~72 处存量降到 ~69，其余按 F141 quarantine/后续增量治理。
