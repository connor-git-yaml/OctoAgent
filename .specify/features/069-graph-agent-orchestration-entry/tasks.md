# Feature 065: Graph Agent 感知与编排入口 — 任务分解

**日期**: 2026-03-19
**状态**: draft
**前置制品**: spec.md, plan.md, research.md, contracts/

---

## 任务总览

| Phase | 任务数 | 可并行组 | 预估复杂度总和 |
|-------|--------|----------|--------------|
| Phase 1: 核心基础 | 9 | 2 组 | 中高 |
| Phase 2: LLM 工具层 | 16 | 3 组 | 高 |
| Phase 3: Butler 路由 + Prompt 注入 | 8 | 2 组 | 中 |
| Phase 4: 管理 API + HITL | 9 | 2 组 | 中 |
| **合计** | **42** | | |

---

## Phase 1: 核心基础（PIPELINE.md 解析 + PipelineRegistry + 通用 Handler）

**阶段目标**: Pipeline 定义可被发现、解析、验证、缓存；通用 handler 就绪。

### 并行组 P1-A（数据模型 + 解析器，无外部依赖，可同时启动）

#### T-065-001: 定义 PipelineSource 枚举

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-02 AC-02 |
| **涉及文件** | 新建 `octoagent/packages/skills/src/octoagent/skills/pipeline_models.py` |
| **验收标准** | `PipelineSource` 枚举包含 BUILTIN / USER / PROJECT 三个值；与 `SkillSource` 结构对齐 |
| **复杂度** | 低 |
| **依赖** | 无 |

#### T-065-002: 定义 PipelineInputField / PipelineOutputField 模型

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-01 AC-06, FR-065-02 AC-02 |
| **涉及文件** | `pipeline_models.py`（同上） |
| **验收标准** | `PipelineInputField` 包含 type/description/required/default 字段；`PipelineOutputField` 包含 type/description 字段；Pydantic BaseModel 且可序列化为 JSON |
| **复杂度** | 低 |
| **依赖** | 无 |

#### T-065-003: 定义 PipelineManifest / PipelineListItem 模型

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-02 AC-02 |
| **涉及文件** | `pipeline_models.py`（同上） |
| **验收标准** | `PipelineManifest` 包含 pipeline_id / description / version / author / tags / trigger_hint / input_schema / output_schema / source / source_path / content / definition / raw_frontmatter / metadata 全部字段（按 contracts/pipeline-models.md）；`PipelineListItem` 为摘要投影，包含 pipeline_id / description / version / tags / trigger_hint / source / input_schema |
| **复杂度** | 低 |
| **依赖** | T-065-001, T-065-002；需导入 `SkillPipelineDefinition` |

#### T-065-004: 定义 PipelineParseError 模型

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-01 AC-05 |
| **涉及文件** | `pipeline_models.py`（同上） |
| **验收标准** | `PipelineParseError` 包含 file_path / error_type / message / details 字段；error_type 覆盖 missing_field / invalid_reference / cycle_detected / orphan_node / unsupported_version / unsupported_node_type / yaml_error / io_error |
| **复杂度** | 低 |
| **依赖** | 无 |

#### T-065-005: 实现 PIPELINE.md frontmatter 解析器

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-01 AC-01, AC-02, AC-04, AC-05, AC-06 |
| **涉及文件** | 新建 `octoagent/packages/skills/src/octoagent/skills/pipeline_registry.py` |
| **验收标准** | 复用 `split_frontmatter()` / `parse_frontmatter()` 解析 YAML frontmatter + Markdown body；必填字段（name / description / version / entry_node / nodes）缺失时返回 `PipelineParseError`；可选字段（author / tags / trigger_hint / input_schema / output_schema）缺失时使用空默认值；`version` 不以 `1.` 开头时返回版本不支持错误；`type: delegation` 时返回不支持错误；成功解析后生成 `PipelineManifest`（含 `SkillPipelineDefinition`） |
| **复杂度** | 中 |
| **依赖** | T-065-003, T-065-004 |

