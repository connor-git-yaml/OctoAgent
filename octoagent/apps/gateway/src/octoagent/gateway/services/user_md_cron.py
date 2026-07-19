"""F146 — USER.md 驱动的 cron 服务共享件（三姊妹 F102/F127/F111 收敛）。

- ``read_user_md_disk_first``（件①）：读 USER.md **磁盘优先**（F111 Codex round9
  P1 修法推广）——盘外编辑（``octo behavior edit`` / 直接改盘）对 cron 配置即时
  可见；读盘失败 → snapshot live state 兜底 → None（Constitution #6 降级链与
  推广前逐级等价）。
- ``register_cron_job``（件③）：注册/替换 cron job 并返回注册 key——三服务据此
  在 cron tick 内比对 key 实现统一热重载语义「改 USER.md 时间字段后，下一次已
  排定 tick 读盘生效、无需重启」。

设计边界：
- 本模块**只依赖 core + APScheduler**，不 import 任何 gateway service（叶子模块，
  无循环 import 风险）。
- log 事件名经 ``log_prefix`` 参数化，与三服务既有事件名逐一对上
  （``{prefix}_read_user_md_disk_failed`` / ``{prefix}_read_user_md_failed``），
  运维 grep 面零变更。
"""

from __future__ import annotations

import zoneinfo
from datetime import UTC
from typing import TYPE_CHECKING, Any

import structlog
from apscheduler.triggers.cron import CronTrigger
from octoagent.core.behavior_workspace import resolve_write_path_by_file_id

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


logger = structlog.get_logger(__name__)


def read_user_md_disk_first(
    project_root: Path,
    snapshot_store: Any,
    *,
    log_prefix: str,
) -> str | None:
    """读 USER.md：**磁盘优先**，snapshot live state 兜底（F111 修法，F146 推广）。

    只读 ``snapshot_store.get_live_state``（内存 dict，bootstrap 填充 +
    write_through 更新）的旧实现让盘外编辑到重启前不可见——cron 配置读取不在
    prompt 装配路径（live-state 间接层是为 prefix cache 设计的），磁盘（USER.md
    本就是 SoT，F084）才是语义正确的来源。

    降级链（#6，与推广前逐级等价）：
    1. 盘上 ``behavior/system/USER.md`` 存在 → 读盘返回
    2. 读盘失败/文件缺失 → ``snapshot_store.get_live_state("USER.md")`` 兜底
    3. live state 不可用/异常 → None（config 层落默认值）

    Args:
        project_root: 项目根目录（USER.md 是 SHARED 文件，slug 无关单一路径）
        snapshot_store: 提供 ``get_live_state`` 的对象（duck-typed；测试 Fake 可用）
        log_prefix: log 事件名前缀（``daily_routine`` / ``consolidation`` /
            ``behavior_compact``），保持三服务既有事件名不变

    Returns:
        USER.md 全文；两级都不可用时 None
    """
    try:
        resolved = resolve_write_path_by_file_id(project_root, "USER.md")
        if resolved.exists():
            return resolved.read_text(encoding="utf-8")
    except Exception:
        logger.exception(f"{log_prefix}_read_user_md_disk_failed")
    get_live = getattr(snapshot_store, "get_live_state", None)
    if get_live is None:
        return None
    try:
        result = get_live("USER.md")
        if isinstance(result, str):
            return result
        return None
    except Exception:
        logger.exception(f"{log_prefix}_read_user_md_failed")
        return None


def register_cron_job(
    scheduler: Any,
    *,
    job_id: str,
    callback: Callable[..., Any],
    cron_expr: str,
    timezone_name: str,
    misfire_grace_sec: int,
) -> tuple[str, str]:
    """注册/替换 cron job，返回注册 key ``(cron_expr, timezone_name)``（F146 件③）。

    ``replace_existing=True``：既服务 startup 首次注册，也服务 tick 内热重载重注册
    ——APScheduler 在 job 回调内替换自身 job 定义是标准安全操作（jobstore 条目被
    替换重算 next_fire，运行中的本次调用不受影响）。

    ``timezone_name`` 非法时降级 UTC（与三服务原 ``_register_cron`` 内联行为一致；
    上游 ``extract_user_timezone_from_user_md`` / env 校验已挡非法值，此处是
    defense-in-depth）。注册 key 记录**请求名**而非降级结果——极端非法名场景下
    key 比对可能触发一次幂等重注册，无行为影响。

    Args:
        scheduler: 裸 APScheduler 实例（调用方传 ``self._scheduler._scheduler``）
        job_id: job 唯一标识
        callback: cron 触发回调
        cron_expr: crontab 表达式（``config.to_crontab()``）
        timezone_name: 生效 IANA 时区名（已经 ``_resolve_user_timezone`` 派生）
        misfire_grace_sec: misfire 宽限秒数

    Returns:
        注册 key ``(cron_expr, timezone_name)``——调用方存下，tick 内比对实现热重载
    """
    try:
        tz = zoneinfo.ZoneInfo(timezone_name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        tz = UTC
    scheduler.add_job(
        callback,
        trigger=CronTrigger.from_crontab(cron_expr, timezone=tz),
        id=job_id,
        replace_existing=True,
        misfire_grace_time=misfire_grace_sec,
    )
    return (cron_expr, timezone_name)


__all__ = [
    "read_user_md_disk_first",
    "register_cron_job",
]
