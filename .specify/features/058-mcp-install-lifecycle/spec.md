# Feature Specification: MCP 安装与生命周期管理

**Feature Branch**: `claude/festive-meitner`
**Feature ID**: 058-mcp-install-lifecycle
**Created**: 2026-03-16
**Status**: Draft
**Input**: 为 OctoAgent 设计和实现完整的 MCP（Model Context Protocol）安装与生命周期管理能力，解决当前仅有配置保存而缺少真正安装/部署能力的问题。

---

## 背景与动机

OctoAgent 当前的 MCP 管理（McpRegistryService）只有配置保存和 per-operation 工具发现能力。用户必须自行在系统上安装 MCP server 可执行文件，然后在 OctoAgent 中手动填写 command/args 等配置。这导致：

1. **安装依赖外部工具** — 如果最初安装 MCP server 的工具（如 Claude Code）被卸载，OctoAgent 的 MCP 功能随之失效
2. **配置门槛高** — 用户需要理解 command、args、环境变量等技术细节才能添加 MCP server
3. **无生命周期管理** — 缺少卸载、更新、健康监测能力；每次工具调用都启动子进程再关闭（per-operation），性能差且不可靠
4. **无安装记录** — 无法追踪已安装 server 的来源、版本、路径等元信息

本特性的目标是让用户通过 Web UI 的安装向导完成"包名输入 → 一键安装 → 自动配置 → 启用"的全流程，同时为系统提供持久连接管理和完整的生命周期维护能力。

### 关键约束

基于接缝分析（integration-gap-analysis.md）的核心发现：

- **OctoAgent 不直接使用 Pydantic AI Agent 类**，工具执行走 SkillRunner → LiteLLMSkillClient → ToolBroker → handler 链路
- **ToolBroker 是治理核心**（hook/policy/event），MCP 工具继续通过 `mcp.{server}.{tool}` 注册
- **McpRegistryService 保留并改进** — 它负责发现+注册+执行，是 MCP 的脊柱
- **不替换现有路径，只做扩展** — ToolBroker / SkillRunner / LiteLLMClient 零改动

---

## User Scenarios & Testing

### User Story 1 — 通过 Web UI 一键安装 npm MCP server（Priority: P1）

作为 OctoAgent 用户，我希望在 Web UI 中输入一个 npm 包名（例如 `@anthropic/mcp-server-files`），点击"安装"后系统自动完成下载、部署和配置，无需我手动操作命令行或填写技术参数，这样我可以快速获得新的 MCP 工具能力。

**Why this priority**: 这是整个特性的核心价值。npm 是 MCP server 生态中最主要的发布渠道，覆盖大多数官方和社区 MCP server。没有安装能力，其他所有生命周期管理都无从谈起。

**Independent Test**: 在 Web UI 点击"安装"按钮，输入一个真实 npm 包名，验证系统能自动安装并在 MCP server 列表中出现该 server，且其提供的工具可被 Agent 调用。

**Acceptance Scenarios**:

1. **Given** 用户在 Web UI 的 MCP 管理页面，**When** 点击"安装"按钮并输入 npm 包名 `@anthropic/mcp-server-files` 后确认，**Then** 系统在后台执行安装，展示安装进度，安装完成后该 server 自动出现在列表中且状态为"已启用"
2. **Given** 用户已输入 npm 包名，**When** 安装过程中网络连接中断或包名不存在，**Then** 系统显示明确的错误提示（如"包 xxx 不存在"或"网络连接失败"），不留下不完整的安装残留
3. **Given** 用户输入的 npm 包需要环境变量（如 API Key），**When** 安装完成后，**Then** 系统自动识别并弹出环境变量配置界面，引导用户填写必需的配置项

---

### User Story 2 — 通过 Web UI 一键安装 pip MCP server（Priority: P1）

作为 OctoAgent 用户，我希望能以相同的简便流程安装 Python 生态的 MCP server（例如 `mcp-server-fetch`），系统会自动创建独立虚拟环境并完成部署，这样我可以使用 Python 社区的 MCP server 而不用担心依赖冲突。

