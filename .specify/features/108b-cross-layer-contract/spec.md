# F108b — Cross-Layer Contract（跨层契约收口，W6-W8）

> 上游：program 计划 `.specify/features/108-capability-layer-refactor/`（v2 双评审闭环 + 用户 2026-06-12 三项拍板）。姊妹：F108a（W1-W5，已合入 master 4ecc74c2）。
> 基线：master ecb1f6ce（F108a 合入后）。分支 `feature/108b-cross-layer-contract`。

## 范围（= program plan W6-W8）

| Wave | 内容 | 性质 |
|------|------|------|
| W6 | `_ApprovalOverrideMemoryCache` 下沉 tooling/permission.py + living-docs 三层职责定调（§2.8） | 零变更 |
| W7 | F118 D8 typed DI：字符串 registry → 构造期 typed 对象 + 3 typed accessor + bind setter | 零变更（错误语义字节级锁定） |
| W8 | C1 F124/F125 遗留 LOW 闭环（零变更）；**C2 AmbientRuntime 挪出冻结前缀（F108 全程唯一显式行为变更，独立 commit）**；C3 收口制品 | C1 零变更 / C2 行为变更 |

## 验收标准

- **AC-1**：每 wave 全量回归 0 真回归 vs F108a 合入后基线（4130 passed 零 deselect；W7 起 +4 registry 测试 = 4134）+ e2e_smoke 8/8（每 commit hook）。
- **AC-2**：W6/W7 零变更（错误语义/迭代语义/绑定语义字节级等价，测试锁定）；**W8-C2 显式行为变更隔离**（独立 commit + 显式标注 + 可单独 revert + 排除出零变更矩阵 + completion-report 单列——用户拍板四要件）。
- **AC-3**：每 wave Codex + Opus 双评审 0 HIGH 残留，分歧人裁。
- **AC-4**：living-docs 漂移闸（harness-and-context.md / module-design.md）。

## 非目标

harness 结构调整；schema 校验/tool_call_id eviction/artifact read-back（用户拍板 spin out）；capability_pack 主文件继续压缩（需改 phase_d 测试 patch 路径）。
