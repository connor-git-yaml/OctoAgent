# Tasks: LiteLLM Proxy 集成 + 成本治理

**Feature**: 002-integrate-litellm-provider
**Date**: 2026-03-01
**Input**: [spec.md](spec.md) + [plan.md](plan.md) + [data-model.md](data-model.md) + [contracts/](contracts/)
**Prerequisites**: plan.md (required), spec.md (required), data-model.md, contracts/ (3 docs)

**Tests**: spec.md 明确要求测试（SC-7 覆盖率 >= 80%，SC-6 M0 回归），因此包含测试任务。采用 Tests FIRST 策略。

**Organization**: 任务按 User Story 组织。US-1~5（P1 MVP 核心）合并为功能维度的 Phase，US-6/US-7（P2 补充）各自独立 Phase。

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[USN]**: 所属 User Story
- Setup/Foundational/Polish 阶段不加 [USN] 标记

---

## Phase 1: Setup（项目初始化）

**Purpose**: 创建 packages/provider 包骨架，配置 workspace 依赖

- [x] T001 创建 packages/provider 包目录结构（`octoagent/packages/provider/src/octoagent/provider/__init__.py` + 空模块文件）
- [x] T002 创建 packages/provider 的 pyproject.toml，声明 litellm/httpx/pydantic/structlog/octoagent-core 依赖（`octoagent/packages/provider/pyproject.toml`）
- [x] T003 更新根 pyproject.toml，将 packages/provider 加入 workspace members 和 sources（`octoagent/pyproject.toml`）
- [x] T004 更新 apps/gateway 的 pyproject.toml，新增 octoagent-provider 依赖（`octoagent/apps/gateway/pyproject.toml`）
- [x] T005 [P] 创建 packages/provider/tests 目录及 conftest.py 和 `__init__.py`（`octoagent/packages/provider/tests/conftest.py`）
- [x] T006 运行 `uv sync` 验证 workspace 依赖解析成功

**Checkpoint**: packages/provider 包骨架就绪，uv sync 通过

---

## Phase 2: Foundational（阻塞性前置依赖）

**Purpose**: 实现所有 User Story 共享的基础组件：数据模型、异常体系、配置加载

- [x] T007 [P] 实现 TokenUsage 数据模型（`octoagent/packages/provider/src/octoagent/provider/models.py`）-- Pydantic BaseModel，prompt_tokens/completion_tokens/total_tokens 三个 int 字段
- [x] T008 [P] 实现 ModelCallResult 数据模型（`octoagent/packages/provider/src/octoagent/provider/models.py`）-- 10+ 字段的 Pydantic BaseModel，替代 M0 LLMResponse
- [x] T009 [P] 实现异常体系 ProviderError/ProxyUnreachableError/CostCalculationError（`octoagent/packages/provider/src/octoagent/provider/exceptions.py`）
- [x] T010 [P] 实现 ProviderConfig + load_provider_config()，从环境变量加载配置（`octoagent/packages/provider/src/octoagent/provider/config.py`）
- [x] T011 更新 `__init__.py` 导出所有公开接口（`octoagent/packages/provider/src/octoagent/provider/__init__.py`）

### Foundational 测试

- [x] T012 [P] 编写 TokenUsage + ModelCallResult 单元测试（`octoagent/packages/provider/tests/test_models.py`）-- 验证字段默认值、校验约束、不变量
- [x] T013 [P] 编写 ProviderConfig + load_provider_config 单元测试（`octoagent/packages/provider/tests/test_config.py`）-- 验证环境变量映射、默认值

**Checkpoint**: 基础数据模型和配置可用，后续组件可依赖

---

## Phase 3: US-1 真实 LLM 调用 + US-2 成本可见性 + US-3 Alias 路由（Priority: P1）

**Goal**: 实现 LiteLLMClient + AliasRegistry + CostTracker，使系统能通过 Proxy 调用真实 LLM，记录成本数据，并按语义 alias 路由到不同模型。US-1/2/3 高度耦合（client 依赖 alias 和 cost），合并为一个 Phase 实现。