#### T-065-006: 实现 DAG 验证逻辑

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-01 AC-03 |
| **涉及文件** | `pipeline_registry.py`（同上，内部函数或独立验证模块） |
| **验收标准** | 验证 `entry_node` 指向已定义节点；验证所有 `next` 引用的节点存在；DFS 环检测：检测到环时返回 `PipelineParseError(error_type="cycle_detected")`，附带环路径描述；孤立节点检查：除终止节点外，每个节点必须被至少一个 `next` 引用或是 `entry_node` |
| **复杂度** | 中 |
| **依赖** | T-065-005 |

### 并行组 P1-B（Registry + Handler + 测试，依赖 P1-A）

#### T-065-007: 实现 PipelineRegistry 三级目录扫描 + 缓存 + refresh

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-02 AC-01, AC-03, AC-04, AC-05 |
| **涉及文件** | `pipeline_registry.py`（同上） |
| **验收标准** | `PipelineRegistry.__init__()` 接受 builtin_dir / user_dir / project_dir 三个可选参数；`scan()` 按 builtin → user → project 顺序扫描，高优先级同名 Pipeline 覆盖低优先级；`get(pipeline_id)` 从内存缓存获取；`list_items()` 返回 `PipelineListItem` 列表（按 pipeline_id 排序）；`refresh()` 重新扫描并更新缓存；单文件解析失败不影响其他文件，通过 structlog 记录 warning |
| **复杂度** | 中 |
| **依赖** | T-065-005, T-065-006 |

#### T-065-008: 创建内置 `echo-test` Pipeline 定义

| 属性 | 值 |
|------|-----|
| **FR 映射** | 测试用；间接验证 FR-065-01 / FR-065-02 |
| **涉及文件** | 新建 `pipelines/echo-test/PIPELINE.md` |
| **验收标准** | PIPELINE.md 格式合法，包含 2-3 个节点（passthrough 类型），entry_node 正确，无 gate 节点；可被 PipelineRegistry 正确扫描和解析 |
| **复杂度** | 低 |
| **依赖** | 无（文件编写不依赖代码，但验证需要 T-065-007） |

#### T-065-009: 实现通用 Pipeline handler

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-05 AC-04 |
| **涉及文件** | 新建 `octoagent/packages/skills/src/octoagent/skills/pipeline_handlers.py` |
| **验收标准** | 实现 4 个 handler：`terminal.exec`（终端命令执行 + 幂等性 cursor）、`approval_gate`（返回 WAITING_APPROVAL）、`input_gate`（返回 WAITING_INPUT）、`transform.passthrough`（透传）；所有 handler 实现 `PipelineNodeHandler` 协议；`terminal.exec` 支持从 state / node.metadata 获取命令；side-effect handler 遵循 cursor 幂等性约定 |
| **复杂度** | 中 |
| **依赖** | 需导入 `PipelineNodeHandler` / `PipelineNodeOutcome`（已存在于 pipeline.py） |

### 独立任务

#### T-065-010: Phase 1 单元测试

| 属性 | 值 |
|------|-----|
| **FR 映射** | 覆盖 FR-065-01 / FR-065-02 全部 AC |
| **涉及文件** | 新建 `tests/unit/skills/test_pipeline_registry.py`、`tests/unit/skills/test_pipeline_models.py`、`tests/unit/skills/test_pipeline_handlers.py` |
| **验收标准** | 解析器测试：正常 PIPELINE.md 解析、必填字段缺失、DAG 环检测、孤立节点检测、delegation 类型拒绝、版本不支持、YAML 格式错误；Registry 测试：三级目录扫描、优先级覆盖、缓存 + refresh、单文件失败隔离；Handler 测试：4 个 handler 的正常/异常路径、cursor 幂等性 |
| **复杂度** | 中 |
| **依赖** | T-065-005, T-065-006, T-065-007, T-065-009 |

### Phase 1 验收门

- [ ] PIPELINE.md 格式文件可被正确解析为 `SkillPipelineDefinition`
- [ ] 三级目录扫描 + 优先级覆盖生效
- [ ] DAG 验证拒绝环路、孤立节点、缺失引用
- [ ] 单文件解析失败不影响其他 Pipeline
- [ ] 4 个通用 handler 可独立运行
- [ ] 全部 Phase 1 单元测试通过

---

## Phase 2: LLM 工具层（GraphPipelineTool + 执行集成）

