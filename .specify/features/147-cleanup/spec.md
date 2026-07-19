# F147 清扫篮（M10 收官 · S-M）

> 状态：收窄 spec（取证完成，待 Codex spec 评审 → 实施）
> 分支：`feature/147-cleanup`（基于 origin/master c17c9ebc）
> 归属：M10 第二波，收官型清扫。5 个不相关小项，每项独立取证 / 实施 / 归档。
> 红线：命中「无需改」诚实写结论归档，不为改而改；生产代码最小改 + 测试锚定 + 0 regression；**不 push origin**。

---

## 总览：5 项取证结论 → 改/不改

| # | 项 | 取证结论 | 决策 | 触碰生产码 |
|---|----|---------|------|-----------|
| 1 | toolsets 死配置（F-CLEAN-1） | `toolsets.yaml` + `toolset_resolver.py` 整链 **0 生产消费方**（F135 已留档「历史冗余」）；`toolset` 字段经 Codex 查=plugin 公共 schema（quickstart+plugin 测试） | **改（v2 收窄）**：只删无契约死配置子系统（yaml+resolver+VALID_ENTRYPOINTS+wrapper）；`toolset`/`entrypoints` 字段保留+标注 | 是（删除为主，无行为变更） |
| 2 | cron 后台失败通知 | 三姊妹 FAILED + F127/F111 spawn_error 路径**只写审计 + log，零通知**（F127/F111 显式「失败静默」，被 M10 里程碑翻转） | **改（v2 加固）**：genuine 失败（FAILED + spawn_error）补 HIGH + `record_when_filtered`（quiet 内入桶可发现）；良性 SKIPPED 静默 | 是（新增通知调用 + notification.py 加 1 backward-compat 参数） |
| 3① | T1-TOOL-CALL-003 断言 | prompt 问时间，AmbientRuntime 已把 `current_datetime_local`+`timezone` 注入 prompt → 模型抄答不调工具 → 撞「必须 TOOL_CALL」断言 FAIL | **改**：prompt 改成确定性需要工具（读文件 + 结构化 JSON），保留 tool_call+结构化输出域意图 | 否（benchmark 非生产） |
| 3② | runner.py 429 退避吃步数 | `while` 循环 `steps+=1` 在顶部无条件；429 分支扁平 `sleep(3)`+`continue` → 回顶部再 `steps+=1` → 退避烧 max_steps；无 rate-limit 测试 | **改**：指数退避 + 退避不计步数 + 独立重试上界 + 成功清零 | 是（skills/runner.py 决策环） |
| 4 | 容器交付评估 | 单用户 + launchd/systemd 常驻已给崩溃自愈/开机自启 + Tailscale 给远程 | **不实施容器**：写评估结论归档 | 否 |
| 5 | console_output 窄终端 | `create_console` 无显式 width + 模块单例 import 时锁死（实测非 TTY width=80 且 COLUMNS 后改不生效）→ 长 CJK 指引行 word-wrap 折断 | **改**：窄时给 width 下限（floor 120），保留自动探测 | 是（provider/dx/console_output.py） |

---

## 项 1：toolsets 死配置清理（F-CLEAN-1）

### 取证（实测）
- **两套同名系统必须区分**：`CoreToolSet`（tooling 包，`models.py:377`）= 真正决定 LLM 工具可见性的**活**系统，**勿碰**；`toolsets.yaml` + `toolset_resolver.py`（gateway harness）= **死配置**，才是 F-CLEAN-1。
- **死项证据**（grep 全仓，排除 .venv）：
  - `toolset_resolver.py`（`resolve_for_entrypoint`/`load_toolsets`/`_resolve_toolset_tools`/`ToolsetConfig`）：**0 生产 importer**（无任何文件 import 它）。
  - `toolsets.yaml`（4 键 core/agent_only/ops_tools/telegram）：唯一读者是死的 `load_toolsets`；不在任何 pyproject/MANIFEST/Dockerfile/install 脚本。
  - `VALID_ENTRYPOINTS`（`tool_registry.py:30`）：全仓**0 消费者**（连 register/list_for_entrypoint 都不校验它）。
  - `list_for_entrypoint` 模块级 wrapper（`builtin_tools/__init__.py:68`）：**0 调用者**。
  - `ToolEntry.toolset` 字段（`tool_registry.py:55`）+ 21 处 `toolset=` 声明：生产**0 读**，仅 2 测试断言（`test_tool_registry.py:213`、`benchmarks/tests/unit/test_tau_bench_adapter.py`）。
