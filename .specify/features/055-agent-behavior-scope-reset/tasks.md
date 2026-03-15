# Tasks

## Slice A - 作用域与目录

- [x] 扩展 `BehaviorWorkspaceScope`，从 `system/project` 升级为四层 scope
- [x] 为 `behavior/system`、`behavior/agents/<agent>`、`projects/<project>/behavior`、`projects/<project>/behavior/agents/<agent>` 建立统一路径解析
- [x] 定义默认文件归属矩阵和模板映射
- [x] 把 `MEMORY.md` 从默认 behavior 集合移除
- [x] 为新增文件类型补齐 budget / truncation / metadata contract
- [x] 定义 `project path manifest` 模型和路径注入规则
- [x] 定义 bootstrap 模板脚手架映射（shared / agent-private / project-shared）
- [x] 产出默认行为文件的初始化模板骨架合同（含用途、应写内容、禁止内容、bootstrap 落点）

## Slice B - 运行时装配

- [x] 重构 `resolve_behavior_workspace(...)`，让它按 agent runtime owner 解析 effective files
- [x] 删除 Butler 专属的文件层级例外，改成所有 Agent 统一装配
- [x] 为 effective chain 增加 scope / override / truncation / visibility 元数据
- [x] 把 project root / workspace root / data / notes / artifacts / behavior roots 注入当前 agent runtime
- [x] 产出 `project_path_manifest` 与 `effective_behavior_source_chain` 的结构化 capsule，供 handoff / subordinate continuation 复用
- [x] 产出 `storage_boundary_hints`，明确 facts / secrets / behavior / workspace 的访问边界
- [x] 补齐 runtime 单测，覆盖 shared / agent / project / project-agent 四层叠加

## Slice C - Agents 页 Behavior Center

- [x] 在 `Agents` 页增加 behavior 信息架构：Shared / Agent Private / Project Shared / Project-Agent / Effective
- [x] 在 `Agents` 页增加 Project Path Manifest 视图
- [x] 明确展示每个文件影响谁、来源在哪、是否覆盖下层
- [x] 明确展示每个文件是可直接编辑、建议 proposal/apply，还是只读
- [x] 为后续 edit / diff / apply 预留入口
- [x] 补齐 Agents 页交互与渲染测试

## Slice D - Settings 收口

- [x] 移除 `Settings` 页现有 behavior section 与相关 CLI snippet
- [x] 保留真正的 provider/runtime/memory/channels 配置
- [x] 在必要处增加“去 Agents 管理行为文件”的迁移引导
- [x] 补齐 Settings 页回归测试

## Slice E - CLI / 治理

- [x] 扩展 `octo behavior ls/show`，支持按 agent/project/project-agent 查看
- [x] 输出 canonical 路径和来源链
- [x] 输出 project/workspace/behavior roots 与关键文件路径
- [x] 输出 editability / review mode 元数据
- [x] 为 proposal/apply 的 scope-aware 后续特性保留参数位
- [x] 补齐 CLI 测试

## Slice F - Bootstrap 与用户画像初始化

- [x] 定义默认会话 Agent 的 bootstrap 访谈问题集与停止条件
- [x] 设计 bootstrap 结果到 Memory / behavior proposal / secret bindings 的路由规则
- [x] 设计 onboarding 与 behavior workspace 的衔接点，避免仍只停留在 provider/runtime 初始化
- [x] 定义默认会话 Agent 名称 / 性格偏好的落点文件（通常为 `IDENTITY.md / SOUL.md`）
- [x] 补齐 bootstrap 相关 contract / integration 测试设计

## Slice G - 文档与事实源

- [x] 更新 `docs/blueprint.md` 中的 BehaviorWorkspace 作用域定义
- [x] 更新 `docs/m4-feature-split.md` / 相关 split 文档，纳入 055
- [x] 回写 Feature 049 的边界说明，明确它只完成了“初版 behavior workspace”，未完成多 scope agent parity
- [x] 回写 055 的边界说明：behavior / memory / secrets / workspace 四种机制各自负责什么
- [x] 回写 055 的边界说明：Agent 必须拿到 `project_path_manifest`，handoff 不得裸转发原始问题
- [x] 回写 055 的边界说明：事实通过 Memory、敏感值通过 secret bindings、bootstrap 负责收集用户画像与 Agent 个性