**阶段目标**: LLM 可通过 tool call 发现、启动、监控、管理 Pipeline。

**前置**: Phase 1 全部完成。

### 并行组 P2-A（Engine 层改造，可同时进行）

#### T-065-011: SkillPipelineEngine handler 缺失改为 FAILED 终态

| 属性 | 值 |
|------|-----|
| **FR 映射** | plan.md CRITICAL #9；FR-065-05 AC-04 |
| **涉及文件** | 修改 `octoagent/packages/skills/src/octoagent/skills/pipeline.py` |
| **验收标准** | `_drive()` 中 handler=None 时不再 raise `PipelineExecutionError`，改为设置 run status=FAILED + metadata 包含 `failure_category="handler_missing"` / `failed_node_id` / `recovery_hint`；发射 `PIPELINE_RUN_UPDATED` 事件 |
| **复杂度** | 低 |
| **依赖** | 无（修改已有代码） |

#### T-065-012: SkillPipelineEngine 节点级超时支持

| 属性 | 值 |
|------|-----|
| **FR 映射** | plan.md WARNING 6.2；NFR-065-02 |
| **涉及文件** | 修改 `octoagent/packages/skills/src/octoagent/skills/pipeline.py` |
| **验收标准** | `_drive()` 中节点有 `timeout_seconds` 时通过 `asyncio.wait_for()` 包装 handler 调用；超时触发 `PipelineNodeOutcome(status=FAILED, summary="...")` + metadata `failure_category="timeout"`；超时后 Pipeline 进入 FAILED 终态 |
| **复杂度** | 中 |
| **依赖** | 无（修改已有代码） |

#### T-065-013: Pipeline FAILED metadata 补充 failure_category + recovery_hint

| 属性 | 值 |
|------|-----|
| **FR 映射** | plan.md CRITICAL #9；FR-065-08 AC-03 |
| **涉及文件** | 修改 `octoagent/packages/skills/src/octoagent/skills/pipeline.py` |
| **验收标准** | Pipeline FAILED 时 run.metadata 包含 `failure_category`（tool / gate / timeout / handler_missing / validation / unknown）、`failed_node_id`、`recovery_hint`（人类可读恢复建议）、`error_message`；所有失败路径统一写入这些字段 |
| **复杂度** | 低 |
| **依赖** | T-065-011 |

### 并行组 P2-B（GraphPipelineTool 核心实现）

#### T-065-014: GraphPipelineTool 骨架 + list action

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-03 AC-01 |
| **涉及文件** | 新建 `octoagent/packages/skills/src/octoagent/skills/pipeline_tool.py` |
| **验收标准** | `GraphPipelineTool` 类，`__init__()` 接受 PipelineRegistry / StoreGroup / EventRecorder / TaskService 依赖；创建独立 `SkillPipelineEngine` 实例并注册 4 个通用 handler；`execute()` 入口方法按 action 分发；`action="list"` 调用 `PipelineRegistry.list_items()` 返回 LLM 可读的 Pipeline 摘要列表；空列表时返回 "No pipelines available" 提示 |
| **复杂度** | 中 |
| **依赖** | T-065-007, T-065-009 |

#### T-065-015: start action — 创建 Child Task + Work + 启动 Engine

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-03 AC-02, FR-065-05 AC-01, AC-02, AC-03 |
| **涉及文件** | `pipeline_tool.py`（同上） |
| **验收标准** | 从 PipelineRegistry 获取 PipelineManifest；创建 Child Task（parent_task_id 指向调用者）；创建 Work（target_kind=GRAPH_AGENT，metadata 含 pipeline_id / run_id）；快照 definition 到 run.metadata["definition_snapshot"]；通过 `asyncio.create_task()` 后台启动 Engine.start_run()；立即返回 run_id + task_id + 使用提示 |
| **复杂度** | 高 |
| **依赖** | T-065-014, T-065-011 |

#### T-065-016: start action — 输入参数验证

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-03 AC-06 |
| **涉及文件** | `pipeline_tool.py`（同上） |
| **验收标准** | `pipeline_id` 不存在时返回 "Error: pipeline not found" 错误；`params` 缺少 `input_schema` 中 required 字段时返回 "Error: invalid params" 错误；验证失败不抛异常，返回结构化错误字符串 |
| **复杂度** | 低 |
| **依赖** | T-065-014 |