**Independent Test**: 配置 Proxy 后，使用不同语义 alias 调用 LLM，验证返回真实响应、成本数据完整、路由正确。

### Tests FIRST（先写测试 -> 确认失败 -> 再实现）

- [x] T014 [P] [US3] 编写 AliasRegistry 单元测试（`octoagent/packages/provider/tests/test_alias.py`）-- 验证 resolve()、get_alias()、get_aliases_by_category()、get_aliases_by_runtime_group()、list_all()、未知 alias 降级到 "main"、运行时 group 透传
- [x] T015 [P] [US2] 编写 CostTracker 单元测试（`octoagent/packages/provider/tests/test_cost.py`）-- 验证 calculate_cost() 双通道策略、parse_usage()、extract_model_info()、cost_unavailable 标记
- [x] T016 [P] [US1] 编写 LiteLLMClient 单元测试（`octoagent/packages/provider/tests/test_client.py`）-- Mock litellm.acompletion()，验证 complete() 返回 ModelCallResult、health_check() 返回 bool、超时处理、ProxyUnreachableError 抛出

### Implementation

- [x] T017 [P] [US3] 实现 AliasConfig + AliasRegistry（`octoagent/packages/provider/src/octoagent/provider/alias.py`）-- 6 个 MVP 默认 alias、resolve() 三级匹配、4 个查询方法
- [x] T018 [P] [US2] 实现 CostTracker（`octoagent/packages/provider/src/octoagent/provider/cost.py`）-- calculate_cost() 双通道、parse_usage()、extract_model_info()，所有方法不抛异常
- [x] T019 [US1] 实现 LiteLLMClient（`octoagent/packages/provider/src/octoagent/provider/client.py`）-- complete() 调用 litellm.acompletion()，内部使用 CostTracker 计算成本，health_check() 调用 Proxy /health/liveliness（依赖 T017, T018）
- [x] T020 [US1] 运行 Phase 3 测试验证 T014/T015/T016 全部通过

**Checkpoint**: LiteLLMClient + AliasRegistry + CostTracker 独立可用，单元测试通过

---

## Phase 4: US-4 Provider 故障自动降级（Priority: P1）

**Goal**: 实现 FallbackManager + EchoMessageAdapter，使 Proxy 不可达时自动降级到 Echo 模式。

**Independent Test**: 停止 Proxy 后调用 LLM，验证 is_fallback=True 且任务正常完成。

### Tests FIRST

- [x] T021 [P] [US4] 编写 EchoMessageAdapter 单元测试（`octoagent/packages/provider/tests/test_echo_adapter.py`）-- 验证 messages -> content 提取、ModelCallResult 构建、provider="echo"
- [x] T022 [P] [US4] 编写 FallbackManager 单元测试（`octoagent/packages/provider/tests/test_fallback.py`）-- 验证 primary 成功不触发 fallback、primary 失败触发 fallback（is_fallback=True + fallback_reason）、双方失败抛 ProviderError、lazy probe 恢复

### Implementation

- [x] T023 [US4] 实现 EchoMessageAdapter（`octoagent/packages/provider/src/octoagent/provider/echo_adapter.py`）-- 提取最后一条 user message 的 content，构建 ModelCallResult（provider="echo"）
- [x] T024 [US4] 实现 FallbackManager（`octoagent/packages/provider/src/octoagent/provider/fallback.py`）-- call_with_fallback() 实现 lazy probe，primary 失败时切换 fallback，设置 is_fallback=True + fallback_reason（依赖 T023）
- [x] T025 [US4] 运行 Phase 4 测试验证 T021/T022 全部通过

**Checkpoint**: FallbackManager 降级链路完整，EchoAdapter 可独立运行

---

## Phase 5: US-5 LLMService 平滑切换（Priority: P1）

**Goal**: 改造 Gateway 层，将 provider 包集成到现有 LLMService 和 TaskService，实现 Echo -> LiteLLM 无缝切换，M0 全部功能向后兼容。

**Independent Test**: 分别在 echo 和 litellm 模式下运行 M0 完整测试套件，全部通过。

### 5a: Event Payload 扩展（阻塞改造的前置）

