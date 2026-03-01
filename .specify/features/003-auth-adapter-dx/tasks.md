# Tasks: Auth Adapter + DX 工具 (Feature 003)

**Feature Branch**: `feat/003-auth-adapter-dx`
**Input**: `plan.md`, `spec.md`, `data-model.md`, `contracts/auth-adapter-api.md`, `contracts/dx-cli-api.md`
**Date**: 2026-03-01
**Total Tasks**: 56
**User Stories**: 7 (US1-US4 P1, US5-US7 P2)
**Parallel Rate**: ~60%

## Format: `[ID] [P?] [Story?] 描述 + 文件路径`

- **[P]**: 可并行执行（不同文件、无依赖）
- **[USN]**: 所属 User Story（US1 ~ US7）
- Setup / Foundational / Polish 阶段不加 [USN] 标记

## Path Base

```
ROOT   = octoagent/
PROV   = octoagent/packages/provider/
AUTH   = octoagent/packages/provider/src/octoagent/provider/auth/
DX     = octoagent/packages/provider/src/octoagent/provider/dx/
TESTS  = octoagent/packages/provider/tests/
CORE   = octoagent/packages/core/src/octoagent/core/models/
GW     = octoagent/apps/gateway/src/octoagent/gateway/
```

---

## Phase 1: Setup (项目初始化)

**Purpose**: 新增包依赖、创建目录结构、初始化模块

- [x] T001 更新 pyproject.toml，新增 click/rich/questionary/python-dotenv/filelock 依赖和 `[project.scripts] octo` 入口 -- `octoagent/packages/provider/pyproject.toml`
- [x] T002 [P] 创建 auth 子包目录和 `__init__.py`（空的公开接口占位） -- `octoagent/packages/provider/src/octoagent/provider/auth/__init__.py`
- [x] T003 [P] 创建 dx 子包目录和 `__init__.py`（空的公开接口占位） -- `octoagent/packages/provider/src/octoagent/provider/dx/__init__.py`
- [x] T004 运行 `uv sync` 验证依赖安装成功 -- (无文件输出，命令行验证)

**Checkpoint**: 目录结构就绪，依赖已安装。

---

## Phase 2: Foundational (阻塞性前置依赖)

**Purpose**: 数据模型、异常体系、脱敏工具、校验工具、EventType 扩展 -- 所有 User Story 的共享基础

### 数据模型 & 异常

- [x] T005 [P] 实现凭证类型体系：ApiKeyCredential / TokenCredential / OAuthCredential + Credential 联合类型（Discriminated Union） -- `octoagent/packages/provider/src/octoagent/provider/auth/credentials.py`
- [x] T006 [P] 实现 ProviderProfile 模型 + CredentialStoreData 模型 -- `octoagent/packages/provider/src/octoagent/provider/auth/profile.py`
- [x] T007 [P] 在 exceptions.py 中新增 CredentialError / CredentialNotFoundError / CredentialExpiredError / CredentialValidationError / OAuthFlowError -- `octoagent/packages/provider/src/octoagent/provider/exceptions.py`
- [x] T008 [P] 在 EventType 枚举中新增 CREDENTIAL_LOADED / CREDENTIAL_EXPIRED / CREDENTIAL_FAILED -- `octoagent/packages/core/src/octoagent/core/models/enums.py`

### 工具函数

- [x] T009 [P] 实现 mask_secret() 凭证脱敏函数（保留前缀 + 末尾，中间替换为 ***） -- `octoagent/packages/provider/src/octoagent/provider/auth/masking.py`
- [x] T010 [P] 实现 validate_api_key() 和 validate_setup_token() 凭证格式校验 -- `octoagent/packages/provider/src/octoagent/provider/auth/validators.py`

### AuthAdapter 抽象接口

- [x] T011 实现 AuthAdapter ABC（resolve / refresh / is_expired 三个抽象方法） -- `octoagent/packages/provider/src/octoagent/provider/auth/adapter.py`

### Foundational 测试

