# Feature 022 产研汇总：Backup/Restore + Export + Recovery Drill

## 输入材料

- 产品调研: `research/product-research.md`
- 技术调研: `research/tech-research.md`
- 在线补充: `research/online-research.md`
- 上游约束: `docs/blueprint.md` §5.1 FR-OPS-4 / §12.4 / `docs/m2-feature-split.md` Feature 022

## 1. 产品×技术交叉分析矩阵

| MVP 功能 | 产品优先级 | 技术可行性 | 实现复杂度 | 综合评分 | 建议 |
|---|---|---|---|---|---|
| `octo backup create` | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| `octo restore dry-run` | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| `octo export chats` | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| 最近 recovery drill 状态持久化 | P1 | 高 | 低 | ⭐⭐⭐ | 纳入 MVP |
| Web 最小 recovery 面板 | P2 | 高 | 中 | ⭐⭐ | 纳入 MVP，保持极简 |
| destructive restore apply | P2 | 中 | 高 | ⭐ | 明确排除 |
| 远程/NAS/S3 备份同步 | P3 | 中 | 高 | ⭐ | 明确排除 |

## 2. 统一结论

1. 022 的价值核心不是“有一个压缩文件”，而是“用户知道自己能不能安全恢复”。
2. 当前代码已具备稳定数据路径、CLI 主入口和 Web 状态页骨架，但缺少 backup/export/recovery 的领域模型与服务层。
3. 最小可行方案是：backup create + restore dry-run + chat export + latest recovery drill status，一起交付。
4. destructive restore apply 不应纳入 022，否则会显著扩大风险面并拖慢并行推进。
5. 备份默认不应包含明文 secrets 文件，但必须给出 sensitivity summary 和用户提示。

## 3. 方案决策

### 选型：结构化 backup service + manifest + dry-run planner（采纳）

- SQLite 使用在线 backup API 生成一致性快照
- bundle 包含 manifest / checksum / sensitivity summary
- restore 只生成 `RestorePlan`，不执行 apply
- recovery drill 结果写入状态文件并暴露给 CLI/Web
- chat export 基于 task/event/artifact 投影生成 manifest

### 不采纳方案

- 直接打包整个 `data/` 目录
- 先做 destructive restore，再补 dry-run
- 先做完整运维后台，再补 CLI 入口

## 4. MVP 范围锁定

### In

- `octo backup create`
- `octo restore dry-run`
- `octo export chats`
- `BackupBundle / RestorePlan / ExportManifest / RecoveryDrillRecord`
- `BACKUP_*` 生命周期事件
- `data/ops/latest-backup.json` 与 `data/ops/recovery-drill.json`
- Web 最小状态入口

### Out

- destructive restore apply
- 远程备份同步 / 加密管道
- Vault 完整恢复
- 完整运维后台
- Chat Import / Memory 写入联动

## 5. 风险矩阵

| 风险 | 等级 | 缓解 |
|---|---|---|
| backup 只有文件打包，没有可解释元数据 | 高 | manifest + checksum + sensitivity summary 必做 |
| 用户恢复前无法判断覆盖风险 | 高 | restore dry-run 输出结构化冲突清单 |
| bundle 含 secrets 却无提醒 | 高 | 默认排除明文 secrets 文件，并输出敏感性提示 |
| Web 入口范围膨胀成运维后台 | 中 | 仅做摘要卡片 + 导出入口 |
| 022 与 021/020 发生 schema 耦合 | 中 | chat export 只用 task/event/artifact 最小投影 |

## 6. Gate 结论

- `GATE_RESEARCH`: PASS（离线调研 + 在线调研均完成，points=3）
- `GATE_DESIGN`: READY（可进入 spec / clarify / checklist）

## 7. 执行建议

1. 先冻结 backup/export/recovery 的 domain model 和 manifest schema。
2. 先把“最近一次恢复验证结果”做成一等状态，再扩展界面。
3. 所有恢复相关能力先走 dry-run / preview 路径，禁止在本 Feature 引入默认 destructive apply。
4. chat export 先满足迁移和留档场景，不主动绑定未来 import/memory 治理。
