# 快速上手: MCP 安装与生命周期管理

**Feature**: 058-mcp-install-lifecycle
**适用开发者**: 参与本 Feature 实现的 OctoAgent 贡献者

---

## 前置条件

- Node.js 18+（npm MCP server 安装需要）
- Python 3.12+（pip MCP server 安装需要）
- OctoAgent 开发环境已搭建（`uv sync` 完成）

## 实现顺序建议

按依赖关系从底向上实现:

### Phase 1: McpSessionPool（基础层，无外部依赖）

```
文件: gateway/services/mcp_session_pool.py
测试: tests/unit/test_mcp_session_pool.py
```

1. 实现 `McpSessionEntry` dataclass
2. 实现 `McpSessionPool.open()` -- 建立持久连接
3. 实现 `McpSessionPool.get_session()` -- 获取连接（含自动重连）
4. 实现 `McpSessionPool.close()` / `close_all()` -- 清理
5. 实现 `McpSessionPool.health_check()` -- 健康探测
6. 编写单元测试（mock stdio_client + ClientSession）

### Phase 2: McpRegistryService 改进（依赖 Phase 1）

```
文件: gateway/services/mcp_registry.py (修改)
测试: tests/unit/test_mcp_registry.py (扩展)
```

1. `__init__` 新增 `session_pool` 可选参数
2. `refresh()` 中对 enabled server 调用 `pool.open()`
3. `_discover_server_tools()` 优先使用 `pool.get_session()`
4. `call_tool()` 优先使用 `pool.get_session()`
5. 新增 `shutdown()` 方法
6. 确保 session_pool=None 时完全向后兼容

### Phase 3: McpInstallerService（依赖 Phase 2）

```
文件: gateway/services/mcp_installer.py
测试: tests/unit/test_mcp_installer.py
```

1. 实现 `McpInstallRecord` / `InstallTask` 数据模型
2. 实现安装注册表读写（`_load_installs` / `_save_installs`）
3. 实现 `_install_npm()` -- npm 安装策略
4. 实现 `_install_pip()` -- pip 安装策略
5. 实现 `install()` -- 异步安装任务调度
6. 实现 `uninstall()` -- 卸载流程
7. 实现 `startup()` -- 加载注册表 + 检查不完整安装
8. 编写单元测试（mock subprocess + McpRegistryService）

### Phase 4: ControlPlaneService 集成（依赖 Phase 3）

```
文件: gateway/services/control_plane.py (修改)
文件: gateway/main.py (修改)
```

1. main.py 中创建 McpSessionPool + McpInstallerService，注入依赖
2. ControlPlaneService 绑定 McpInstallerService
3. 新增 `_handle_mcp_provider_install()` action handler
4. 新增 `_handle_mcp_provider_install_status()` action handler
5. 新增 `_handle_mcp_provider_uninstall()` action handler
6. 修改 `get_mcp_provider_catalog_document()` 合并安装记录

### Phase 5: 数据模型扩展（依赖 Phase 4）

```
文件: core/models/control_plane.py (修改)
文件: frontend/src/types/index.ts (修改)
```

1. McpProviderItem 新增 4 个字段（install_source, install_version, install_path, installed_at）
2. 前端 TypeScript 类型同步更新

### Phase 6: 前端安装向导（依赖 Phase 5）

```
文件: frontend/src/components/McpInstallWizard.tsx (新建)
文件: frontend/src/pages/McpProviderCenter.tsx (修改)
```

1. 实现 McpInstallWizard 组件（5 步向导）
2. McpProviderCenter 新增"安装"按钮
3. Provider 列表显示安装来源标签
4. 已安装 server 显示卸载按钮

---

## 关键文件速查

| 用途 | 文件路径 |
|------|---------|
| 安装服务 | `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_installer.py` |
| 连接池 | `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_session_pool.py` |
| 注册服务 | `octoagent/apps/gateway/src/octoagent/gateway/services/mcp_registry.py` |
| Control Plane | `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` |
| 启动入口 | `octoagent/apps/gateway/src/octoagent/gateway/main.py` |
| 数据模型 | `octoagent/packages/core/src/octoagent/core/models/control_plane.py` |
| 安装向导 | `octoagent/frontend/src/components/McpInstallWizard.tsx` |
| MCP 管理页 | `octoagent/frontend/src/pages/McpProviderCenter.tsx` |
| 前端类型 | `octoagent/frontend/src/types/index.ts` |
| 安装注册表 | `data/ops/mcp-installs.json` (运行时生成) |
| 运行时配置 | `data/ops/mcp-servers.json` (已有) |
| 安装目录 | `~/.octoagent/mcp-servers/` (运行时生成) |

---

## 手动验证步骤

### 验证 npm 安装

1. 启动 OctoAgent
2. 打开 Web UI -> MCP Providers 页面
3. 点击"安装" -> 选择 npm -> 输入 `@anthropic/mcp-server-files`
4. 确认安装 -> 等待安装完成
5. 验证:
   - Provider 列表出现 mcp_server_files，状态为"运行中"
   - `~/.octoagent/mcp-servers/mcp_server_files/` 目录存在
   - `data/ops/mcp-installs.json` 包含安装记录
   - `data/ops/mcp-servers.json` 包含运行时配置
   - 在 Agent 对话中调用 server 的工具能正常工作

### 验证 pip 安装

1. 同上步骤，选择 pip -> 输入 `mcp-server-fetch`
2. 验证独立 venv: `~/.octoagent/mcp-servers/mcp_server_fetch/venv/` 存在

### 验证持久连接

1. 安装一个 MCP server 并启用
2. 连续调用其工具 3 次
3. 验证日志中没有重复的"子进程启动"记录（确认复用连接）

### 验证卸载

1. 在 MCP Providers 页面点击已安装 server 的"卸载"
2. 确认后验证:
   - Provider 从列表消失
   - 安装目录被删除
   - 安装记录和运行时配置被清理
