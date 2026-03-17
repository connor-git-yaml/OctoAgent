"""infer_memory_partition() 分区推断纯函数单元测试。"""

import pytest

from octoagent.memory import MemoryPartition
from octoagent.memory.partition_inference import infer_memory_partition


class TestInferMemoryPartition:
    """覆盖各分区关键词命中和 fallback 场景。"""

    def test_empty_text_returns_work(self):
        assert infer_memory_partition("") == MemoryPartition.WORK

    def test_none_like_empty_returns_work(self):
        # 空白文本也应 fallback
        assert infer_memory_partition("   ") == MemoryPartition.WORK

    # ---- health 分区 ----
    @pytest.mark.parametrize("text", [
        "今天去医院体检，血压偏高",
        "用药提醒：每天早上吃一片降压药",
        "睡眠质量不好，需要运动改善",
        "健身计划：每周三次有氧运动",
        "My health checkup results are concerning",
    ])
    def test_health_partition(self, text: str):
        assert infer_memory_partition(text) == MemoryPartition.HEALTH

    # ---- finance 分区 ----
    @pytest.mark.parametrize("text", [
        "这个月银行账单已经到了，需要预算调整",
        "投资基金的收益需要报税",
        "薪资到账后先还信用卡",
        "Monthly budget review: income vs expense",
    ])
    def test_finance_partition(self, text: str):
        assert infer_memory_partition(text) == MemoryPartition.FINANCE

    # ---- core 分区 ----
    @pytest.mark.parametrize("text", [
        "Connor 的生日是 3 月 15 日",
        "联系方式：邮箱 test@example.com",
        "个人偏好：喜欢喝美式咖啡",
        "家庭住址变更了",
        "联系人张三的电话号码",
        "朋友推荐了一家餐厅",
    ])
    def test_core_partition(self, text: str):
        assert infer_memory_partition(text) == MemoryPartition.CORE

    # ---- chat 分区 ----
    @pytest.mark.parametrize("text", [
        "这只是一段闲聊对话，没有什么重要内容",
        "Casual chat about weekend plans",
    ])
    def test_chat_partition(self, text: str):
        assert infer_memory_partition(text) == MemoryPartition.CHAT

    # ---- work 分区（默认 fallback）----
    @pytest.mark.parametrize("text", [
        "项目进度汇报会议纪要",
        "Review the pull request before merging",
        "部署新版本到测试环境",
        "some random text without keywords",
    ])
    def test_work_partition_fallback(self, text: str):
        assert infer_memory_partition(text) == MemoryPartition.WORK

    def test_mixed_keywords_picks_highest_count(self):
        # 含多个健康关键词和一个工作关键词，应选健康
        text = "去医院体检，血压正常，体重下降了，睡眠也改善了"
        assert infer_memory_partition(text) == MemoryPartition.HEALTH

    def test_case_insensitive(self):
        text = "HEALTH checkup at the HOSPITAL"
        assert infer_memory_partition(text) == MemoryPartition.HEALTH
