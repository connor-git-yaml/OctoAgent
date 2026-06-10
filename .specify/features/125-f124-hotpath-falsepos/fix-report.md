# F125 问题修复报告（诊断阶段）

> 模式：spec-driver-fix | 基线：master 167b9cf4 | 分支：feature/125-f124-hotpath-falsepos
> 来源：2026-06-08 并行合并集成 review（milestones.md「M6 并行合并集成 review 结论」F125 条目）

## 问题描述

F124（工具结果威胁扫描）合入后，集成 review 第二视角 + 真实语料实测抓出三个同源问题：

1. **HIGH 热路径阻塞**：broker `_finalize_result` 内同步调 `scan_tool_context`，近上限输出阻塞 async event loop。
2. **MED 高误报踩 SC-008**：CONTEXT pattern 对常见 web.fetch/技术文档误报率 ~89%，踩 F124 spec 自己的反"狼来了"护栏（噪声训练 LLM 忽略标注 → 真注入也被忽略）。
3. **LOW docstring 漂移**：`threat_scanner.py` scan_context docstring + `protocols.py:158` 仍写 chunk/overlap，实现已改单遍全文（FR-F2）。

## 诊断实测（本 worktree 复核，2026-06-10）

### 延迟实测（uv run，worktree venv）

| 场景 | 输入 | event loop 阻塞 |
|------|------|----------------|
| 干净大输入（最常见路径） | ~2M chars（近 `_MAX_SCAN_INPUT`） | **228ms** |
| 尾部命中（PI-001 短路） | ~2M chars | 30ms |

关键洞察：**干净输入是最坏情况**（27 条 pattern 全文跑完无短路 + O(n) 零宽字符 Python 级逐字符遍历），而干净输入恰是绝大多数 tool 结果的实际路径——最常见路径就是最慢路径。review 报的 176ms 偏保守。

### 误报实测（18 条真实风格 web.fetch 语料）

**16/18 命中（89%）**，与集成 review 实测一致。按 pattern 归因：

| Pattern | 命中语料 | 误报机制 |
|---------|---------|---------|
| CTX-C2-001 `register a node` | k8s 节点注册文档 | 分布式系统高频正常用语 |
| CTX-C2-002 `heartbeat/beacon to` | consul/调度器心跳文档 | 监控/分布式高频用语 |
| CTX-C2-004 裸框架名 | 安全新闻 ×3 + **普通英文 ×2** | `sliver`/`havoc`/`mythic` 是常见英文词（"a sliver of"、"wreak havoc"、"mythic difficulty"）|
| **PI-004**（双 scope，review 未点名，本次实测新抓） | "you are now a member/an administrator"（欢迎页）| negative lookahead 仅排除 5 个褒义词 |
| CTX-LEAK-001 `print system prompt` | LLM 安全博客 ×2 | 讨论 prompt leaking 的文章必然含该短语 |
| CTX-DEC-001 `do not tell the user` | 隐私/UX 设计文档 | 防枚举攻击类指南正常用语 |
| **CTX-HID-001**（review 未点名，本次实测新抓） | 合法 HTML 注释 ×2 | 触发词含裸 `hidden`/`system`（feature flag、TODO 注释全中）|
| CTX-RH-001 `you are now a/the` | （被 PI-004 先命中遮蔽，移除 PI-004 后接管欢迎页误报）| 无角色语义约束 |

未误报且设计已稳的：CTX-C2-003（`you must` 锚定）、CTX-RH-002/003（指令式约束）、CTX-HID 之外的 baseline 17 条。

### docstring 漂移确认

- `threat_scanner.py:551-553`（`scan_context` docstring）："带 overlap 分块扫全文" ↔ 同文件 578-581 行实现注释明确"单遍全文扫描（review FR-F2 修正）——不分块"
- `protocols.py:158`（`ContentThreatScanProtocol.scan_tool_context` docstring）："chunk 全覆盖 + degraded 兜底"

## 5-Why 根因追溯

