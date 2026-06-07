"""SQLite 写连接级 PRAGMA 单一事实源。

connection-level PRAGMA（`foreign_keys` / `busy_timeout`）在 SQLite 中是**每连接独立**
（不跨连接共享，区别于 `journal_mode=WAL` 这种库级状态）。任何会执行写入的连接——主连接
（init_db 内部复用本 helper）与 F104 versionable append 专用独立写连接（create_store_group
注入）——都必须各自启用，否则外键约束在不同连接上行为分裂（孤儿写入 / 行为不一致）。
"""

import aiosqlite

# 连接级写锁等待兜底（毫秒），主连接与 versionable 独立连接保持一致。
WRITE_CONNECTION_BUSY_TIMEOUT_MS = 5000


async def apply_write_connection_pragmas(conn: aiosqlite.Connection) -> None:
    """对任意写连接启用连接级 PRAGMA（foreign_keys=ON + busy_timeout）。

    journal_mode=WAL 是库级状态（首个连接设置后跨连接共享），不在此 helper 内重复设置，
    避免破坏 init_db 的现有 PRAGMA 顺序（WAL 必须由主连接先建立）。
    """
    await conn.execute("PRAGMA foreign_keys = ON;")
    await conn.execute(f"PRAGMA busy_timeout = {WRITE_CONNECTION_BUSY_TIMEOUT_MS};")
