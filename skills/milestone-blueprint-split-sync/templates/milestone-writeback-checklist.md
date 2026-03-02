# Milestone Writeback Checklist

> 使用场景：完成里程碑拆解后，回写 `docs/blueprint.md` 前后执行一次。

## A. 基线事实确认

- [ ] 已检查 `git log --oneline -n 20 --decorate`
- [ ] 已检查 `git status --short`
- [ ] 已确认里程碑目标与 FR 映射

## B. 拆解文档质量

- [ ] 有依赖图与并行 Track
- [ ] 每个 Feature 有“目标 / 覆盖需求 / 任务拆解 / 验收标准”
- [ ] 集成 Feature 未引入新能力

## C. 调研与约束固化

- [ ] 已完成 1-3 个关键技术点调研
- [ ] 每个关键点有“合理性结论 + 调整建议”
- [ ] Must 约束已写入文档
- [ ] 设计门禁（Design Gate）已落地

## D. Blueprint 回写

- [ ] 过时状态已清理（如“进行中”“待合并”）
- [ ] 拆解文档与 blueprint 的术语一致
- [ ] 约束与验收标准已在 blueprint 反映

## E. 一致性检查命令

```bash
rg -n "待合并|进行中：|Feature 007（进行中）" docs/blueprint.md docs/m1.5-feature-split.md
rg -n "contract_version|hop_count|max_hops|幂等|watchdog|trace_id|span_id" docs/blueprint.md docs/m1.5-feature-split.md
```

## F. 交付输出

- [ ] 变更文件列表
- [ ] 每个文件关键改动说明
- [ ] 未覆盖风险与后续计划