#### T-065-017: start action — 并发 run 计数 + 上限检查

| 属性 | 值 |
|------|-----|
| **FR 映射** | NFR-065-01 |
| **涉及文件** | `pipeline_tool.py`（同上） |
| **验收标准** | `GraphPipelineTool` 维护 `_active_run_count: int`；start 时检查计数 < 上限（默认 10）；超限时返回 "Error: maximum concurrent pipeline runs reached" 错误；Pipeline run 进入终态时递减计数；进程重启后从数据库查询 status=RUNNING 的 run 数量重建计数 |
| **复杂度** | 中 |
| **依赖** | T-065-015 |

#### T-065-018: status action

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-03 AC-03, AC-07 |
| **涉及文件** | `pipeline_tool.py`（同上） |
| **验收标准** | 从 Engine 获取 run 状态；返回 LLM 可读格式（run_id / pipeline / status / current_node / completed_nodes / pending_nodes / started_at / elapsed）；暂停状态时额外返回 waiting_for + resume_command；run_id 不存在时返回 "Error: pipeline run not found" |
| **复杂度** | 中 |
| **依赖** | T-065-015 |

#### T-065-019: resume action（WAITING_INPUT + WAITING_APPROVAL）

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-03 AC-04, FR-065-06 AC-03, AC-04 |
| **涉及文件** | `pipeline_tool.py`（同上） |
| **验收标准** | WAITING_INPUT：验证 `input_data` 非空 → 调用 `Engine.resume_run(state_patch=input_data)`；WAITING_APPROVAL + `approved=true`：调用 `Engine.resume_run()`；WAITING_APPROVAL + `approved=false`：调用 `Engine.cancel_run()` + Task 转 CANCELLED；run 不在暂停状态时返回明确错误 |
| **复杂度** | 中 |
| **依赖** | T-065-015 |

#### T-065-020: cancel action

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-03 AC-05 |
| **涉及文件** | `pipeline_tool.py`（同上） |
| **验收标准** | 调用 `Engine.cancel_run()`；同步更新 Child Task 和 Work 到终态；递减并发计数；run 已在终态时返回错误；返回确认信息 + side-effect 不可撤销提示 |
| **复杂度** | 低 |
| **依赖** | T-065-015 |

#### T-065-021: retry action

| 属性 | 值 |
|------|-----|
| **FR 映射** | plan.md CRITICAL #9 |
| **涉及文件** | `pipeline_tool.py`（同上） |
| **验收标准** | 验证 run 状态为 FAILED → 调用 `Engine.retry_current_node()`；通过 `asyncio.create_task()` 后台继续执行；run 不在 FAILED 状态时返回错误；返回确认信息 + status 查询提示 |
| **复杂度** | 低 |
| **依赖** | T-065-015, T-065-013 |

### 并行组 P2-C（注册 + 集成测试）

#### T-065-022: graph_pipeline 工具 @tool_contract 装饰 + ToolBroker 注册

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-03 AC-08, plan.md FAIL |
| **涉及文件** | `pipeline_tool.py`；修改 `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py` |
| **验收标准** | `@tool_contract(side_effect_level=IRREVERSIBLE, tool_profile=FULL, tool_group="orchestration", tool_tier=CORE)` 装饰；通过 `ToolBroker.register()` 注册为 `graph_pipeline`；schema 由 `reflect_tool_schema()` 从函数签名自动生成；Constitution 原则 3 满足：schema 与实现签名一致 |
| **复杂度** | 低 |
| **依赖** | T-065-014 |

#### T-065-023: Pipeline 执行过程中 Task/Work 终态同步

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-05 AC-07 |
| **涉及文件** | `pipeline_tool.py` |
| **验收标准** | Pipeline SUCCEEDED → Child Task SUCCEEDED + Work SUCCEEDED；Pipeline FAILED → Child Task FAILED + Work FAILED；Pipeline CANCELLED → Child Task CANCELLED + Work CANCELLED；终态同步在后台执行回调中完成 |
| **复杂度** | 中 |
| **依赖** | T-065-015 |

#### T-065-024: Pipeline 启动快照 definition