- [x] T026 [US5] 扩展 ModelCallCompletedPayload 新增 cost_usd/cost_unavailable/model_name/provider/is_fallback 字段（`octoagent/packages/core/src/octoagent/core/models/payloads.py`）-- 所有字段有默认值，M0 旧事件兼容
- [x] T027 [US5] 扩展 ModelCallFailedPayload 新增 model_name/provider/is_fallback 字段（`octoagent/packages/core/src/octoagent/core/models/payloads.py`）-- 所有字段有默认值

### 5b: Gateway LLMService 改造

- [x] T028 [US5] 改造 LLMService 构造器和 call() 方法（`octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`）-- 接受 FallbackManager + AliasRegistry，call() 支持 str | list[dict]，返回 ModelCallResult，保留向后兼容
- [x] T029 [US5] 改造 TaskService.process_task_with_llm() 使用 ModelCallResult 新字段构建事件（`octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`）-- MODEL_CALL_COMPLETED/FAILED payload 填充新字段；当 response 超过 8KB 时截断为 response_summary 并附截断标记，完整响应通过 Artifact 引用存储（FR-002-CL-4）（依赖 T026, T027, T028）
- [x] T030 [US5] 改造 main.py lifespan，根据 LLM_MODE 初始化 LiteLLMClient/FallbackManager/AliasRegistry/LLMService（`octoagent/apps/gateway/src/octoagent/gateway/main.py`）-- litellm 模式 vs echo 模式分支初始化（依赖 T028, T029）

### 5c: 现有测试适配

- [x] T031 [US5] 适配 test_us4_llm_echo.py 以匹配 ModelCallResult 返回类型和新 LLMService 构造器（`octoagent/apps/gateway/tests/test_us4_llm_echo.py`）
- [x] T032 [US5] 适配 test_sc8_llm_echo.py 集成测试（`octoagent/tests/integration/test_sc8_llm_echo.py`）
- [x] T033 [US5] 运行 M0 完整测试套件验证全部 105 个测试通过（`pytest octoagent/`）

**Checkpoint**: Gateway 改造完成，M0 所有测试通过，echo 模式行为不变

---

## Phase 6: US-6 LiteLLM Proxy 健康检查（Priority: P2）

**Goal**: 扩展 /ready 端点支持 profile 参数，实现 LiteLLM Proxy 可达性检测。

**Independent Test**: 分别在 Proxy 可达/不可达时请求 /ready?profile=llm，验证返回正确状态。

### Tests FIRST

- [x] T034 [US6] 编写 /ready?profile=llm 健康检查测试（`octoagent/apps/gateway/tests/test_us6_health_llm.py`）-- 验证 profile=llm 返回 litellm_proxy="ok"/"unreachable"、profile=core 返回 "skipped"、Echo 模式返回 "skipped"

### Implementation

- [x] T035 [US6] 改造 /ready 端点，新增 profile 查询参数支持（`octoagent/apps/gateway/src/octoagent/gateway/routes/health.py`）-- 通过 LiteLLMClient.health_check() 探测 Proxy，profile=core/None 时 litellm_proxy="skipped"（依赖 T030 的 app.state.litellm_client）
- [x] T036 [US6] 运行健康检查测试验证通过

**Checkpoint**: /ready?profile=llm 返回真实 Proxy 健康状态

---

## Phase 7: US-7 Proxy 部署即开即用（Priority: P2）

**Goal**: 提供 Docker Compose 配置和环境变量模板，实现"从零到真实 LLM 响应 < 15 分钟"。

**Independent Test**: 按照配置从零启动 Proxy 并完成一次 LLM 调用。

