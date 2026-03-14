# 产研汇总：Agent Management Simplification

**特性分支**: `codex/050-agent-management-simplification`  
**汇总日期**: 2026-03-14  
**输入**: [product-research.md](product-research.md) + [tech-research.md](tech-research.md) + 本 Feature 在线调研审计记录  
**执行者**: 主编排器

## 1. 产品 × 技术交叉矩阵

| 方案项 | 产品价值 | 技术可行性 | 实现复杂度 | 结论 |
|-------|---------|-----------|-----------|------|
| 以“当前项目 Agent 列表”替代“模板工作台”作为默认首页 | 高 | 高 | 中 | 纳入 MVP |
| 把内置 3 个 Agent 改为仅在创建流出现的模板 | 高 | 高 | 中 | 纳入 MVP |
| 把主 Agent 定义为“当前项目默认 Agent”并禁止删除 | 高 | 高 | 中 | 纳入 MVP |
| 将编辑页改成结构化选择器而非大量 textarea | 高 | 高 | 中 | 纳入 MVP |
| 将 runtime/policy/tags 等底层字段降级到高级区 | 中 | 高 | 低 | 纳入 MVP |
| 新增全新后端持久化实体替代 `worker_profiles` | 中 | 中 | 高 | 暂不纳入 MVP |
| 大规模重做 runtime / revision 系统 | 低 | 中 | 高 | 排除 |

## 2. 可行性评估

### 产品可行性

可行性高。  
用户对 `Agents` 页面真正需要的，是：

- 看到自己拥有的 Agent
- 知道哪个是主 Agent
- 创建一个新的 Agent
- 编辑和删除一个 Agent

而不是理解 `Worker 模板 / archetype / revision / runtime kinds` 这些概念。

### 技术可行性

可行性高。  
现有后端已经具备：

- `WorkerProfile.project_id`
- `Project.default_agent_profile_id`
- `origin_kind`
- clone / apply / bind_default / archive 动作

因此，050 的首版主要是产品层和 view-model 层重整，不需要先发明新的底层实体。

## 3. 推荐方案

### 3.1 产品模型

按当前项目组织：

- **主 Agent**：当前项目默认 Agent，唯一、可编辑、不可删除
- **已创建 Agent**：当前项目下的其他自定义 Agent，可编辑、可删除
- **内置模板**：只在创建流中出现，不出现在默认列表

### 3.2 页面结构

`Agents` 默认页：

1. 主 Agent 摘要卡
2. 已创建 Agent 列表
3. `新建 Agent` 主按钮

`新建 Agent`：

1. 选择模板
2. 填写名称与用途
3. 选择项目、模型、工具
4. 可选绑定能力

`编辑 Agent`：

1. 基本信息
2. 模型与工具
3. 能力绑定
4. 高级设置（折叠）

### 3.3 技术策略

- 沿用 `worker_profiles` 作为后端事实源
- 在 `domains/agents` 新建专用 adapter / view-model
- UI 层不再直接使用“模板编辑工作台”作为首屏
- 对 builtin 默认 Agent 增加“建立项目主 Agent”迁移路径

## 4. MVP 范围

### 纳入

- 当前项目主 Agent 与已创建 Agent 列表
- 模板仅在创建流可见
- 结构化编辑页
- 主 Agent 删除限制
- 项目归属显式化
- 当前默认 Agent 与聊天绑定兼容

### 排除

- 全面重做 runtime 诊断页
- 重做 `worker_profiles` 持久化结构
- 引入新的模板 marketplace 或复杂版本中心
- 让普通用户在日常路径直接管理 runtime revision 细节

## 5. 主要风险与缓解

| 风险 | 影响 | 缓解策略 |
|------|------|---------|
| 当前默认 Agent 指向 builtin，导致“主 Agent 可编辑”语义不成立 | 高 | 首次编辑时自动创建 project-scoped 主 Agent 并绑定默认 |
| 只改 UI 文案不改对象分层，后续再次泄漏底层语义 | 高 | 建立 `MainAgent / ProjectAgent / BuiltinAgentTemplate` view-model |
| 工具/能力选择器过重，反而把编辑页做复杂 | 中 | 默认页只展示必要字段，高级字段折叠；能力区分组呈现 |
| 删除非主 Agent 后影响已有聊天或 work | 中 | 删除前做确认；如存在活跃 work，则提示改为归档或延迟删除 |

## 6. 最终结论

050 适合作为独立 Feature 推进，而且应按“长期主义的产品收口”来做，而不是局部修补：

1. 先固定对象心智
2. 再重整信息架构
3. 最后对齐编辑控件和迁移边界

只要做到这三步，`Agents` 页面就能从“开发者工作台”变成“普通用户可理解的 Agent 管理中心”。

## 7. 实施后跟进建议

050 落地后，一个额外结论已经很明确：当前前端仍然需要从 `worker_profiles`、`summary.default_profile_id`、`origin_kind`、`scope/project_id` 里拼出三种产品对象：

- 主 Agent
- 已创建 Agent
- 内置模板

这说明现有 control-plane canonical 资源还偏底层。后续如果继续扩展 Agent 管理，建议补一个更贴近产品语义的 `agent-management` 资源，至少直接提供：

- 当前项目主 Agent
- 当前项目普通 Agent 列表
- 可用于创建的 builtin templates
- `can_establish_main_agent` / `main_agent_source_kind` 一类迁移提示

这样可以减少前端 adapter 复杂度，也能降低未来在 Chat / Agents / Settings 之间重复推导同一套语义的风险。