| 层级 | 问题 | 发现 |
|------|------|------|
| Why 1 | event loop 为何被阻塞 228ms？ | async `_finalize_result` 直接同步调 `scan_tool_context`：O(n) Python 级零宽字符遍历 + 27 条 regex 全文匹配，干净大输入全 pattern 跑满无短路，CPU-bound 占住事件循环 |
| Why 2 | F124 为何没卸载到线程？ | 扫描被定位为"微秒级纯正则"（threat_scanner.py 头注释）——这是 MEMORY 路径（用户 profile 写入，KB 级短文本）的心智模型，CONTEXT 路径输入可达 2MB（差 3-4 个数量级）却沿用同步调用 |
| Why 3 | 误报为何高达 89%？ | CONTEXT pattern 从 Hermes 逐字移植，但 **Hermes 原版注释明示这些单 pattern "appears in legitimate docs"、其信号强度依赖"in combination with the other patterns"**——F124 实现为 first-hit 单 pattern 即标注，组合语义在移植中丢失，单关键词误报全暴露 |
| Why 4 | 设计缺陷为何成立？ | tool 结果语料（web.fetch 技术文档/新闻/HTML）与 memory 写入语料（用户口述事实）分布完全不同；F124 spec 的验收维度（覆盖完整性、防绕过、ReDoS-safe）没包含"event-loop 阻塞预算"与"真实语料误报率"两个 CONTEXT 量级特有维度 |
| Why 5 | 为何未被现有机制捕获？ | ①性能：无 event-loop 阻塞断言（单测只验功能正确）；②误报：SC-008 阈值锁在 9 条**作者构造**的负样本上（刻意避开 pattern 字面写法），非真实抓取语料，0% 通过给了假信心；③docstring：FR-F2 chunk→单遍是 review 中途改的，改了实现处注释但漏了函数 docstring 与跨包 protocols.py |

**Root Cause**: F124 把为 MEMORY 短文本设计的同步扫描心智模型（微秒级假设、单关键词 pattern、构造式小负样本集）直接外推到 CONTEXT 大文本路径；Hermes 移植时丢失"多 pattern 组合才是强信号"的隐含设计语义。
**Root Cause Chain**: 阻塞/误报 → 同步调用 + 单关键词 first-hit → MEMORY 心智模型外推 → spec 缺 CONTEXT 量级验收维度（阻塞预算/真实语料）→ 测试用构造负样本自证通过。

## 影响范围扫描

### 同源问题（需同步修复）

| 文件 | 位置 | 模式 | 修复动作 |
|------|------|------|----------|
| `packages/tooling/src/octoagent/tooling/broker.py` | L637-693 `_finalize_result` | async 内同步 CPU 扫描 | 纯 CPU 收集块抽 sync helper，整体 `await asyncio.to_thread(...)`（与 L519 sync handler 卸载同范式）|
| `apps/gateway/src/octoagent/gateway/harness/threat_scanner.py` | L253-332 CONTEXT pattern | 单关键词 first-hit | 8 条收紧（共现约束/scope 回收，详见修复策略）|
| `apps/gateway/src/octoagent/gateway/harness/threat_scanner.py` | L551-553 docstring | chunk/overlap 漂移 | 改单遍全文表述 |
| `packages/tooling/src/octoagent/tooling/protocols.py` | L158 docstring | chunk 漂移 | 改单遍全文表述 |
| `apps/gateway/tests/harness/test_tool_result_threat_scan_false_positive.py` | 全文件 | 构造式 9 条负样本 | 扩 ≥30 条真实风格语料 + 阈值锁死 + per-pattern 正样本对照 |

### 类似模式（已评估）

| 文件 | 位置 | 模式 | 评估结果 |
|------|------|------|----------|
| `apps/gateway/src/octoagent/gateway/services/agent_context.py` | L4194 research handoff 重扫 | 同步调 `scan_tool_context` | **[安全]** sync 方法（非 async 上下文）+ 输入截断有界（1200+1800+600 ≈ 4KB → <1ms）；pattern 收紧自动惠及该路径，不动调用方式 |
| `apps/gateway/src/octoagent/gateway/services/policy.py` | L40 MEMORY 拦截入口 | 同步 `scan_memory` | **[安全]** 拦截语义必须同步等结果才能决定 block；profile 写入文本 KB 级；MEMORY 17 条 baseline 冻结零回归不变量，不动 |
| `apps/gateway/src/octoagent/gateway/harness/octo_harness.py` | L525 装配点 | 注入 broker | **[安全]** 纯装配，无需变更 |

### 同步更新清单

- 调用方：无签名变更（`_finalize_result` 内部实现改动；`ContentThreatScanProtocol` 接口不变）
- 测试：false_positive 扩充（≥30 负样本 + per-pattern 正样本）；新增 event-loop 非阻塞测试（单元级 to_thread 断言 + 集成级心跳停顿断言）；MEMORY 零回归显式断言（PI-004 scope 回收后 MEMORY 行为不变）
- 文档：threat_scanner.py / protocols.py docstring；F124 spec 不改（SC-008 阈值语义"≤ plan 设定阈值"兼容新锁定值）

## 修复策略

### 方案 A（推荐）：to_thread 整体卸载 + 共现约束收紧 + docstring 对齐