- [x] T037 [P] [US7] 创建 docker-compose.litellm.yml（`octoagent/docker-compose.litellm.yml`）-- litellm 服务定义、端口映射、配置文件挂载、env_file、healthcheck
- [x] T038 [P] [US7] 创建 litellm-config.yaml Proxy 配置模板（`octoagent/litellm-config.yaml`）-- cheap/main/fallback 三个运行时 group、router_settings fallback 策略
- [x] T039 [P] [US7] 创建 .env.example 通用配置示例（`octoagent/.env.example`）-- OCTOAGENT_LLM_MODE、LITELLM_PROXY_URL、LITELLM_PROXY_KEY 等
- [x] T040 [P] [US7] 创建 .env.litellm.example Provider API Key 示例（`octoagent/.env.litellm.example`）-- OPENAI_API_KEY、ANTHROPIC_API_KEY 占位
- [x] T041 [US7] 更新 .gitignore 确保 .env 和 .env.litellm 不被提交（`octoagent/.gitignore`）-- 排除 .env/.env.* 但保留 .example 文件

**Checkpoint**: Proxy 部署配置就绪，从零启动路径清晰

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: 集成测试、覆盖率验证、M0 回归、代码清理

### 集成测试

- [x] T042 [P] 编写 Echo 模式全链路集成测试（`octoagent/tests/integration/test_f002_echo_mode.py`）-- 验证 LLM_MODE=echo 时全链路行为与 M0 一致，事件 payload 包含新字段默认值
- [x] T043 [P] 编写 LiteLLM 模式 Mock Proxy 集成测试（`octoagent/tests/integration/test_f002_litellm_mode.py`）-- Mock httpx/litellm，验证全链路调用、ModelCallResult 字段完整、事件链正确
- [x] T044 [P] 编写降级与恢复集成测试（`octoagent/tests/integration/test_f002_fallback.py`）-- 模拟 Proxy 不可达 -> 降级 Echo -> Proxy 恢复 -> 自动恢复 LiteLLM
- [x] T045 编写 Payload 向后兼容契约测试（`octoagent/tests/integration/test_f002_payload_compat.py`）-- 构造 M0 旧事件 JSON，验证新版 ModelCallCompletedPayload 反序列化成功，新字段使用默认值

### 验证与清理

- [x] T046 运行 Provider 包覆盖率分析，确保 >= 80%（`pytest --cov=octoagent.provider octoagent/packages/provider/tests/`）
- [x] T047 运行全量测试套件（M0 105 + Feature 002 新增），确保全部通过（`pytest octoagent/`）
- [x] T048 更新 `__init__.py` 确保所有公开接口完整导出（`octoagent/packages/provider/src/octoagent/provider/__init__.py`）-- 最终验证 __all__ 列表与实现一致
- [x] T049 [P] 在 M0 LLMResponse 上添加 @deprecated 标记并添加迁移注释（`octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py`）

---

## FR 覆盖映射表

| FR 编号 | 描述 | 级别 | 覆盖 Task |
|---------|------|------|-----------|
| FR-002-CL-1 | LiteLLM Proxy 调用封装 | MUST | T016, T019 |
| FR-002-CL-2 | 异步优先 + 可配超时 | MUST | T010, T013, T016, T019 |
| FR-002-CL-3 | 调用结果数据模型 (ModelCallResult) | MUST | T007, T008, T012 |
| FR-002-CL-4 | 响应摘要与截断 (8KB) | MUST | T029, T045 |
| FR-002-AL-1 | Alias 注册表 | MUST | T014, T017 |
| FR-002-AL-2 | Alias 静态配置 | MUST | T010, T017 |
| FR-002-AL-3 | Alias 查询接口 | SHOULD | T014, T017 |
| FR-002-CT-1 | 实时成本计算（双通道） | MUST | T015, T018 |
| FR-002-CT-2 | Token Usage 解析 | MUST | T015, T018 |
| FR-002-CT-3 | 成本数据零依赖查询 | SHOULD | T018 |
| FR-002-FM-1 | 应用层降级策略（两级降级） | MUST | T022, T024 |
| FR-002-FM-2 | 降级标记 (is_fallback + reason) | MUST | T022, T024 |
| FR-002-FM-3 | 自动恢复（lazy probe） | SHOULD | T022, T024 |
| FR-002-EP-1 | ModelCallCompletedPayload 扩展 | MUST | T026, T029 |
| FR-002-EP-2 | 向后兼容（M0 旧事件） | MUST | T026, T027, T045 |
| FR-002-EP-3 | ModelCallFailedPayload 扩展 | MUST | T027, T029 |
| FR-002-LS-1 | 默认 Provider 切换 (Echo -> LiteLLM) | MUST | T028, T030 |
| FR-002-LS-2 | Messages 格式支持 | MUST | T023, T028 |
| FR-002-LS-3 | LLM 模式可配置 (litellm/echo) | MUST | T010, T030 |
| FR-002-HC-1 | Proxy 健康检查 | MUST | T016, T019, T035 |
| FR-002-HC-2 | /ready 端点扩展 (profile 参数) | MUST | T034, T035 |
| FR-002-SK-1 | API Key 不进应用层 | MUST | T010, T019, T037, T040 |
| FR-002-SK-2 | Secrets 环境变量分层 | SHOULD | T039, T040, T041 |
| FR-002-DC-1 | Docker Compose 配置 | SHOULD | T037 |
| FR-002-DC-2 | Proxy 配置模板 | SHOULD | T038 |