- [x] T012 [P] 凭证模型单元测试：三种类型创建 / Discriminated Union 反序列化 / SecretStr 脱敏 -- `octoagent/packages/provider/tests/test_credentials.py`
- [x] T013 [P] Profile 模型单元测试：ProviderProfile 创建 / CredentialStoreData 序列化反序列化 -- `octoagent/packages/provider/tests/test_profile.py`
- [x] T014 [P] 脱敏函数单元测试：长字符串 / 短字符串 / 边界情况 -- `octoagent/packages/provider/tests/test_masking.py`
- [x] T015 [P] 校验函数单元测试：各 Provider 格式校验 / Setup Token 前缀校验 / 空值拒绝 -- `octoagent/packages/provider/tests/test_validators.py`

**Checkpoint**: 数据模型就绪，异常体系完整，工具函数可用。所有 User Story 的前置依赖已满足。

---

## Phase 3: US1 - 使用 API Key 完成首次认证配置 (Priority: P1) -- MVP

**Goal**: 用户通过 `octo init` 选择 API Key 认证模式，输入 Key 后系统存入 credential store 并生成配置文件。

**Independent Test**: 运行 `octo init` 选择 OpenRouter + API Key，检查 credential store 和 .env/.env.litellm/litellm-config.yaml 生成情况。

### 存储层

- [x] T016 [US1] 实现 CredentialStore 类：load() / save() / get_profile() / set_profile() / remove_profile() / get_default_profile() / list_profiles()，含 filelock 并发保护 + 原子写入 + 文件权限 0o600 + 文件损坏恢复 -- `octoagent/packages/provider/src/octoagent/provider/auth/store.py`
- [x] T017 [US1] 凭证事件发射函数 emit_credential_event()，记录凭证生命周期到 Event Store（仅元信息，不含凭证值） -- `octoagent/packages/provider/src/octoagent/provider/auth/events.py`

### Adapter 层

- [x] T018 [US1] 实现 ApiKeyAuthAdapter：resolve() 返回 key / refresh() 返回 None / is_expired() 返回 False -- `octoagent/packages/provider/src/octoagent/provider/auth/api_key_adapter.py`

### DX 层

- [x] T019 [US1] 实现 DX 数据模型 InitConfig + CheckResult + DoctorReport + CheckStatus + CheckLevel -- `octoagent/packages/provider/src/octoagent/provider/dx/models.py`
- [x] T020 [US1] 实现 octo init 核心逻辑（run_init_wizard）：运行模式选择 -> Provider 选择 -> 认证模式选择 -> API Key 输入 + 格式校验 -> 存入 store -> Master Key 生成 -> Docker 检测 -> 配置文件生成 -- `octoagent/packages/provider/src/octoagent/provider/dx/init_wizard.py`
- [x] T021 [US1] 实现配置文件生成函数：generate_env_file() / generate_env_litellm_file() / generate_litellm_config()，含中断恢复检测 detect_partial_init() -- `octoagent/packages/provider/src/octoagent/provider/dx/init_wizard.py`（续）
- [x] T022 [US1] 实现 click CLI 入口：main group + init command + doctor command -- `octoagent/packages/provider/src/octoagent/provider/dx/cli.py`

### US1 测试

- [x] T023 [P] [US1] Credential Store 单元测试：CRUD / filelock / 原子写入 / 文件权限 / 文件损坏恢复 -- `octoagent/packages/provider/tests/test_store.py`
- [x] T024 [P] [US1] ApiKeyAuthAdapter 单元测试：resolve / refresh / is_expired / 缺失凭证异常 -- `octoagent/packages/provider/tests/test_api_key_adapter.py`
- [x] T025 [P] [US1] octo init CLI 测试（CliRunner）：API Key 完整流程 / 格式校验失败 / 已有配置覆盖提示 / 中断恢复 -- `octoagent/packages/provider/tests/test_init_wizard.py`

**Checkpoint**: US1 完成后，开发者可以通过 `octo init` + API Key 完成首次配置并生成所有配置文件。

---

## Phase 4: US2 - 使用 Anthropic Setup Token 免费开发测试 (Priority: P1)

**Goal**: 用户通过 `octo init` 选择 Setup Token 模式，输入 Token 后系统验证格式、记录获取时间、标记过期策略。

**Independent Test**: 运行 `octo init` 选择 Anthropic Setup Token，输入 `sk-ant-oat01-*` Token，检查 credential store 中过期时间标记。

