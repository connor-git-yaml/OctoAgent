# Technical Research: Feature 084 — Context + Harness 全栈重构

> 所有 NEEDS CLARIFICATION 项已在 spec.md 自动解决（4 个 AUTO-CLARIFIED，1 个 C1 用户决策）。
> 本文件记录 6 个技术选型决策的结论、理由和替代方案。

## D1 — SnapshotStore 持久化策略

**决策**：内存 dict 冻结（`_system_prompt_snapshot`）+ atomic rename（`tempfile.mkstemp` + `os.replace()`）+ `fcntl.flock(LOCK_EX)`

**理由**：
- Hermes Agent 生产验证此模式：session 开始时 `load_from_disk()` 一次性冻结，`format_for_system_prompt()` 始终返回冻结副本，工具写入只改磁盘和 live state，prefix cache 100% 保护
- 零新外部依赖（SC-002），100% 标准库
- 单用户系统并发写竞争极低，fcntl.flock 开销可忽略

**替代方案被拒**：
- SQLite snapshot 表：过度设计，单用户场景不需要按 session_id 查询历史快照
- mtime watch：实时轮询会在 session 中途触发系统提示刷新，直接破坏 prefix cache 保护目标

---

## D2 — Tool Registry 工具发现机制

**决策**：AST 扫描 `builtin_tools/` 目录 + module-level `registry.register()` 自注册

**理由**：
- Hermes ToolRegistry 约 450 行可直接移植（threading.RLock + snapshot 读取 + deregister 级联清理）
- entrypoints 字段（`web` / `agent_runtime` / `telegram`）直接根治 D1 断层（web 入口工具不可见）
- AST 扫描仅启动时执行一次（约 50-200ms），运行期无开销
- ToolBroker 保留外层契约检查，ToolRegistry 作为工具来源，职责分离符合 Constitution C3

**替代方案被拒**：
- Pydantic AI @tool 装饰器：与 OctoAgent PolicyGate 和 ApprovalManager 解耦困难，工具列表在 Agent 构造时固化（无热更新）
- 保留 CapabilityPack 简化：不解决 entrypoints 缺失问题，维护成本无改善

---

## D3 — Threat Scanner 实现策略

**决策**：纯正则 pattern table（≥ 15 条）+ invisible unicode 检测（`_INVISIBLE_CHARS` frozenset）

**理由**：
- Hermes `_MEMORY_THREAT_PATTERNS` 覆盖主要威胁向量（prompt injection / role hijacking / exfil / SSH backdoor / base64 payload），已生产验证
- 微秒级扫描，完全离线，Constitution C6 降级兼容
- FR-3.6 明确标注 YAGNI-移除：smart_scan（utility model 仲裁）首版不做，接口预留即可
- pattern 添加词边界（`\b`）和上下文锚点缓解 R2 误杀风险

**替代方案被拒**：
- LLM-based detection：100ms+ 延迟，依赖网络，Constitution C6 不兼容（网络不可用时无法降级）
- 专门安全库（safety / bandit）：针对 Python 包漏洞扫描，不适用于 prompt injection 检测

---

## D4 — Routine Scheduler 调度机制

**决策**：独立 `asyncio.Task`（不经 APScheduler），每 30 分钟 interval 循环

**理由**：
- 与现有 APScheduler cron jobs 完全隔离，不共享线程池，不争抢 LLM 调用窗口（R4 缓解）
- `asyncio.Task.cancel()` 精确停止语义，Constitution C7 用户可取消
- observation routine 只需固定 interval，不需要 cron 表达式，asyncio.sleep() 足够

**替代方案被拒**：
- 复用 APScheduler：Hermes cron scheduler 有 file-based lock，observation routine 进入同一 lock 域会产生不必要阻塞；两者耦合使 observation routine 测试复杂度增加

---

## D5 — USER.md 合并策略

**决策**：append-only `§` 分隔符 + add / replace / remove 三操作

**理由**：
- Hermes 生产验证：Agent 自主决定追加新 fact（add）、替换旧 fact（replace，substring 匹配 old_text）、删除过期 fact（remove）
- 零 token 成本，用户审视成本最低（每次写入都是独立 entry，diff 清晰）
- append-only 模式不需要 markdown parser，SC-002 零新依赖
- replace / remove 触发 Approval Gate two-phase 确认（Constitution C4），防止误覆盖

**替代方案被拒**：
- utility model 调和（全文重写）：不可预测，高 token 成本，Hermes 明确以 add/replace/remove 工具替代全文重写
- section upsert（markdown heading 级）：需引入 markdown-it-py，SC-002 限制；首版不做，接口预留

---

## D6 — replace/remove Two-Phase 确认交互形态

**决策**：Approval Gate 卡片（Option B），replace/remove 触发 `APPROVAL_REQUESTED` 事件，Web UI 展示含 diff 内容的审批卡片，用户点击批准后执行

**理由**（用户已确认 → Option B）：
- 复用 FR-4 已有 Approval Gate 机制，符合 Constitution C10（Policy-Driven Access 收敛到单一入口）
- 工具无需维护 pending diff 中间状态（工具无状态原则）
- Approval Gate 卡片展示结构化 diff，用户体验比 LLM 对话轮次更直观

**替代方案被拒**（Option A）：
- LLM 对话轮次内 two-phase：工具需维护 pending diff 状态（临时存储），引入额外状态管理复杂度，与"工具无状态"设计原则相悖

---

*Research 生成于 2026-04-28，作为 plan.md 技术决策的补充说明。*
