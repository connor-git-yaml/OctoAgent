# Feature 086 — 工程债清理（Engineering Debt Cleanup）

> Baseline：commit `090b27b`（F085 完成 + Codex F43-F45 修复）
> 模式：story（轻量 spec + 每步 commit）

## 1. 背景

F084 收尾 retrospective 中标的 P2（过渡期妥协，可推迟）。F086 实际 scope 评估后比初想小：
- 部分 P2 项是 spec 设计而非真 bug（read 工具 produces_write=False 不强制 BaseModel return）
- 部分 P2 项工程量大风险高价值低（17 老工具迁 BaseModel 不做）
- F085 漏的真工程债（capability_pack thin proxy）一并清理

## 2. 改进项清单（精简后）

| # | 类别 | 问题 | 决策 |
|---|------|------|------|
| **T1** | 文档明确 | `GraphPipelineResult.detail` 字段（无 200 字符限制） | **保留**（routes/telegram.py 真消费）+ 加 docstring 说明语义边界 |
| **T2** | dead proxy 清理（F085 漏）| `capability_pack._resolve_tool_entrypoints()` 是 F084 D1 修复后留下的 thin proxy（内部从 ToolRegistry 查询）+ 1 处调用方手动 fallback | 删 proxy 函数；调用方 line 304 直接用 ToolEntry.entrypoints |
| **T3** | 显式排除（不做）| 17 老工具 `return json.dumps(...)` → 迁移 `*Result(BaseModel)` 子类 | **不做**：read 工具 `produces_write=False` 是 spec FR-2.4 设计；改造 ~3-5h + 17+ 测试断言改动，价值低风险高 |
| **T4** | 显式排除（不做）| memory.* 模块 write 用 BaseModel + 5 read 用 str（同模块双风格）| **不做**：spec 设计（write→BaseModel 强制；read→str 灵活），不算 bug |

## 3. 验收准则

| SC | 验证 |
|----|------|
| SC-086-1 | `capability_pack._resolve_tool_entrypoints` 函数 grep = 0 |
| SC-086-2 | BundledToolDefinition.entrypoints 直接来自 ToolEntry.entrypoints（不再走 proxy） |
| SC-086-3 | GraphPipelineResult.detail 字段保留 + docstring 说明 LLM context 影响 |
| SC-086-4 | 全量 ≥ 2038 passed / 0 regression |
| SC-086-5 | E2E 25/25 passed |
| SC-086-6 | Codex review 0 high finding |

## 4. 工时

| 任务 | 工时 |
|------|------|
| T1 GraphPipelineResult.detail docstring | 5min |
| T2 删 _resolve_tool_entrypoints proxy + 调用方迁移 | ~1h |
| T3 Codex review + 全量回归 | ~30min |
| **总计** | **~1.5h** |

## 5. 不做的事（明确排除避免 scope creep）

- ❌ 17 老工具迁移 BaseModel return（spec 决策 + 风险高）
- ❌ memory.* 模块统一风格（spec 设计）
- ❌ ApprovalGate 接入主 broker WARN→approval 路径（F086 不是新功能）