**①热路径卸载**（broker.py）：
- 把 `_finalize_result` 内"扫描 + 去重 + bounded hash"纯 CPU 块抽成模块级 sync helper，单次 `await asyncio.to_thread(helper, ...)`（一次线程往返做完 output/raw/error 全部收集，避免 3 次往返）。
- 语义不变量逐条保持：永不 block（卸载只是换执行线程，仍 await 结果后 finalize）；fail-open（try/except 包住 await，线程内异常传播回来走现有 fail-open 分支）；不改 raw；finding 挂 result + emit 留在 event loop。
- 无条件卸载（不做大小分流）：to_thread 调度开销 ~0.1-0.5ms，对单次 tool call 总延迟不可感知；分流引入新常量 + 双路径测试负担。与 broker.py:519 现有 `asyncio.to_thread(handler, **args)` 同范式。

**②pattern 收紧**（threat_scanner.py，8 条）——把 Hermes "组合才是强信号" 语义落实进单 pattern 内（共现约束，全部保持有界量词 ReDoS-safe）：

| Pattern | 收紧方式 |
|---------|---------|
| CTX-C2-001 | `register as a node`（as 必选）+ C2 名词（beacon/c2/implant/botnet/swarm）有界窗口共现 |
| CTX-C2-002 | heartbeat/beacon/check-in to + C2 名词共现或第二人称指令前缀 |
| CTX-C2-004 | 强词（cobalt strike/metasploit/brainworm）须伴命令动词（run/launch/execute/deploy/use/install）；弱词（sliver/havoc/mythic，普通英文词）须 + C2 名词（c2/framework/implant/beacon/listener/payload）共现 |
| CTX-RH-001 | `you are now a/an/the` + AI 身份/越狱词（assistant/agent/AI/bot/model/persona/character/jailbroken/unrestricted...）有界窗口 |
| CTX-DEC-001 | `do not tell the user` + 自指/隐瞒语境（about this/that、that you、anything、what you）|
| CTX-LEAK-001 | print/output/reveal/repeat + `your` system/initial prompt，或 + 强化副词（verbatim/above/exactly/in full）|
| CTX-HID-001 | HTML 注释触发词从裸 `hidden/system/secret` 收紧为指令式注入组合（ignore/override + instructions/prompt、you are/must、do not tell、system prompt）|
| PI-004 | **scope 回收**：`_MEM_CTX` → MEMORY-only。CONTEXT 同语义由收紧后 CTX-RH-001 接管；MEMORY 路径行为字节级不变（scopes 字段只影响 CONTEXT 过滤）|

保持不动：CTX-C2-003 / CTX-RH-002 / CTX-RH-003（已有指令式锚定，实测未误报）+ baseline 17 条 MEMORY 行为。

**③docstring**：两处改"单遍全文（`_MAX_SCAN_INPUT` 上限 + 全 pattern 有界量词 ReDoS-safe）"。

### 方案 B（备选，仅热路径维度）：大小阈值分流

len < 16KB 同步扫（<2ms），≥ 16KB 走 to_thread。保留小输入零线程开销。
不推荐：引入新常量与双代码路径，0.3ms 级收益对 tool call 总延迟不可感知；违背"消除特殊情况"。

## 验证方案

1. **MEMORY 零回归**：现有 `test_threat_scanner.py` 全过 + PI-004 MEMORY scope 显式断言（"you are now a hacker" 仍 WARN）
2. **误报**：扩展负样本集（≥30 条真实风格，按来源分组：k8s/监控/安全新闻/普通英文/欢迎页/LLM 博客/UX 文档/release notes/HTML）收紧后命中率锁死阈值；原 9 条负样本保持 0%
3. **检出力**：每条收紧 pattern 配对应真注入正样本（promptware 指令/角色劫持/自指隐瞒/prompt 泄露/HTML 注释注入/C2 框架使用指令）
4. **热路径**：单元级（patch `asyncio.to_thread` 断言扫描走线程 + 结果等价）+ 集成级（2MB 输入 finalize 期间并发心跳协程最大停顿 < 100ms；同步实现为 228ms 可显著区分，阈值留 CI 抖动余量）
5. **全量回归**：0 regression vs 167b9cf4（master 实测 3919 passed）+ e2e_smoke 必过（不用 SKIP_E2E）

## Spec 影响

- 需要更新的 spec：**无需更新 F124 spec.md**——SC-008 原文"负样本集标注率 ≤ plan 设定阈值"语义兼容（本次把"plan 设定阈值"从构造集 0% 升级为真实语料集锁定值，属阈值实例化非语义变更）；FR-1.5/FR-2.x 不变量全部保持
- F125 本身走 fix 模式制品（本报告 + plan.md + tasks.md + verification）

## 范围检测

5 个文件（3 生产 + 2 测试），2 个模块（tooling/gateway harness）——未触发"范围过大"建议（< 10 文件 / ≤ 3 模块），fix 模式继续。
