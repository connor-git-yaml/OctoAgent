# Feature Specification: Auth Adapter + DX 工具

**Feature Branch**: `feat/003-auth-adapter-dx`
**Created**: 2026-03-01
**Status**: Draft v3（合并原 003.5）
**Input**: User description: "完成 Feature 003 — Auth Adapter + DX 工具。构建完整 Auth 基础设施，支持 OpenAI/OpenRouter API Key、Anthropic Setup Token、Codex OAuth 三种认证模式；引导式配置降低首次部署门槛。"
**Blueprint 依据**: SS8.9.4（Auth Adapter）、SS12.9（DX 工具）
**Scope 冻结**: `docs/feature-003-scope.md`（Frozen v3）
**前序依赖**: Feature 002（LiteLLM Proxy 集成）已交付

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 使用 API Key 完成首次认证配置 (Priority: P1)

作为首次使用 OctoAgent 的开发者，我希望通过交互式引导完成 LLM Provider 的 API Key 配置，使得系统能立即调用 LLM 服务，而无需手动编辑多个配置文件。

**Why this priority**: 这是最基础的认证场景。绝大多数 Provider（OpenAI、OpenRouter、Anthropic 标准模式）使用 API Key 认证。没有有效凭证，系统无法调用任何 LLM 服务——这是所有后续功能的前提。

**Independent Test**: 可通过运行 `octo init` 选择一个 Provider（如 OpenRouter）、输入 API Key、检查 `.env`/`.env.litellm`/`litellm-config.yaml` 是否正确生成来完整测试。配合 `octo doctor --live` 验证端到端连通性。

**Acceptance Scenarios**:

1. **Given** 开发者首次 clone 项目且未配置任何凭证, **When** 运行 `octo init` 并选择 OpenRouter Provider 和输入 API Key, **Then** 系统自动生成 `.env`、`.env.litellm` 和 `litellm-config.yaml` 文件，API Key 被安全存储到 credential store，后续 Gateway 启动可正常调用 LLM。
2. **Given** 开发者已完成 `octo init`, **When** Gateway 启动, **Then** 系统自动加载 `.env` 中的配置，AuthAdapter 从 credential store 解析出 API Key 并注入运行时环境，无需手动 `source .env`。
3. **Given** 开发者输入了无效格式的 API Key, **When** `octo init` 接收到输入, **Then** 系统提示格式校验失败，要求重新输入，不写入无效凭证。

---

### User Story 2 - 使用 Anthropic Setup Token 免费开发测试 (Priority: P1)

作为希望零成本试用 OctoAgent 的开发者，我希望系统支持 Anthropic Setup Token（`sk-ant-oat01-*`），使得我可以利用 Claude 免费额度进行开发和测试，无需先购买 API 额度。

**Why this priority**: Setup Token 是降低入门门槛的核心差异化功能。它让新用户无需付费即可体验完整的 LLM 调用链路，直接影响产品的首次体验质量。

**Independent Test**: 可通过 `octo init` 选择 Anthropic Setup Token 模式、输入合法的 `sk-ant-oat01-` Token，验证系统将其存入 credential store 并标记过期时间。随后通过 `octo doctor --live` 发送一次 LLM 调用验证连通性。

**Acceptance Scenarios**:

1. **Given** 开发者拥有合法的 Anthropic Setup Token, **When** 通过 `octo init` 选择 Anthropic Provider 的 Setup Token 模式并输入 Token, **Then** 系统验证 Token 格式（以 `sk-ant-oat01-` 开头）、存入 credential store 并记录过期时间，生成相应的 `.env.litellm` 配置。
2. **Given** 已配置的 Setup Token 已过期, **When** 系统在 LLM 调用前执行凭证检查, **Then** 系统提示用户 Token 已过期，并引导用户生成新的 Setup Token 或切换到 API Key 模式。
3. **Given** 开发者输入的 Token 不以 `sk-ant-oat01-` 开头, **When** `octo init` 校验 Token 格式, **Then** 系统拒绝该输入，明确提示 Setup Token 的格式要求。

