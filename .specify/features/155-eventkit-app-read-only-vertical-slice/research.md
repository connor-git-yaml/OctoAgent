# F155 Research Synthesis

## 结论

技术上可实现“系统 full access + Octo App-read-only”，但这是产品权限决策，不是
工程师可替用户默认接受的实现细节。用户已于 2026-08-01 明确选择方案 A；当前
Product Decision/Design/Tasks Gate 已通过，Implement 仍等待 F154 Verify 与 F151
architecture authority：

```text
用户明确接受系统 full access
  -> 明确用户动作打开系统权限 sheet
  -> EventKit 有限窗口一次性读取
  -> 本地最小化/预览（标题默认不上传）
  -> 当次批准
  -> F153 device proof
  -> F152 review/approved packet
  -> 单次 Agent 分析
  -> 删除
  -> 可选 Memory candidate 二次确认
```

## 已冻结的推荐方案

若用户选择保留 F155：

1. 仅 events，不读取 reminders/contacts。
2. 未来 24h/3d/7d，默认 24h、最大 7d。
3. raw EventKit object 不落盘、不上传。
4. 标题本地可见、默认不上送；当前 preview 可明确 opt-in。
5. notes/location/URL/attendees/organizer/identifier 永不进入 normalized model。
6. 所有 calendar 写 capability、API、protocol 与 EventKit mutation symbol 为零。
7. F152/F153 Verify 已完成；F154 Verify 与 F151 authority 完成前无 production
   entitlement 或行为 RED。

## 建议

个人单用户部署已选择方案 A，并接受系统权限文字会显得比产品实际能力更宽；Octo
App/代码仍必须保持物理只读、mutation path=0。该决定不会替代上游 Verify 与独立
architecture authority，也不得通过含糊文案降低权限风险的可见性。

## 证据

- Apple 官方资料：`research/online-research.md`
- 产品边界：`research/product-research.md`
- 技术/测试边界：`research/tech-research.md`