| 属性 | 值 |
|------|-----|
| **FR 映射** | plan.md 风险 R-NEW-1 |
| **涉及文件** | `pipeline_tool.py` |
| **验收标准** | start 时将 `SkillPipelineDefinition` 序列化存入 `run.metadata["definition_snapshot"]`；resume / retry 时从 metadata 反序列化 definition（而非重新从 PipelineRegistry 获取）；保证运行期间 PIPELINE.md 修改不影响已启动的 run |
| **复杂度** | 低 |
| **依赖** | T-065-015 |

#### T-065-025: skills 包 __init__.py 导出新模块

| 属性 | 值 |
|------|-----|
| **FR 映射** | 工程配套 |
| **涉及文件** | 修改 `octoagent/packages/skills/src/octoagent/skills/__init__.py` |
| **验收标准** | 导出 `PipelineRegistry`、`PipelineManifest`、`PipelineListItem`、`PipelineSource`、`GraphPipelineTool`、通用 handler 函数 |
| **复杂度** | 低 |
| **依赖** | T-065-007, T-065-014, T-065-009 |

#### T-065-026: Phase 2 单元测试 + 集成测试

| 属性 | 值 |
|------|-----|
| **FR 映射** | 覆盖 FR-065-03 / FR-065-05 全部 AC |
| **涉及文件** | 新建 `tests/unit/skills/test_pipeline_tool.py`、`tests/integration/test_pipeline_execution.py` |
| **验收标准** | 单元测试：6 个 action 的正常/异常路径、参数验证、并发上限、run_id 不存在；集成测试：端到端 Pipeline 执行（start → 节点逐步执行 → Checkpoint → SUCCEEDED）；HITL 审批流（start → gate → WAITING_APPROVAL → resume → SUCCEEDED）；节点失败 + retry（start → FAILED → retry → SUCCEEDED）；并发上限（10 个 run → 第 11 个被拒绝） |
| **复杂度** | 高 |
| **依赖** | T-065-014 ~ T-065-024 |

### Phase 2 验收门

- [ ] LLM 调用 `graph_pipeline(action="list")` 返回可用 Pipeline 列表
- [ ] LLM 调用 `graph_pipeline(action="start")` 成功创建 Child Task + Work + Pipeline run
- [ ] Pipeline 运行过程中每个节点生成 Checkpoint + Event
- [ ] Pipeline 遇到 gate 节点暂停 → resume 后继续
- [ ] cancel / retry 正常工作
- [ ] 并发上限生效
- [ ] handler 缺失 / 节点超时均进入 FAILED 终态 + 正确 metadata
- [ ] 工具已注册到 CapabilityPack
- [ ] 全部 Phase 2 测试通过

---

## Phase 3: Butler 路由感知 + System Prompt 注入

**阶段目标**: Butler 可直接选择 DELEGATE_GRAPH；所有 Agent 层感知 Pipeline 存在。

**前置**: Phase 2 中 T-065-014（GraphPipelineTool 骨架）完成即可开始 P3-A；Phase 2 全部完成后开始 P3-B。

### 并行组 P3-A（Butler 模型扩展，可与 Phase 2 后半段并行）

#### T-065-027: ButlerDecisionMode 新增 DELEGATE_GRAPH

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-04 AC-01 |
| **涉及文件** | 修改 `octoagent/packages/core/src/octoagent/core/models/behavior.py` |
| **验收标准** | `ButlerDecisionMode` 新增 `DELEGATE_GRAPH = "delegate_graph"` 枚举值；不破坏现有 6 个枚举值 |
| **复杂度** | 低 |
| **依赖** | 无 |

#### T-065-028: ButlerDecision 新增 pipeline_id / pipeline_params 字段

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-04 AC-02, AC-05 |
| **涉及文件** | 修改 `octoagent/packages/core/src/octoagent/core/models/behavior.py` |
| **验收标准** | `ButlerDecision` 新增 `pipeline_id: str = Field(default="")`；新增 `pipeline_params: dict[str, Any] = Field(default_factory=dict)`；mode 为 DELEGATE_GRAPH 时 pipeline_id 必须非空（验证逻辑在 behavior 层） |
| **复杂度** | 低 |
| **依赖** | T-065-027 |

