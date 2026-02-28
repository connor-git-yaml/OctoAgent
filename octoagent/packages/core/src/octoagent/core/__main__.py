"""CLI 入口模块 -- python -m octoagent.core <command>

支持的命令：
  rebuild-projections  从 events 表重建 tasks 表
"""

import asyncio
import sys

from .config import get_artifacts_dir, get_db_path


def main() -> None:
    """CLI 主入口"""
    if len(sys.argv) < 2:
        print("用法: python -m octoagent.core <command>")
        print("命令:")
        print("  rebuild-projections  从 events 表重建 tasks 表")
        sys.exit(1)

    command = sys.argv[1]

    if command == "rebuild-projections":
        asyncio.run(rebuild_projections())
    else:
        print(f"未知命令: {command}")
        print("可用命令: rebuild-projections")
        sys.exit(1)


async def rebuild_projections() -> None:
    """执行 Projection 重建"""
    from .projection import rebuild_all
    from .store import create_store_group

    db_path = get_db_path()
    artifacts_dir = get_artifacts_dir()

    print(f"数据库路径: {db_path}")
    print(f"Artifacts 目录: {artifacts_dir}")
    print("开始重建 Projection...")

    store_group = await create_store_group(db_path, artifacts_dir)

    try:
        event_count = await rebuild_all(
            store_group.conn,
            store_group.event_store,
            store_group.task_store,
        )
        print(f"重建完成，处理 {event_count} 条事件")
    finally:
        await store_group.conn.close()


if __name__ == "__main__":
    main()
