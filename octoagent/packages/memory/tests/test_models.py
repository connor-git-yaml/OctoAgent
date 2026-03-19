"""Memory 模型测试。"""

from datetime import UTC, datetime

import pytest
from octoagent.memory import (
    BrowseGroup,
    BrowseItem,
    BrowseResult,
    EvidenceRef,
    MemoryPartition,
    SorStatus,
    WriteAction,
    WriteProposal,
)


class TestWriteProposal:
    def test_add_requires_subject_key(self):
        with pytest.raises(ValueError):
            WriteProposal(
                proposal_id="01JTEST_PROPOSAL_0000000001",
                scope_id="work/project-x",
                partition=MemoryPartition.WORK,
                action=WriteAction.ADD,
                subject_key=None,
                content="new state",
                rationale="update",
                confidence=0.8,
                evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
                created_at=datetime.now(UTC),
            )

    def test_add_requires_evidence(self):
        with pytest.raises(ValueError):
            WriteProposal(
                proposal_id="01JTEST_PROPOSAL_0000000002",
                scope_id="work/project-x",
                partition=MemoryPartition.WORK,
                action=WriteAction.ADD,
                subject_key="work.project-x.status",
                content="new state",
                rationale="update",
                confidence=0.8,
                evidence_refs=[],
                created_at=datetime.now(UTC),
            )

    def test_none_action_allows_empty_fields(self):
        proposal = WriteProposal(
            proposal_id="01JTEST_PROPOSAL_0000000003",
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.NONE,
            rationale="noop",
            confidence=0.0,
            evidence_refs=[],
            created_at=datetime.now(UTC),
        )
        assert proposal.subject_key is None


class TestEnumExtensions:
    """T006: 验证新增枚举值存在且值正确。"""

    def test_sor_status_archived(self):
        assert SorStatus.ARCHIVED == "archived"
        assert SorStatus.ARCHIVED.value == "archived"
        # 确认不影响已有值
        assert SorStatus.CURRENT == "current"
        assert SorStatus.SUPERSEDED == "superseded"
        assert SorStatus.DELETED == "deleted"

    def test_memory_partition_solution(self):
        assert MemoryPartition.SOLUTION == "solution"
        assert MemoryPartition.SOLUTION.value == "solution"
        # 确认不影响已有值
        assert MemoryPartition.CORE == "core"
        assert MemoryPartition.WORK == "work"

    def test_write_action_merge(self):
        assert WriteAction.MERGE == "merge"
        assert WriteAction.MERGE.value == "merge"
        # 确认不影响已有值
        assert WriteAction.ADD == "add"
        assert WriteAction.UPDATE == "update"
        assert WriteAction.DELETE == "delete"
        assert WriteAction.NONE == "none"


class TestBrowseModels:
    """T007: BrowseItem / BrowseGroup / BrowseResult 序列化/反序列化测试。"""

    def test_browse_item_defaults(self):
        item = BrowseItem(subject_key="用户偏好/编程语言", partition="core")
        assert item.summary == ""
        assert item.status == "current"
        assert item.version == 1
        assert item.updated_at is None

    def test_browse_item_full(self):
        now = datetime.now(UTC)
        item = BrowseItem(
            subject_key="妈妈/兴趣爱好",
            partition="core",
            summary="妈妈喜欢园艺和烹饪...",
            status="current",
            version=3,
            updated_at=now,
        )
        data = item.model_dump(mode="json")
        restored = BrowseItem.model_validate(data)
        assert restored.subject_key == "妈妈/兴趣爱好"
        assert restored.version == 3

    def test_browse_group(self):
        now = datetime.now(UTC)
        group = BrowseGroup(
            key="core",
            count=2,
            items=[
                BrowseItem(subject_key="a", partition="core", updated_at=now),
                BrowseItem(subject_key="b", partition="core", updated_at=now),
            ],
            latest_updated_at=now,
        )
        data = group.model_dump(mode="json")
        restored = BrowseGroup.model_validate(data)
        assert restored.count == 2
        assert len(restored.items) == 2

    def test_browse_result_empty(self):
        result = BrowseResult()
        assert result.groups == []
        assert result.total_count == 0
        assert result.has_more is False
        assert result.offset == 0
        assert result.limit == 20

    def test_browse_result_round_trip(self):
        now = datetime.now(UTC)
        result = BrowseResult(
            groups=[
                BrowseGroup(
                    key="work",
                    count=5,
                    items=[
                        BrowseItem(
                            subject_key="项目决策/选型",
                            partition="work",
                            summary="选用 Python 3.12...",
                            version=2,
                            updated_at=now,
                        )
                    ],
                    latest_updated_at=now,
                )
            ],
            total_count=42,
            has_more=True,
            offset=0,
            limit=20,
        )
        data = result.model_dump(mode="json")
        restored = BrowseResult.model_validate(data)
        assert restored.total_count == 42
        assert restored.has_more is True
        assert len(restored.groups) == 1
        assert restored.groups[0].key == "work"
        assert restored.groups[0].items[0].subject_key == "项目决策/选型"
