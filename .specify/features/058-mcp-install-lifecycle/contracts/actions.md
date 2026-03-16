# API 契约: MCP Provider Actions

**Feature**: 058-mcp-install-lifecycle
**Date**: 2026-03-16
**Protocol**: OctoAgent Control Plane Action (submitAction)

---

## 概述

本文档定义 MCP 安装生命周期管理相关的 Control Plane action 契约。所有 action 通过现有 `submitAction` 机制调用，遵循 `ActionRequestEnvelope -> ActionResultEnvelope` 模式。

---

## Action: mcp_provider.install

启动 MCP server 异步安装任务。

### Request

```typescript
submitAction("mcp_provider.install", {
  install_source: "npm" | "pip",      // 必填，安装来源
  package_name: string,                // 必填，包名
  env?: Record<string, string>,        // 可选，环境变量
})
```

### Response (成功)

```typescript
{
  status: "completed",
  code: "MCP_INSTALL_STARTED",
  message: "MCP server 安装已启动",
  data: {
    task_id: string,                   // 安装任务 ID，用于轮询进度
    server_id: string,                 // 预计的 server ID
  },
  resource_refs: [
    { resource_type: "mcp_provider_catalog", resource_id: "mcp-providers:catalog" },
  ]
}
```

### Errors

| code | message | 场景 |
|------|---------|------|
| `MCP_INSTALLER_UNAVAILABLE` | MCP Installer 未绑定 | McpInstallerService 未初始化 |
| `MCP_INSTALL_SOURCE_INVALID` | 安装来源不合法 | install_source 不是 npm/pip |
| `MCP_PACKAGE_NAME_REQUIRED` | 包名不能为空 | package_name 为空 |
| `MCP_PACKAGE_NAME_INVALID` | 包名格式不合法 | 包名包含危险字符 |
| `MCP_SERVER_ALREADY_INSTALLED` | 该 MCP server 已安装 | server_id 已存在于安装注册表 |

---

## Action: mcp_provider.install_status

查询安装任务进度。前端每 2 秒轮询一次。

### Request

```typescript
submitAction("mcp_provider.install_status", {
  task_id: string,                     // 必填，安装任务 ID
})
```

### Response (成功)

```typescript
{
  status: "completed",
  code: "MCP_INSTALL_STATUS",
  message: "安装状态查询成功",
  data: {
    task_id: string,
    status: "pending" | "running" | "completed" | "failed",
    progress_message: string,          // 当前进度描述
    error: string,                     // 失败原因（status=failed 时有值）
    result: {                          // 安装结果（status=completed 时有值）
      server_id: string,
      version: string,
      install_path: string,
      command: string,
      tools_count: number,
      tools: Array<{
        name: string,
        description: string,
      }>,
    } | null,
  },
}
```

### Errors

| code | message | 场景 |
|------|---------|------|
| `MCP_INSTALLER_UNAVAILABLE` | MCP Installer 未绑定 | McpInstallerService 未初始化 |
| `MCP_INSTALL_TASK_NOT_FOUND` | 安装任务不存在 | task_id 无效或已过期 |

---

## Action: mcp_provider.uninstall

卸载已安装的 MCP server。同步执行（卸载通常较快）。

### Request

```typescript
submitAction("mcp_provider.uninstall", {
  server_id: string,                   // 必填，要卸载的 server ID
})
```

### Response (成功)

```typescript
{
  status: "completed",
  code: "MCP_SERVER_UNINSTALLED",
  message: "MCP server 已卸载",
  data: {
    server_id: string,
    install_source: string,
    cleaned_path: string,              // 已清理的安装目录
  },
  resource_refs: [
    { resource_type: "mcp_provider_catalog", resource_id: "mcp-providers:catalog" },
    { resource_type: "capability_pack", resource_id: "capability:bundled" },
    { resource_type: "skill_governance", resource_id: "skills:governance" },
  ]
}
```

### Errors

| code | message | 场景 |
|------|---------|------|
| `MCP_INSTALLER_UNAVAILABLE` | MCP Installer 未绑定 | McpInstallerService 未初始化 |
| `MCP_SERVER_ID_REQUIRED` | server_id 不能为空 | server_id 为空 |
| `MCP_SERVER_NOT_INSTALLED` | 该 MCP server 未安装 | server_id 不存在于安装注册表 |

---

## 现有 Action 变更

### mcp_provider.save (不变)

保持现有行为不变。用于手动配置 MCP server。

### mcp_provider.delete (不变)

保持现有行为不变。对于通过安装向导安装的 server，前端应引导用户使用 `mcp_provider.uninstall`（完整卸载）而非 `mcp_provider.delete`（仅删除配置）。

---

## McpProviderCatalogDocument 扩展

catalog document 的 `capabilities` 数组新增安装能力声明:

```python
capabilities=[
    # 现有
    ControlPlaneCapability(
        capability_id="mcp_provider.save",
        label="手动添加 MCP Provider",
        action_id="mcp_provider.save",
    ),
    # 新增
    ControlPlaneCapability(
        capability_id="mcp_provider.install",
        label="安装 MCP Provider",
        action_id="mcp_provider.install",
    ),
    ControlPlaneCapability(
        capability_id="mcp_provider.uninstall",
        label="卸载 MCP Provider",
        action_id="mcp_provider.uninstall",
    ),
]
```

`summary` 字段扩展:

```python
summary={
    "installed_count": len(items),                              # 现有
    "enabled_count": len([i for i in items if i.enabled]),      # 现有
    "healthy_count": len([i for i in items if i.status == "available"]),  # 现有
    # 新增
    "auto_installed_count": len([i for i in items if i.install_source and i.install_source != "manual"]),
    "manual_count": len([i for i in items if not i.install_source or i.install_source == "manual"]),
}
```