### Adapter 层

- [x] T026 [US2] 实现 SetupTokenAuthAdapter：resolve() 检查过期后返回 token / refresh() 返回 None / is_expired() 基于 acquired_at + TTL 计算，支持 OCTOAGENT_SETUP_TOKEN_TTL_HOURS 环境变量覆盖默认 24h -- `octoagent/packages/provider/src/octoagent/provider/auth/setup_token_adapter.py`

### DX 层

- [x] T027 [US2] 在 init_wizard.py 中扩展 Setup Token 认证路径：Token 输入 + sk-ant-oat01- 前缀校验 + acquired_at 记录 + expires_at 计算 -- `octoagent/packages/provider/src/octoagent/provider/dx/init_wizard.py`（扩展）

### US2 测试

- [x] T028 [P] [US2] SetupTokenAuthAdapter 单元测试：正常解析 / 过期检测 / TTL 覆盖 / 格式校验失败 -- `octoagent/packages/provider/tests/test_setup_token_adapter.py`
- [x] T029 [P] [US2] octo init Setup Token 路径 CLI 测试：完整流程 / 格式拒绝 / 过期提示 -- `octoagent/packages/provider/tests/test_init_wizard.py`（追加）

**Checkpoint**: US2 完成后，开发者可通过 Setup Token 零费用完成认证配置。

---

## Phase 5: US3 - 使用 Codex OAuth 免费开发测试 (Priority: P1)

**Goal**: 用户通过 `octo init` 选择 Codex OAuth 模式，系统触发 Device Flow 浏览器授权，轮询获取 token 并持久化。

**Independent Test**: 运行 `octo init` 选择 Codex OAuth，验证 Device Flow 流程（可用 mock 测试）。

### OAuth 层

- [x] T030 [US3] 实现 Device Flow：DeviceFlowConfig / DeviceAuthResponse 模型 + start_device_flow() + poll_for_token() -- `octoagent/packages/provider/src/octoagent/provider/auth/oauth.py`

### Adapter 层

- [x] T031 [US3] 实现 CodexOAuthAdapter：resolve() 返回 access_token / refresh() M1 返回 None / is_expired() 基于 expires_at 判断 -- `octoagent/packages/provider/src/octoagent/provider/auth/codex_oauth_adapter.py`

### DX 层

- [x] T032 [US3] 在 init_wizard.py 中扩展 Codex OAuth 认证路径：触发 Device Flow -> 显示 user_code + verification_uri -> 打开浏览器 -> 轮询等待 -> 超时处理 -> token 存储 -- `octoagent/packages/provider/src/octoagent/provider/dx/init_wizard.py`（扩展）

### US3 测试

- [x] T033 [P] [US3] Device Flow 单元测试（mock httpx）：正常授权 / 超时 / 端点不可达 / 轮询间隔 -- `octoagent/packages/provider/tests/test_oauth_flow.py`
- [x] T034 [P] [US3] CodexOAuthAdapter 单元测试：resolve / is_expired / 过期检测 -- `octoagent/packages/provider/tests/test_codex_oauth_adapter.py`
- [x] T035 [P] [US3] octo init Codex OAuth 路径 CLI 测试（mock Device Flow）：完整流程 / 超时 / 错误降级 -- `octoagent/packages/provider/tests/test_init_wizard.py`（追加）

**Checkpoint**: US3 完成后，三种认证模式均可通过 `octo init` 配置。

---

## Phase 6: US4 - 诊断环境配置问题 (Priority: P1)

**Goal**: `octo doctor` 执行 13 项检查，输出格式化报告，支持 `--live` 端到端验证。

**Independent Test**: 模拟各种故障场景（删除 .env / 无效 Key / 关闭 Proxy），运行 `octo doctor` 验证诊断结果。

### 实现