- **保留（存疑，有活消费方）**：`ToolEntry.entrypoints` 字段 + 25 处 `entrypoints=` + `ToolRegistry.list_for_entrypoint` 方法——`entrypoints` 被 `capability_pack.py`（6 处）读入展示 metadata（UI/日志/discovery，**不进过滤逻辑**）；`list_for_entrypoint` 方法读该活字段，且 `test_graph_pipeline_contract.py` 用它断言 graph_pipeline 的 entrypoint 标签契约（真断言）。

### 决策：改（**v2 Codex 后收窄**——只删无契约的死配置子系统）
- **删**（4 项，全 0 生产消费方 + 不在 plugin/公共 schema）：① `octoagent/apps/gateway/toolsets.yaml` ② `octoagent/apps/gateway/src/octoagent/gateway/harness/toolset_resolver.py`（整模块 + 其测试若有）③ `VALID_ENTRYPOINTS` 常量（0 消费，仅 F084 历史 spec 提及非 living）④ `builtin_tools/__init__.py` 的 `list_for_entrypoint` module wrapper（0 调用者）。
- **保留 + 标注**（v2 翻转，Codex 项1 MED/LOW）：
  - `ToolEntry.toolset` 字段 + `toolset=` kwargs：**不删**。取证发现它是 `ToolEntry` **公共/plugin schema** 的一部分——`quickstart.md:28` 教人写、`test_plugin_registry.py` 多处构造（含 plugin code-string `register(ToolEntry(...toolset='x'...))`）。删字段=运行时静默吞（pydantic extra=ignore）但文档/样例/plugin 仍要求 = **契约漂移**。它是描述性字段（0 过滤消费但属 schema），保留 + docstring 标注「描述性 metadata，entrypoint 过滤链已退役」。
  - `ToolEntry.entrypoints` + `ToolRegistry.list_for_entrypoint` 方法：保留（`entrypoints` 活喂 capability_pack 展示 metadata；`list_for_entrypoint` 被 `test_graph_pipeline_contract` 当真契约断言 + 读活字段）。docstring 标注（Codex LOW 校准措辞）：**不称「纯展示」**，准确表述=「registry 级 entrypoint 标签 contract；已退役的是 `toolsets.yaml` 配置驱动的那层过滤（resolver），非本方法/字段」。
- **理由**：v1 想删 `toolset` 字段被 Codex 正确挡回（公共 schema 契约，非纯内部死字段）；收窄到「删无契约的死配置子系统」既满足 F-CLEAN-1（删 toolsets.yaml + resolver 死链），又避免 schema/plugin 契约漂移 + 21 站点 churn。这是「删确认无消费方的，有疑虑保留+标注」的精确落点。

### 验证
- 全量 0 regression；删 `toolset_resolver` 及其测试（测死代码）；`resolve_for_entrypoint`/`ToolsetConfig`/`load_toolsets` grep 归零；`VALID_ENTRYPOINTS`/wrapper grep 归零。`toolset`/`entrypoints` 字段构造零改动。

---

## 项 2：cron 后台失败 HIGH 通知

### 取证（实测）
- **三姊妹 FAILED 路径全部零通知**：
  - F102 `daily_routine.py:339` `except Exception` → `_emit_routine_failed`（写 ROUTINE_FAILED）+ `logger.exception`，**无 notify**。
  - F127 `memory_consolidation.py` 发现端 `except`（`:653`）→ `_emit_failed`（MEMORY_CONSOLIDATION_FAILED）+ log，**无 notify**。
  - F111 `behavior_compaction.py` 发现端 `except`（`:479`）→ `_emit_failed`（BEHAVIOR_COMPACT_FAILED）+ log，**无 notify**。
