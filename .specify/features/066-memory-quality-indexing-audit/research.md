# 技术决策研究: Feature 066 Memory 提取质量、索引利用与审计优化

**Feature**: 066-memory-quality-indexing-audit
**Date**: 2026-03-19
**Status**: Resolved

---

## Decision 1: memory.browse 实现策略

**问题**: 新增 `memory.browse` 工具应在哪一层实现？直接查 SQLite 还是经过向量后端？

**决策**: 纯 SQLite 结构化查询，不经过向量检索路径。

**理由**:
1. browse 是目录浏览操作（按 subject_key 前缀、partition 分组统计），本质是结构化查询
2. `SqliteMemoryStore.search_sor()` 已具备 scope/partition/status 过滤能力，只需新增 GROUP BY 聚合查询
3. 不走向量后端意味着 LanceDB 不可用时 browse 仍然正常工作（满足 Constitution "Degrade Gracefully"）
4. 向量检索对"列出所有 subject_key"这类查询没有语义价值

**替代方案**:
- (A) 经过向量后端的 hybrid search → 拒绝：browse 不需要语义匹配，且增加复杂度
- (B) 引入独立的 memory index service → 拒绝：过度设计，SQLite 已满足需求

---

## Decision 2: SoR 编辑走 Proposal 流程 vs 直接写入

**问题**: 用户通过 UI 编辑 SoR 时，应直接修改数据库还是走 propose-validate-commit 流程？

**决策**: 复用已有的 `propose_write → validate_proposal → commit_memory` 三步流程，新增 `source=user_edit` 元数据标记。

**理由**:
1. 满足 Constitution "Side-effect Must be Two-Phase"——编辑是不可逆操作（旧版本标记 superseded）
2. 满足 Constitution "Everything is an Event"——Proposal 流程自带审计记录
3. 满足 Constitution "记忆写入必须治理"——不绕过仲裁器
4. 代码复用最大化，不引入第二条写入路径

**替代方案**:
- (A) 直接调用 `update_sor_status` + `insert_sor` → 拒绝：绕过治理，违反 Constitution 原则 4 和 12
- (B) 新增独立的 `sor_edit_service` → 拒绝：与 Proposal 流程功能重叠，增加维护成本

---

## Decision 3: MERGE 写入操作类型设计

**问题**: MERGE（多条合并为一条）应作为新的 WriteAction 还是复用 UPDATE？

**决策**: 新增 `WriteAction.MERGE` 枚举值。

**理由**:
1. MERGE 的语义与 UPDATE 根本不同：UPDATE 是 1:1 更新，MERGE 是 N:1 合并
2. MERGE 需要在 Proposal 中携带多个 `target_memory_ids`（被合并的原始 SoR 列表），UPDATE 只有一个目标
3. 审计链中需要区分"这条记忆是更新产生的"和"这条记忆是多条合并产生的"
4. 独立的枚举值使得未来分析 consolidation 策略分布更直观

**替代方案**:
- (A) 复用 UPDATE + metadata 标记 → 拒绝：丢失了操作语义的精确性，且 N:1 和 1:1 的操作流程不同

---

## Decision 4: REPLACE 写入操作类型设计

**问题**: REPLACE（语义矛盾时的替换）应作为新的 WriteAction 还是复用 UPDATE？

**决策**: 复用 `WriteAction.UPDATE`，通过 `metadata.reason = "replace"` 标记。

**理由**:
1. REPLACE 的底层操作与 UPDATE 完全相同：supersede 旧版本 + 创建新版本，都是 1:1 关系
2. 区别仅在于触发原因（语义矛盾 vs 信息补充），用 metadata 标记即可
3. 减少枚举膨胀，降低下游消费方的适配成本

**替代方案**:
- (A) 新增 `WriteAction.REPLACE` → 拒绝：操作流程与 UPDATE 完全一致，仅原因不同，枚举值不值得

---

## Decision 5: ARCHIVED 状态 vs 软删除方案

**问题**: 归档功能应新增 `ARCHIVED` 状态还是复用 `DELETED` 状态？

**决策**: 新增 `SorStatus.ARCHIVED` 枚举值。

**理由**:
1. ARCHIVED 和 DELETED 语义明确不同：ARCHIVED 可恢复，DELETED 不可恢复
2. 归档后的记忆仍保留在存储层（证据引用链有效），只是从 recall 排除
3. 用户可在"已归档"视图中查看和恢复，DELETED 不提供此能力
4. 已有 `update_sor_status()` 方法接受任意 status 字符串，无需修改存储层

**替代方案**:
- (A) 复用 DELETED + metadata 标记 → 拒绝：混淆了可恢复和不可恢复的语义
- (B) 布尔字段 `is_archived` → 拒绝：SoR 已有 status 状态机，新增布尔字段会造成状态矛盾

---

## Decision 6: SOLUTION 分区设计

**问题**: Solution 记忆应存储在独立分区还是嵌入现有分区（如 work）？

**决策**: 新增 `MemoryPartition.SOLUTION` 枚举值，作为独立分区。