---

### User Story 3 - 使用 Codex OAuth 免费开发测试 (Priority: P1)

作为 OpenAI Codex 用户，我希望系统支持 Codex OAuth Device Flow 授权，使得我可以利用 Codex 免费额度进行开发和测试。

**Why this priority**: Codex OAuth 是第二条免费认证通道，与 Setup Token 共同覆盖两大主流 LLM Provider（OpenAI + Anthropic）的免费场景，最大化降低入门门槛。

**Independent Test**: 可通过 `octo init` 选择 Codex OAuth 模式，触发 Device Flow（浏览器授权），验证 token 被持久化到 credential store，随后通过 `octo doctor --live` 验证连通性。

**Acceptance Scenarios**:

1. **Given** 开发者选择 Codex OAuth 模式, **When** 通过 `octo init` 触发 Device Flow, **Then** 系统打开浏览器显示授权页面，轮询等待用户授权，授权成功后将 token 存入 credential store。
2. **Given** Device Flow 授权超时（用户未在浏览器中确认）, **When** 轮询超时, **Then** 系统提示授权超时并建议重试或切换到 API Key 模式。
3. **Given** Codex OAuth token 已存入 credential store, **When** Gateway 启动并解析凭证, **Then** AuthAdapter 正确解析 OAuth token 用于 LLM 调用。

---

### User Story 4 - 诊断环境配置问题 (Priority: P1)

作为遇到 LLM 调用失败的开发者，我希望有一个一键诊断工具帮我快速定位配置问题（缺失文件、无效凭证、服务不可达），而不是逐个排查环境变量和服务状态。

**Why this priority**: 诊断工具是开发者体验的关键保障。Auth 配置出错是最常见的首次使用障碍。配置与诊断是配对交付的。

**Independent Test**: 可通过模拟各种故障场景（删除 `.env`、设置无效 Key、关闭 Proxy 容器），运行 `octo doctor` 验证诊断输出是否准确定位了每个问题。

**Acceptance Scenarios**:

1. **Given** 开发者已完成配置且环境正常, **When** 运行 `octo doctor`, **Then** 所有检查项均通过，输出清晰的检查摘要和"就绪"状态。
2. **Given** `.env` 文件缺失, **When** 运行 `octo doctor`, **Then** 系统明确报告 `.env` 缺失，并建议运行 `octo init` 修复。
3. **Given** credential store 中的凭证无效或已过期, **When** 运行 `octo doctor`, **Then** 系统报告凭证问题的具体原因（格式无效/已过期/空值），并给出修复建议。
4. **Given** LiteLLM Proxy 容器未运行, **When** 运行 `octo doctor`, **Then** 系统报告 Proxy 不可达，提示启动 Proxy 的命令。
5. **Given** 开发者使用 `--live` 标志, **When** 运行 `octo doctor --live`, **Then** 系统发送一次 cheap 模型 ping 验证端到端连通性，报告是否能成功完成 LLM 调用。

---

### User Story 5 - 凭证安全存储与脱敏 (Priority: P2)

作为安全意识较强的开发者，我希望凭证被安全存储在独立文件中、不出现在日志和事件流中，使得即使有人查看运行日志或 Event Store，也无法获取我的 API Key 或 Token。

**Why this priority**: 凭证安全是 Constitution C5（Least Privilege）的硬性要求。归为 P2 是因为系统在安全机制不完善时仍可功能性运行，但必须在 MVP 中交付。

**Independent Test**: 可通过配置凭证后检查 auth-profiles.json 文件权限、查看系统结构化日志输出中是否包含明文凭证、检查 Event Store 中凭证相关事件是否脱敏来验证。

**Acceptance Scenarios**:

