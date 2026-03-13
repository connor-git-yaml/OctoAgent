# Data Model: Frontend Workbench Architecture Renewal

## 1. WorkbenchSurface

表示前端一级 surface，用来约束 IA 与组件职责。

### 字段

- `surface_id`: `home | chat | work | agents | settings | memory | advanced`
- `surface_kind`: `daily | advanced`
- `primary_jobs`: string[]
- `allowed_content_levels`: `operational | explanatory | diagnostic`
- `entry_route`: string

### 规则

- `advanced` 是唯一允许默认展示 `diagnostic` 内容的 surface
- `daily` surface 只能将 diagnostic 内容作为次级入口或折叠信息出现

## 2. ResourceQueryDescriptor

描述某个 control-plane resource 在前端如何被获取、缓存和失效。

### 字段

- `resource_type`: canonical resource type
- `query_key`: string[]
- `fetch_mode`: `snapshot | resource-only | detail`
- `invalidate_on_actions`: string[]
- `degraded_fallback`: `reuse-last-good | empty-doc | full-snapshot-refetch`

## 3. WorkbenchActionMutation

统一描述前端 action 提交后的处理方式。

### 字段

- `action_id`: string
- `request_builder`: function reference
- `success_invalidation`: string[]
- `success_resource_refs`: `auto | explicit`
- `error_surface`: `inline-banner | toast | modal`

## 4. DomainModule

表示一个前端业务域模块。

### 字段

- `domain_id`: `home | chat | work | agents | settings | memory | advanced`
- `owned_routes`: string[]
- `owned_resources`: string[]
- `owned_patterns`: string[]
- `legacy_sources`: string[]

### 规则

- 一个页面主组件只可组合多个 `DomainModule` 暴露的 section/pattern
- 不允许跨域直接消费未封装的内部 local draft-state

## 5. DesignTokenSet

定义工作台视觉基础。

### 字段

- `color_tokens`
- `typography_tokens`
- `spacing_tokens`
- `border_tokens`
- `state_tokens`
- `elevation_tokens`
- `motion_tokens`

### 规则

- `warning / error / degraded / ready / muted` 必须有统一状态 token
- inspector、table、form、list row 必须从 shared primitive/pattern 派生

## 6. ContractArtifact

表示从后端 canonical model 派生到前端的契约产物。

### 字段

- `artifact_id`
- `source_model`
- `generated_path`
- `resource_types`
- `action_types`
- `schema_version`

### 规则

- `resource_type`、关键 `ActionRequest/ActionResult` 结构必须可追溯到 `ContractArtifact`
- 前端手写扩展字段必须与生成字段显式分层

## 7. GoldenPathScenario

表示工作台关键路径回归测试对象。

### 字段

- `scenario_id`
- `surface_path`
- `preconditions`
- `user_steps`
- `expected_resources`
- `expected_ui_states`

### 典型场景

- `home-readiness`
- `settings-provider-management`
- `agent-selection-and-inspector`
- `chat-start-and-stream`
- `work-review`