**FR 覆盖率**: 25/25 = **100%**

---

## Dependencies & Execution Order

### Phase 依赖关系

```
Phase 1 (Setup) ──────> Phase 2 (Foundational) ──────> Phase 3 (US-1/2/3)
                                                    |-> Phase 4 (US-4) [可与 Phase 3 并行的测试编写部分]
                                                    |
                                         Phase 3 + Phase 4 ──> Phase 5 (US-5 Gateway 改造)
                                                                    |
                                                         Phase 5 ──> Phase 6 (US-6 健康检查)
                                                         Phase 5 ──> Phase 7 (US-7 部署配置) [可与 Phase 6 并行]
                                                                    |
                                                    Phase 5 + 6 + 7 ──> Phase 8 (Polish)
```

### User Story 间依赖

- **US-1/2/3 (Phase 3)**: 依赖 Foundational（Phase 2），三者高度耦合需合并实现
- **US-4 (Phase 4)**: 依赖 Phase 2（ModelCallResult），与 Phase 3 测试编写可部分并行
- **US-5 (Phase 5)**: 依赖 Phase 3 + Phase 4（所有 provider 组件就绪后才能集成改造 Gateway）
- **US-6 (Phase 6)**: 依赖 Phase 5（需要 app.state.litellm_client 注入）
- **US-7 (Phase 7)**: 依赖 Phase 5（需要确认完整配置变量列表），但文件独立可提前编写

### Story 内部并行机会

- **Phase 2**: T007/T008/T009/T010 全部可并行（独立文件）；T012/T013 可并行
- **Phase 3**: T014/T015/T016 测试可并行；T017/T018 可并行；T019 依赖 T017+T018
- **Phase 4**: T021/T022 测试可并行；T023 独立，T024 依赖 T023
- **Phase 5**: T026/T027 可并行；T028->T029->T030 串行
- **Phase 7**: T037/T038/T039/T040 全部可并行
- **Phase 8**: T042/T043/T044 可并行

### Recommended Implementation Strategy

**MVP First（推荐）**: 按 Phase 1 -> 2 -> 3 -> 4 -> 5 顺序实现，达到 US-1~5 全部交付即为 MVP。之后增量交付 US-6、US-7。

1. Phase 1 + 2: 搭建骨架和基础设施（~0.5 天）
2. Phase 3: 核心 LLM 调用能力（~1 天）
3. Phase 4: 降级能力（~0.5 天）
4. Phase 5: Gateway 改造（~1 天）-- **MVP 完成点**
5. Phase 6 + 7: 健康检查 + 部署配置（~0.5 天）
6. Phase 8: 集成测试 + 回归验证（~0.5 天）

**预估总工作量**: ~4 天（与 plan.md 的 3.5-5 天估算一致）

---

## Summary

| 维度 | 数值 |
|------|------|
| 总任务数 | 49 |
| User Stories | 7（P1: 5, P2: 2） |
| Phase 数 | 8 |
| 可并行任务 | 26（53%） |
| FR 覆盖率 | 24/24 (100%) |
| 新建文件 | ~18 |
| 修改文件 | ~8 |
| 预估工作量 | 3.5-4.5 天 |