- **现状是刻意设计**：`memory_consolidation.py:703-704` 白纸黑字「巩固 FAILED / SKIPPED → 不发（失败静默，事件已审计）」。M10 里程碑（`milestones.md:667`）显式决定翻转此项（「cron 后台失败 HIGH 通知」）+ 预置「H1 通知非对话轮次」化解 H1 顾虑。
- **通知范式已齐**：`NotificationService.notify_task_state_change`（keyword-only：task_id/event_type/payload/priority/state_transition_event_id/session_id/channels）；`NotificationPriority.HIGH="worker_failed"`（语义正好对应任务失败）；现成模板 `_notify_pending_review`（memory_consolidation.py:690）。三 service 已持 `self._notification_service`，无需新依赖。
- **quiet hours 交互（关键事实）**：`_is_quiet_hours` 仅 CRITICAL 豁免；**HIGH 在 quiet hours 内被 discard**（只写 `NOTIFICATION_DISPATCHED(filtered=true)` 审计，不推 channel、`session_id=""` 时仍进 `_record_active` 全局桶）。F127 深夜 / F111 默认 03:30 触发 → 大概率落 quiet hours → HIGH 失败通知被过滤（审计留痕，channel 不推）。

### 决策：改（**v2 Codex 后加固**——覆盖 spawn_error + quiet-hours discoverable + 稳定幂等键）
- **通知面 = genuine 失败（v2 扩，Codex 项2 HIGH#1）**：三个 FAILED 事件路径（F102 ROUTINE_FAILED / F127 MEMORY_CONSOLIDATION_FAILED / F111 BEHAVIOR_COMPACT_FAILED）**+ F127/F111 的 `spawn_error` skip 路径**（`memory_consolidation.py:428` / `behavior_compaction.py:376` 的 orchestration `except Exception` → `_emit_skipped(reason="spawn_error")`——那是「任务根本没起起来」的异常失败，v1 漏了）。**其余 SKIPPED 保持静默**（`already_running`/`disabled`/`no_scope`/capacity——良性优雅降级，非失败）。
- **quiet-hours discoverable（v2，Codex 项2 HIGH#2）**：给 `notify_task_state_change` 加 `record_when_filtered: bool = False`（默认 False = 所有现有 caller 行为零变更）。cron 失败通知传 True → **quiet hours 内被过滤时仍 `_record_active` 进全局收件箱**（不推 channel 不吵醒深夜，但用户次日开 Web 能发现），只有非 quiet 才推 channel。修「深夜失败 filter 后在 `_record_active` 前 return → 既不推也不入桶 → '补通知'名不副实」的死角。**不升 CRITICAL**（CRITICAL 语义=审批待办且会 3am 吵醒，失败不值当）。
- **稳定幂等键（v2，Codex 项2 MED）**：`state_transition_event_id` **不用**随机 FAILED event_id（每次新 ULID → 反复失败刷屏）；F127/F111 用 **`run_id`**（同一 run 失败不双发），F102 用 **日期**（`f"routine-failed:{date}"`，同一天失败去重）。
- 参数：`priority=HIGH`；`session_id=""`（全局桶 + F116 rehydrate）；`channels`=各 service 成功通知同款（F102=`config.summary_channels`；F127/F111=None）；payload 只含计数/错误摘要（无敏感原文，PII 惯例）。
- try/except 包裹通知（#6 降级：通知失败不得让 cron 崩）；`_notification_service is None` 守卫。
- 更新 `memory_consolidation.py:703-704` 等 stale 注释为新语义（genuine 失败/spawn_error → 发 HIGH + record_when_filtered；良性 SKIPPED → 不发）。

