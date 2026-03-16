# Tasks: MCP 安装与生命周期管理

**Feature**: 058-mcp-install-lifecycle
**Branch**: `claude/festive-meitner`
**Input**: plan.md, spec.md, data-model.md, contracts/actions.md, contracts/session-pool.md
**Generated**: 2026-03-16

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行任务（不同文件、无依赖）
- **[USN]**: 所属 User Story（US1~US8）
- Setup/Foundational/Polish 阶段不加 [USN] 标记
- 所有文件路径相对于仓库根目录

---

## Phase 1: Setup

**Purpose**: 项目骨架与目录准备

- [x] T001 确认 `~/.octoagent/mcp-servers/` 安装目标目录存在（若不存在则 `main.py` 启动时自动创建）。验证 `data/ops/` 目录可写，用于存放 `mcp-installs.json`

---

## Phase 2: Foundational -- McpSessionPool 持久连接

**Purpose**: McpSessionPool 是 McpRegistryService 改进和后续所有 User Story 的阻塞性前置。必须先完成持久连接池，才能进行 registry 改造和安装服务开发。

**CRITICAL**: Phase 3+ 全部阻塞于本 Phase 完成。

- [x] T002 创建 `McpSessionEntry` dataclass 和 `McpSessionPool` 类骨架，包含 `__init__`、`_entries` 字典、`_lock` 定义。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_session_pool.py`（新建，~250 行）
- [x] T003 实现 `McpSessionPool.open()` 方法 -- AsyncExitStack + stdio_client + ClientSession + session.initialize()，含超时控制（init_timeout=10s）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_session_pool.py`
- [x] T004 实现 `McpSessionPool.get_session()` 方法 -- 连接状态检查 + 自动重连逻辑（reconnect_max_attempts=3）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_session_pool.py`
- [x] T005 实现 `McpSessionPool.close()` 和 `close_all()` -- exit_stack cleanup + entry 移除 + 异常捕获日志记录。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_session_pool.py`
- [x] T006 实现 `McpSessionPool.health_check()` 和 `health_check_all()` -- 通过 `session.list_tools(cursor=None)` 探测，超时 5s，失败标记 disconnected。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_session_pool.py`
- [x] T007 实现 `McpSessionPool.get_entry()` 和 `list_entries()` 只读查询方法。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_session_pool.py`

**Checkpoint**: McpSessionPool 独立模块完成，可单元测试。

---

## Phase 3: User Story 4 -- 持久连接管理提升可靠性 (Priority: P1)

**Goal**: 将 MCP server 连接从 per-operation 模式升级为持久连接池，工具调用复用同一连接，子进程崩溃后自动恢复。

**Independent Test**: 启用一个 MCP server 后连续调用其工具多次，验证不会每次启动新进程；手动终止 server 子进程后验证下次调用自动恢复。

**Why first**: US4 是基础设施改造，US1/US2（安装）和 US5（向导）都依赖 McpRegistryService 已完成 session pool 集成。

### Implementation

