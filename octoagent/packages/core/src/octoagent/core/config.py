"""配置常量模块 -- 可通过环境变量覆盖

包含数据库路径、artifacts 目录、事件 payload 大小限制等可配置常量。
"""

import os
from pathlib import Path


def _get_base_dir() -> Path:
    """获取项目 data 基础目录"""
    return Path(os.environ.get("OCTOAGENT_DATA_DIR", "data"))


def get_db_path() -> str:
    """获取 SQLite 数据库路径"""
    return os.environ.get(
        "OCTOAGENT_DB_PATH",
        str(_get_base_dir() / "sqlite" / "octoagent.db"),
    )


def get_artifacts_dir() -> Path:
    """获取 Artifact 文件存储目录"""
    return Path(
        os.environ.get(
            "OCTOAGENT_ARTIFACTS_DIR",
            str(_get_base_dir() / "artifacts"),
        )
    )


# 事件 payload 最大字节数（超过此阈值的内容转存 Artifact）
EVENT_PAYLOAD_MAX_BYTES: int = int(
    os.environ.get("OCTOAGENT_EVENT_PAYLOAD_MAX_BYTES", "8192")
)

# Artifact inline 阈值（小于此值的文本内容 inline 存储）
ARTIFACT_INLINE_THRESHOLD: int = int(
    os.environ.get("OCTOAGENT_ARTIFACT_INLINE_THRESHOLD", "4096")
)

# SSE 心跳间隔（秒）
SSE_HEARTBEAT_INTERVAL: int = int(
    os.environ.get("OCTOAGENT_SSE_HEARTBEAT_INTERVAL", "15")
)

# 消息预览截断长度
MESSAGE_PREVIEW_LENGTH: int = 200
