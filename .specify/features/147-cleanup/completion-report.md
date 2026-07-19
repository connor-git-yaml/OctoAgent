# F147 清扫篮 — Completion Report

> 分支 `feature/147-cleanup`（基于 origin/master c17c9ebc）。M10 第二波收官。**未 push，等用户拍板。**
> 终门：全量确定性 `-m "not real_llm"` **5419 passed / 0 failed**（baseline 5409 + 10 新测试，0 regression）；e2e_smoke 26 passed（每 commit hook）；0 净新增 ruff 错。

## 5 项：取证结论 → 实际改动/不改

| # | 项 | 取证结论 | 实际做了 | commit |
|---|----|---------|---------|--------|
| 1 | toolsets 死配置（F-CLEAN-1）| `toolsets.yaml`+`toolset_resolver.py` 整链 0 生产消费方；`toolset` 字段经 Codex 查=plugin 公共 schema | **改（收窄）**：删 toolsets.yaml + toolset_resolver.py + VALID_ENTRYPOINTS + list_for_entrypoint wrapper（4 无契约死项）；toolset/entrypoints 字段 + list_for_entrypoint 方法保留+标注 | c16ebe25 |
| 2 | cron 失败通知 | 三姊妹 FAILED + spawn_error 路径只写审计+log，零通知（F127/F111 显式静默设计，里程碑翻转）| **改**：genuine 失败（FAILED + spawn_error）补 HIGH；notification.py +record_when_filtered（深夜入桶可发现）；幂等键 run_id/date；manual notify=False 不推 | d698f3ef |
| 3① | T1-TOOL-CALL-003 | AmbientRuntime 注入时间→模型抄答不调工具→撞断言 FAIL | **改**：prompt 改读 README+JSON（确定性需读工具）+ 收紧 tool_name_contains:read | af1a6223 |
| 3② | runner.py 429 退避吃步数 | `while` 循环 steps 顶部无条件+1，429 扁平 sleep+continue 烧 max_steps | **改**：指数退避 + 回滚 steps/tracker.steps（不吃预算）+ 上界复用 max_attempts + 成功清零 | 9c04410b (+ e501 拆行) |
| 4 | 容器交付评估 | 单用户 + launchd(F129)/Tailscale(F130) 已覆盖崩溃自愈/开机自启/远程 | **不实施**：deployment-and-ops.md §12.1.5 归档「不做容器」结论 + 触发重评条件 | 732f291b |
| 5 | console_output 窄终端 | create_console 无显式 width + 模块单例 import 时锁死（实测非 TTY=80）→ 长 CJK 指引折断 | **改**：探测 width<120 给 floor（覆盖非 TTY + 真 80 列 SSH）；option a 漏真 TTY 故选 b | a70ee92f |

## 生产代码触碰（最小改 + 测试锚 + 0 regression）

- **项3② `skills/runner.py`**（决策环）：429 分支重写 + `_rate_limit_backoff` 辅助 + `_RATE_LIMIT_BACKOFF_CAP_MS` 常量 + 计数器 init/reset。4 新测试（退避不吃步数含 tracker.steps 回滚 HIGH / 指数序列 / 上界 / 成功清零）。
- **项2 `notification.py`**（共享服务）：+`record_when_filtered: bool = False`（默认保所有现有 caller 行为）+ filtered 分支 record。1 新测试（record vs default backward-compat）。
- **项2 三 service**（daily_routine / memory_consolidation / behavior_compaction）：各加 `_notify_*_failed` 辅助（None 守卫 + try/except + HIGH + record_when_filtered + 稳定幂等键）+ FAILED/spawn_error 路径接入 + stale 注释更新。7 新/改测试。
- **项1 `tool_registry.py` + `builtin_tools/__init__.py`**：纯删除（VALID_ENTRYPOINTS + wrapper）+ 字段/方法 docstring 标注。行为零变更（584 harness/tools/services 测试 0 regression）。
- **项5 `console_output.py`**：`create_console` 探测+floor（无调用点/soft_wrap 穿透，Panel 结构不变）。254 dx 命令输出测试 0 破坏。