- [x] T008 [US4] 修改 `McpRegistryService.__init__()` -- 新增可选参数 `session_pool: McpSessionPool | None = None`，保存为 `self._session_pool`。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_registry.py`
- [x] T009 [US4] 修改 `McpRegistryService.refresh()` -- 对 enabled server 调用 `session_pool.open()` 建立持久连接；对 disabled server 调用 `session_pool.close()` 关闭连接。session_pool 为 None 时保持原逻辑。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_registry.py`
- [x] T010 [US4] 修改 `McpRegistryService._discover_server_tools()` -- 优先通过 `session_pool.get_session()` 获取 session，fallback 到原 `_open_session()`。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_registry.py`
- [x] T011 [US4] 修改 `McpRegistryService.call_tool()` -- 优先通过 `session_pool.get_session()` 获取 session，fallback 到原 `_open_session()`。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_registry.py`
- [x] T012 [US4] 新增 `McpRegistryService.shutdown()` 方法 -- 调用 `session_pool.close_all()`。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_registry.py`
- [x] T013 [US4] 修改 `main.py` lifespan -- 创建 McpSessionPool 实例，注入 McpRegistryService 构造函数；shutdown 时调用 `McpRegistryService.shutdown()`。文件: `octoagent/apps/gateway/src/octoagent/gateway/main.py`

**Checkpoint**: MCP 工具调用已通过持久连接池复用 session，per-operation 路径作为 fallback 保留。可通过启用一个 MCP server 并连续调用工具验证。

---

## Phase 4: User Story 3 -- 安装注册表持久化与追溯 (Priority: P1)

**Goal**: 建立安装注册表数据模型和持久化机制，为后续安装/卸载操作提供数据基础。

**Independent Test**: 手动构造 McpInstallRecord 写入 `mcp-installs.json`，重启后验证记录完整恢复。

**Why before US1/US2**: 安装注册表是安装/卸载操作的数据基础，US1/US2 的安装逻辑依赖 record 的写入/查询/更新。

### Implementation

- [x] T014 [P] [US3] 创建 `InstallSource`、`InstallStatus`、`McpInstallRecord`、`InstallTaskStatus`、`InstallTask` 数据模型。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`（新建）
- [x] T015 [P] [US3] 扩展 `McpProviderItem` 后端模型 -- 新增 `install_source: str = ""`、`install_version: str = ""`、`install_path: str = ""`、`installed_at: str = ""` 四个可选字段。文件: `octoagent/packages/core/src/octoagent/core/models/control_plane.py`
- [x] T016 [US3] 实现 `McpInstallerService` 类骨架 -- `__init__`（注入 McpRegistryService）、`_installs_path`、`_mcp_servers_dir`、`_install_records` 字典、`_install_tasks` 字典。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T017 [US3] 实现注册表持久化 -- `_load_installs()`（从 `data/ops/mcp-installs.json` 加载）和 `_save_installs()`（写入 JSON，含 `schema_version: 1`）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T018 [US3] 实现 `McpInstallerService.startup()` -- 加载注册表 + 检测 status="installing" 的不完整安装并标记为 "failed" + 清理残留文件。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T019 [US3] 实现 `McpInstallerService.shutdown()` -- 取消进行中的安装任务（cancel asyncio.Task）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T020 [US3] 实现 `list_installs()` 和 `get_install()` 查询方法。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T021 [US3] 修改 `ControlPlaneService.get_mcp_provider_catalog_document()` -- 合并 McpInstallRecord 数据到 McpProviderItem（install_source/install_version/install_path/installed_at）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T022 [US3] 修改 `main.py` lifespan -- 创建 McpInstallerService 实例（注入 McpRegistryService），调用 startup()；shutdown 时调用 shutdown()。绑定到 ControlPlaneService。文件: `octoagent/apps/gateway/src/octoagent/gateway/main.py`

**Checkpoint**: 安装注册表可持久化和恢复。ControlPlane catalog document 能展示安装元信息。

---

## Phase 5: User Story 1 -- 通过 Web UI 一键安装 npm MCP server (Priority: P1)

**Goal**: 用户输入 npm 包名后系统自动完成下载、部署、配置和启用。

**Independent Test**: 调用 `McpInstallerService.install(install_source="npm", package_name="@anthropic/mcp-server-files")` 验证安装目录创建、node_modules 安装、入口点检测、配置写入、工具发现完成。

### Implementation