- [x] T036 [US4] 实现 DoctorRunner 类：13 项检查函数（python_version / uv_installed / env_file / env_litellm_file / llm_mode / proxy_key / master_key_match / docker_running / proxy_reachable / db_writable / credential_valid / credential_expiry / live_ping） -- `octoagent/packages/provider/src/octoagent/provider/dx/doctor.py`
- [x] T037 [US4] 实现 run_all_checks() 汇总逻辑 + overall_status 计算 + --live 条件检查 -- `octoagent/packages/provider/src/octoagent/provider/dx/doctor.py`（续）
- [x] T038 [US4] 实现 format_report()：rich 格式化输出（PASS 绿色 / WARN 黄色 / FAIL 红色 + 修复建议） -- `octoagent/packages/provider/src/octoagent/provider/dx/doctor.py`（续）
- [x] T039 [US4] 在 cli.py 中完善 doctor command 实现：调用 DoctorRunner + 输出格式化报告 + --live 标志 -- `octoagent/packages/provider/src/octoagent/provider/dx/cli.py`（扩展）

### US4 测试

- [x] T040 [P] [US4] octo doctor 单元测试：每项检查 PASS/FAIL 场景 / 整体汇总逻辑 / --live mock -- `octoagent/packages/provider/tests/test_doctor.py`
- [x] T041 [P] [US4] octo doctor CLI 测试（CliRunner）：正常输出 / 有故障输出 / --live 标志 -- `octoagent/packages/provider/tests/test_doctor.py`（续）

**Checkpoint**: US4 完成后，开发者可一键诊断环境问题。四个 P1 User Story 全部交付。

---

## Phase 7: US5 - 凭证安全存储与脱敏 (Priority: P2)

**Goal**: 确保凭证文件权限 0o600、日志脱敏、Event Store 事件不含凭证值、配置/凭证物理隔离。

**Independent Test**: 检查 auth-profiles.json 权限、审查 structlog 输出无明文、检查 Event Store 事件。

> 注意：US5 的核心实现（masking.py / store.py 权限 / events.py）已在 Phase 2 和 Phase 3 中完成。此 Phase 聚焦集成验证和补充。

- [x] T042 [US5] 在 auth/__init__.py 中导出公开接口，确保 masking / validators / store / chain / adapters 可通过 `from octoagent.provider.auth import ...` 访问 -- `octoagent/packages/provider/src/octoagent/provider/auth/__init__.py`
- [x] T043 [US5] 凭证安全集成测试：验证 structlog 日志输出不含明文凭证 / Event Store 事件仅含元信息 / credential store 文件权限 0o600 -- `octoagent/packages/provider/tests/test_credential_security.py`

**Checkpoint**: US5 完成后，凭证安全保障通过集成测试验证。

---

## Phase 8: US6 - 多 Provider 凭证管理与切换 (Priority: P2)

**Goal**: Handler Chain 按优先级自动选择凭证，支持多 Provider 配置和降级到 echo 模式。

**Independent Test**: 配置两个 Provider 凭证，验证 Handler Chain 解析优先级和 fallback 行为。

### 实现

- [x] T044 [US6] 实现 HandlerChain 类：register_adapter_factory() / resolve() 按优先级链解析（显式 profile > store > 环境变量 > 默认值） + HandlerChainResult 模型 -- `octoagent/packages/provider/src/octoagent/provider/auth/chain.py`
- [x] T045 [US6] 实现 Handler Chain 降级逻辑：所有 handler 均无有效凭证时发出 CREDENTIAL_FAILED 事件 + 降级到 echo 模式 -- `octoagent/packages/provider/src/octoagent/provider/auth/chain.py`（续）

### US6 测试

- [x] T046 [P] [US6] Handler Chain 单元测试：单 Provider 解析 / 多 Provider 优先级 / 环境变量 fallback / 全部失败降级 / adapter factory 注册 -- `octoagent/packages/provider/tests/test_chain.py`

**Checkpoint**: US6 完成后，多 Provider 凭证管理和自动切换就绪。

---

## Phase 9: US7 - Gateway 启动自动加载环境配置 (Priority: P2)

**Goal**: Gateway 启动时自动加载 .env，已设置的环境变量不被覆盖。

**Independent Test**: 不执行 `source .env`，直接启动 Gateway，验证环境变量正确加载。

### 实现

