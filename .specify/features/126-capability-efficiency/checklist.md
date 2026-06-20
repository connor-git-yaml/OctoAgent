# F126 Capability 效率改进 — 质量检查表（checklist.md）

- **Feature ID**: F126 / Slug: capability-efficiency
- **基线**: master cd9a56c3 / worktree `F126-cap-eff`
- **性质**: 行为变更（非零变更重构），HIGH 复杂度，最高风险 = 项 2 tail eviction prefix-cache 打断
- **用途**: verify 阶段逐条机械对照。每条判据可被确定性检查（grep / pytest -k / 文档比对）。

> 检查项标注 `[P0]`=前置硬门 / `[CRIT]`=prefix-cache 关键不变量 / 其余为常规质量门。

---

## 1. 需求完整性

- [ ] 3 项 FR 覆盖全部 User Story：US-1.1↔FR-1.1~1.5 / US-2.1~2.2↔FR-2.1~2.6 / US-3.1~3.2↔FR-3.1~3.5，无孤儿 US（每条 US 至少 1 条 FR 承接）
- [ ] 全部 P1 AC 有显式 test 绑定路径：AC-1.1/1.2/1.3、AC-2.1/2.2/2.3、AC-3.1/3.2、AC-LOOP-1 紧邻处均标注 `test:` 文件::用例名
- [ ] 每条 P1 AC 绑定的 test 文件**真实存在**且 `pytest -k` 该用例 PASS（机械校验，非人脑映射）
- [ ] FR↔AC↔test 三向可追溯：解析 FR ID → AC 引用 → test 路径，无 orphan FR（声明但无 AC/test 覆盖）、无 uncovered P1 AC
- [ ] SD-1~SD-4 可机械校验：SD-1 前置门状态可查（kv-cache-probe.md 存在且结论 PASS）、SD-2/SD-3/SD-4 耦合在 tasks 中显式体现批次划分
- [ ] [NEEDS CLARIFICATION] C1~C5 在 implement 前已被 GATE_DESIGN 拍板消解，spec/plan 无残留未决标记进入编码

## 2. prefix-cache 不变量（项 2 专项，重点）

- [ ] **[P0]** AC-GATE-1 证据落点明确且已产出：`.specify/features/126-capability-efficiency/kv-cache-probe.md` 存在，含 chat/responses/anthropic **三 transport 各自结论 + 复现方法**
- [ ] **[P0]** KV-cache 实测硬门时序可证：probe.md 时间戳 / commit 顺序证明实测**先于**任何 tail-eviction 实现代码（决策 B / SD-1，未通过则项 2 代码不得开工）
- [ ] **[CRIT]** 占位确定性冻结字节级可测：`test_deterministic_frozen_placeholder` 断言同一 `tool_call_id` 多轮 compaction 后占位串**字节级一致**（非"相等近似"）
- [ ] **[CRIT]** 覆盖"多轮重复 compaction 占位不变"：连续 N 轮 compaction 后占位无可变计数 / 时间戳 / 每轮重算结果（FR-2.2 反例断言）
- [ ] **[CRIT]** 覆盖"不改写中段"：`test_no_mid_history_rewrite` 断言 eviction 只折叠旧 tool 结果、中段非折叠内容字节不变、配对 assistant tool_call 不动
- [ ] **[CRIT]** 覆盖 resume 配对：`test_resume_pairing_intact` 断言折叠后 history 进 checkpoint + resume 重建已折叠版本、无 `conversation_state_lost`、tool_call/tool_result 配对不错位
- [ ] **[CRIT]** AC-GATE-1 实测结论已转化为确定性回归断言：`test_placeholder_does_not_break_prefix` 把实测结论固化为可重跑断言（非仅一次性 probe）
- [ ] **[CRIT]** 与 F108 W8 system 折叠层边界已声明：FR-2.3 验证 eviction **不触及** `_merge_system_messages_to_front`（provider_client.py:113）与 AmbientRuntime Block 2 尾，且不变量表述已与 `harness-and-context.md:172-178` 合并

## 3. Constitution 合规

- [ ] **#2 Everything is an Event**：校验拒绝 emit `TOOL_CALL_FAILED`、折叠 / read-back / per-turn 预算触发均 emit 事件（逐路径 grep emit 点 + 事件 schema 校验）
- [ ] **#3 Tools are Contracts**：项 1 校验源 = LLM 看到的同一份 `parameters_json_schema`（models.py:144），`test_validation_uses_same_schema_source` 断言取自同一来源，未另建事实源
- [ ] **#9 Agent Autonomy**：read-back / per-turn 预算是机制（按 token/归属/阈值客观判定），非硬编码关键词或规则替代 LLM 决策（grep 无关键词白名单式拦截）
- [ ] **#10 Policy-Driven Access**：项 1 校验拒绝走中央权限（broker.py:370）；read-back 越权防护走中央 `check_permission`，`test_cross_task_read_denied` 断言跨 task 读被拒（非工具层私自拦截绕过统一入口）
- [ ] **#4 Two-Phase / 无新不可逆副作用**：read-back 为只读、eviction / 预算为上下文改写（可由 artifact 恢复），未引入新的不可逆写副作用
- [ ] **#5 Least Privilege**：read-back 校验 artifact 归属当前 task/scope，C5 task 隔离落点（工具层 vs store 层）已落地且测试覆盖