- [x] T023 [US1] 实现包名校验工具函数 `_validate_package_name(source, name)` -- 正则校验防注入，npm 允许 `@scope/name` 格式。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T024 [US1] 实现 `_slugify_server_id(source, package_name)` -- 将包名转为安全的 server_id（替换 `@`、`/`、`.` 为 `_`）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T025 [US1] 实现路径安全检查 `_validate_install_path(path, base_dir)` -- `resolved.is_relative_to(base_dir)` 防路径遍历。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T026 [US1] 实现 `McpInstallerService.install()` 入口方法 -- 参数校验、重复安装检测、创建 InstallTask、启动 asyncio 后台任务，立即返回 task_id。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T027 [US1] 实现 `McpInstallerService.get_install_status()` -- 根据 task_id 查询 InstallTask 状态。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T028 [US1] 实现 `_install_npm()` 核心逻辑 -- 创建安装目录、`npm install --prefix`（subprocess，超时 120s）、捕获 stdout/stderr。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T029 [US1] 实现 npm 入口点检测 `_detect_npm_entrypoint()` -- 分层策略：bin 字段 -> main 字段 -> npx 回退。读取 `node_modules/{pkg}/package.json`。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T030 [US1] 实现 npm 安装后验证 -- 尝试启动 server 执行 `tools/list`，验证是有效 MCP server。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T031 [US1] 实现 npm 安装完成后的配置写入 -- 生成 McpServerConfig，调用 `_registry.save_config()` + `_registry.refresh()`，写入 McpInstallRecord，更新 InstallTask.result（version/tools_count/tools）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`

**Checkpoint**: npm 安装全流程可通过后端 API 调用完成。`mcp-installs.json` 和 `mcp-servers.json` 均正确更新，工具通过 ToolBroker 可发现。

---

## Phase 6: User Story 2 -- 通过 Web UI 一键安装 pip MCP server (Priority: P1)

**Goal**: 用户选择 pip 来源输入包名后，系统创建独立虚拟环境、安装包、自动配置并启用。

**Independent Test**: 调用 `McpInstallerService.install(install_source="pip", package_name="mcp-server-fetch")` 验证 venv 创建、pip 安装、入口点检测、配置写入完成。

### Implementation

- [x] T032 [US2] 实现 `_install_pip()` 核心逻辑 -- 创建安装目录、`python -m venv` 创建独立虚拟环境、`{venv}/bin/pip install`（subprocess，超时 120s）、提取版本号（`pip show`）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T033 [US2] 实现 pip 入口点检测 `_detect_pip_entrypoint()` -- 分层策略：扫描 `venv/bin/` 新增可执行文件 -> 匹配包名 -> `python -m` 回退。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [x] T034 [US2] 实现 pip 安装后验证与配置写入 -- 复用 T030 的验证逻辑和 T031 的配置写入逻辑（抽取为通用方法 `_finalize_install()`）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`

**Checkpoint**: pip 安装全流程可通过后端 API 调用完成，每个 pip server 拥有独立 venv。

---

## Phase 7: User Story 5 -- Web UI 安装向导体验 + Control Plane 集成 (Priority: P1)

**Goal**: 前端安装向导分步引导用户完成安装，Control Plane 提供 install/install_status/uninstall action 路由。

**Independent Test**: 在 Web UI 点击"安装"按钮，走完向导全流程（选择来源 -> 输入包名 -> 确认 -> 查看进度 -> 完成摘要）。

### Control Plane Action 实现（后端）