- [x] T047 [US7] 实现 dotenv_loader.py 封装：load_project_dotenv() 函数，支持 .env 不存在静默跳过 + 语法错误 warning 日志不阻塞启动 -- `octoagent/packages/provider/src/octoagent/provider/dx/dotenv_loader.py`
- [x] T048 [US7] 修改 Gateway main.py：在 create_app() 中集成 dotenv 自动加载（override=False） -- `octoagent/apps/gateway/src/octoagent/gateway/main.py`

### US7 测试

- [x] T049 [P] [US7] dotenv 加载集成测试：.env 存在 / .env 不存在 / 环境变量不被覆盖 / 语法错误处理 -- `octoagent/packages/provider/tests/test_dotenv_loader.py`

**Checkpoint**: US7 完成后，Gateway 自动加载 .env，开发者无需手动 source。

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: 导出整合、.gitignore 更新、端到端验收

- [x] T050 [P] 更新 provider __init__.py，导出 auth 和 dx 子模块公开接口 -- `octoagent/packages/provider/src/octoagent/provider/__init__.py`
- [x] T051 [P] 更新 .gitignore，确保 `auth-profiles.json` 和 `~/.octoagent/` 被排除 -- `octoagent/.gitignore`
- [x] T052 [P] 更新 dx/__init__.py，导出 CLI 入口和 dotenv_loader -- `octoagent/packages/provider/src/octoagent/provider/dx/__init__.py`
- [x] T053 AuthAdapter 接口一致性 Contract 测试：验证三种 Adapter 均正确实现 ABC 接口 -- `octoagent/packages/provider/tests/test_adapter_contract.py`
- [x] T054 端到端集成测试：模拟 SC-001 ~ SC-008 验收场景 -- `octoagent/packages/provider/tests/test_e2e_integration.py`
- [x] T055 运行全量测试套件 `uv run pytest`，确保所有测试通过 -- (命令行验证)
- [x] T056 运行 quickstart.md 验证流程（如果存在） -- (手动验证)

**Checkpoint**: 所有 User Story 交付，全量测试通过，验收标准覆盖。

---

## FR 覆盖映射表

| FR | 描述 | 覆盖 Task |
|----|------|----------|
| FR-001 | 三种凭证类型 | T005, T012 |
| FR-002 | AuthAdapter 统一接口 | T011, T053 |
| FR-003 | API Key 适配器 | T010, T018, T024 |
| FR-004 | Anthropic Setup Token 适配器 | T010, T026, T028 |
| FR-005 | Codex OAuth 适配器 | T030, T031, T033, T034 |
| FR-006 | Credential Store 持久化 | T006, T016, T023 |
| FR-007 | 交互式引导配置 octo init | T019, T020, T021, T022, T025, T027, T032 |
| FR-008 | 环境诊断 octo doctor | T019, T036, T037, T038, T039, T040, T041 |
| FR-009 | dotenv 自动加载 | T047, T048, T049 |
| FR-010 | Handler Chain | T044, T045, T046 |
| FR-011 | 凭证脱敏 | T009, T014, T043 |
| FR-012 | 凭证生命周期事件 | T008, T017, T043 |
| FR-013 | Config/Credential 分离 | T051, T043 |

**覆盖率**: 13/13 FR = **100%**

---

## Edge Case 覆盖映射

| EC | 描述 | 覆盖 Task |
|----|------|----------|
| EC-1 | Setup Token 过期时间无法从 Token 解析 | T026, T028 |
| EC-2 | credential store 文件 JSON 损坏 | T016, T023 |
| EC-3 | octo init 中断恢复 | T021, T025 |
| EC-4 | Handler Chain 全部凭证无效降级 echo | T045, T046 |
| EC-5 | 多进程同时写入 credential store | T016, T023 |
| EC-6 | --live 区分 Proxy/Provider 故障 | T036, T040 |
| EC-7 | .env 语法错误不阻塞启动 | T047, T049 |
| EC-8 | Codex OAuth 端点不可达 | T030, T033 |

**覆盖率**: 8/8 EC = **100%**

---

## 验收标准映射

