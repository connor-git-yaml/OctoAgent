"""Memory 模型测试。"""

from datetime import UTC, datetime

import pytest
from octoagent.memory import EvidenceRef, MemoryPartition, WriteAction, WriteProposal


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