## 4. 回归与隔离

- [ ] **0 regression vs cd9a56c3** 验证方式明确：用 `PYTHONPATH` 锁定 worktree（防主仓 .venv symlink 假 0），**禁 `uv sync`**（见 project memory `project_worktree_venv_symlink`）
- [ ] 全量回归数对照 cd9a56c3 baseline 给出 passed 数 + 0 failed/0 error，并说明 deselect 情况
- [ ] `e2e_smoke` 必过（pre-commit hook 跑通，必要时记录 `SKIP_E2E=1` bypass 理由）
- [ ] 项 1 新增能力 unit 齐：`test_schema_validation_hook.py` + `test_structured_validation_feedback.py` 全 PASS
- [ ] 项 2 新增能力 unit 齐：`test_provider_model_client_tail_eviction.py`（4 用例：frozen / no-mid-rewrite / resume / does_not_break_prefix）全 PASS
- [ ] 项 3 新增能力 unit+e2e 齐：`test_artifact_read_back_tool.py` + `test_per_turn_budget_hook.py` + e2e `test_offload_placeholder_readback_loop.py` 全 PASS

## 5. 耦合与边界

- [ ] **AC-LOOP-1 端到端可验**：项 2 折叠占位的 `artifact_ref` 能被项 3 read-back 工具成功读回，e2e `test_evicted_placeholder_readable` 断言"卸载-占位-读回"闭环成立（占位非信息单向丢失）
- [ ] 项 2↔项 3 共享 `artifact_ref` 格式 + `get_artifact_content` 后端统一（SD-2），无两套占位语义
- [ ] Out-of-scope 边界守住：**不碰** gateway `ContextCompactionService`（context_compaction.py:201，tool 结果不在其 turn 序列）
- [ ] Out-of-scope 边界守住：**不重做** F108 W8 system 组装层 / AmbientRuntime Block 2
- [ ] Out-of-scope 边界守住：**不扩** `ConversationTurn` 的 `tool_call_id` 字段（B 层不做 id 级 eviction）
- [ ] Out-of-scope 边界守住：`octoagent-sdk` 独立运行面不纳入
- [ ] 批次划分符合 SD-3：项 1（批次 1，低风险先行）/ 项 2+项 3（批次 2，强耦合同批），未把强耦合两项拆成独立半成品

## 6. living-docs 漂移闸

- [ ] hooks_legacy.py:148 旧约束已推翻：该处注释「ArtifactStore 仅审计、不作为 LLM 恢复途径」已改写，`grep` 旧约束文案消失（AC-3.3）
- [ ] `docs/codebase-architecture/harness-and-context.md` 已同步：新增 artifact read-back 能力描述 + tail eviction 不变量表述（与 :172-178 合并）+ 新工具/hook 说明
- [ ] completion-report.md 含 living-docs 比对表，列出本 Feature 触碰模块的 code↔doc drift，drift 入"已知 limitations"而非留"下个 Feature 顺手清"

## 7. 双评审 panel 就绪

- [ ] 已标注命中"重大架构变更"节点（动 capability/tooling/context 层 + 项 2 命中 prefix-cache 不变量节点）
- [ ] 走 **Codex + Opus 双评审 panel**，两者分歧项显式列为"必须人裁"清单
- [ ] 双评审范围含三项确定性论证：① 确定性占位生成规则 ② 折叠单调收敛论证 ③ 与 F108 W8 system 折叠边界
- [ ] **0 HIGH 残留**（含 re-review：大改 commit 后再 review，参照 F099 三轮收敛先例，至少 2 轮收敛到 0 HIGH）
- [ ] LLM judge 分歧配确定性打底：双评审结论由测试 / 类型 / probe 实测证据支撑，非纯主观"是否满足 spec"
- [ ] 交付物齐：`completion-report.md`（对照 Phase 列表标"实际做 vs 计划"+ Phase 跳过显式归档）+ `handoff.md`，不主动 push 等用户拍板

---

**检查项总数：47**
（需求完整性 6 / prefix-cache 专项 9 / Constitution 6 / 回归隔离 6 / 耦合边界 8 / living-docs 3 / 双评审 9）
