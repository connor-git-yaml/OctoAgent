# Contract: Frontend Workbench Renewal

## 1. Goal

本 Feature 不以新增大规模后端 API 为核心，而是冻结一套前端消费 control-plane canonical contracts 的方式，避免后续页面继续各自拼装 fetch / refresh / action 行为。

## 2. Canonical Resource Consumption Contract

### 输入事实源

- `/api/control/snapshot`
- `/api/control/resources/*`
- `/api/control/actions`

### 前端消费约束

- 所有页面必须通过统一 query/action 层消费上述 canonical routes
- `Advanced` 不得继续维护独立 snapshot bootstrap 逻辑
- resource route 到 query key 的映射必须集中注册

## 3. Action Invalidations Contract

### 统一行为

- action 成功后，优先根据 `resource_refs` 精准失效对应 query
- 如 `resource_refs` 不足或未知，则回退到受控的 snapshot invalidation
- 页面层不再手工决定每次 action 后要刷新哪些资源

## 4. Type Sync Contract

### 目标

- 关键 resource document 与 action envelope 的前端类型来源可集中追踪
- 不再主要依赖 `types/index.ts` 手工扩容

### 约束

- 生成类型与手写 UI 扩展类型分层管理
- 生成类型更新时必须可定位源头 model 与版本

## 5. Surface Responsibility Contract

### Daily Surfaces

- `Home / Chat / Work / Agents / Settings / Memory`
- 优先展示 operational + explanatory 信息
- diagnostic 内容只允许次级展示

### Advanced Surface

- `Advanced`
- 允许展示 raw diagnostics、audit、projection、lineage
- 不得反向承担日常操作主路径

## 6. UI Primitive Contract

共享 pattern 至少覆盖：

- `PageHeader`
- `StatusCard`
- `SectionPanel`
- `CatalogList`
- `Inspector`
- `KeyValueGrid`
- `Callout`
- `EmptyState`
- `ActionBar`

任何复杂页面都应基于这些 primitive/pattern 组合，而不是继续自由拼装结构。

