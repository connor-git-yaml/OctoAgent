# F126 Capability 效率改进 — Trace

基线: master cd9a56c3。分支 feature/126-capability-efficiency。
模式: feature（完整编排）。preset=quality-first（全 Opus）。research_mode=codebase-scan。

## 范围（3 项 spin-out 设计输入，源 F108b completion-report §27）
1. 执行前 schema 校验 + 结构化 retry feedback（Pydantic F1）
2. tool_call_id 确定性 tail eviction（Claude Code F3）
3. artifact read-back + per-turn 预算（Hermes F3）

## 关键 gate
- GATE_DESIGN: always + HARD → 强制暂停。承载用户要求的"impact 分析后回用户决定 3 项一起做/拆分 + prefix-cache 评估"。
- GATE_TASKS / GATE_VERIFY: always（暂停）。

## 执行链路
[11:06:31] init: worktree+env ready, feature_dir created
[11:06:31] phase_1b tech_research(codebase-scan): STARTED | model=opus
[11:13:55] phase_1b tech_research: COMPLETED | artifacts=research/tech-research.md | 触碰面: 项1独立/项2+3强耦合, 最高风险=项2 prefix-cache
[11:13:55] GATE_SCOPE(pre-spec, 用户显式要求): PAUSE | 范围/排序 + prefix-cache 评估回用户拍板
[12:21:42] GATE_SCOPE: RESOLVED | 用户拍板①3项一把做(单一交付) ②项2 tail eviction 逐transport KV-cache实测=实现前硬前置门
[12:21:42] phase_2 specify: STARTED | model=opus
[12:25:49] phase_2 specify: COMPLETED | artifacts=spec.md | FR16/AC13/SD4/C5
[12:25:49] phase_3 clarify+checklist [并行/DESIGN_PREP_GROUP]: STARTED | model=opus
[12:32:19] phase_3 clarify+checklist: COMPLETED | artifacts=clarifications.md(C1-C5决议),checklist.md(47项)
[12:32:19] GATE_DESIGN (always+HARD): PAUSE | 设计状态+C2/C5 回用户拍板
[12:36:03] GATE_DESIGN: PASSED | C5=工具层+store Optional task纵深(纳入) / C2=独立artifact.read_content / C1C3C4默认锁定
[12:36:03] phase_4 plan: STARTED | model=opus
[12:40:34] phase_4 plan: COMPLETED | artifacts=plan.md | 批次序+KV-cache硬门+回滚策略
[12:40:34] phase_5 tasks: STARTED | model=opus
[12:44:06] phase_5 tasks: COMPLETED | artifacts=tasks.md | 30任务/批次序+硬门T120
[12:44:06] phase_5.5 analyze: STARTED | model=opus
[12:49:31] phase_5.5 analyze: COMPLETED | artifacts=analysis.md | PASS 0C/0H/2M/4L; M1+L1 主节点已修
[12:49:31] GATE_TASKS (always,soft): AUTO_CONTINUE | 依据 feedback_one_session_completion(仅硬门停); 范围/设计已 GATE_DESIGN 拍板
[12:49:31] phase_6 implement: STARTED | 批次1(项1)先行
[12:53:03] T000 baseline: 4047 passed/10 skipped/97 deselected/1 xfailed/1 xpassed @ cd9a56c3 (PYTHONPATH锁worktree, 126s)
[13:01:08] 批次1(项1) 实现完成: SchemaValidationHook + ToolMeta.skip_arg_validation + BeforeHookResult/ToolResult/ToolFeedbackMessage.validation_errors 透传 + runner映射 + provider_model_client结构化渲染 + octo_harness wiring
[13:01:08] 批次1 测试: 新11 passed (test_schema_validation_hook 9 + test_structured_validation_feedback 3... 实际11) / tooling+skills聚焦552 passed / e2e_smoke 8/8
[13:03:10] 批次1 全量回归: 4058 passed (=baseline 4047 + 11新) / 0 regression / e2e_smoke 8/8 / 120s
[13:03:10] CHECKPOINT: 项2 KV-cache实测硬门(T120/决策B) 需 live多transport access — 回用户; 项3 unblocked
[13:28:46] CHECKPOINT RESOLVED: ①项2硬门=用户提供live key跑真实测(到T120要key) ②现在续做项3
[13:28:46] 批次2-step1 项3: STARTED (store隔离+read-back工具+per-turn预算)
[13:37:44] 批次2-step1 项3 实现完成: store Optional task隔离(SQL WHERE,内部caller零变更) + artifact.read_content工具(字节分页,task隔离,中央权限) + per-turn预算hook(warn-only最小版,PER_TURN_BUDGET_EXCEEDED) + 10新测试 passed / e2e_smoke 8/8
[13:37:44] 决议偏离记录: AC-3.4 降为warn-only(C3 spec许可的降级,analyze M3); 聚合卸载+AC-LOOP-1闭环 推迟到 项2(KV-cache硬门后)
[13:40:01] 批次2(项1+项3) 全量回归: 4068 passed (=baseline 4047 +11批次1 +10项3) / 0 regression / e2e_smoke 8/8 / 120s
[13:40:01] living-docs: hooks_legacy.py:148 旧约束已推翻(FR-3.3); spec AC-3.4 warn-only标注
[13:40:01] 双评审 panel(Codex+Opus): STARTED (项1+项3 diff)
[13:56:36] 双评审 panel 完成: Opus 0H/2M/4L(全闭环或归档) + Codex 0H(decorator缺口已修/storage_ref非问题). 0 HIGH 残留
[13:56:36] 评审修复: skip_arg_validation decorator+reflection接通(Codex) + read-back空task守卫/弱断言收紧/CJK注记/broker e2e(Opus L1-L4); M1工具层比对归档(store SQL即权威比对点) + M2 harness-and-context.md已同步
[13:56:36] 最终全量回归: 4071 passed (=baseline 4047 +24 F126新测试) / 0 regression / e2e_smoke 8/8
[13:56:36] STATUS: 项1+项3 完成+双评审; 项2 tail eviction BLOCKED on KV-cache实测硬门(等用户 live key); 不push等拍板
[13:58:56] 本地提交 7973baf7（未 push，等用户拍板）。项1+项3 闭环；项2 等用户 live key 跑 T120 KV-cache 实测硬门
[16:37:41] T120 KV-cache 实测(用户选①): chat transport 实测 PASS(DeepSeek 真数据 R1-R4) — 改写旧消息其前前缀保活/其后失效一次, 确定性占位 R4 回升95.5% 单调收敛, token -56%. 反证 C4 占位冻结必要性
[16:37:41] transport 覆盖缺口: OPENAI_API_KEY 实为JWT(api.openai.com 401), 无 ANTHROPIC key → responses/anthropic 仅文档语义推断未实测. GATE 决议回用户(选项A实测1/3+文档2/3 vs 选项B补key 3/3)
[17:34:52] T120 补测(用户给 SiliconFlow/Gemini/OpenRouter key): OpenRouter→Claude 区域封锁403 / Gemini cached_tokens恒0(shim不透传) / 无native OpenAI+Anthropic key → 3/3 native实测结构性不可行. 最好2/3(DeepSeek chat ✅ + codex-OAuth responses 需用户OK ToS灰区一次性). anthropic引擎用户渠道完全摸不到. 安全:用户明文贴3 key,提醒轮换
[17:40:44] T120 responses 实测(codex OAuth, 用户拍板): cached_tokens 可观测 + 折叠版 R4 79% 命中 → cache-compatible PASS. 插桩已 git checkout 还原(0残留)
[17:40:44] AC-GATE-1 判 PASS: chat(DeepSeek)实测 + responses(codex)实测 2/3 + anthropic 文档机制(结构不可达). 解锁 项2
[17:40:44] phase 项2 tail eviction: STARTED (落 _maybe_compact_history)
[18:00:17] 项2 实现完成: _maybe_compact_history 确定性tail eviction + _fold_meta sidecar + TOOL_RESULT_EVICTED + octo_harness wiring. 7测试 passed / 全量4078 / e2e_smoke 8/8
[18:00:17] 项2 双评审: Opus 0H/2M/3L + Codex 1H/3M. 分歧人裁: Codex resume HIGH 降MED(采纳Opus: 内存态baseline行为非项2回归, 修AC-2.3措辞). Codex 3MED全闭环(normalize_ref占位边界/token估算纳tool_calls/原始→折叠前标签). 0 HIGH残留
[18:04:04] F126 三项全完成: 2 commits 本地未push(7973baf7 项1+项3 / ff5da64e 项2). 最终回归 4079 passed(+32新测试) 0 regression / e2e_smoke 8/8 / 双评审 0 HIGH. 等用户拍板 push
