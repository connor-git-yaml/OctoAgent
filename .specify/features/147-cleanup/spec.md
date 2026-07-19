# F147 清扫篮（M10 收官 · S-M）

> 状态：收窄 spec（取证完成，待 Codex spec 评审 → 实施）
> 分支：`feature/147-cleanup`（基于 origin/master c17c9ebc）
> 归属：M10 第二波，收官型清扫。5 个不相关小项，每项独立取证 / 实施 / 归档。
> 红线：命中「无需改」诚实写结论归档，不为改而改；生产代码最小改 + 测试锚定 + 0 regression；**不 push origin**。

---

## 总览：5 项取证结论 → 改/不改

| # | 项 | 取证结论 | 决策 | 触碰生产码 |
|---|----|---------|------|-----------|
| 1 | toolsets 死配置（F-CLEAN-1） | `toolsets.yaml` + `toolset_resolver.py` 整链 **0 生产消费方**（F135 已留档「历史冗余，超范围未清」）；`ToolEntry.toolset` 字段 0 生产读 | **改**：删死配置子系统 + 死字段；`entrypoints`（活 metadata）保留+标注 | 是（删除为主，无行为变更） |
| 2 | cron 后台失败通知 | 三姊妹 FAILED 路径**只写审计事件 + log，零通知**（现状是 F127/F111 显式「失败静默」的设计，被 M10 里程碑决定翻转） | **改**：FAILED 补 HIGH 通知（SKIPPED 保持静默） | 是（新增通知调用） |
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

### 决策：改（删死配置子系统 + 死字段，保留活 metadata）
- **删**：① `octoagent/apps/gateway/toolsets.yaml` ② `octoagent/apps/gateway/src/octoagent/gateway/harness/toolset_resolver.py`（整模块）③ `VALID_ENTRYPOINTS` 常量 ④ `builtin_tools/__init__.py` 的 `list_for_entrypoint` wrapper ⑤ `ToolEntry.toolset` 字段 + 全部 `toolset=` kwargs + 2 处断言测试。
- **保留 + 标注**：`ToolEntry.entrypoints`（活 metadata）+ `ToolRegistry.list_for_entrypoint`（读活字段 + 有真测试）；在 `ToolEntry.entrypoints` docstring 注明「entrypoint 过滤链（toolsets.yaml）已退役，本字段仅作 capability_pack 展示 metadata」。
- **理由**：`toolset` 字段 0 消费 = 真死（宪法「去功能直接删」）；`entrypoints` 有活读者 = 保留（「有疑虑保留+标注」）。删死字段避免留 orphan `toolset=` kwargs（pydantic extra=ignore 会静默吞，比留字段更脏）。

### 验证
- 全量 0 regression；`toolset_resolver` 测试若存在一并删（测死代码）。删 `toolset` 后所有 `ToolEntry(...)` 构造无 `toolset=`（grep 归零）。

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

### 决策：改（FAILED 补 HIGH 通知；SKIPPED 保持静默）
- 在三个 **genuine FAILED** 路径补一条 `priority=HIGH` 的 `notify_task_state_change`（不动 SKIPPED——那是 F127/F111 刻意的 capacity 优雅降级，非失败）。
- 参数：`session_id=""`（全局收件箱桶，可经 Web 查 + F116 落盘 rehydrate）；`state_transition_event_id` = FAILED 事件 event_id（幂等：同一失败事件不双发）；`channels`=各 service 成功通知同款（F102=`config.summary_channels`；F127/F111=None 全推，同 pending-review）；payload 只含计数/错误摘要（无敏感原文，PII 惯例）。
- try/except 包裹通知调用（#6 降级：通知失败不得让 cron 崩）；`_notification_service is None` 守卫（F127/F111 Optional）。
- 更新 `memory_consolidation.py:703-704` 等 stale 注释为新语义（FAILED → 发 HIGH；SKIPPED → 不发）。
- **quiet hours 决策留档（供用户拍板）**：按里程碑用「HIGH 级」。后果=F127/F111 深夜失败在 quiet hours 内被过滤（审计留痕但 channel 不推），F102（用户配置的 summary_time，可能在 active hours）与手动触发能真达。**未做**「失败豁免 quiet hours / 升 CRITICAL」——那会吵醒用户且超「HIGH 级」范围。若要夜间失败必达，是独立 follow-up 决策，归总报告会 flag。

### 验证 / 测试锚
- 单测：三 service FAILED 路径断言 `notify_task_state_change` 被以 `priority=HIGH` 调用一次（用现有 service 测试 fixture + Mock notification_service）。
- 单测：SKIPPED 路径断言**不**调 notify（回归护栏，别误伤优雅降级）。
- 单测：`_notification_service is None` 时 FAILED 不炸（降级）。

---

## 项 3①：T1-TOOL-CALL-003 断言收敛

### 取证（实测）
- Task `benchmarks/tiers/tier1/t1_tool_call_003.yaml`：prompt「请查看系统当前时间，并以 JSON 格式返回 `{current_time, timezone}`」，`expected_events` 硬要 `TOOL_CALL_STARTED`+`TOOL_CALL_COMPLETED`+`MODEL_CALL_COMPLETED`。
- Scorer（`benchmarks/runner/scorer.py:182-321`）按 event_store 命中率判 PASS/FAIL：模型抄答不调工具 → 仅 `MODEL_CALL_COMPLETED` 命中 → match_ratio 1/3≈0.33 → 落 `(0.0,0.5)` → **FAIL 0.0**。
- AmbientRuntime 确实注入时间：`agent_context_prompt_assembly.py:346-360` 把 `current_datetime_local`+`timezone`+`utc_offset` 拼进 prompt system 段 → 模型直接抄答是合理行为，撞断言。归因散文在 `milestones.md:574`。