1. **Given** 用户通过 `octo init` 配置了 API Key, **When** 检查 credential store 文件, **Then** 凭证存储在 `~/.octoagent/auth-profiles.json` 中，文件权限仅限当前用户读写。
2. **Given** 系统运行中使用了凭证进行 LLM 调用, **When** 查看系统结构化日志输出, **Then** 任何凭证值均以脱敏形式显示（如 `sk-***abc`），不出现完整明文。
3. **Given** 凭证加载或过期事件发生, **When** 查看 Event Store 中的事件记录, **Then** 事件中仅记录凭证元信息（provider、类型、时间戳），不包含凭证值本身。

---

### User Story 6 - 多 Provider 凭证管理与切换 (Priority: P2)

作为同时使用多个 LLM Provider 的开发者，我希望系统能管理多组凭证，并根据 Handler Chain 的优先级自动选择合适的凭证，使得我可以灵活配置 fallback 策略而无需手动切换。

**Why this priority**: 多 Provider 支持是 OctoAgent 架构的核心优势之一，也是 FallbackManager 发挥作用的前提。归为 P2 是因为单 Provider 即可完成 MVP 功能验证，多 Provider 是增强。

**Independent Test**: 可通过 `octo init` 配置两个不同 Provider 的凭证，验证 credential store 中正确存储多组凭证，Handler Chain 按优先级解析。

**Acceptance Scenarios**:

1. **Given** 开发者先后通过 `octo init` 配置了 OpenRouter 和 Anthropic 两个 Provider, **When** 查看 credential store, **Then** 两组凭证分别以独立 profile 存储，互不干扰。
2. **Given** 多 Provider 已配置, **When** 系统运行时解析凭证, **Then** Handler Chain 按照显式 profile > credential store > 环境变量 > 默认值的优先级链解析。
3. **Given** 首选 Provider 的凭证无效, **When** 系统尝试使用该凭证, **Then** Handler Chain 自动尝试下一个可用 Provider 的凭证（对齐 FallbackManager 降级逻辑）。

---

### User Story 7 - Gateway 启动自动加载环境配置 (Priority: P2)

作为日常开发的开发者，我希望 Gateway 启动时自动加载 `.env` 文件中的环境变量，使得我不需要每次手动执行 `source .env` 就能正常运行系统。

**Why this priority**: 消除重复的手动操作，提升日常开发体验。归为 P2 是因为虽然手动 `source .env` 也能工作，但自动加载是 DX 承诺的基本体验。

**Independent Test**: 可通过不执行 `source .env`、直接启动 Gateway，验证环境变量是否被正确加载、LLM 调用是否正常工作来测试。

**Acceptance Scenarios**:

1. **Given** `.env` 文件存在且包含有效配置, **When** Gateway 启动, **Then** 系统自动加载 `.env` 中的环境变量，无需手动 source。
2. **Given** 环境变量已手动设置（如在 Docker 容器中）, **When** Gateway 启动并执行 dotenv 加载, **Then** 已设置的环境变量不被 `.env` 文件覆盖（环境变量优先级高于文件）。
3. **Given** `.env` 文件不存在, **When** Gateway 启动, **Then** 系统正常启动不报错，使用现有环境变量或默认值运行。

---

### Edge Cases