### 验证 / 测试锚
- 单测：三 service FAILED + spawn_error 路径断言 `notify_task_state_change(priority=HIGH, record_when_filtered=True, state_transition_event_id=run_id/date)` 调一次。
- 单测：良性 SKIPPED（already_running/disabled）断言**不**调 notify（回归护栏）。
- 单测：`_notification_service is None` 时失败不炸（降级）。
- 单测（notification.py）：`record_when_filtered=True` + quiet hours → `_record_active` 被调（进 inbox）但 channel 不推；`=False`（默认）→ 现状（filter 即 return，不 record）。全 backward-compat。

---

## 项 3①：T1-TOOL-CALL-003 断言收敛

### 取证（实测）
- Task `benchmarks/tiers/tier1/t1_tool_call_003.yaml`：prompt「请查看系统当前时间，并以 JSON 格式返回 `{current_time, timezone}`」，`expected_events` 硬要 `TOOL_CALL_STARTED`+`TOOL_CALL_COMPLETED`+`MODEL_CALL_COMPLETED`。
- Scorer（`benchmarks/runner/scorer.py:182-321`）按 event_store 命中率判 PASS/FAIL：模型抄答不调工具 → 仅 `MODEL_CALL_COMPLETED` 命中 → match_ratio 1/3≈0.33 → 落 `(0.0,0.5)` → **FAIL 0.0**。
- AmbientRuntime 确实注入时间：`agent_context_prompt_assembly.py:346-360` 把 `current_datetime_local`+`timezone`+`utc_offset` 拼进 prompt system 段 → 模型直接抄答是合理行为，撞断言。归因散文在 `milestones.md:574`。

### 决策：改（选 option b：prompt 确定性需要工具）
- **不选 option a（放松断言接受 ambient 路径）**：003 属 tool_call **域**（三 task 都验证工具调用能力），放松断言会让 003 不再测工具调用，把 tool_call 域回归护栏从 3 task 削到 2——弱化护栏。
- **选 option b，但用确定性诱导**：把 prompt 从「查时间」改为「用工具读 README.md 并结构化 JSON 输出」——模型**无法**从 ambient 抄答（文件内容/行数不在 prompt），必须调 filesystem 读工具，`TOOL_CALL_STARTED/COMPLETED` 必然产生；保留 003 的「工具调用 + 结构化输出（JSON）」域意图（001 是读+总结，003 是读+结构化，区分特征=JSON 输出要求）。比「明示用工具查时间」更确定（弱控变量 model 可能仍抄 ambient 时间，文件读则provably 需工具）。
- 具体 prompt（拟）：读 README.md 返回 `{"line_count": int, "has_content": bool}`（line_count 不在 prompt → 必须真读文件）；`expected_events` 保持 TOOL_CALL_STARTED/COMPLETED + MODEL_CALL_COMPLETED。
- **v2（Codex 项3① MED#2）收紧 `required_fields`**：`TOOL_CALL_STARTED` 加 `tool_name_contains: "read"`（照 001 范式）——防「模型乱调工具/乱打一枪也过关」，锁定确实调了读工具。
- **v2 已知限制留档（Codex 项3① MED#1）**：tier1 scorer 是 **event-based**（只判 TOOL_CALL/MODEL_CALL 事件命中率，`scorer.py:241`），**不校验 JSON 输出结构**——所以「结构化输出」本就不被 tier1 主链验证（003 与 001 在 tool_call 域天然有读工具重叠，小域 3 task 难完全正交）。真验证结构化输出需 tier2/专门 rubric，超 F147 scope；本项只修「AmbientRuntime 泄漏致确定性 FAIL」，不扩 scorer。

### 验证
- 结构层：yaml 合法可被 runner 加载（yaml.safe_load + benchmark task loader 单测若有）。不跑真 benchmark（需真 model + $，超 F147 scope）。

---

## 项 3②：runner.py 429 退避跳出步数预算