**Why this priority**: Python 是 MCP server 的第二大生态来源，且 OctoAgent 自身是 Python 项目。许多高质量的 MCP server 仅以 pip 包形式发布。与 npm 安装并列为 MVP 必要能力。

**Independent Test**: 在 Web UI 安装向导中选择"pip"来源，输入 Python 包名，验证系统创建独立虚拟环境、安装包并正确配置 server。

**Acceptance Scenarios**:

1. **Given** 用户在安装向导中选择"pip"来源，**When** 输入包名 `mcp-server-fetch` 后确认，**Then** 系统创建独立虚拟环境，安装该包，自动生成配置并启用 server
2. **Given** 系统上已有多个 pip 安装的 MCP server，**When** 查看各 server 的安装信息，**Then** 每个 server 使用独立的虚拟环境，互不影响
3. **Given** 安装的 pip 包不包含有效的 MCP server 入口点，**When** 安装完成后尝试启动，**Then** 系统报告"无法识别 MCP server 入口点"并引导用户手动配置启动命令

---

### User Story 3 — 安装注册表持久化与追溯（Priority: P1）

作为 OctoAgent 用户，我希望系统记录每个 MCP server 的安装来源、版本、安装时间和安装路径，这样我可以随时了解系统中 MCP server 的状态，也为后续的更新和卸载提供基础。

**Why this priority**: 安装注册表是安装、卸载、更新等所有后续操作的数据基础。没有安装记录，系统无法区分"手动配置的 server"和"系统安装的 server"，也无法执行自动化的生命周期管理。

**Independent Test**: 安装一个 MCP server 后，在 Web UI 中查看该 server 详情，验证安装元信息完整可见。系统重启后信息依然存在。

**Acceptance Scenarios**:

1. **Given** 用户通过安装向导安装了一个 MCP server，**When** 在 Web UI 中查看该 server 的详情，**Then** 可以看到安装来源（npm/pip）、包名、版本号、安装路径、安装时间
2. **Given** OctoAgent 进程重启，**When** 重新加载 MCP server 列表，**Then** 所有安装记录完整保留，已安装的 server 自动恢复到重启前的状态
3. **Given** 用户此前通过手动配置添加了 MCP server（非安装向导），**When** 查看 server 列表，**Then** 手动配置的 server 标识为"手动配置"，与系统安装的 server 在视觉上有明确区分

---

### User Story 4 — 持久连接管理提升可靠性（Priority: P1）

作为 OctoAgent 用户，我希望 MCP server 连接在系统运行期间保持活跃，而不是每次调用工具都重新启动子进程，这样工具调用的响应速度更快，且不会因频繁启停进程导致不稳定。

**Why this priority**: 当前 per-operation session 模式是已知的性能瓶颈和可靠性风险。每次工具调用都启动子进程开销大、延迟高，且进程启停频繁容易引发资源泄漏。持久连接是 MCP 服务可靠运行的基础。

**Independent Test**: 启用一个 MCP server 后，连续调用其工具多次，验证不会每次都启动新进程；在 server 进程意外退出后，验证下次调用能自动恢复。

**Acceptance Scenarios**:

1. **Given** 一个 MCP server 已启用并完成首次工具发现，**When** 连续调用该 server 的工具 3 次，**Then** 3 次调用复用同一个持久连接，无额外进程启动
2. **Given** MCP server 子进程意外崩溃，**When** 下一次尝试调用该 server 的工具，**Then** 系统自动重建连接并完成调用，用户无需手动干预 [AUTO-RESOLVED: 选择自动重连而非要求用户手动重启，因为 Constitution #6 "Degrade Gracefully" 要求系统自动恢复]
3. **Given** OctoAgent 正常关闭，**When** 系统执行 shutdown，**Then** 所有 MCP server 连接和子进程被优雅清理，无资源泄漏

---

### User Story 5 — Web UI 安装向导体验（Priority: P1）

作为 OctoAgent 用户，我希望安装向导是一个分步引导流程，让我在不了解技术细节的情况下也能成功安装 MCP server，这样 MCP 能力对非技术用户也是可达的。

**Why this priority**: Web UI 是用户与安装功能交互的唯一界面（MVP 阶段）。向导的质量直接决定安装功能的可用性。如果向导不够友好，用户仍然需要依赖命令行，安装功能的价值就大打折扣。

**Independent Test**: 让一位不了解 MCP 技术细节的用户尝试通过安装向导安装一个 server，验证其能在无外部指导下完成全过程。

**Acceptance Scenarios**:

1. **Given** 用户打开 MCP 管理页面，**When** 点击"安装"按钮，**Then** 弹出安装向导，第一步让用户选择安装来源（npm/pip），第二步输入包名，第三步配置必需的环境变量（如有），第四步确认安装
2. **Given** 安装正在进行中，**When** 用户查看安装向导，**Then** 可以看到当前安装步骤和进度提示（如"正在下载..."、"正在配置..."），不会出现长时间无反馈的等待
3. **Given** 安装完成，**When** 向导显示结果，**Then** 用户可以看到安装摘要（包名、版本、提供的工具列表），并可选择直接启用或稍后配置

---

### User Story 6 — 卸载 MCP server（Priority: P2）

作为 OctoAgent 用户，我希望能一键卸载不再需要的 MCP server，系统自动清理安装文件、配置和连接，这样系统不会积累无用的 server 占用资源。

**Why this priority**: 卸载是安装的对称操作，对完整生命周期管理不可或缺。但在 MVP 阶段用户可以通过禁用 server 来暂时达到类似效果，因此略低于安装优先级。

**Independent Test**: 安装一个 MCP server 后执行卸载，验证安装文件、配置记录和连接全部被清理。

**Acceptance Scenarios**:

1. **Given** 一个 MCP server 已通过安装向导安装，**When** 用户在 Web UI 中点击"卸载"并确认，**Then** 系统关闭该 server 的连接、从 ToolBroker 注销其工具、删除安装文件和依赖、清理安装记录和运行时配置
2. **Given** 用户尝试卸载一个手动配置的 server（非安装向导安装），**When** 点击"删除"，**Then** 系统仅删除配置条目（与当前行为一致），不尝试清理文件系统

---

### User Story 7 — 健康检查与状态可见性（Priority: P2）

作为 OctoAgent 用户，我希望在 Web UI 中看到每个 MCP server 的实时运行状态（运行中/已停止/异常），这样我能快速知道哪些 server 可用、哪些需要关注。

**Why this priority**: 健康检查是运维可观测性的基础要求（Constitution #8）。但 MVP 阶段用户通常通过工具调用是否成功来间接判断 server 状态，显式健康检查是更好的体验但非首日必需。

**Independent Test**: 启用多个 MCP server，手动终止其中一个的进程，验证 Web UI 在合理时间内反映状态变化。

**Acceptance Scenarios**:

1. **Given** 多个 MCP server 已启用，**When** 用户在 MCP 管理页面查看列表，**Then** 每个 server 旁显示实时状态指示（如绿色"运行中"、红色"已停止"、黄色"异常"）
2. **Given** 一个 MCP server 进程意外退出，**When** 健康检查探测到异常，**Then** Web UI 的状态指示在 30 秒内更新为"异常"，并显示简要原因

---

### User Story 8 — Docker 安装来源（Priority: P2）

作为 OctoAgent 用户，我希望能安装以 Docker 镜像形式发布的 MCP server，这样在需要更强隔离性的场景下有安全的选择。

**Why this priority**: Docker 安装提供比 npm/pip 更强的进程和文件系统隔离，满足高安全场景需求。但 MCP 生态中以 Docker 形式发布的 server 较少，且 OctoAgent 本身不强制要求 Docker 环境，因此列为 P2。

**Independent Test**: 在安装向导中选择"Docker"来源，输入镜像名，验证系统拉取镜像并通过容器运行 MCP server。

**Acceptance Scenarios**:

1. **Given** 用户在安装向导中选择"Docker"来源，**When** 输入 Docker 镜像名（如 `mcp/server-files:latest`）后确认，**Then** 系统拉取镜像、启动容器、完成 MCP server 注册
2. **Given** 用户的系统上未安装 Docker，**When** 尝试选择 Docker 安装来源，**Then** 系统检测到 Docker 不可用并给出提示"Docker 未安装，请先安装 Docker 或选择其他安装方式"

---

### Edge Cases

- **EC-01** (关联 FR-001, FR-002): 安装过程中 OctoAgent 进程被终止 — 重启后系统检测到不完整安装，自动清理残留文件并将安装状态标记为"失败"
- **EC-02** (关联 FR-003): 多个 MCP server 声明同名工具 — ToolBroker 的 `mcp.{server}.{tool}` 命名空间机制已天然隔离，无需额外处理
- **EC-03** (关联 FR-001, FR-002): npm/pip 包存在但不是有效的 MCP server — 安装完成后启动验证失败，系统标记为"安装完成但启动失败"，保留安装文件供用户排查
- **EC-04** (关联 FR-005): 持久连接的 MCP server 长时间空闲 — 连接保持活跃但不做额外操作 [AUTO-RESOLVED: MVP 阶段不实现空闲超时回收，避免增加复杂度；后续可通过配置控制空闲超时]
- **EC-05** (关联 FR-004): 安装注册表文件被意外删除或损坏 — 系统启动时检测到注册表缺失，从现有安装目录结构重建基本记录，并记录警告日志
- **EC-06** (关联 FR-006): 用户尝试安装同一个包的不同版本 — 系统提示该 server 已安装，询问用户是否要更新到新版本
- **EC-07** (关联 FR-009): 卸载正在被 Agent 使用中的 MCP server — 系统等待当前工具调用完成后再执行卸载，或在超时后强制关闭连接

---

## Requirements

### Functional Requirements

**安装能力**

- **FR-001**: 系统 MUST 支持从 npm registry 安装 MCP server，包括下载包、安装依赖到独立 node_modules 目录、自动生成运行时配置（command/args/cwd）
  - *追溯*: User Story 1
- **FR-002**: 系统 MUST 支持从 PyPI 安装 MCP server，包括创建独立虚拟环境、安装包、自动生成运行时配置（command 指向 venv 内的可执行文件）
  - *追溯*: User Story 2
- **FR-003**: 每个安装的 MCP server MUST 拥有独立的依赖目录（npm 包独立 node_modules，pip 包独立 venv），不与系统全局或其他 server 的依赖产生冲突
  - *追溯*: User Story 1, User Story 2

**安装注册表**

- **FR-004**: 系统 MUST 为每个安装的 MCP server 记录安装元数据，包括：安装来源（npm/pip）、包名、版本号、安装路径、安装时间、安装状态
  - *追溯*: User Story 3
- **FR-005**: 安装注册表 MUST 与运行时配置（mcp-servers.json）分离存储，安装记录不污染运行时配置
  - *追溯*: User Story 3
- **FR-006**: 安装注册表 MUST 持久化到磁盘，OctoAgent 进程重启后 MUST 完整恢复
  - *追溯*: User Story 3

**持久连接管理**

- **FR-007**: 系统 MUST 将 MCP server 的连接管理从 per-operation 模式改为持久连接模式，同一 server 的多次工具调用 MUST 复用同一连接
  - *追溯*: User Story 4
- **FR-008**: 持久连接 MUST 在 MCP server 子进程异常退出时自动检测并重建连接
  - *追溯*: User Story 4
- **FR-009**: OctoAgent 正常关闭时 MUST 优雅清理所有 MCP server 连接和子进程
  - *追溯*: User Story 4

**Web UI 安装向导**

- **FR-010**: Web UI MUST 在 MCP 管理页面提供"安装"入口，与现有的"新建"（手动配置）入口并列
  - *追溯*: User Story 5
- **FR-011**: 安装向导 MUST 提供分步引导流程：选择安装来源 → 输入包名 → 配置环境变量（可选） → 确认安装
  - *追溯*: User Story 5
- **FR-012**: 安装向导 MUST 在安装过程中展示进度反馈（如当前步骤、状态信息），不允许出现长时间无反馈的等待
  - *追溯*: User Story 5
- **FR-013**: 安装完成后 MUST 展示安装摘要，包括包名、版本、发现的工具列表
  - *追溯*: User Story 5

**卸载能力**

- **FR-014**: 系统 SHOULD 支持一键卸载已安装的 MCP server，清理内容包括：关闭连接、注销工具、删除安装文件和依赖、清理安装记录和运行时配置
  - *追溯*: User Story 6
- **FR-015**: 手动配置的 MCP server 执行删除时，系统 MUST 仅删除配置条目，不尝试清理文件系统
  - *追溯*: User Story 6

**健康检查与状态**

- **FR-016**: 系统 SHOULD 对已启用的 MCP server 进行周期性健康检查，检测连接和进程状态
  - *追溯*: User Story 7
- **FR-017**: Web UI SHOULD 为每个 MCP server 展示实时运行状态（运行中/已停止/异常）
  - *追溯*: User Story 7

**Docker 安装来源**

- **FR-018**: 系统 SHOULD 支持从 Docker 镜像安装 MCP server，通过容器运行并建立 stdio 或 HTTP 连接
  - *追溯*: User Story 8
- **FR-019**: Docker 安装 SHOULD 在 Docker 不可用时给出明确提示并引导用户选择其他安装方式
  - *追溯*: User Story 8

**安全与权限**

- **FR-020**: 安装操作 MUST 要求用户确认后方可执行（Constitution #4 "Side-effect Must be Two-Phase"），不允许静默自动安装
  - *追溯*: Constitution #4, User Story 1, User Story 2
- **FR-021**: 安装的 MCP server 文件 MUST 限制在指定目录范围内（`~/.octoagent/mcp-servers/`），安装过程 MUST 检测并拒绝路径遍历攻击（如 `../` 逃逸）
  - *追溯*: Constitution #5 "Least Privilege by Default"
- **FR-022**: MCP server 的环境变量（含 API Key 等敏感信息）MUST 以 per-server 隔离方式管理，子进程 MUST 不继承宿主进程的完整环境变量
  - *追溯*: Constitution #5 "Least Privilege by Default"
- **FR-023**: 安装来源 MUST 校验包的完整性（npm integrity / pip hash），检测到完整性不匹配时 MUST 中止安装并报告错误
  - *追溯*: Constitution #5 "Least Privilege by Default"

**系统集成**

- **FR-024**: 新安装的 MCP server 的工具 MUST 继续通过现有工具注册和治理链路（命名空间隔离、权限控制、策略检查、事件记录、Hook 链）完整兼容
  - *追溯*: 全部 User Stories（系统约束）
- **FR-025**: 安装服务完成后 MUST 自动将运行时配置写入现有配置管理模块并触发工具发现
  - *追溯*: User Story 1, User Story 2
- **FR-026**: 安装、卸载、连接状态变更操作 MUST 生成事件记录，写入 Event Store
  - *追溯*: 全部 User Stories（Constitution #2 "Everything is an Event"）

### Key Entities

- **MCP Server 安装记录 (McpInstallRecord)**: 描述一个已安装 MCP server 的元数据。关键属性：server_id（唯一标识）、install_source（npm/pip/docker/manual）、package_name、version、install_path、installed_at、status（installed/failed/uninstalling）。与 McpServerConfig（运行时配置）是一对一关系，通过 server_id 关联。
- **MCP Server 运行时配置 (McpServerConfig)**: 描述一个 MCP server 的运行时参数（已有实体，本次**不扩展**）。关键属性：name、command、args、env、enabled。保持现有结构不变，安装元数据通过 McpInstallRecord 独立存储，前端展示时由 ControlPlaneService 合并两个数据源。[参见 CL-003]
- **MCP Session（持久连接）**: 描述一个到 MCP server 的活跃连接。关键属性：server_name、session 状态（connected/disconnected/reconnecting）、创建时间、最后活跃时间。生命周期与 OctoAgent 进程一致。
- **安装向导状态 (InstallWizardState)**: 前端组件状态。关键属性：当前步骤（来源选择/包名输入/环境变量配置/确认安装/安装中/完成/失败）、用户输入（来源、包名、环境变量）、安装进度信息。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 用户从打开安装向导到完成一个 MCP server 的安装并看到其工具可用，整个流程不超过 3 分钟（不含网络下载时间）
- **SC-002**: 已安装 MCP server 的工具首次调用延迟（持久连接已建立后）与现有手动配置 server 的工具调用延迟相当，不引入额外的性能开销
- **SC-003**: MCP server 子进程异常退出后，系统在下一次工具调用时自动恢复连接，用户无需任何手动干预
- **SC-004**: OctoAgent 进程重启后，所有已安装的 MCP server 自动恢复到重启前的运行状态（已启用的 server 自动重连，安装记录完整保留）
- **SC-005**: 安装向导的每一步都有即时的界面反馈，任何步骤不出现超过 5 秒的无反馈等待（网络操作除外，网络操作需展示进度提示）
- **SC-006**: 通过安装向导安装的 MCP server 数量在 30 天内占新增 MCP server 总量的 80% 以上（用户偏好使用安装向导而非手动配置）

---

## Clarifications

### Session 2026-03-16

#### CL-001: 安装注册表存储格式 — JSON 文件 vs SQLite

**问题**: FR-005 要求安装注册表与运行时配置分离存储，但未明确存储格式。research-synthesis.md 建议 SQLite，integration-gap-analysis.md 示例使用 JSON 文件（`data/ops/mcp-installs.json`）。

[AUTO-CLARIFIED: JSON 文件 — 与现有 `mcp-servers.json` 运行时配置保持一致的存储模式，降低实现复杂度。安装注册表是低频写入、小规模数据（通常 <50 条记录），SQLite 的事务/查询优势在此场景无明显收益。后续若需要复杂查询（如跨表 JOIN 安装记录与事件），可迁移到 SQLite。存储路径为 `data/ops/mcp-installs.json`。]

#### CL-002: McpSessionPool 的所有权与生命周期管理

**问题**: FR-007~FR-009 要求持久连接管理，但 spec 和 gap-analysis 中对 McpSessionPool 的所有权描述存在微妙歧义：gap-analysis 建议 session pool 是 McpRegistryService 的内部实现（`_session_pool`），但同时又将其列为独立新建文件（`mcp_session_pool.py`）。需要明确：谁创建 pool、谁负责关闭、McpInstallerService 是否需要直接访问 pool。

[AUTO-CLARIFIED: McpSessionPool 作为独立模块实现（`gateway/services/mcp_session_pool.py`），但由 McpRegistryService 独占持有和管理。具体职责链路如下：
- **创建**: Gateway 启动时由 `main.py` 创建 McpSessionPool 实例，注入 McpRegistryService 构造函数
- **使用**: 仅 McpRegistryService 内部调用 `pool.get_session()` / `pool.close()` / `pool.health_check()`；外部模块（包括 McpInstallerService）不直接访问 pool
- **关闭**: Gateway shutdown 时由 McpRegistryService.shutdown() 调用 `pool.close_all()`
- **安装/卸载对 pool 的间接影响**: McpInstallerService 安装完成后调用 `McpRegistryService.save_config()` + `refresh()`，refresh 内部通过 pool 建立新连接；卸载时 McpInstallerService 调用 `McpRegistryService.delete_config()` + `refresh()`，refresh 内部通过 pool 关闭对应连接
这样保持了 McpRegistryService 作为 MCP 运行时唯一入口的脊柱角色，同时 pool 作为独立模块便于单元测试。]

#### CL-003: McpServerConfig 扩展字段 vs McpInstallRecord 关联方式

**问题**: Key Entities 中描述 McpServerConfig "新增关联属性：install_source、install_path"，同时 McpInstallRecord 也包含 install_source、install_path。这产生数据冗余，且未明确在配置更新场景下（如用户手动编辑已安装 server 的 command）两者如何保持一致。

[AUTO-CLARIFIED: McpServerConfig 不扩展 install_source/install_path 字段，保持现有结构不变。安装元数据仅存于 McpInstallRecord（`mcp-installs.json`），通过 server_id == McpServerConfig.name 关联。理由：
- FR-005 明确要求"安装记录不污染运行时配置"，在 McpServerConfig 中添加安装字段违反此原则
- 关键约束明确"ToolBroker / SkillRunner / LiteLLMClient 零改动"，McpServerConfig 是这些模块的数据契约，不宜扩展
- 前端展示安装信息时，由 ControlPlaneService 合并两个数据源（McpServerConfig + McpInstallRecord）生成 McpProviderItem，在 McpProviderItem 上添加 install_source/install_path/version 展示字段即可
- 用户手动编辑已安装 server 的 command/args 时，仅更新 McpServerConfig；McpInstallRecord 保持原始安装快照不变（记录的是"怎么安装的"，不是"当前怎么运行的"）]

#### CL-004: 安装进度反馈的通信机制

**问题**: FR-012 要求安装过程中展示进度反馈，但未指定前后端通信机制。安装是异步长时操作（npm install 可能耗时 30s+），当前 ControlPlane 的 action 机制是 request-response 模式（submitAction → 等待 result），不支持中间进度推送。

[AUTO-CLARIFIED: 采用轮询（polling）模式，而非新增 SSE 通道。具体方案：
- 后端：`mcp_provider.install` action 立即返回一个 `install_task_id`，安装在后台异步执行。McpInstallerService 维护一个内存中的安装任务状态字典（task_id -> {status, progress_message, error}）
- 前端：安装向导在收到 task_id 后，定时轮询（每 2 秒）一个新的查询 action `mcp_provider.install_status`（传入 task_id），获取当前进度
- 安装完成后：McpInstallerService 更新任务状态为 completed/failed，同时触发 McpRegistryService.refresh() 刷新配置和工具发现。前端轮询到终态后停止轮询，展示安装摘要
- 理由：OctoAgent 已有 SSE 通道（snapshot push），但 snapshot 是全量推送机制，不适合细粒度的安装进度。新增专用 SSE 通道增加复杂度且 MVP 阶段无必要。轮询实现简单，2 秒间隔对安装体验无感知差异。]

#### CL-005: npm 包入口点自动检测策略

**问题**: FR-001 要求"自动生成运行时配置（command/args/cwd）"，但未说明如何从 npm 包中自动确定 MCP server 的启动命令。npm 包的入口点格式多样：有的在 package.json 的 `bin` 字段声明可执行文件，有的需要通过 `npx` 运行，有的需要 `node` 直接执行特定文件。pip 包有类似问题（console_scripts 入口点 vs `python -m` 模块）。

[AUTO-CLARIFIED: 采用分层检测策略，优先自动检测，检测失败时回退到合理默认值并允许用户修正：
- **npm 检测策略**（按优先级）：
  1. 读取安装后的 `node_modules/{package}/package.json`，检查 `bin` 字段 → 如有，command 设为 bin 路径的绝对路径
  2. 如无 bin，检查 package.json 的 `main` 字段 → command="node"，args=[main 路径]
  3. 如都无，回退到 command="npx"，args=["-y", package_name]（利用 npx 的自动解析能力）
- **pip 检测策略**（按优先级）：
  1. 安装后扫描 venv/bin/ 目录中新增的可执行文件 → 如有唯一新增文件，command 设为该文件的绝对路径
  2. 如新增多个可执行文件，检查是否有与包名匹配的（如 mcp-server-fetch 包 → mcp-server-fetch 可执行文件）
  3. 如无法确定，回退到 command="python"（venv 内的），args=["-m", package_name.replace("-", "_")]
- **所有来源通用**：检测完成后，McpInstallerService 尝试启动 server 并执行一次 `tools/list` 验证。验证成功则自动完成配置；验证失败则在安装向导中提示用户"自动检测的启动命令可能不正确"，允许用户手动修正 command/args 后重新验证。]