- **EC-1** (关联 FR-004, Story 2): Setup Token 过期时间无法从 Token 本身解析 -- 系统应在首次存储时记录获取时间，并依据默认过期策略（24 小时）估算过期时间。默认值可通过 `OCTOAGENT_SETUP_TOKEN_TTL_HOURS` 环境变量覆盖。
- **EC-2** (关联 FR-006, Story 5): credential store 文件被外部工具修改导致 JSON 格式损坏 -- 系统应在读取时验证 JSON 完整性，损坏时备份原文件并提示用户重新配置。
- **EC-3** (关联 FR-007, Story 1): `octo init` 执行到一半被中断（Ctrl+C）-- 已写入的部分配置应保持一致性，不产生半成品的 `.env` 文件；下次运行应检测并提示是否覆盖。
- **EC-4** (关联 FR-003, Story 6): Handler Chain 中所有 Provider 的凭证均无效 -- 系统应明确报告"无可用凭证"，降级到 echo 模式（对齐 C6 优雅降级），并建议运行 `octo doctor` 诊断。
- **EC-5** (关联 FR-006, Story 5): 多进程同时写入 credential store -- 系统应使用文件锁防止竞态条件，写入失败时重试有限次数后报错。
- **EC-6** (关联 FR-008, Story 4): `octo doctor --live` 时 LiteLLM Proxy 可达但上游 LLM Provider 不可达 -- 系统应区分 Proxy 层故障和 Provider 层故障，分别给出对应的修复建议。
- **EC-7** (关联 FR-009, Story 7): `.env` 文件包含语法错误（如未闭合引号）-- 解析失败时系统应记录警告日志并继续启动，不因配置文件格式问题导致 Gateway 崩溃。
- **EC-8** (关联 FR-005, Story 3): Codex OAuth 端点不可达或返回错误 -- 系统应明确报告 OAuth 服务不可用，建议切换到 API Key 模式。

---

## Requirements *(mandatory)*

### Functional Requirements

**凭证数据模型**

- **FR-001**: 系统 MUST 支持三种凭证类型：API Key 凭证（标准 Provider 密钥）、Token 凭证（带过期时间的临时 Token，如 Anthropic Setup Token）、OAuth 凭证（包含访问令牌和刷新令牌的 OAuth 凭证）。每种凭证类型包含 Provider 标识和凭证值，凭证值使用安全字符串类型存储。
  *Traces to: Story 1, Story 2, Story 3, Story 6*

**AuthAdapter 接口**

- **FR-002**: 系统 MUST 提供统一的认证适配器接口，包含以下能力：(1) 解析当前可用的凭证值、(2) 刷新过期凭证（不支持刷新的适配器应明确声明）、(3) 检查凭证是否已过期。
  *Traces to: Story 1, Story 2, Story 3, Story 6*

**API Key 认证**

- **FR-003**: 系统 MUST 提供 API Key 适配器，能从 credential store 或环境变量中解析 API Key。支持所有标准 API Key Provider（OpenAI、OpenRouter、Anthropic 等）。API Key 不过期，refresh 操作应返回空值表示不支持自动刷新。
  *Traces to: Story 1, Story 6*

**Anthropic Setup Token 认证**

- **FR-004**: 系统 MUST 提供 Anthropic Setup Token 适配器，能验证 Token 格式（以 `sk-ant-oat01-` 前缀识别）、检测过期状态，并在过期时提示用户重新获取。Setup Token 过期检测 MUST 基于记录的获取时间和默认过期策略（24 小时 TTL，可通过环境变量覆盖）。
  *Traces to: Story 2*

**Codex OAuth 认证**

- **FR-005**: 系统 MUST 提供 Codex OAuth 适配器，支持 Device Flow 授权流程：(1) 生成设备授权请求、(2) 引导用户在浏览器中完成授权、(3) 轮询授权服务器获取 token、(4) 将 token 持久化到 credential store。授权超时 SHOULD 提示用户重试或切换到 API Key 模式。
  *Traces to: Story 3*

**Credential Store**

- **FR-006**: 系统 MUST 提供持久化的凭证存储，存储位置为用户主目录下的专用配置目录（`~/.octoagent/auth-profiles.json`）。存储操作 MUST 使用文件锁防止并发写入冲突。读取操作 MUST 验证文件完整性。
  *Traces to: Story 1, Story 2, Story 3, Story 5, Story 6*

**交互式引导配置（`octo init`）**

