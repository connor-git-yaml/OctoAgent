# F142 确定性护栏补齐（M9 波② / L3+L4）

> 收窄 spec（设计即实施边界）。上游：`docs/blueprint/milestones.md` M9 表 F142 行 +
> 「Fable 5 复审调整」②③ + 「首波落地」段 4 个 CI-skip 欠账；审计证据：
> `CLAUDE.local.md` §M9 竞品采纳明细 partial 段 + qa_audit_survivors.md（agent-zero
> 库钉住/预算范式、claude-agent-sdk wire 粘包范式、pydantic-ai dirty-equals/xdist_group）。
> 基线：origin/master `6972ddc7`（worktree PYTHONPATH 锁实测 collected 4991）。

## 0. 总原则（红线）

- **生产代码原则零改**。唯一预许可例外：wire 行长/缓冲上限评估若发现无界内存吃入
  且修复极小才最小化修并显式报（实测结论见 §3，本 Feature 判定：**不修，只评估归档**）。
- **不做 changed-lines coverage 门**（Fable 复审已挪 F141）。
- **不碰 F140 地盘**（frontend/**、新增 playwright CI job）；CI workflow 只动
  backend-deterministic job 的**并行参数区块**。不碰前端 vitest excluded 文件（chip 地盘，
  已由 6972ddc7 清零，本 Feature 不回头动）。
- pyproject 只加 dev-deps + markers/ini 注释。
- worktree 验证禁 `uv sync`；一律 PYTHONPATH 锁 + `uv run --project . --no-sync python -m pytest`。
- 新测试若 import 新 dev-dep（dirty_equals）必须 `pytest.importorskip` 防御——pre-commit
  hook 跑共享 venv，安装收敛前不得炸 hook。
- ruff 对账：改动文件 ruff 违规数不高于 baseline；禁全仓 `--fix`（I001 会搬 lazy import）。

## 1. 五件范围——「已有 vs 补什么」复核表

### 件 1：第三方库语义钉住 ×3（+1 略过项）

治本对象：三次踩坑全是「依赖语义假设破裂」（anyio 4.12.1 TLS 读竞态 / APScheduler
DOW off-by-one / piper synthesize API 错用），但现有回归全是 fake 钉自家调用点，
**零测试 import 真库验证假设**——依赖升级破坏假设时本地 pytest 不会暴露。

| 库 | 已有（fake 钉自家） | 补什么（真库钉住） |
|----|---------------------|--------------------|
| anyio/httpx | `packages/provider/tests/test_provider_client_chat.py:239-313`：`_ReadErrorResponse` fake 直接 raise `httpx.ReadError`，钉的是**我们的重试逻辑**，不验证真栈在 TLS 中断时抛的就是这个类型 | 真本地 TLS server（`cryptography` 46.0.5 已在 venv，测试内生成 ephemeral 自签证书 + `asyncio.start_server` ssl，零外网零新依赖）+ 繁忙 event loop（后台任务压调度）→ 服务端 mid-stream 硬断（不发 TLS close_notify）→ 断言真 httpx `aiter_lines` 抛的异常 ∈ `_TRANSIENT_TRANSPORT_ERRORS`（provider_client.py:44-48）→ 再端到端：真 `httpx.AsyncClient` 注入 `ProviderClient`，重试后成功。**钉住的语义**：真 anyio/httpx 栈对「TLS 流中断」的异常面与我们重试 family 匹配；升级破坏时本测试红 |
| APScheduler | `apps/gateway/tests/tools/test_cron_tools.py:134,372`：测我们自家校验器 `_cron_field_is_numeric_dow` 拒数字 DOW；全仓测试 **0 个 import apscheduler** | 真 `CronTrigger.from_crontab`（apscheduler 3.11.2，gateway 既有依赖）断言 next_fire_time 语义：①`* * * * mon` 落 Python `weekday()==0`（周一）；②`* * * * 0` 在 APScheduler 落**周一**而非 Unix cron 的周日——这正是 off-by-one 陷阱本体；③`sun`==数字 6。**钉住的语义**：`cron_tools.py` DP-3 拒数字 DOW 的根据（Monday=0）持续成立；APScheduler 若改语义（4.x 讨论过）本测试红 |
| piper | `apps/gateway/tests/test_tts_service.py:250+`：fake 模块注入 sys.modules 锁 `synthesize_wav` 签名——**保留互补**（无 piper 环境也跑） | `pytest.importorskip("piper")` 门控的真库 API 钉住：`PiperVoice.load` 存在 + `synthesize_wav` 存在且 `inspect.signature` 第二参数名 `wav_file`（对齐 `piper_backend.py:131-155` 用法）。**诚实声明**：piper 是 voice optional extra，dev venv/CI 未装 → 本测试默认 SKIP；价值在装了 piper 的环境（生产近似环境 / F110 式 ephemeral venv 真验证流程）自动激活 |
| aiosqlite | **已有真库覆盖，略过**：全部 store 层测试（packages/core/tests + gateway）经 `create_store_group` 走真 aiosqlite 真 SQLite 文件（packages/core/src/octoagent/core/store/__init__.py:10 直接 import aiosqlite），语义假设每天在集成层被真库验证 | 不新增；本表即归档说明 |

落点：新目录 `octoagent/tests/lib_semantics/`（root testpaths 已含 `tests`；根 pyproject dev
group 含 gateway+provider，apscheduler/httpx/cryptography 均可 import）。3 文件 + `__init__.py`
（模块 docstring 写明 guard family 定位 + aiosqlite 略过理由）。TLS 测试驱动
`ProviderClient.call()` 需过 F137 硬闸：局部 fixture 用 `allow_model_requests()`（防御式
import，照 provider tests conftest 先例）。

### 件 2：prompt token 预算护栏（agent-zero 范式）

| 已有 | 补什么 |
|------|--------|
| 运行时**裁剪**：`agent_context_prompt_assembly.py:455` `_fit_prompt_budget`（超预算静默修剪，prompt creep 只挤占 conversation 预算，CI 不报警）；预算规划器 `context_budget.py` 及其单测；组件级 `test_capability_pack_tools.py:2125-2135`（render_bootstrap_context ≤250 token）。**全量组装硬上限护栏 grep 0 hit** | 新 L3 测试文件：F138 scripted harness 范式（全 11 段真 bootstrap + `ScriptedModelClient`，零真 LLM 零 OAuth）驱动真 chat 主路径 `POST /api/message` → task_runner → `build_task_context` → 捕获脚本脑收到的 `execution_context.conversation_messages`（含真 BehaviorPack envelope system blocks，来源 `packages/core/behavior_templates/` 默认模板——`resolve_behavior_workspace` source_chain `default_behavior_templates`）→ ①`estimate_messages_tokens`（生产同源估算器）断言 system 面 ≤ 硬 cap（**实测值收口 + ~15% 余量**，实施时测定写死并注释测定日期/值）；②工具 schema 面：`tool_broker.discover()` 复刻 `provider_model_client._get_tool_schemas` 同构 payload → JSON 序列化 token 估算 ≤ 独立 cap；③关键工具指令短语在场断言（从 system blocks 实测挑 3-5 条承重短语，如 behavior tool guide 关键句）；④已退役内容负向扫描：组装产物 + `behavior_templates/*.md` 全库 not-in 断言（`LiteLLM`、`BootstrapSession` 等退役物） |

落点：`apps/gateway/tests/e2e_live/test_prompt_budget_guard.py`，标 `e2e_scripted`（+`e2e_live`
正交标）。**已知 limitation 显式归档**：CI 现 `--ignore=e2e_live` → 本护栏跑在本地全量 +
F141 lane 接入后进 CI；不在本 Feature 私改 CI 范围（红线只许动并行参数）。

### 件 3：wire 边界用例族（claude-agent-sdk 范式）

| 已有 | 补什么 |
|------|--------|
| 单缝注入已具备（`ProviderClient.__init__(runtime, http_client)`）；fake 行级驱动三 transport（`_FakeResponse.aiter_lines` 喂**预切好的行**）；tool_call arguments 跨 chunk 累积已测（fake 行级）；`_ReadErrorResponse` 瞬态重试已测。**缺**：①`except json.JSONDecodeError: continue` 容错分支（chat:856 / responses:634 / anthropic:1231）零测试；②字节级粘包/半包重组全托管 httpx 从未对抗测试；③无行长/缓冲上限守卫 | 新文件 `packages/provider/tests/test_provider_client_wire_boundaries.py`，用 `httpx.MockTransport` + **真 `httpx.AsyncClient`**（真 LineDecoder/aiter_lines 重组路径，非 fake 行）：①malformed JSON data 行 ×3 transport——坏行跳过、后续好行照常解析、结果正确；②粘包/半包族——多事件挤一 chunk、`data: ` 前缀跨 chunk 切断、UTF-8 多字节（CJK）跨 chunk 切断、tool_call arguments JSON 在怪异字节位切断、`\r\n` 混合行尾、空 chunk；③**LineDecoder splitlines 全集边界钉住**：httpx `_decoders.py` 按 `str.splitlines` 全集切行（含 U+2028/U+2029/U+0085）——data 行内含未转义 U+2028 时该行被切两半 → 前半 JSONDecodeError 跳过 + 后半无 `data: ` 前缀跳过 = **静默丢 delta**。用例钉住此真实行为（documented behavior，非 assert 理想行为）；④超长单行（~2MB）可解析不崩（现状钉住） |

**行长/缓冲上限评估结论（先评估承诺的交付）**：httpx `LineDecoder.buffer` 无上限，行未终止
则无界吃内存。威胁模型：provider api_base 是用户显式配置的可信端点（非任意 URL——LLM 可诱导
的 fetch 面已由 F123 SSRF 覆盖），恶意/故障 provider 才可触发；修复需弃 `aiter_lines` 改
`aiter_bytes` + 自管 SSE framing + 上限——**非极小改动**（三 transport 解析循环重写），不满足
预许可例外条件 → **不动生产，评估归档**（同时 U+2028 静默丢 delta 与此同源，一并归档为
「provider SSE framing 自管化」候选，留给 F139 wire 真样本回归或独立 fix Feature 决策）。

### 件 4：dirty-equals 引入 + 样例改造（pydantic-ai 范式）

| 已有 | 补什么 |
|------|--------|
| 事件 payload/审计链断言全手写逐字段（单 `test_control_plane_api.py` 184 处 `payload[...]`）；时间字段绕开不断言或手写窗口比较；**dev deps 零 matcher 库**（grep dirty_equals/inline_snapshot/IsNow 全零） | ①pyproject dev group + `dirty-equals`（uv lock 同步）；②样例改造 2-3 处立范式**不全仓改写**：a) `test_us4_llm_echo.py` 状态迁移 payload → 整 dict 相等 + `IsStr`/`IsDatetime` 打洞（full-shape 钉住，新增字段漂移可见）；b) `test_control_plane_api.py` 挑 1 个代表性 payload 断言簇同款改造；c) 时间断言场景用 `IsNow(delta=...)` 展示（治慢机窗口）；③每个改造文件顶部 `pytest.importorskip("dirty_equals")` 防御（hook 共享 venv 未装窗口不炸）+ 改造注释标「F142 范式样例」 |

**inline-snapshot 评估（可 defer 的承诺交付）**：结论写入 completion-report——引入需配
①ruff format 写回流程（`--inline-snapshot=fix` 改源码）②xdist 兼容惰性 stub（pydantic-ai
`tests/_inline_snapshot.py` 前例）③团队约定何时 fix/review。三件配套均超本 Feature 范围，
**defer**：本轮只引 dirty-equals（零风险标准件），inline-snapshot 待 F141 lane 稳定后独立评估。

### 件 5：xdist_group + 治 4 个 CI-skip 欠账

**5a. xdist_group（F083 债治标，解锁 `-n auto`）**

| 已有 | 补什么 |
|------|--------|
| pytest-xdist 3.8.0 已装但默认不启用（pyproject:75-90 注释归档 F083 长尾 race：task_runner 状态机 + f009 单 sleep + 全仓 ~72 处 sleep 断言）；全仓 **0 处 xdist_group/loadgroup**；CI backend job 串行 6m25s | ①给已知 race/时序文件标模块级 `pytestmark = pytest.mark.xdist_group("<域名组>")`——初始清单（sleep 密度 + F083 归档驱动，实测迭代扩充）：`test_task_runner.py`、`tests/integration/`（f009/f013 watchdog/sc*，共用 `integration_timing` 组或按文件域分组）、`test_f101_phase_b.py`、`test_pipeline_tool.py`（30 sleep）、`test_chat_send_route.py`、`test_context_compaction.py`、`test_f133_voice_async.py`、`test_callback_server.py`/oauth 计时类、harness 两个性能基准文件；②本地 `-n auto --dist=loadgroup`（CI 同 scope：`--ignore=e2e_live`）**连跑 3 次全绿**→ 翻 CI backend job 并行（只动 run 参数区块 + 注释）；任一轮不稳 → 按失败证据补组再跑；仍不稳 → CI 保持串行 + 归档写明 |
| | 性能断言与并行本质冲突预案：绝对时长断言（threat_scanner <1ms / finalize_offload <130ms）在并行负载下测量无效——若 3 轮实测抖，用 `PYTEST_XDIST_WORKER` 检测 skipif（「并行下性能测量无意义」是诚实语义非逃避），串行 lane 照跑；只对实测抖的测试做 |

**5b. 4 个 CI-skip 欠账处置（925bc29b 标的）**

| 欠账 | 类型 | 处置 |
|------|------|------|
| `test_f131_outbound_spool.py::test_startup_drains_spool` | 时序（`sleep(0.05)` 赌后台 drain 首轮完成） | **治根因**：固定 sleep → 条件轮询 `ok_bot.sent`（10ms 间隔 + 5s deadline），完成信号确定性等待 → **移除 skipif** |
| `tests/integration/test_f009_worker_runtime_flow.py::test_timeout_path_generates_worker_timeout_result` | 时序（`sleep(0.4)` 赌 watchdog 超时链完成） | **治根因**：轮询 `/api/tasks/{id}` 至终态（deadline 数秒）再断言 FAILED + WORKER_RETURNED → **移除 skipif** |
| `test_finalize_result_offload.py::test_eventloop_not_blocked_by_large_scan` | 性能（max 心跳停顿 <130ms，阈值按 M4 Pro 校准） | **永久 CI 豁免**：阈值区分「卸载 ~54ms vs 同步 200-325ms」依赖机器单核性能，2-core 共享 runner 上裕量被吞——且卸载机制本身已有确定性伴测（`TestScanOffloadedToThread` 断言真跑在线程）。skip 注释升级为永久豁免 + 理由 |
| `test_threat_scanner_boundary.py::test_long_content_scan_under_1ms` | 性能（5000 字符扫描均值 <1ms，FR-3 机器校准值） | **永久 CI 豁免**：同上；两个兄弟基准（短文本/恶意短路）CI 已稳绿继续跑，本条是同族中机器敏感度最高的一格。skip 注释升级为永久豁免 + 理由 |

## 2. 验收（AC ↔ test 显式绑定）

| AC | 断言 | test |
|----|------|------|
| AC-1 | 真 anyio/httpx 栈 TLS 流中断异常 ∈ `_TRANSIENT_TRANSPORT_ERRORS` + ProviderClient 真栈重试恢复 | `tests/lib_semantics/test_httpx_anyio_tls_read_semantics.py` |
| AC-2 | 真 CronTrigger 数字 0=周一（非 Unix 周日）+ 命名 mon 语义 | `tests/lib_semantics/test_apscheduler_cron_dow_semantics.py` |
| AC-3 | 真 piper `synthesize_wav(text, wav_file)` 签名在场（importorskip 门控） | `tests/lib_semantics/test_piper_api_semantics.py` |
| AC-4 | 全量真实组装 system 面 token ≤ cap + 工具 schema 面 ≤ cap + 关键短语在场 + 退役内容负向 | `apps/gateway/tests/e2e_live/test_prompt_budget_guard.py` |
| AC-5 | 三 transport malformed JSON 行跳过不中断 + 粘包/半包/怪异切分穿透真 httpx 重组 + U+2028 行为钉住 | `packages/provider/tests/test_provider_client_wire_boundaries.py` |
| AC-6 | dirty-equals 范式样例落地（≥2 文件）+ hook 防御 | `test_us4_llm_echo.py` / `test_control_plane_api.py` 改造处 |
| AC-7 | `-n auto --dist=loadgroup` CI scope ×3 全绿（或归档不稳证据回退） | 运行记录进 completion-report |
| AC-8 | 4 欠账处置落地：2 治愈移除 skipif + 2 永久豁免注明 | 上表 4 文件 |
| AC-9 | 全量 0 regression vs baseline + e2e_smoke 8/8 | 终门运行记录 |

## 3. 显式范围外

- changed-lines coverage 门（F141）/ flaky quarantine manifest（F141）/ CI lane 分层（F141）
- inline-snapshot 落地（评估结论归档，defer）
- provider SSE framing 自管化 + 行缓冲上限（评估归档，见件 3 结论）
- e2e_live conftest 的 `flaky(reruns=1)` 机制调整（e2e 真 LLM 不确定性豁免合理，非竞态掩盖）
- 全仓 ~72 处 sleep 断言逐个治本（xdist_group 是治标解锁；逐个治本仍是长尾债）
- 前端一切（F140/F143/chip 地盘）