### 决策：改（选 option b：prompt 确定性需要工具）
- **不选 option a（放松断言接受 ambient 路径）**：003 属 tool_call **域**（三 task 都验证工具调用能力），放松断言会让 003 不再测工具调用，把 tool_call 域回归护栏从 3 task 削到 2——弱化护栏。
- **选 option b，但用确定性诱导**：把 prompt 从「查时间」改为「用工具读 README.md 并结构化 JSON 输出」——模型**无法**从 ambient 抄答（文件内容/行数不在 prompt），必须调 filesystem 读工具，`TOOL_CALL_STARTED/COMPLETED` 必然产生；保留 003 的「工具调用 + 结构化输出（JSON）」域意图（001 是读+总结，003 是读+结构化，区分特征=JSON 输出要求）。比「明示用工具查时间」更确定（弱控变量 model 可能仍抄 ambient 时间，文件读则provably 需工具）。
- 具体 prompt（拟）：读 README.md 返回 `{"file_exists": bool, "line_count": int, "first_heading": str}`；`expected_events` 保持 TOOL_CALL_STARTED/COMPLETED + MODEL_CALL_COMPLETED；`required_fields` 保持 `{}`（任意读工具）。更新注释同步。

### 验证
- 结构层：yaml 合法可被 runner 加载（跑 benchmark task loader 单测若有 / 或 yaml.safe_load 校验）。不跑真 benchmark（需真 model + $，超 F147 scope）。

---

## 项 3②：runner.py 429 退避跳出步数预算

### 取证（实测，`skills/runner.py`）
- 主循环 `while tracker.check_limits(limits) is None:`（:145）；`steps += 1`（:146 顶部无条件）；`attempts += 1`（:147）；`tracker.steps = steps`（:148）。
- 429 分支（:217-221）：`logger.warning` + `await asyncio.sleep(3)`（扁平，非指数，不走 `_backoff`）+ `continue` → 回 :145 → :146 `steps` 再 +1。**每次 429 扣一步 max_steps 预算**（实测 baseline 626×429 会把 steps 烧到 max_steps 提前 STEP_LIMIT_EXCEEDED 截断决策链）。债属实。
- 429 识别：`provider_client.py:98-99` 把 status 429 映射 `LLMCallError("rate_limit", retriable=True, status_code=429)`；runner `:217 exc.error_type=="rate_limit"`。`LLMCallError` 无 `retry_after` 字段（Retry-After 不解析 → 本次不引入，指数退避即可）。
- 无 rate_limit 路径测试（`skills/tests/` grep 零命中）；模板 `test_runner.py:1230/1268`（LLMCallError 分支注入 `QueueModelClient`）。`ErrorCategory` 无 RATE_LIMIT，用现有 `REPEAT_ERROR`（语义=模型调用连续失败）。

### 决策：改（指数退避 + 退避不计步数 + 独立上界）
- 引入 `_RATE_LIMIT_MAX_RETRIES`（如 6）+ `_RATE_LIMIT_BACKOFF_BASE_S`（如 2.0）+ `_RATE_LIMIT_BACKOFF_CAP_S`（如 30.0）常量。
- 429 分支改：`rate_limit_retries += 1`；若 `> _RATE_LIMIT_MAX_RETRIES` → `_terminate_with_failure(category=REPEAT_ERROR, "速率限制重试耗尽…")`（补上界，防永久 429 死循环——原来靠 max_steps 兜底，现解耦须新上界）；否则**指数退避** `min(base * 2**(retries-1), cap)` + **`steps -= 1`（撤销本轮步增，退避不吃步数）** + `continue`。
- `rate_limit_retries` 在**成功一轮 generate 后清零**（隔离的 429 每次拿满重试额度）。
- 退避 sleep 抽 `_rate_limit_backoff(retries)` 辅助（可测；测试 monkeypatch `asyncio.sleep` 记录 wait 序列断言指数）。
- **不动** 非 429 分支（api_error retry_failures / auth / context_overflow / conversation_state_lost 路径行为零变更）。

### 验证 / 测试锚（`test_runner.py` 新增）
- 注入 N 个 rate_limit（429）后接成功：断言 `result.steps` 只反映**成功步**（不含 N 次退避）→ 证退避不吃步数（当前实现此断言会 FAIL）。
- monkeypatch `asyncio.sleep` 记录 wait 序列：断言指数递增（2,4,8… 到 cap）。
- 注入 `> _RATE_LIMIT_MAX_RETRIES` 个 429：断言 FAILED + `REPEAT_ERROR` category（上界生效）。
- 回归：现有 api_error/auth 分支测试全绿。

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

### 验证 / 测试锚（`test_console_output.py` 新增）
- 新增测试：模拟窄环境（非 TTY / COLUMNS=80）经 `create_console` 渲染 `octo remote enable` 关键指引行，断言长 CJK 行**完整不折断**（子串完整在单行）。
- 回归：现有 4 个 console_output 测试全绿；`test_remote_commands.py`（注入 width=10000）不冲突（10000 > floor）；全量 dx 命令输出测试（service_commands/doctor/remote/behavior 等）全绿（floor 只放宽窄环境、只让更多子串完整，不破坏断言）。

---

## 执行约束
- worktree 验证：**禁 uv sync**；PYTHONPATH 锁本 worktree 8 packages src + gateway src；`uv run --no-sync python -m pytest`；hook 陷阱防御。
- 每项独立 commit（中文，无 Co-Authored-By）；不 push origin。
- 终门：全量 `-m "not real_llm"` 0 regression vs 实测 baseline + e2e_smoke/scripted 过闸 + dx 输出测试全绿（项 5）。
- 双评审：Codex spec（本文件）→ 实施 → Codex final + Opus 对抗自审，0 HIGH。