- **FR-007**: 系统 MUST 提供交互式命令行引导工具，完成以下配置流程：(1) 检测并提示选择运行模式（echo/litellm）、(2) 列出支持的 Provider 及认证模式供用户选择（API Key / Setup Token / Codex OAuth）、(3) 引导用户输入凭证或触发 OAuth 流程并进行格式校验、(4) 自动生成 Master Key、(5) 检测 Docker 可用性、(6) 生成 `.env`、`.env.litellm` 和 `litellm-config.yaml` 配置文件、(7) 输出配置摘要和下一步操作提示。
  *Traces to: Story 1, Story 2, Story 3*

**环境诊断（`octo doctor`）**

- **FR-008**: 系统 MUST 提供环境诊断命令行工具，检查项包含：Python 版本、uv 工具链、`.env` 和 `.env.litellm` 文件、LLM 运行模式、Proxy 密钥配置、Docker 服务状态、LiteLLM Proxy 可达性、数据存储可写性、credential store 凭证有效性（包含过期检测）。检查项 MUST 分为"必须通过"（阻断）和"建议通过"（警告）两级。系统 SHOULD 支持 `--live` 标志，发送一次 cheap 模型调用验证端到端连通性。
  *Traces to: Story 4*

**dotenv 自动加载**

- **FR-009**: Gateway 启动时 MUST 自动加载项目根目录的 `.env` 文件。加载 MUST 遵循"不覆盖"原则——已存在的环境变量优先级高于文件中的值。`.env` 文件不存在时 MUST 静默跳过，不影响启动。
  *Traces to: Story 7*

**Handler Chain**

- **FR-010**: 系统 MUST 实现 Handler Chain 模式进行凭证解析：每个 Provider 对应一个 handler，按 Chain of Responsibility 模式依次匹配。解析优先级为：显式指定的 profile > credential store > 环境变量 > 默认值。当所有 handler 均无法解析有效凭证时，系统 SHOULD 降级到 echo 模式并记录警告。
  *Traces to: Story 6*

**凭证脱敏**

- **FR-011**: 凭证值 MUST NOT 出现在以下位置的明文输出中：(1) 系统结构化日志、(2) Event Store 事件记录、(3) LLM 上下文（发送给模型的 prompt）。日志中引用凭证时 MUST 使用脱敏格式（仅显示前缀和末尾少量字符）。
  *Traces to: Story 5; Constitution C5 合规*

**凭证生命周期事件**

- **FR-012**: 凭证生命周期事件（加载、过期、失效）MUST 记录到 Event Store，但仅包含元信息（provider、类型、时间戳），不包含凭证值。系统 MUST 在 EventType 枚举中新增对应的凭证事件类型。
  *Traces to: Story 5; Constitution C2 合规*

**Config / Credential 分离**

- **FR-013**: 系统 MUST 将配置元数据与凭证值物理隔离。配置层（`.env`、`.env.litellm`）存储 Provider 选择、Proxy 地址等元数据；凭证层（`auth-profiles.json`）存储实际密钥。凭证文件 MUST 在 `.gitignore` 中排除版本管理。
  *Traces to: Story 5; Constitution C5 合规*

### Key Entities