### 并行组 P3-B（Prompt 注入 + 路由，依赖 P3-A + Phase 2）

#### T-065-029: Butler system prompt 注入 Pipeline 列表 + trigger_hint

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-04 AC-03, FR-065-07 AC-05 |
| **涉及文件** | 修改 `octoagent/apps/gateway/src/octoagent/gateway/services/butler_behavior.py` |
| **验收标准** | `build_butler_behavior_prompt()` 的 decision_modes 描述中包含 `delegate_graph` 及其说明；Pipeline 列表为空时不注入（FR-065-07 AC-04）；非空时注入 Pipeline id + description + trigger_hint + input_schema 摘要；格式与 contracts/system-prompt-injection.md 一致 |
| **复杂度** | 中 |
| **依赖** | T-065-028, T-065-007 |

#### T-065-030: Butler DELEGATE_GRAPH 路由分支实现

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-04 AC-04 |
| **涉及文件** | 修改 `octoagent/apps/gateway/src/octoagent/gateway/services/butler_behavior.py` |
| **验收标准** | `_parse_butler_decision()` 识别 `delegate_graph` mode 并填充 pipeline_id / pipeline_params；Butler 选择 DELEGATE_GRAPH 时直接调用 `GraphPipelineTool.execute(action="start", ...)`；跳过 Worker LLM 中转 |
| **复杂度** | 中 |
| **依赖** | T-065-028, T-065-015 |

#### T-065-031: DELEGATE_GRAPH fallback 到 DELEGATE_DEV/OPS

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-04 AC-06 |
| **涉及文件** | 修改 `octoagent/apps/gateway/src/octoagent/gateway/services/butler_behavior.py` |
| **验收标准** | pipeline_id 不存在或参数不合法时触发 fallback；按 Pipeline tags 就近匹配：deploy/ci-cd/ops → DELEGATE_OPS，dev/code/build → DELEGATE_DEV，其他 → DELEGATE_RESEARCH；决策结果中携带 fallback 理由 |
| **复杂度** | 低 |
| **依赖** | T-065-030 |

#### T-065-032: Worker/Subagent system prompt 注入 Pipeline 列表 + 语义区分指引

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-07 AC-01, AC-02, AC-03, AC-04 |
| **涉及文件** | 修改 `octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py` |
| **验收标准** | 在 `## Available Skills` 段落之后注入 `## Available Pipelines` 段落；格式：`- **{pipeline_id}**: {description} (trigger: {trigger_hint})`；单个 Pipeline 摘要不超过 3 行；附加 Pipeline vs Subagent 语义区分指引（按 contracts/system-prompt-injection.md 模板）；Pipeline 列表为空时不注入 |
| **复杂度** | 中 |
| **依赖** | T-065-007 |

#### T-065-033: Phase 3 单元测试

| 属性 | 值 |
|------|-----|
| **FR 映射** | 覆盖 FR-065-04 / FR-065-07 全部 AC |
| **涉及文件** | 新建 `tests/unit/services/test_butler_delegate_graph.py`、`tests/unit/services/test_prompt_pipeline_injection.py` |
| **验收标准** | Butler 可解析 `delegate_graph` decision mode；Butler system prompt 包含 Pipeline 列表；Worker system prompt 包含 Pipeline 列表 + 语义区分指引；DELEGATE_GRAPH 失败时正确 fallback；Pipeline 列表为空时不注入 |
| **复杂度** | 中 |
| **依赖** | T-065-029, T-065-030, T-065-031, T-065-032 |

### Phase 3 验收门

- [ ] Butler 可解析 `delegate_graph` decision mode
- [ ] Butler system prompt 包含 Pipeline 列表（含 trigger_hint）
- [ ] Worker/Subagent system prompt 包含 Pipeline 列表 + 语义区分指引
- [ ] DELEGATE_GRAPH 失败时正确 fallback
- [ ] Pipeline 列表为空时不注入任何 Pipeline 段落
- [ ] 全部 Phase 3 测试通过

---

## Phase 4: 管理 API + HITL 渠道集成

**阶段目标**: REST API 可查询 Pipeline 定义和运行实例；审批渠道集成。

**前置**: Phase 2 全部完成。Phase 3 不阻塞 Phase 4（可并行进行 P4-A）。