### 取证（实测，`skills/runner.py`）
- 主循环 `while tracker.check_limits(limits) is None:`（:145）；`steps += 1`（:146 顶部无条件）；`attempts += 1`（:147）；`tracker.steps = steps`（:148）。
- 429 分支（:217-221）：`logger.warning` + `await asyncio.sleep(3)`（扁平，非指数，不走 `_backoff`）+ `continue` → 回 :145 → :146 `steps` 再 +1。**每次 429 扣一步 max_steps 预算**（实测 baseline 626×429 会把 steps 烧到 max_steps 提前 STEP_LIMIT_EXCEEDED 截断决策链）。债属实。
- 429 识别：`provider_client.py:98-99` 把 status 429 映射 `LLMCallError("rate_limit", retriable=True, status_code=429)`；runner `:217 exc.error_type=="rate_limit"`。`LLMCallError` 无 `retry_after` 字段（Retry-After 不解析 → 本次不引入，指数退避即可）。
- 无 rate_limit 路径测试（`skills/tests/` grep 零命中）；模板 `test_runner.py:1230/1268`（LLMCallError 分支注入 `QueueModelClient`）。`ErrorCategory` 无 RATE_LIMIT，用现有 `REPEAT_ERROR`（语义=模型调用连续失败）。

### 决策：改（**v2 Codex 后修正**——回滚 tracker.steps + 复用 max_attempts + 精确清零）
- **429 上界复用 `manifest.retry_policy.max_attempts`（v2，Codex 项3② MED）**：**不**引入独立 `_RATE_LIMIT_MAX_RETRIES`（会与 retry_policy 打成两套预算、调用方无法从 manifest 预测总重试）。用独立计数器 `rate_limit_retries` 但**与 `max_attempts` 同界**比较：`if rate_limit_retries > manifest.retry_policy.max_attempts → _terminate_with_failure(REPEAT_ERROR, "速率限制重试耗尽…")`。单一可配置预算源。
- **指数退避**：base = `retry_policy.backoff_ms`（现成配置，测试可置 0 免真 sleep）；`wait_ms = min(backoff_ms * 2**(rate_limit_retries-1), _RATE_LIMIT_BACKOFF_CAP_MS)`（cap 是天花板常量，非竞争预算）。抽 `_rate_limit_backoff(retries)` 辅助（可测）。
- **退避不吃步数（v2 修 Codex 项3② HIGH：回滚 tracker.steps）**：429 分支 continue 前**同步回滚 `steps -= 1` + `attempts -= 1` + `tracker.steps = steps`**——只减本地 `steps` 不够，因为下一轮 `:145 while check_limits` 读的是 `tracker.steps`（本轮 `:148` 已写成增长值），不回滚 tracker.steps 会在 steps 接近 max 时被 STEP_LIMIT_EXCEEDED 提前打死（Codex 实证 off-by-one）。回滚 tracker.steps 后下轮入口 check 用 pre-429 值。
- **精确清零（v2，Codex 项3② MED）**：`rate_limit_retries = 0` **紧接一次成功 `generate()` 返回后立即置**（try 块内 generate 成功即置，**不**等整步走完）——防后续 tool 失败/stop hook/complete 早退路径漏清零、把旧 429 计数泄漏到后续正常步。
- **不动**非 429 分支（api_error retry_failures / auth / context_overflow / conversation_state_lost 行为零变更）；不动 time/token/cost limit（长限流风暴仍受 time budget 合理兜底）。

### 验证 / 测试锚（`test_runner.py` 新增，用 `QueueModelClient` 注入 429）
- 注入 N 个 429 后接成功：断言 `result.steps` 只反映**成功步**（退避不吃步数，当前实现此断言 FAIL）；断言不被 STEP_LIMIT_EXCEEDED 提前截断（覆盖 Codex HIGH 的 tracker.steps 回滚）。
- monkeypatch `asyncio.sleep` 记录 wait 序列：断言指数递增到 cap。
- 注入 `> max_attempts` 个 429：断言 FAILED + `REPEAT_ERROR`（上界=max_attempts 生效）。
- 断言：429 storm 后接成功再接单个 429，第二段 429 拿满重试额度（成功清零生效）。
- 回归：现有 api_error/auth/step_limit 分支测试全绿。