## 双评审闭环

### Codex spec 评审（v2，spec 阶段）
2 HIGH + 5 MED + 3 LOW 全处理（详见 spec.md §Codex spec 评审闭环）：
- HIGH：spawn_error 漏报 → 扩通知面；HIGH quiet hours discard → record_when_filtered；runner steps-=1 不回滚 tracker.steps off-by-one → 同步回滚。
- MED：toolset 字段=plugin 契约 → 不删收窄；幂等键随机 event_id → run_id/date；required_fields 空 → tool_name_contains:read；独立 429 预算 → 复用 max_attempts；清零位置 → 紧接 generate 成功。

### Codex final 评审（实现阶段）
**0 HIGH** + 2 MED + 2 LOW 全闭环（commit e0cfe1fb）：
- MED-1：cron 失败通知 helper 未传 channels → 落所有渠道，绕过 F102 summary_channels。**文档化**：失败=告警应广达（区别于 daily-summary 摘要路由），与 F127/F111 pending-review 同 None 约定，3 helper 加注。
- MED-2：003 只断言 tool_name_contains:read，"读错文件再胡答"假阳性。**+`args_summary_contains:"README.md"`**（scorer `_match_required_fields` 通用 `_contains` 支持，锁读对文件）。
- LOW-1：429 回滚 `attempts` 破坏 telemetry（真实 provider 调用数）。**只回滚 steps/tracker.steps**，attempts 保持单调。
- LOW-2：架构文档（core-design.md §8.5.7.2 / harness-and-context.md 树）仍列 toolset_resolver 现役。**同步标注退役删除**。
- Codex 补充确认：无 list_for_entrypoint/VALID_ENTRYPOINTS/toolset_resolver 残留消费方；record_when_filtered 无双写；F111 notify=False 手动路径无漏口。

### Opus 对抗自审
逐项复核（steps 不变负 / attempts 回滚不影响 telemetry / 清零异常路径不误清 / record 无双写 / spawn+pending 不双发 / manual 门控 / 循环 import 惰性 / floor 双构造无副作用）——无 HIGH；1 个自查修正=runner E501 拆行（唯一新增 ruff 错清零）。

## 已知 limitations / deferred（诚实留档）

- **项2 quiet hours**：cron 失败 HIGH 在 quiet hours 内仍不推 channel（record_when_filtered 保证入全局收件箱可次日 Web 发现，不吵醒深夜）。**未升 CRITICAL**（cron 失败不值当 3am 吵醒）。若要夜间失败**强推达**是独立产品决策（CRITICAL / quiet-hours 豁免），归总 flag。
- **项3①**：tier1 scorer event-based，不校验 JSON 结构——「结构化输出」不被主链验证（小域固有 + scorer 设计），需 tier2/rubric（超 scope）。未跑真 benchmark（需真 model + $）。
- **项5**：width floor 是「可读下限」非「永不折断」——单行 > 120（超长 home 路径）仍折；面板作者应保持单行 ≤ floor。
- **项1**：`ToolEntry.toolset`/`entrypoints` 字段 + `list_for_entrypoint` 方法保留为描述性 metadata / registry 标签契约（toolsets.yaml 配置过滤层已退役）——若未来要连字段一起收口，是独立带 plugin 契约迁移的 Feature。

## Living-docs 漂移

- `docs/blueprint/deployment-and-ops.md`：§12.1.5 新增容器评估结论（**取代** §12.1.2 Docker Compose 生产愿景——M0 蓝图与 M8 决策的既存漂移，本次归档纠正）。§12.1.2/§12.2 仍保留历史 Docker 文本作参考（非交付物，已在 §12.1.5 注明）。
- `docs/blueprint/milestones.md`：F147 ✅ + M10 收官段。