### 并行组 P4-A（REST API，可与 Phase 3 并行）

#### T-065-034: Pipeline REST API 响应模型定义

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-09 AC-06 |
| **涉及文件** | 新建 `octoagent/apps/gateway/src/octoagent/gateway/routes/pipelines.py`（模型部分） |
| **验收标准** | 定义 `PipelineItemResponse` / `PipelineListResponse` / `PipelineDetailResponse` / `PipelineNodeResponse` / `PipelineRunItemResponse` / `PipelineRunListResponse` / `PipelineRunDetailResponse` / `PipelineCheckpointResponse` 全部响应模型（按 contracts/pipeline-rest-api.md） |
| **复杂度** | 低 |
| **依赖** | T-065-003 |

#### T-065-035: 实现 Pipeline 管理 API（5 个端点）

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-09 AC-01, AC-02, AC-03, AC-04, AC-05 |
| **涉及文件** | `octoagent/apps/gateway/src/octoagent/gateway/routes/pipelines.py`（路由部分） |
| **验收标准** | `GET /api/pipelines` 返回已注册 Pipeline 列表；`GET /api/pipelines/{pipeline_id}` 返回单个详情（含完整节点拓扑），不存在时 404；`POST /api/pipelines/refresh` 触发 PipelineRegistry.refresh() 返回更新后列表；`GET /api/pipeline-runs` 支持分页（page / page_size）+ 筛选（pipeline_id / status / task_id）；`GET /api/pipeline-runs/{run_id}` 返回 run 详情（含 checkpoint 历史），不存在时 404 |
| **复杂度** | 中 |
| **依赖** | T-065-034, T-065-007 |

#### T-065-036: 挂载 Pipeline 路由到 FastAPI app

| 属性 | 值 |
|------|-----|
| **FR 映射** | 工程配套 |
| **涉及文件** | 修改 `octoagent/apps/gateway/src/octoagent/gateway/app.py`（或路由注册处） |
| **验收标准** | Pipeline router 挂载到 FastAPI app；`/api/pipelines` 和 `/api/pipeline-runs` 前缀路由可访问 |
| **复杂度** | 低 |
| **依赖** | T-065-035 |

### 并行组 P4-B（HITL 渠道集成）

#### T-065-037: HITL — Pipeline WAITING_APPROVAL/WAITING_INPUT → Task 状态同步

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-06 AC-01, AC-02 |
| **涉及文件** | `pipeline_tool.py`（后台执行回调中） |
| **验收标准** | Pipeline 进入 WAITING_APPROVAL 时，Child Task 状态同步更新为 WAITING_APPROVAL；Pipeline 进入 WAITING_INPUT 时，Child Task 状态同步更新为 WAITING_INPUT + 携带 input_request 描述所需输入字段；状态同步通过 Engine 执行回调实现 |
| **复杂度** | 中 |
| **依赖** | T-065-015 |

#### T-065-038: HITL — 渠道审批桥接（Web/Telegram → graph_pipeline resume）

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-06 AC-05 |
| **涉及文件** | `pipeline_tool.py` 或新增桥接代码 |
| **验收标准** | Web UI 审批按钮操作时，效果等同于调用 `graph_pipeline(action="resume", approved=true/false)`；Telegram 渠道审批回调时同理；复用现有渠道审批基础设施（Task 状态变更 → Engine resume/cancel） |
| **复杂度** | 中 |
| **依赖** | T-065-019, T-065-037 |

#### T-065-039: HITL — 拒绝审批 → Pipeline CANCELLED + Task CANCELLED

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-06 AC-06 |
| **涉及文件** | `pipeline_tool.py` |
| **验收标准** | 用户拒绝审批（`approved=false`）时 Pipeline 状态转为 CANCELLED；Child Task 状态同步转为 CANCELLED；返回确认信息包含被拒绝节点名称 |
| **复杂度** | 低 |
| **依赖** | T-065-019 |

### 独立任务

#### T-065-040: HITL — WAITING 状态下不消耗 LLM token 验证

