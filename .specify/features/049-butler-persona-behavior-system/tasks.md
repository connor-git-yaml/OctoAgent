# Tasks - Feature 049

## T001 - 设计 Butler behavior pack

- 状态：已完成（已落地 7 个核心 markdown behavior file 的默认模板、layer 装配与 worker slice）
- 确定 7 个核心 markdown 文件及职责
- 确定 project / profile 绑定与缺失回退规则
- 明确哪些文件属于私有、哪些可转交 Worker

## T002 - 设计 clarification-first 决策框架

- 状态：已完成（已落地通用 `ClarificationDecision`，覆盖排期 / 推荐 / 比较 / 天气缺位置）
- 识别 under-specified 请求分类
- 定义 `direct / clarify / fallback / delegate-after-clarify` 判定
- 定义最大追问轮数和 fallback 规则

## T003 - 设计 runtime 装配链

- 状态：已完成（已接入 Butler runtime system blocks、control snapshot 与 worker shareable slice 摘要）
- 将 behavior pack 拆为 role / communication / solving / tool-boundary / memory-policy 逻辑层
- 定义 Butler runtime 的 effective behavior source
- 定义 Worker inheritance 的 behavior slice envelope

## T004 - 设计 behavior pack 更新治理

- 状态：进行中（本轮仅补齐 `BehaviorPatchProposal` 模型，review/apply runtime 还未接通）
- 定义 BehaviorPatchProposal 结构
- 明确提案、review、apply、audit 链路
- 区分可自动提案与默认需审批的文件类别

## T005 - 定义 acceptance matrix

- 状态：已完成（已新增 behavior/orchestrator 定向回归，覆盖缺信息先补问与 weather follow-up）
- 覆盖任务排期、推荐、比较、实时查询缺位置、预算缺失等 under-specified 请求
- 验证“先补问再答”与“fallback 明示边界”

## T006 - 文档与 blueprint/split 对齐

- 状态：待实现
- 更新 milestone split 或相关 backlog 说明
- 确保 049 与 041/042 边界清楚，不回退成 case-by-case patch