**理由**:
1. Agent 需要按 `partition="solution"` 精确筛选历史方案，独立分区查询效率最高
2. Solution 记忆有特殊的结构（problem + solution），与其他分区的纯文本 content 不同
3. browse 按 partition 分组时，Solution 作为独立类别展示，用户辨识度高
4. 参考 Agent Zero 的 `solutions_sum` area 独立存储方案

**替代方案**:
- (A) 存储在 `work` 分区 + `derived_type=solution` → 拒绝：solution 不是 derived 记忆，是 SoR 层的一等公民
- (B) 存储在 metadata 标记 → 拒绝：无法利用分区索引做高效过滤

---

## Decision 7: Solution 提取在 Consolidation Pipeline 中的位置

**问题**: Solution 记忆的检测和提取应在 Consolidation 的哪个阶段执行？

**决策**: 在 Phase 1（SoR 提取）之后、Phase 2（Derived 提取）之前，新增独立的 Solution 检测阶段。

**理由**:
1. Solution 提取需要读取刚 commit 的 SoR 来识别是否包含 problem-solution 模式
2. Solution 本身是 SoR（只是分区不同），不是 Derived 记忆，所以不应放在 Phase 2
3. 作为独立阶段便于单独开关、单独测试、单独配置模型
4. 不影响现有 Phase 2/Phase 3 的执行逻辑

**替代方案**:
- (A) 集成到 Phase 1 的同一 LLM 调用 → 拒绝：增加 Phase 1 prompt 复杂度，可能降低基础事实提取质量
- (B) 放在 Phase 2 之后 → 拒绝：Solution 不是 Derived，语义位置不对

---

## Decision 8: browse 返回格式——目录树 vs 扁平列表

**问题**: `memory.browse` 返回 subject_key 时，应按前缀构建树结构还是返回扁平列表？

**决策**: 返回按 `group_by` 参数分组的二层结构（group → items），不构建完整树。

**理由**:
1. LLM 消费扁平分组比深层树结构更可靠（减少 JSON 解析复杂度）
2. 前缀分组已满足"按 subject_key 前缀浏览"的需求（如 `prefix="家庭/"` 返回所有家庭相关条目）
3. 树结构需要额外的 schema 设计（TreeNode 递归模型），对 MVP 来说过度设计
4. 返回格式包含 `total_count` + `has_more`，支持分页

**替代方案**:
- (A) 完整树结构 → 拒绝：LLM 处理深层嵌套 JSON 不稳定，且 subject_key 层级通常只有 2-3 层
- (B) 纯扁平列表无分组 → 拒绝：无法快速获取各 partition/scope 的概览统计

---

## Decision 9: 并发编辑冲突——乐观锁实现方式

**问题**: 乐观锁应基于 SoR 的 version 字段还是引入独立的 etag/revision？

**决策**: 基于现有的 `SorRecord.version` 字段，编辑/归档请求必须携带 `expected_version`。

**理由**:
1. SoR 已有 `version: int` 字段且每次更新自增，天然适合做乐观锁
2. `WriteProposal` 已有 `expected_version` 字段，Proposal 验证阶段已检查版本匹配
3. 无需引入额外的数据结构或存储字段

**替代方案**:
- (A) 引入 etag header → 拒绝：SoR 操作不走 HTTP API（走 Control Plane action），etag 不适用
- (B) Last Write Wins → 拒绝：可能静默覆盖用户或 Agent 的修改

---

## Decision 10: memory.search 扩展参数——时间范围过滤

**问题**: 时间范围过滤应在 SQLite 层还是应用层实现？

**决策**: SQLite 层 WHERE 子句过滤，利用 `updated_at` 索引。

**理由**:
1. `memory_sor` 表的 `updated_at` 字段已存在且有索引
2. SQL WHERE 过滤效率远高于应用层过滤（减少数据传输和内存占用）
3. 与现有 `search_sor` 方法的 SQL 查询模式一致

**替代方案**:
- (A) 应用层过滤 → 拒绝：大量记忆时性能差
- (B) 新建时间索引表 → 拒绝：过度设计，现有索引已足够

---

## Decision 11: Profile 信息密度提升策略

**问题**: 如何在不破坏现有 Profile schema 的前提下支持多段详细描述？

**决策**: 修改 Profile prompt，将"1-3 句话"限制改为"多段详细描述"，输出格式从 `string | null` 改为 `string | null`（保持同类型但放宽长度限制）。

**理由**:
1. Profile 内容以 SoR content（纯文本）存储，本身无长度限制
2. 变更集中在 prompt 指令层面，不涉及数据模型变更
3. 保持 JSON 输出格式不变（每个维度仍是 string 或 null），只是内容更丰富
4. 向后兼容——旧的简洁 Profile 仍然有效

**替代方案**:
- (A) 每个维度改为数组 `list[string]` → 拒绝：改变 JSON schema 会影响下游消费方
- (B) 每个维度拆分为多条 SoR → 拒绝：增加 subject_key 膨胀，browse 时信息碎片化
