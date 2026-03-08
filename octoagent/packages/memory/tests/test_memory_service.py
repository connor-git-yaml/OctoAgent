"""MemoryService 测试。"""

from octoagent.memory import (
    EvidenceRef,
    MemoryAccessDeniedError,
    MemoryAccessPolicy,
    MemoryLayer,
    MemoryPartition,
    ProposalNotValidatedError,
    VaultAccessDecision,
    VaultAccessGrantStatus,
    VaultAccessRequestStatus,
    WriteAction,
)


class TestMemoryService:
    async def test_add_then_update_keeps_single_current(self, memory_service, memory_store):
        add = await memory_service.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="work.project-x.status",
            content="running",
            rationale="initial sync",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        )
        add_validation = await memory_service.validate_proposal(add.proposal_id)
        assert add_validation.accepted is True
        add_commit = await memory_service.commit_memory(add.proposal_id)
        assert add_commit.sor_id is not None

        update = await memory_service.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.UPDATE,
            subject_key="work.project-x.status",
            content="done",
            rationale="project delivered",
            confidence=0.95,
            evidence_refs=[EvidenceRef(ref_id="artifact-2", ref_type="artifact")],
            expected_version=1,
        )
        update_validation = await memory_service.validate_proposal(update.proposal_id)
        assert update_validation.accepted is True
        update_commit = await memory_service.commit_memory(update.proposal_id)
        assert update_commit.sor_id is not None

        current = await memory_store.get_current_sor("work/project-x", "work.project-x.status")
        history = await memory_store.list_sor_history("work/project-x", "work.project-x.status")
        assert current is not None
        assert current.version == 2
        assert current.content == "done"
        assert len([item for item in history if item.status.value == "current"]) == 1
        assert len(history) == 2

    async def test_invalid_proposal_rejected(self, memory_service):
        proposal = await memory_service.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="work.project-x.status",
            content="running",
            rationale="missing evidence in store",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="missing-fragment", ref_type="fragment")],
        )
        validation = await memory_service.validate_proposal(proposal.proposal_id)
        assert validation.accepted is False
        assert "fragment evidence 不存在: missing-fragment" in validation.errors

    async def test_commit_requires_validation(self, memory_service):
        proposal = await memory_service.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="work.project-x.status",
            content="running",
            rationale="initial sync",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        )
        try:
            await memory_service.commit_memory(proposal.proposal_id)
            raise AssertionError("expected ProposalNotValidatedError")
        except ProposalNotValidatedError:
            pass

    async def test_sensitive_partition_creates_vault_and_denies_default_access(
        self,
        memory_service,
    ):
        proposal = await memory_service.propose_write(
            scope_id="profile/user",
            partition=MemoryPartition.HEALTH,
            action=WriteAction.ADD,
            subject_key="profile.user.health.note",
            content="blood pressure raw data",
            rationale="health note updated",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        )
        validation = await memory_service.validate_proposal(proposal.proposal_id)
        assert validation.accepted is True
        result = await memory_service.commit_memory(proposal.proposal_id)
        assert result.vault_id is not None

        hits = await memory_service.search_memory(scope_id="profile/user")
        assert hits == []

        try:
            await memory_service.get_memory(result.vault_id, layer=MemoryLayer.VAULT)
            raise AssertionError("expected MemoryAccessDeniedError")
        except MemoryAccessDeniedError:
            pass

        vault = await memory_service.get_memory(
            result.vault_id,
            layer=MemoryLayer.VAULT,
            policy=MemoryAccessPolicy(allow_vault=True),
        )
        assert vault is not None

    async def test_stale_validated_update_is_rejected_at_commit(
        self,
        memory_service,
        memory_store,
    ):
        add = await memory_service.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="work.project-x.status",
            content="running",
            rationale="initial sync",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        )
        await memory_service.validate_proposal(add.proposal_id)
        await memory_service.commit_memory(add.proposal_id)

        update_a = await memory_service.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.UPDATE,
            subject_key="work.project-x.status",
            content="done",
            rationale="update a",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-2", ref_type="artifact")],
            expected_version=1,
        )
        update_b = await memory_service.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.UPDATE,
            subject_key="work.project-x.status",
            content="archived",
            rationale="update b",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-3", ref_type="artifact")],
            expected_version=1,
        )
        validation_a = await memory_service.validate_proposal(update_a.proposal_id)
        validation_b = await memory_service.validate_proposal(update_b.proposal_id)
        assert validation_a.accepted is True
        assert validation_b.accepted is True

        await memory_service.commit_memory(update_a.proposal_id)
        try:
            await memory_service.commit_memory(update_b.proposal_id)
            raise AssertionError("expected ProposalNotValidatedError")
        except ProposalNotValidatedError:
            pass

        current = await memory_store.get_current_sor("work/project-x", "work.project-x.status")
        assert current is not None
        assert current.version == 2
        assert current.content == "done"

    async def test_stale_validated_delete_is_rejected_at_commit(
        self,
        memory_service,
        memory_store,
    ):
        add = await memory_service.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="work.project-x.status",
            content="running",
            rationale="initial sync",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        )
        await memory_service.validate_proposal(add.proposal_id)
        await memory_service.commit_memory(add.proposal_id)

        delete = await memory_service.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.DELETE,
            subject_key="work.project-x.status",
            content=None,
            rationale="delete stale state",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-2", ref_type="artifact")],
            expected_version=1,
        )
        update = await memory_service.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.UPDATE,
            subject_key="work.project-x.status",
            content="done",
            rationale="fresh state",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-3", ref_type="artifact")],
            expected_version=1,
        )
        delete_validation = await memory_service.validate_proposal(delete.proposal_id)
        update_validation = await memory_service.validate_proposal(update.proposal_id)
        assert delete_validation.accepted is True
        assert update_validation.accepted is True

        await memory_service.commit_memory(update.proposal_id)
        try:
            await memory_service.commit_memory(delete.proposal_id)
            raise AssertionError("expected ProposalNotValidatedError")
        except ProposalNotValidatedError:
            pass

        current = await memory_store.get_current_sor("work/project-x", "work.project-x.status")
        assert current is not None
        assert current.version == 2
        assert current.content == "done"

    async def test_before_compaction_flush_does_not_write_sor(self, memory_service, memory_store):
        flush = await memory_service.before_compaction_flush(
            scope_id="work/project-x",
            summary="recent conversation summary",
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            partition=MemoryPartition.WORK,
            subject_key="work.project-x.summary",
        )
        assert flush.proposal is not None
        current = await memory_store.get_current_sor("work/project-x", "work.project-x.summary")
        assert current is None

    async def test_vault_access_request_resolve_and_retrieval_audit(self, memory_service):
        request = await memory_service.create_vault_access_request(
            project_id="project-default",
            workspace_id="workspace-primary",
            scope_id="memory/project-x",
            partition=MemoryPartition.HEALTH,
            subject_key="profile.user.health.note",
            requester_actor_id="user:web",
            requester_actor_label="Owner",
            reason="排障需要查看敏感引用",
        )
        assert request.status is VaultAccessRequestStatus.PENDING

        resolved, grant = await memory_service.resolve_vault_access_request(
            request.request_id,
            decision=VaultAccessDecision.APPROVE,
            granted_by_actor_id="user:web",
            granted_by_actor_label="Owner",
        )
        assert resolved.status is VaultAccessRequestStatus.APPROVED
        assert grant is not None
        assert grant.status is VaultAccessGrantStatus.ACTIVE

        latest_grant = await memory_service.get_latest_valid_vault_grant(
            actor_id="user:web",
            project_id="project-default",
            workspace_id="workspace-primary",
            scope_id="memory/project-x",
            partition=MemoryPartition.HEALTH,
            subject_key="profile.user.health.note",
        )
        assert latest_grant is not None
        assert latest_grant.grant_id == grant.grant_id

        audit = await memory_service.record_vault_retrieval_audit(
            actor_id="user:web",
            actor_label="Owner",
            project_id="project-default",
            workspace_id="workspace-primary",
            scope_id="memory/project-x",
            partition=MemoryPartition.HEALTH,
            subject_key="profile.user.health.note",
            reason_code="VAULT_RETRIEVE_AUTHORIZED",
            authorized=True,
            grant_id=grant.grant_id,
            retrieved_vault_ids=["vault-1"],
        )
        assert audit.authorized is True
        audits = await memory_service.list_vault_retrieval_audits(
            project_id="project-default",
            workspace_id="workspace-primary",
            scope_ids=["memory/project-x"],
        )
        assert audits
        assert audits[0].retrieval_id == audit.retrieval_id
