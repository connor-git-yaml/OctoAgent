# 接口契约: McpSessionPool

**Feature**: 058-mcp-install-lifecycle
**Date**: 2026-03-16
**Module**: `gateway/services/mcp_session_pool.py`

---

## 概述

McpSessionPool 管理 MCP server 的持久 stdio 连接。它是一个独立模块（便于单元测试），但由 McpRegistryService 独占持有和管理。外部模块（包括 McpInstallerService）不直接访问 pool。

---

## 类接口

```python
class McpSessionPool:
    """管理 MCP server 的持久连接池。"""

    def __init__(self) -> None:
        """初始化空连接池。"""

    async def open(self, server_name: str, config: McpServerConfig) -> None:
        """
        建立到指定 MCP server 的持久连接。

        如果 server_name 已有连接，先关闭旧连接再建立新连接。
        连接建立包括:
        1. 启动 stdio 子进程
        2. 建立 ClientSession
        3. 执行 session.initialize()

        Raises:
            RuntimeError: 连接建立失败（子进程启动失败、初始化超时等）

        Side Effects:
            - 启动子进程
            - 创建 McpSessionEntry 并缓存
        """

    async def get_session(self, server_name: str) -> ClientSession:
        """
        获取已建立的 ClientSession。

        如果 session 已断开，尝试自动重建连接。
        如果 server_name 没有对应 entry，抛出 KeyError。

        Raises:
            KeyError: server_name 不存在
            RuntimeError: 连接断开且重建失败

        Returns:
            活跃的 ClientSession 实例

        Thread Safety:
            通过 asyncio.Lock 保证并发安全
        """

    async def close(self, server_name: str) -> None:
        """
        关闭指定 server 的连接并清理资源。

        如果 server_name 不存在，静默返回（幂等操作）。

        Side Effects:
            - 关闭 ClientSession
            - 终止子进程
            - 清理 AsyncExitStack
            - 从 _entries 中移除
        """

    async def close_all(self) -> None:
        """
        关闭所有连接。用于系统 shutdown。

        对每个 entry 调用 close()，捕获并记录异常但不中断。
        调用完成后 _entries 为空。
        """

    async def health_check(self, server_name: str) -> bool:
        """
        探测指定 server 的连接健康状态。

        通过发送 tools/list RPC 判断 session 是否正常响应。
        超时 5 秒。

        Returns:
            True: 连接健康
            False: 连接异常或不存在

        Side Effects:
            如果健康检查失败，将 entry.status 标记为 "disconnected"
        """

    async def health_check_all(self) -> dict[str, bool]:
        """
        批量健康检查所有已连接 server。

        Returns:
            {server_name: is_healthy} 字典
        """

    def get_entry(self, server_name: str) -> McpSessionEntry | None:
        """
        获取连接条目（只读快照）。

        Returns:
            McpSessionEntry 或 None（不存在时）
        """

    def list_entries(self) -> list[McpSessionEntry]:
        """
        列出所有连接条目。

        Returns:
            McpSessionEntry 列表（快照，不保证实时一致性）
        """
```

---

## 状态转换

```
                open()
  (不存在) ──────────> connected
                          |
                          | (子进程退出 / health_check 失败)
                          v
                     disconnected
                          |
              +-----------+-----------+
              |                       |
         get_session()           close()
              |                       |
              v                       v
         reconnecting            (移除 entry)
              |
         +----+----+
         |         |
      成功       失败
         |         |
         v         v
    connected  disconnected
```

---

## 所有权与生命周期

| 角色 | 操作 |
|------|------|
| **main.py** | 创建 McpSessionPool 实例，注入 McpRegistryService |
| **McpRegistryService** | 独占持有 pool。通过 `refresh()` 触发 `pool.open()`/`pool.close()`。通过 `_discover_server_tools()` 和 `call_tool()` 调用 `pool.get_session()` |
| **McpInstallerService** | **不直接访问 pool**。安装完成后调用 `McpRegistryService.save_config()` + `refresh()`，refresh 内部通过 pool 建立新连接 |
| **ControlPlaneService** | **不直接访问 pool**。通过 McpRegistryService 间接使用 |
| **main.py shutdown** | 调用 `McpRegistryService.shutdown()`，后者调用 `pool.close_all()` |

---

## 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `init_timeout` | 10s | session.initialize() 超时 |
| `health_check_timeout` | 5s | tools/list 健康检查超时 |
| `reconnect_max_attempts` | 3 | 单次 get_session 的最大重连尝试次数 |

---

## 错误处理

| 场景 | 行为 |
|------|------|
| `open()` 子进程启动失败 | 抛出 RuntimeError，不创建 entry |
| `open()` session 初始化超时 | 抛出 RuntimeError，清理已启动的子进程 |
| `get_session()` 自动重连失败 | 抛出 RuntimeError，entry.status 保持 "disconnected" |
| `close()` 清理异常 | 记录 warning 日志，强制移除 entry |
| `close_all()` 部分 close 异常 | 记录 warning，继续关闭其他 entry |
| `health_check()` 超时 | 返回 False，标记 entry.status = "disconnected" |