- **Credential（凭证）**: 用户用于访问 LLM Provider 的认证信息。包含三种子类型：API Key（永久密钥）、Token（临时令牌，有过期时间）、OAuth（访问令牌 + 刷新令牌）。每个凭证关联一个 Provider 标识。
- **Credential Store（凭证存储）**: 凭证的持久化存储容器，以文件形式存在于用户主目录下。支持多 Provider 多凭证的 profile 管理。
- **AuthAdapter（认证适配器）**: 封装特定认证模式的逻辑单元，负责凭证的解析、有效性检查和刷新。每种认证模式对应一个适配器实现。
- **Handler Chain（处理器链）**: 按优先级排列的 AuthAdapter 序列，运行时依次尝试解析凭证，直到找到有效凭证或穷尽所有选项。
- **Provider Profile（Provider 配置档）**: 一组关联的配置元数据和凭证，标识一个特定的 LLM Provider 连接。一个 credential store 可包含多个 profile。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 新用户从 `git clone` 到首次成功调用 LLM 的路径可在 3 分钟内完成（通过 `octo init` 引导）。
- **SC-002**: 使用 Anthropic Setup Token 的用户可以零费用完成从凭证配置到真实 LLM 调用的完整链路。
- **SC-003**: 使用 Codex OAuth 的用户可以通过 Device Flow 完成授权并成功调用 LLM。
- **SC-004**: `octo doctor` 能准确诊断所有预定义的故障场景（缺失 `.env`、无效凭证、过期 Token、Proxy 不可达），每种故障场景输出明确的错误描述和修复建议。
- **SC-005**: `octo doctor --live` 能通过发送一次 cheap 模型请求验证端到端连通性，并区分 Proxy 层故障与 Provider 层故障。
- **SC-006**: Gateway 启动后自动加载 `.env`，开发者无需手动执行 `source .env` 即可正常运行所有功能。
- **SC-007**: 在所有运行日志、Event Store 事件、LLM 上下文中，凭证值均以脱敏形式出现，无完整明文泄露。
- **SC-008**: credential store 文件权限仅限当前用户读写（600 或等效），凭证文件不被版本管理追踪。

---

## Scope Exclusions

以下内容明确不在 Feature 003 范围内：

- **OAuth token 自动刷新后台任务** -- M2 实现
- **Azure AD / GCP Vertex AI 认证** -- LiteLLM Proxy 内置支持，无需应用层实现
- **GUI 配置界面** -- M1 仅提供 CLI
- **多 Agent credential 继承** -- M2 实现

---

## Appendix: Constitution Compliance Notes

| Constitution 条款 | 合规要求 | 对应 FR |
| --- | --- | --- |
| C2 (Everything is an Event) | 凭证生命周期事件（加载、过期、失效）必须记录到 Event Store；EventType 枚举新增凭证事件类型 | FR-012 |
| C5 (Least Privilege) | 凭证与配置物理隔离；凭证不进入日志/事件/LLM 上下文 | FR-011, FR-013 |
| C6 (Degrade Gracefully) | 凭证全部失效时降级到 echo 模式，不整体不可用 | FR-010 |
| C7 (User-in-Control) | `octo init` 交互式确认；`octo doctor` 可视化诊断 | FR-007, FR-008 |

---

## Clarifications

### Session 2026-03-01

**Q1 - `octo init` 产出是否包含 `litellm-config.yaml`?**
- **状态**: [AUTO-CLARIFIED: 包含]
- **理由**: Blueprint SS12.9.1 明确列出产出文件

**Q2 - `AuthAdapter.resolve()` 返回类型?**
- **状态**: [AUTO-CLARIFIED: 返回 `str`]
- **理由**: Blueprint SS8.9.4 代码示例 `async def resolve(self) -> str`

**Q3 - Setup Token 默认过期时长?**
- **状态**: [AUTO-CLARIFIED: 默认 24 小时，可通过 `OCTOAGENT_SETUP_TOKEN_TTL_HOURS` 覆盖]
- **理由**: Anthropic 未公开精确值，24h 为社区保守估算

**Q4 - 凭证注入 Proxy 机制?**
- **状态**: [AUTO-CLARIFIED: `.env.litellm` 环境变量注入]
- **理由**: M1 仅支持静态密钥，动态 API 属 M2 范畴

### Session 2026-03-01 v3（合并 003.5）

**Q5 - Feature 003 Scope 最终定义**
- **状态**: [USER-DECIDED: 003 和 003.5 合并，一期完成]
- **影响**: 三种 Adapter（ApiKey + Setup Token + Codex OAuth）均在 003 交付
- **理由**: 用户决策——Auth 能力完整交付，避免分期增加集成成本
