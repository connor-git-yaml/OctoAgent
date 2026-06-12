# F108b W8 对账清单（顺手项 + AmbientRuntime 行为变更）

> 双评审：Codex **CONDITIONAL 0H/0M/2L（已闭环）** + Opus **CONDITIONAL ACCEPT 0H/2M（=C3 制品本身）/2L**。

## C1（零变更类，commit `fix(gateway)`）

| 项 | 处置 | 双评审复核 |
|---|------|-----------|
| ① `assert len(_THREAT_PATTERNS)>=15` | → 显式 raise AssertionError 同文案（-O 安全） | Codex C：等价确认；`python -O` 实测 29 pattern 在册 |
| ② scan_context docstring | **零动作**：F125 已修（现文"单遍全文非分块"；577/681 的 chunk 提及是"为何不 chunk"正当注释） | Opus O4②确认 |
| ③ render 幂等 | **零动作（评估结论）**：全部 sink 实测从 raw/持久化渲染（provider_model_client:186/198 / persisted-turn 路径 / assembly:452），by-construction 无双渲染；加 startswith 守卫反开"伪 advisory 前缀抑制真标注"注入面 | Codex D + Opus O4③ 双席独立验证论证成立 |
| ④ 现场 new service | `DEFAULT_CONTENT_THREAT_SCAN_SERVICE` 模块级默认实例（类无 __init__ 无实例属性，无状态属实——Opus O4④ 通读确认）+ research handoff 复用（lazy import 同位） | Codex B：import 无副作用；no-bypass 双不变量 green |

## C2（显式行为变更，commit `feat(gateway)` 9839927a——F108 全程唯一）

- **变更**：AmbientRuntime 9 字段块从 Block 1 core_sections（冻结前缀中段）→ Block 2 context_sections **尾部**；块内容字节不变（Codex A：Python 缩进不进字符串产物，拼接产物逐字节一致）。
- **用户拍板四要件落实**（Opus O7 实测全过）：独立 commit ✓ / message 显式标注 ✓ / **可单独 revert**（`git revert --no-commit` 实测干净无冲突，C1/C2 同文件不同区域真正独立可 bisect）✓ / 排除出零变更矩阵 + 本 completion-report 单列 ✓。
- **位置最优性**（Opus O5 独立推理）：Block 2 尾 > Block 3 头（后者把运行时环境元数据混进会话历史 = 概念泄漏；turn 内缓存差异仅一个 ~120-token 块量级）> 降粒度（有 LLM 可见语义损失）。
- **budget 安全**（Codex A）：`_fit_prompt_budget` 不按 context_sections 尾部截断 AmbientRuntime；Block 2 恒非空前提成立（bootstrap_block_content 无条件 append）。
- **测试透明性**：既有断言均 joined 文本级 → C2 透明通过；**新增块级锚**（Codex F2 闭环）：Block 1 不含 / 后续 block 完整保留。

## 双评审 finding 闭环表

| Finding | 处置 |
|---------|------|
| Codex F1（LOW）：no-bypass `_SINKS` 指 stale agent_context.py（F113 拆分残留漂移）+ 泛化 marker | **已修**：表项 → agent_context_prompt_assembly.py + 完整函数名（test commit） |
| Codex F2（LOW）：C2 缺块级测试 | **已补**：集成测试块级断言 |
| Opus O1（MED）：C3 收口制品 | 本清单 + spec.md + completion-report（本 commit 链） |
| Opus O2（MED）：living-docs 漂移闸 | **已增补**：harness-and-context.md §2.8 新增 context-assembly 侧 prefix-cache 不变量 + AmbientRuntime Block 2 归属 |
| Opus O3（LOW）：三处 ContentThreatScanService 实例并存 | 记账：harness:633 是 DI 根（合法）；policy.py:40 私有单例与新 default 语义重复，**后续可收敛**（非阻断，C10 不变量不依赖实例个数） |
| Opus O6（LOW）：cap_pack bootstrap 模板 `{{current_datetime_local}}` 秒级占位符是另一条注入路径 | 记账：**prefix-cache 完整性后续核查项**（若该投影进冻结前缀会部分抵消 C2 收益；本 wave 超 scope 不动） |

## 验证

- 全量门：**4134 passed + 1 已知 flaky**（test_sc3_projection，F083 race 家族——W5 同一个，隔离 ×3 复跑全过 1.5s/次；触发条件 = 全量与双评审 agent 同机抢 CPU）**= 等效全绿**；评审闭环测试修复后焦点 no-bypass 19 + 集成 43 passed
- Opus 加跑：e2e_smoke 8/8 / threat+security harness 123 passed / C10+no-bypass 40 passed / assembly+ambient 40 passed
- e2e_smoke：每 commit hook 自动

**0 HIGH 残留。**