| SC | 描述 | 验证 Task |
|----|------|----------|
| SC-001 | git clone 到首次 LLM 调用 < 3 分钟 | T054 |
| SC-002 | Setup Token 零费用完整链路 | T028, T054 |
| SC-003 | Codex OAuth Device Flow 授权成功 | T033, T034, T054 |
| SC-004 | octo doctor 诊断所有预定义故障 | T040, T041, T054 |
| SC-005 | --live 区分 Proxy/Provider 故障 | T040, T054 |
| SC-006 | Gateway 自动加载 .env | T049, T054 |
| SC-007 | 凭证值无明文泄露 | T043, T054 |
| SC-008 | credential store 文件权限 0o600 | T023, T043, T054 |

---

## Dependencies & Execution Order

### Phase 依赖关系

```
Phase 1 (Setup)          -- 无依赖，立即开始
  |
  v
Phase 2 (Foundational)   -- 依赖 Phase 1
  |
  v
Phase 3 (US1 - API Key)  -- 依赖 Phase 2（阻塞性）
  |
  +---> Phase 4 (US2 - Setup Token)  -- 依赖 Phase 3（复用 store + init_wizard）
  +---> Phase 6 (US4 - Doctor)       -- 依赖 Phase 3（复用 store + DX models）
  |
  v
Phase 5 (US3 - Codex OAuth)  -- 依赖 Phase 3（复用 store + init_wizard）
  |
  v
Phase 7 (US5 - 安全验证)     -- 依赖 Phase 3 ~ 5（需要所有 adapter 和 store）
Phase 8 (US6 - Handler Chain) -- 依赖 Phase 3 ~ 5（需要所有 adapter）
Phase 9 (US7 - Gateway dotenv) -- 依赖 Phase 2（仅需基础设施）
  |
  v
Phase 10 (Polish)             -- 依赖所有前序 Phase
```

### User Story 间依赖

- **US1 (API Key)** -- 核心 Story，建立 store / init_wizard / CLI 骨架，所有后续 Story 复用
- **US2 (Setup Token)** -- 依赖 US1 的 store 和 init_wizard 骨架，扩展 Token 路径
- **US3 (Codex OAuth)** -- 依赖 US1 的 store 和 init_wizard 骨架，新增 OAuth 路径
- **US4 (Doctor)** -- 依赖 US1 的 DX models 和 store，独立实现诊断逻辑
- **US5 (安全)** -- 跨 Story 验证，依赖 US1~US3 的所有 adapter 和 store
- **US6 (Handler Chain)** -- 依赖所有 adapter（US1~US3），独立实现 Chain 逻辑
- **US7 (dotenv)** -- 最低依赖，仅需 Phase 2 基础设施

### Story 内部并行机会

| Phase | 可并行任务 | 说明 |
|-------|-----------|------|
| Phase 2 | T005, T006, T007, T008, T009, T010 | 不同文件，无相互依赖 |
| Phase 2 | T012, T013, T014, T015 | 测试文件互不依赖 |
| Phase 3 | T023, T024, T025 | 测试文件互不依赖 |
| Phase 4 | T028, T029 | 测试文件互不依赖 |
| Phase 5 | T033, T034, T035 | 测试文件互不依赖 |
| Phase 6 | T040, T041 | 测试文件互不依赖 |
| Phase 10 | T050, T051, T052 | 不同文件的导出更新 |

### 推荐实现策略

**MVP First（推荐）**:
1. Phase 1 + Phase 2: 基础设施（约 2h）
2. Phase 3 (US1): API Key 完整链路 -- **MVP 交付点**
3. Phase 4 (US2) + Phase 5 (US3): 扩展两种免费认证
4. Phase 6 (US4): 诊断工具
5. Phase 7 (US5) + Phase 8 (US6) + Phase 9 (US7): P2 增强
6. Phase 10: 收尾

**理由**: US1 交付后即可完成"git clone -> 首次 LLM 调用"的核心链路，是最小可验证闭环。US2/US3 扩展认证覆盖面，US4 补充 DX，三者合计为完整 P1 交付。

---

## Notes

- [P] 标记的任务可并行执行（不同文件、无依赖）
- [USN] 标记对应所属 User Story
- Spec 要求测试：先写测试确认失败，再实现
- 每完成一个 Checkpoint 后建议运行 `uv run pytest` 验证
- 任务粒度约 50~100 行代码
- Commit 建议在每个 Phase 或 Story 完成后提交