| 属性 | 值 |
|------|-----|
| **FR 映射** | FR-065-06 AC-07 |
| **涉及文件** | 验证性测试 |
| **验收标准** | Pipeline 处于 WAITING_APPROVAL / WAITING_INPUT 状态时，不触发任何 LLM 调用；验证 Engine._drive() 在暂停后返回、不进入 LLM 工具调用循环 |
| **复杂度** | 低 |
| **依赖** | T-065-019 |

#### T-065-041: Phase 4 集成测试

| 属性 | 值 |
|------|-----|
| **FR 映射** | 覆盖 FR-065-06 / FR-065-09 全部 AC |
| **涉及文件** | 新建 `tests/integration/test_pipeline_api.py`、`tests/integration/test_pipeline_hitl.py` |
| **验收标准** | REST API 测试：5 个端点 + 分页 + 筛选 + 404 错误；HITL 审批流：start → gate → WAITING_APPROVAL → Web/Telegram resume → SUCCEEDED；HITL 拒绝流：start → gate → WAITING_APPROVAL → 拒绝 → CANCELLED；Task 状态同步验证 |
| **复杂度** | 中 |
| **依赖** | T-065-035, T-065-037, T-065-038, T-065-039 |

### Phase 4 验收门

- [ ] 所有 5 个 API 端点可用且响应格式正确
- [ ] Pipeline run 列表支持分页和筛选
- [ ] Pipeline WAITING_APPROVAL 时 Task 状态同步
- [ ] Web UI / Telegram 审批按钮可触发 Pipeline resume
- [ ] 拒绝审批时 Pipeline + Task 同步转 CANCELLED
- [ ] WAITING 状态不消耗 LLM token
- [ ] 全部 Phase 4 测试通过

---

## 跨 Phase 依赖全景图

```
Phase 1                    Phase 2                      Phase 3                    Phase 4
─────────────────────────────────────────────────────────────────────────────────────────────

[P1-A] T-001~006
  数据模型 + 解析器
       │
       ▼
[P1-B] T-007~009 ──────▶ [P2-A] T-011~013             [P3-A] T-027~028
  Registry + Handler       Engine 改造                    Butler 模型扩展
       │                      │                              │
       ▼                      ▼                              ▼
  T-010 测试            [P2-B] T-014~021 ─────────────▶ [P3-B] T-029~032          [P4-A] T-034~036
                          Tool 核心实现                    Prompt + 路由             REST API
                              │                              │                         │
                              ▼                              ▼                         │
                        [P2-C] T-022~025                T-033 测试                    │
                          注册 + 配套                                                  │
                              │                                              [P4-B] T-037~040
                              ▼                                                HITL 集成
                        T-026 测试                                                │
                                                                                  ▼
                                                                            T-041 测试
```

### 可并行调度总结

| 时间窗口 | 可并行任务组 |
|----------|------------|
| 窗口 1 | P1-A（T-001~006） |
| 窗口 2 | P1-B（T-007~009） + T-008（echo-test PIPELINE.md） |
| 窗口 3 | T-010（P1 测试） + P2-A（T-011~013） + P3-A（T-027~028） |
| 窗口 4 | P2-B（T-014~021） |
| 窗口 5 | P2-C（T-022~025） + P4-A（T-034~036） + P3-B（T-029~032） |
| 窗口 6 | T-026（P2 测试） + P4-B（T-037~040） + T-033（P3 测试） |
| 窗口 7 | T-041（P4 测试） |

---

## 风险缓解任务映射

| 风险 | 对应任务 | 缓解手段 |
|------|---------|---------|
| R-NEW-1: Pipeline 定义热更新冲突 | T-065-024 | 启动时快照 definition 到 run.metadata |
| R-NEW-2: DAG 环路 | T-065-006 | DFS 环检测 |
| R-NEW-3: Token 预算失控 | T-065-017 | 并发 run 上限 + Loop Guard 集成 |
| R-NEW-4: Checkpoint 恢复后上下文丢失 | T-065-009 | handler 幂等性 cursor 约定 |
| handler_id 不存在 | T-065-011 | handler 缺失 → FAILED 终态（不 raise） |
| 节点长时间阻塞 | T-065-012 | 节点级 timeout_seconds 消费 |
| Butler 误判 DELEGATE_GRAPH | T-065-031 | trigger_hint 引导 + fallback 逻辑 |