- [x] T035 [P] [US5] 实现 `_handle_mcp_provider_install()` action handler -- 参数校验（install_source/package_name/env）、调用 `installer.install()`、返回 task_id。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T036 [P] [US5] 实现 `_handle_mcp_provider_install_status()` action handler -- 根据 task_id 查询安装进度，返回 status/progress_message/error/result。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T037 [P] [US5] 实现 `_handle_mcp_provider_uninstall()` action handler -- 校验 server_id、调用 `installer.uninstall()`、返回结果（此处先注册 handler，uninstall 实际逻辑在 US6 实现，MVP 可返回 not_implemented）。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T038 [US5] 注册 3 个新 action 到 ControlPlaneService action dispatch -- 在 `_dispatch_action` 中新增 `mcp_provider.install`、`mcp_provider.install_status`、`mcp_provider.uninstall` 路由。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T039 [US5] 扩展 `get_mcp_provider_catalog_document()` -- 新增 install/uninstall capability 声明；summary 新增 `auto_installed_count` / `manual_count`。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`

### 前端类型扩展

- [x] T040 [P] [US5] 扩展 `McpProviderItem` 前端类型 -- 新增 `install_source`、`install_version`、`install_path`、`installed_at` 字段。文件: `octoagent/frontend/src/types/index.ts`

### 安装向导组件（前端）

- [x] T041 [US5] 创建 `McpInstallWizard.tsx` 组件骨架 -- props 定义（open/onClose/onComplete/submitAction）、向导步骤状态机（source_select -> package_input -> env_config -> confirm -> installing -> result）。文件: `octoagent/frontend/src/components/McpInstallWizard.tsx`（新建，~300 行）
- [x] T042 [US5] 实现 Step 1: 来源选择 -- npm/pip 两个选项卡，带图标和简要说明。文件: `octoagent/frontend/src/components/McpInstallWizard.tsx`
- [x] T043 [US5] 实现 Step 2: 包名输入 -- 输入框 + 格式提示（npm: `@scope/name`，pip: `name`）+ 可选环境变量 KEY=VALUE 多行输入。文件: `octoagent/frontend/src/components/McpInstallWizard.tsx`
- [x] T044 [US5] 实现 Step 3: 确认安装 -- 展示安装摘要（来源、包名、环境变量）+ "确认安装" 按钮，调用 `submitAction("mcp_provider.install", ...)`。文件: `octoagent/frontend/src/components/McpInstallWizard.tsx`
- [x] T045 [US5] 实现 Step 4: 安装进行中 -- 每 2 秒轮询 `mcp_provider.install_status`，显示 progress_message，超时 300 秒。文件: `octoagent/frontend/src/components/McpInstallWizard.tsx`
- [x] T046 [US5] 实现 Step 5: 安装结果 -- 成功展示版本号 + 工具列表 + "完成"按钮；失败展示错误信息 + "重试"/"关闭"按钮。文件: `octoagent/frontend/src/components/McpInstallWizard.tsx`

### McpProviderCenter 集成（前端）

- [x] T047 [US5] 修改 McpProviderCenter.tsx 顶栏 -- "新建"按钮旁新增"安装"按钮，点击打开 McpInstallWizard modal。文件: `octoagent/frontend/src/pages/McpProviderCenter.tsx`
- [x] T048 [US5] 修改 McpProviderCenter.tsx 列表项 -- 显示安装来源标签（npm/pip chip vs 手动配置 chip），展示安装版本号。文件: `octoagent/frontend/src/pages/McpProviderCenter.tsx`

**Checkpoint**: 用户可通过 Web UI 完整走通"安装"按钮 -> 向导 -> 选择来源 -> 输入包名 -> 确认 -> 查看进度 -> 完成摘要的全流程。

---

## Phase 8: User Story 6 -- 卸载 MCP server (Priority: P2)

**Goal**: 一键卸载已安装的 MCP server，自动清理安装文件、配置和连接。

**Independent Test**: 安装一个 MCP server 后执行卸载，验证安装文件、配置记录和连接全部被清理。

### Implementation

- [ ] T049 [US6] 实现 `McpInstallerService.uninstall()` -- 查找 InstallRecord、判断 MANUAL 来源仅删配置、更新 status="uninstalling"、调用 `_registry.delete_config()` + `refresh()`、`shutil.rmtree` 删除安装目录、删除安装记录、保存注册表。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [ ] T050 [US6] 完善 T037 中的 `_handle_mcp_provider_uninstall()` -- 替换 not_implemented 为实际调用 `installer.uninstall()`，返回 `MCP_SERVER_UNINSTALLED` + resource_refs。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [ ] T051 [US6] 前端卸载交互 -- McpProviderCenter 中对已安装 server（install_source != "" && install_source != "manual"）的删除按钮改为"卸载"，调用 `mcp_provider.uninstall` action，弹出确认对话框。文件: `octoagent/frontend/src/pages/McpProviderCenter.tsx`

**Checkpoint**: 已安装 server 可通过 Web UI 一键卸载，安装文件和配置全部清理。

---

## Phase 9: User Story 7 -- 健康检查与状态可见性 (Priority: P2)

**Goal**: Web UI 展示每个 MCP server 的实时运行状态，后端周期性健康检查。

**Independent Test**: 启用多个 MCP server，手动终止其中一个进程，验证 Web UI 状态在合理时间内更新。

### Implementation

- [ ] T052 [US7] 在 `McpRegistryService` 中新增周期性健康检查 -- 使用 `asyncio.create_task` 启动后台循环，每 30 秒调用 `session_pool.health_check_all()`，异常 server 标记 status="error"。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_registry.py`
- [ ] T053 [US7] 健康检查结果反映到 `McpServerRecord.status` -- 将 session entry 的 connected/disconnected 状态同步到 server record（available/error），使 catalog document 能展示实时状态。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_registry.py`
- [ ] T054 [US7] 前端状态指示器 -- McpProviderCenter 列表项中根据 status 字段显示颜色标记（绿色"运行中"/ 红色"已停止"/ 黄色"异常"）。文件: `octoagent/frontend/src/pages/McpProviderCenter.tsx`

**Checkpoint**: Web UI 实时显示各 MCP server 运行状态，异常能在 30 秒内反映。

---

## Phase 10: User Story 8 -- Docker 安装来源 (Priority: P2, 预留)

**Goal**: 支持从 Docker 镜像安装 MCP server。

**Independent Test**: 在安装向导选择 Docker 来源，输入镜像名，验证拉取并启动容器。

### Implementation

- [ ] T055 [US8] 实现 Docker 可用性检测 `_check_docker_available()` -- 运行 `docker version` 检查，不可用时返回友好提示。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [ ] T056 [US8] 实现 `_install_docker()` -- `docker pull` 拉取镜像、生成 `docker run` 配置（stdin/stdout 转发或 HTTP transport）、配置写入。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [ ] T057 [US8] 前端安装向导新增 Docker 来源选项 -- Step 1 新增 Docker 选项卡，选中后检测 Docker 可用性，不可用时灰化并提示。文件: `octoagent/frontend/src/components/McpInstallWizard.tsx`

**Checkpoint**: Docker 安装路径可用（取决于宿主机 Docker 环境）。

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: 事件记录、安全加固、文档、清理

- [ ] T058 [P] 安装/卸载事件记录 -- 在 McpInstallerService 的 install/uninstall 流程中生成 `mcp.server.installed` / `mcp.server.install_failed` / `mcp.server.uninstalled` 事件写入 Event Store。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [ ] T059 [P] 连接状态事件记录 -- 在 McpSessionPool 的 open/close/reconnect 中生成 `mcp.session.connected` / `mcp.session.disconnected` / `mcp.session.reconnected` / `mcp.session.health_check_failed` 事件。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_session_pool.py`
- [ ] T060 [P] 安全加固: env 隔离 -- 确保 subprocess 启动时不继承宿主进程完整 env，仅传递 per-server 配置的 env。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py` + `mcp_session_pool.py`
- [ ] T061 [P] 安全加固: integrity 校验 -- npm 安装后读取 `package-lock.json` 中的 integrity 值；pip 安装后计算 dist-info 中的 RECORD hash。记录到 McpInstallRecord.integrity。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [ ] T062 [P] structlog 结构化日志 -- 在 McpInstallerService 和 McpSessionPool 中添加 structlog 日志，覆盖安装开始/完成/失败、连接建立/断开/重连等关键路径。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py` + `mcp_session_pool.py`
- [ ] T063 [P] npm/pip 命令可用性预检 -- install() 启动时检查 `npm --version` / `pip --version` 是否可用，不可用时 fail fast 并给出友好错误。文件: `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
- [ ] T064 代码审查与清理 -- 检查所有新建/修改文件的类型注解完整性、docstring、import 整理。文件: 所有本特性涉及的文件

---

## FR 覆盖映射表

| FR | 描述 | 对应任务 |
|----|------|---------|
| FR-001 | npm 安装支持 | T023, T024, T025, T026, T028, T029, T030, T031 |
| FR-002 | pip 安装支持 | T023, T024, T025, T026, T032, T033, T034 |
| FR-003 | 独立依赖目录 | T028 (独立 node_modules), T032 (独立 venv) |
| FR-004 | 安装元数据记录 | T014, T017, T020, T031, T034 |
| FR-005 | 注册表与运行时配置分离 | T014, T017 (mcp-installs.json 独立于 mcp-servers.json) |
| FR-006 | 注册表持久化与恢复 | T017, T018 |
| FR-007 | 持久连接复用 | T002~T007, T008~T011 |
| FR-008 | 自动重连 | T004 (get_session 自动重连) |
| FR-009 | 优雅关闭 | T005 (close_all), T012 (registry shutdown), T013 (main shutdown) |
| FR-010 | "安装"入口 | T047 |
| FR-011 | 分步引导流程 | T041~T046 |
| FR-012 | 进度反馈 | T027, T036, T045 |
| FR-013 | 安装完成摘要 | T046 |
| FR-014 | 一键卸载 | T049, T050 |
| FR-015 | 手动配置仅删配置 | T049 (MANUAL 分支), T051 |
| FR-016 | 周期性健康检查 | T006, T052 |
| FR-017 | 实时运行状态展示 | T053, T054 |
| FR-018 | Docker 安装支持 | T055, T056 |
| FR-019 | Docker 不可用提示 | T055, T057 |
| FR-020 | 安装需用户确认 | T044 (确认步骤) |
| FR-021 | 路径遍历防护 | T025 |
| FR-022 | env 隔离 | T060 |
| FR-023 | 完整性校验 | T061 |
| FR-024 | 现有工具链路兼容 | T008~T011 (fallback 保留), T031 (通过 registry.save_config + refresh) |
| FR-025 | 安装后自动配置+工具发现 | T031, T034 |
| FR-026 | 事件记录 | T058, T059 |

**覆盖率**: 26/26 FR = **100%**

---

## Dependencies & Execution Order

### Phase 依赖关系

```
Phase 1 (Setup)
  |
  v