---

## 项 4：容器交付评估结论归档

### 取证（实测）
- 全仓无 `Dockerfile*` / `docker-compose*`（排除 .venv/_references）——从未实施容器。
- M8 部署形态已定（`CLAUDE.local.md` §M8 部署形态）：单用户 = 禁睡常驻 Mac + launchd user unit + Tailscale。F129（`octo service` launchd/systemd）已给崩溃自愈 + 开机自启；F130（Tailscale serve）已给手机远程。

### 决策：不实施容器，写评估结论归档
- 归档到 `docs/blueprint/deployment-and-ops.md`（部署运维权威文档）新增「容器交付评估」小节：结论 = **不做容器**。理由：①单用户无编排/横向扩展需求；②launchd/systemd user unit 已覆盖崩溃自愈 + 开机自启（容器的核心卖点）；③Tailscale 已解决远程触达；④容器徒增迁移复杂度（镜像构建/卷挂载/凭证注入/时区/Docker-in-Docker JobRunner 嵌套）无对应收益。触发重评条件：迁移到 NAS / 多实例 / 需与其他服务编排共存时。
- **不写任何 Dockerfile / compose**。

---

## 项 5：console_output 窄终端适配

### 取证（实测 Rich 行为）
- `create_console`（`console_output.py:61`）建 `Console` **无显式 width**；消费方 `console = create_console()` 是**模块单例**（remote_commands/behavior_commands/chat_import/cli/backup 等 dx 命令共用）。
- 实测（worktree venv）：非 TTY（CI/pipe）`create_console().width == 80`，且**同实例改 COLUMNS 不生效**（width import 时锁死）；width=80 时长 CJK 指引行被 Rich word-wrap 折断（`octo remote enable` 的「将生成强随机 bearer token 写入 …（变量 …，不打印明文）」断成两行）；width=120 时该行完整单行。
- F134 CI triage（commit 2955bc46）已实证此坑：测试注入 `Console(width=10000)` 覆盖单例绕开，并显式把「真实窄终端 CJK 折行的 console_output 适配」归 F147 followup。

### 决策：改（选 option b：width floor，保留自动探测）
- **不选 option a（非 TTY→plain_output）**：`not isatty` 只覆盖 CI/pipe，**漏掉真实 80 列 SSH（是 TTY）**——而 80 列 SSH 正是 headline 抱怨（`milestones.md:667`）；且改 plain 需 soft_wrap 穿透 render_panel/console.print 调用点，波及面大。
- **选 option b**：`create_console` 探测宽度，**低于下限（`_MIN_CONSOLE_WIDTH=120`）时给 Console 显式 width=下限；否则保持 width=None（自动探测，宽/正常终端不变）**。覆盖非 TTY（探测 80→floor 120）**和**真实 80 列 SSH（80→floor 120，box 溢出由终端软换行、但指引行内容不被 Rich 硬折断）。单函数改，无调用点/soft_wrap 穿透，Panel 结构不变 → 对 40+ dx 输出测试低风险。
- 实现：探测用临时 `Console(stderr=stderr)` 读 `.width`；`< floor` 则 `Console(..., width=floor)`，否则 `Console(..., width=None)`（现状）。其余参数（no_color/emoji/safe_box/color_system）不变。

- **v2 已知限制留档（Codex 项5 LOW×2）**：floor 是「可读下限」非「永不折断」——单行 > floor（如超长 home 路径）仍会折。这是把阈值从 80 抬到 120（覆盖当前所有 remote/dx 指引行，实测最长 token 行 ~110 宽），不是任意长度根治；面板作者应保持单行 ≤ floor。「真实 80 列终端 box 溢出由终端软换行」是取舍（内容完整优先于 box 对齐，符合里程碑「关键指引可读性」诉求），已由 worktree venv 实测锚定（width=80 折断 / width=120 完整），非纯审美假设。

