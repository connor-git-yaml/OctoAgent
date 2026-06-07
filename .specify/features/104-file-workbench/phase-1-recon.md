# F104 Phase 1 — T1.3 事务模型硬 gate 实测结论（权威）

> 日期：2026-06-07　方法：aiosqlite 实测脚本（复刻 `create_store_group` 配置：`aiosqlite.connect(db_path)` 无 isolation_level 参数 + WAL）
> **本文是 T1.3 硬 gate 产物，覆盖 plan §1.2 的 BEGIN IMMEDIATE 文案——Phase 1 实现以本文方案为准。**

## 实测结果

| # | 验证项 | 结果 |
|---|--------|------|
| 1 | 默认 `isolation_level` | **`''`**（隐式事务：DML 前自动 BEGIN，需手动 commit）|
| 2 | INSERT 后 `in_transaction` | **True**（隐式 BEGIN 已开）|
| 2 | 隐式事务内 `BEGIN IMMEDIATE` | ❌ **ERROR: "cannot start a transaction within a transaction"** |
| 3 | 隐式事务内 `SAVEPOINT`+`ROLLBACK TO`+`RELEASE` | ✅ **OK**（保留主表行 main_row2 + 撤回退 attempt1、留 attempt2）|
| 4 | `isolation_level=None`(autocommit) + 手动 `BEGIN IMMEDIATE` | ✅ OK |
| 5 | autocommit 裸 `SAVEPOINT` | ✅ OK（SAVEPOINT 自身开事务）|

## 关键结论（推翻 plan 原 BEGIN IMMEDIATE 方案）

**plan §1.2 原定 "`BEGIN IMMEDIATE` 包主表+版本" 在 OctoAgent 默认配置下不可行**（实测 #2：主表 INSERT 已开隐式事务，再 BEGIN IMMEDIATE 报错）。改全局 `isolation_level=None`（实测 #4 可行）会改变所有现有写路径行为（高 regression），不取。

## 选定方案（实测验证可行，无 BEGIN IMMEDIATE）

versionable=True 路径——`_write_lock`（asyncio.Lock）串行化 + **隐式事务 + SAVEPOINT 重试**：

```python
# artifact_store.__init__: self._write_lock = asyncio.Lock()
async with self._write_lock:                         # 串行化 versionable 写 = 原 BEGIN IMMEDIATE 写锁的等价作用
    try:
        # 主表 INSERT 自动开隐式事务（isolation_level=''，实测 #2），不再显式 BEGIN
        await conn.execute("INSERT INTO artifacts ...")
        for attempt in range(MAX_VERSION_RETRY):     # =3
            await conn.execute("SAVEPOINT sp_ver")    # 隐式事务内 SAVEPOINT（实测 #3 OK）
            next_no = COALESCE(MAX(version_no),0)+1 WHERE (task_id, logical_file_id)
            try:
                await conn.execute("INSERT INTO artifact_versions ... version_no=next_no")
                await conn.execute("RELEASE sp_ver"); break
            except aiosqlite.IntegrityError:          # UNIQUE 冲突
                await conn.execute("ROLLBACK TO sp_ver")   # 撤版本 INSERT、保留主表行（实测 #3）
                if attempt == MAX_VERSION_RETRY-1: raise
        await conn.commit()                            # 提交隐式事务（主表+版本原子，FR-021）
    except Exception:
        await conn.rollback()                          # 失败回滚主表+版本，无脏事务
        # 事务外 durable 失败信号：structlog.warning + event_store.append_event_committed(独立提交)
        raise
```

要点：
- **`_write_lock` 取代 BEGIN IMMEDIATE 的写锁作用**（aiosqlite 单连接 + asyncio.Lock 串行化 versionable 写之间）
- **SAVEPOINT 重试**在隐式事务内可行（实测 #3），满足 SAVEPOINT 粒度要求（Codex high #2）
- 主表 INSERT + 版本 INSERT 同一隐式事务，commit 原子（FR-021）
- 默认 versionable=False 路径**不变**（不开 _write_lock、不碰版本表，0 regression）

## mixed-writer 写并发模型（用户拍板：实测驱动）

- 单 conn 进程级共享（StoreGroup）；`isolation_level=''` 隐式事务是**连接级**
- `task_service.py` 无 `asyncio.gather`/`create_task`/`TaskGroup`（grep）→ **倾向单 task 顺序写**
- **残留约束**：若多 task 真并发，默认写在 versionable 隐式事务期间 INSERT → 同连接级事务被一起 commit。当前证据倾向顺序队列 → `_write_lock`+隐式事务+SAVEPOINT 足够（v0.1）。真并发完全正确需连接级事务管理（架构 follow-up 超 v0.1，已知约束记录）。
- **T5.1**：测调度串行化不变量（versionable 写串行 + 版本号连续）；合成 versionable+默认交错标 **xfail/非目标**（不声明"互不污染"）。