Phase 2 (McpSessionPool)  -------- BLOCKS ALL --------+
  |                                                     |
  v                                                     |
Phase 3 (US4: Registry 持久连接改造)                      |
  |                                                     |
  v                                                     |
Phase 4 (US3: 安装注册表)                                |
  |                                                     |
  +------+------+                                       |
  |             |                                       |
  v             v                                       |
Phase 5       Phase 6                                   |
(US1: npm)    (US2: pip)                                |
  |             |                                       |
  +------+------+                                       |
         |                                              |
         v                                              |
Phase 7 (US5: Web UI 向导 + Control Plane)              |
  |                                                     |
  +------+------+------+                                |
  |             |      |                                |
  v             v      v                                |
Phase 8       Phase 9  Phase 10                         |
(US6: 卸载)  (US7: 健康) (US8: Docker)                   |
  |             |      |                                |
  +------+------+------+                                |
         |                                              |
         v                                              |
Phase 11 (Polish)  <------------------------------------+
```

### User Story 间依赖

| Story | 依赖 | 说明 |
|-------|------|------|
| US4 (持久连接) | Phase 2 (McpSessionPool) | 直接消费 session pool |
| US3 (注册表) | US4 完成 | installer 需要通过改造后的 registry 写配置 |
| US1 (npm 安装) | US3 完成 | 安装需要注册表模型和持久化 |
| US2 (pip 安装) | US3 完成 | 同 US1 |
| US1 与 US2 | 互不依赖 | 可并行开发 |
| US5 (Web UI) | US1 + US2 完成 | 向导需要后端安装 API 已就绪 |
| US6 (卸载) | US5 完成 | 前端卸载入口在 US5 中建立 |
| US7 (健康检查) | US4 完成 | 依赖 session pool 的 health_check |
| US8 (Docker) | US3 完成 | 与 US1/US2 平级，但优先级低 |

### Story 内部并行机会

| Phase | 可并行任务组 | 说明 |
|-------|------------|------|
| Phase 2 | T002~T007 为严格顺序 | 同一文件，逐步构建 |
| Phase 4 | T014 + T015 可并行 | 不同文件（installer.py vs control_plane.py models） |
| Phase 5 | T023~T025 可并行 | 都是独立工具函数 |
| Phase 7 | T035 + T036 + T037 可并行 | 三个独立 action handler |
| Phase 7 | T040 可与 T035~T039 并行 | 前端类型 vs 后端 action |
| Phase 8 | T049 + T051 依赖 T050 | 后端先完成，再前端集成 |
| Phase 11 | T058~T063 全部可并行 | 不同关注点，不同文件/位置 |

---

## Implementation Strategy

### 推荐: Incremental Delivery (MVP First)

1. **Phase 1 + 2**: Setup + McpSessionPool -- 基础设施就绪
2. **Phase 3 (US4)**: Registry 持久连接改造 -- **立即可验证性能提升**
3. **Phase 4 (US3)**: 安装注册表 -- 数据层就绪
4. **Phase 5 + 6 (US1 + US2)**: npm + pip 安装 -- 并行开发，**后端 MVP 完成**
5. **Phase 7 (US5)**: Web UI 向导 -- **用户可见 MVP 完成**
6. **Phase 8 (US6)**: 卸载 -- 完善生命周期
7. **Phase 9 (US7)**: 健康检查 -- 增强可观测性
8. **Phase 10 (US8)**: Docker -- 扩展安装来源（可推迟）
9. **Phase 11**: Polish -- 事件记录 + 安全加固 + 日志

**MVP 范围**: US4 + US3 + US1 + US2 + US5 = Phase 1~7，共 48 个任务
**P2 增强**: US6 + US7 + US8 = Phase 8~10，共 6 个任务
**Polish**: Phase 11，共 7 个任务

---

## Notes

- [P] 任务 = 不同文件、无依赖，可并行执行
- [USN] 标签 = 映射到 spec.md 中的 User Story N
- 每个 User Story 独立可交付、可测试
- 每个 Checkpoint 处可暂停验证
- 安装目录始终限制在 `~/.octoagent/mcp-servers/`
- ToolBroker / SkillRunner / LiteLLMClient 全程零改动
