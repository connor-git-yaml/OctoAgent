---
name: milestone-blueprint-split-sync
description: 面向 OctoAgent 的通用里程碑拆解与蓝图回写流程 Skill。用于将“blueprint 需求提取 -> milestone feature 并行拆解 -> 调研复核 -> blueprint 回写 -> 一致性校验”标准化，避免文档与实现状态漂移。适用于 M1.5/M2/M3 及后续里程碑。
version: 1.0.0
author: OctoAgent
tags: [planning, feature-split, blueprint, spec-driven, milestone]
trigger_patterns:
  - "M2 拆解"
  - "M3 拆解"
  - "里程碑拆解"
  - "milestone 拆解"
  - "feature split"
  - "回写 blueprint"
  - "并行化拆解"
  - "里程碑规划"
---

# Milestone Planning -> Blueprint Writeback

## 1. 适用场景

当用户提出以下需求时使用本 Skill：

- 基于 `docs/blueprint.md` 拆解某个里程碑（如 M1.5/M2/M3）到 Feature 级任务。
- 需要参考竞品实现（OpenClaw / Agent Zero / AgentStudio）做建设性方案评估。
- 需要把拆解结果回写到 `docs/blueprint.md`，并清理过时状态。
- 需要把“调研结论 -> 设计门禁 -> 验收标准”固化到文档。

## 2. 输入与输出

输入（最少）：

- `docs/blueprint.md`
- 历史拆解文档（如 `docs/m1-feature-split.md`）
- 当前目标拆解文档（如 `docs/m2-feature-split.md`，可新建）
- 当前代码与提交状态（`git log` / `git status`）

输出（必须）：

- 里程碑拆解文档更新（Feature、依赖、并行策略、验收、门禁）
- `docs/blueprint.md` 同步回写（状态、验收口径、执行约束）
- 一致性检查结果（明确“哪些过时项被修正”）

## 3. 标准流程（Phase 0-6）

### Phase 0：建立事实基线

1. 读取目标文档与当前仓库状态，不依赖记忆。
2. 明确“已交付 / 进行中 / 未开始”真实状态，标注具体日期。
3. 抽取 Blueprint 中本里程碑的 FR 与验收条目。

推荐命令：

```bash
git log --oneline -n 20 --decorate
git status --short
rg -n "M1.5|M2|M3|Feature 00[0-9]|验收标准|FR-" docs/blueprint.md docs/*-feature-split.md
```

### Phase 1：做并行拆解（Feature 级）

1. 先定义依赖图与并行 Track，再展开单 Feature 任务。
2. 每个 Feature 必须包含：
   - 一句话目标
   - 覆盖需求（对应 FR / Blueprint 条目）
   - 任务拆解（可执行到工程任务）
   - 验收标准（可测试）
3. 集成 Feature 只做联调与验收，不引入新能力。

### Phase 2：建设性调研复核（1-3 个关键技术点）

1. 聚焦 1-3 个“决定成败”的技术点（例如：控制平面契约、Checkpoint 幂等恢复、Watchdog 可观测）。
2. 对每个技术点给出“合理 / 需调整”的结论。
3. 从调研结论提炼 Must/Should 约束。

要求：

- 结论必须映射到具体 Feature（例如 008/010/011/012）。
- 约束必须可验证，不能停留在口号层。

### Phase 3：写入设计门禁（Design Gates）

必须把高风险约束写成 gate，避免 013 集成时才暴露问题。建议至少包含：

- Contract Gate：协议版本化、跳数上限、扩展位
- Recovery Gate：重复恢复幂等、快照损坏降级
- Watchdog Gate：默认阈值、判定可复盘、trace 关联

### Phase 4：回写 Blueprint

回写时必须同步两类信息：

1. 状态事实回写：把“进行中/待合并”等过时描述改成真实状态。
2. 约束口径回写：把拆解文档中的 Must 约束和验收条目映射回里程碑段落。

禁止：

- 只改拆解文档，不改 blueprint。
- blueprint 与拆解文档使用不同术语或矛盾阈值。

### Phase 5：一致性检查

检查目标：

1. blueprint 与 split 文档的状态一致。
2. 必改约束在“任务拆解 + 验收标准 + gate”三处都可追溯。
3. 无明显过时标记残留（如“待合并”“进行中”）。

推荐命令：

```bash
rg -n "待合并|进行中：|（进行中）" docs/blueprint.md docs/*-feature-split.md
rg -n "contract_version|hop_count|max_hops|幂等|watchdog|trace_id|span_id" docs/blueprint.md docs/*-feature-split.md
```

### Phase 6：交付说明

最终输出要包含：

1. 改动了哪些文件。
2. 每个文件改动的关键点（不是泛泛描述）。
3. 哪些过时内容被修正。
4. 哪些风险仍保留到后续里程碑。

## 4. 双端兼容约定（Codex + Claude）

1. 所有流程规范写在仓库内 Skill 文件，避免平台私有漂移。
2. 通过文件路径显式引用 Skill：
   - `[$milestone-blueprint-split-sync](skills/milestone-blueprint-split-sync/SKILL.md)`
3. 仅在 `.agent-config/shared.md` 维护索引入口，再用 `./repo-scripts/sync-agent-config.sh` 同步到 `AGENTS.md` 与 `CLAUDE.md`。
4. 不直接手改 `AGENTS.md` / `CLAUDE.md` 生成文件。

## 5. 质量红线

1. 不能基于“记忆中的状态”更新 blueprint，必须以当前仓库事实为准。
2. 不能只给建议不落地文档。
3. 不能把 should-have 写成 must-have（或反过来）而不解释依据。
4. 不能缺失可验证验收标准。

## 6. 配套模板

使用本目录下模板辅助执行：

- `templates/milestone-writeback-checklist.md`
