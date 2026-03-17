"""分区推断：基于关键词匹配将文本内容映射到 MemoryPartition。

此模块为分区推断的单一事实源，被 agent_context（新写入路径）和
migration_063（存量重分配）共同使用，确保关键词表一致性。
"""

from __future__ import annotations

from .enums import MemoryPartition

# 每个分区对应一组中英文关键词，匹配时忽略大小写
PARTITION_KEYWORDS: dict[MemoryPartition, list[str]] = {
    MemoryPartition.HEALTH: [
        "体检", "用药", "健身", "医院", "血压", "体重", "睡眠", "运动",
        "健康", "心率", "过敏", "疫苗", "病历", "处方", "复查",
        "health", "medical", "exercise", "workout", "medicine",
        "hospital", "blood pressure", "weight", "sleep",
    ],
    MemoryPartition.FINANCE: [
        "银行", "投资", "预算", "报销", "薪资", "财务", "税",
        "理财", "基金", "股票", "贷款", "信用卡", "账单", "收入", "支出",
        "finance", "budget", "salary", "tax", "investment", "bank",
        "expense", "income",
    ],
    MemoryPartition.CORE: [
        "姓名", "生日", "偏好", "习惯", "联系方式", "家庭", "住址",
        "昵称", "邮箱", "电话", "地址", "爱好", "性格", "个人信息",
        "联系人", "朋友", "同事",
        "name", "birthday", "preference", "habit", "contact",
        "address", "email", "phone", "hobby",
    ],
    MemoryPartition.CHAT: [
        "聊天", "对话", "闲聊",
        "chat", "conversation",
    ],
}


def infer_memory_partition(text: str) -> MemoryPartition:
    """基于关键词匹配推断文本所属的 MemoryPartition。

    遍历各分区关键词列表，统计命中次数，取最高命中的分区。
    无任何命中时 fallback 到 WORK（零 LLM 调用开销）。
    """
    if not text:
        return MemoryPartition.WORK

    lowered = text.lower()
    best_partition = MemoryPartition.WORK
    best_count = 0

    for partition, keywords in PARTITION_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in lowered)
        if count > best_count:
            best_count = count
            best_partition = partition

    return best_partition