### 验证 / 测试锚（`test_console_output.py` 新增）
- 新增测试：模拟窄环境（非 TTY，探测 width=80）经 `create_console` 渲染 `octo remote enable` 关键指引行，断言长 CJK 行**完整不折断**（子串完整在单行）+ 断言 `create_console().width >= _MIN_CONSOLE_WIDTH`。
- 回归：现有 4 个 console_output 测试全绿；`test_remote_commands.py`（注入 width=10000）不冲突（10000 > floor）；全量 dx 命令输出测试（service_commands/doctor/remote/behavior 等）全绿（floor 只放宽窄环境、只让更多子串完整，不破坏断言）。

---

## Codex spec 评审闭环（v2，2026-07-19）

Codex（gpt-5.4）逐项挑战，2 HIGH + 5 MED + 3 LOW 全处理：

| Finding | Sev | 处理 |
|---------|-----|------|
| 项2 spawn_error→SKIPPED 漏报「起不来的失败」 | HIGH | **接受**：通知面扩到 spawn_error skip 路径（异常失败），良性 SKIPPED 仍静默 |
| 项2 HIGH 在 quiet hours 被 discard→深夜失败既不推也不入桶 | HIGH | **接受**：加 `record_when_filtered` 参数，cron 失败 quiet 内仍进全局收件箱（次日 Web 可发现），不推 channel 不吵醒；不升 CRITICAL |
| 项3② `steps-=1` 不回滚 tracker.steps→off-by-one 仍被 step limit 打死 | HIGH | **接受**：429 continue 前同步回滚 `tracker.steps`（Codex 实证 :145 check 读 tracker.steps） |
| 项1 删 `toolset` 字段=plugin/公共 schema 契约漂移（quickstart+plugin 测试） | MED | **接受**：不删 toolset 字段，收窄到只删无契约的死配置子系统（yaml+resolver+VALID_ENTRYPOINTS+wrapper）+ 标注 |
| 项2 幂等键用随机 event_id→反复失败刷屏 | MED | **接受**：改用 run_id（F127/F111）/日期（F102）稳定键 |
| 项3① required_fields 全空→乱调工具也过关 | MED | **接受**：收紧 `tool_name_contains:"read"` |
| 项3② 独立 `_RATE_LIMIT_MAX_RETRIES` 与 max_attempts 两套预算 | MED | **接受**：复用 `retry_policy.max_attempts` 作 429 上界 |
| 项3② 成功清零位置不精确 | MED | **接受**：紧接 generate() 成功即清零（不等整步走完） |
| 项1 `entrypoints` 标「纯展示」措辞乱 schema 语义 | LOW | **接受**：标注改「registry entrypoint 标签 contract；退役的是 toolsets.yaml 配置层」 |
| 项3① README+JSON 与 001 重叠 + tier1 不验 JSON 结构 | MED→归 LOW 留档 | **部分接受**：收紧读工具断言；「结构化输出不被 tier1 event-scorer 验证」是小域固有 + scorer 设计，留档不扩 scorer（超 scope） |
| 项5 floor 非根治 + box 溢出审美假设无锚点 | LOW×2 | **接受**：留档「可读下限非永不折断」+ 已 worktree 实测锚定（非纯审美） |

## 执行约束
- worktree 验证：**禁 uv sync**；PYTHONPATH 锁本 worktree 8 packages src + gateway src；`uv run --no-sync python -m pytest`；hook 陷阱防御。
- 每项独立 commit（中文，无 Co-Authored-By）；不 push origin。
- 终门：全量 `-m "not real_llm"` 0 regression vs 实测 baseline + e2e_smoke/scripted 过闸 + dx 输出测试全绿（项 5）。
- 双评审：Codex spec（本文件）→ 实施 → Codex final + Opus 对抗自审，0 HIGH。
