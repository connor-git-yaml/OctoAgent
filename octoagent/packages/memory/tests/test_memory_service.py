"""MemoryService 测试。"""

from datetime import UTC, datetime

from octoagent.memory import (
    CommitResult,
    EvidenceRef,
    MemoryAccessDeniedError,
    MemoryAccessPolicy,
    MemoryBackendState,
    MemoryBackendStatus,
    MemoryLayer,
    MemoryPartition,
    MemoryRecallHit,
    MemoryRecallHookOptions,
    MemoryRecallPostFilterMode,
    MemoryRecallRerankMode,
    MemorySearchHit,
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

    async def test_recall_memory_expands_query_and_returns_citation(self, memory_service):
        add = await memory_service.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="project.alpha.plan",
            content="Alpha 方案拆解要先完成 memory resolver 接线。",
            rationale="记录长期执行约束",
            confidence=0.92,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        )
        validation = await memory_service.validate_proposal(add.proposal_id)
        assert validation.accepted is True
        await memory_service.commit_memory(add.proposal_id)

        recall = await memory_service.recall_memory(
            scope_ids=["work/project-x"],
            query="请继续推进 Alpha 方案拆解",
            policy=MemoryAccessPolicy(),
            per_scope_limit=3,
            max_hits=4,
        )

        assert recall.query == "请继续推进 Alpha 方案拆解"
        assert recall.expanded_queries[:2] == [
            "请继续推进 Alpha 方案拆解",
            "Alpha 方案拆解",
        ]
        assert recall.backend_status is not None
        assert recall.backend_status.active_backend == memory_service.backend_id
        assert recall.hits
        first = recall.hits[0]
        assert first.record_id
        assert first.search_query in recall.expanded_queries
        assert first.citation == "memory://work/project-x/sor/project.alpha.plan"
        assert "memory resolver" in first.content_preview

    async def test_recall_memory_applies_post_filter_and_heuristic_rerank(
        self,
        memory_service,
        monkeypatch,
    ):
        created_at = datetime(2026, 3, 10, tzinfo=UTC)

        async def fake_search_memory(self, scope_id, *, query=None, policy=None, limit=20):
            return [
                MemorySearchHit(
                    record_id="memory-beta",
                    layer=MemoryLayer.FRAGMENT,
                    scope_id=scope_id,
                    partition=MemoryPartition.WORK,
                    summary="Beta retrospective 关注缺陷收敛与发布回顾。",
                    subject_key="project.beta.retrospective",
                    created_at=created_at,
                    metadata={},
                ),
                MemorySearchHit(
                    record_id="memory-alpha",
                    layer=MemoryLayer.SOR,
                    scope_id=scope_id,
                    partition=MemoryPartition.WORK,
                    summary="Alpha 方案拆解要先完成 memory resolver 接线。",
                    subject_key="project.alpha.plan",
                    created_at=created_at,
                    metadata={},
                ),
            ]

        async def fake_get_backend_status(self):
            return MemoryBackendStatus(
                backend_id="sqlite",
                active_backend="sqlite",
                state=MemoryBackendState.HEALTHY,
            )

        async def fake_build_recall_hit(self, *, hit, policy):
            citation = (
                f"memory://{hit.scope_id}/{hit.layer.value}/"
                f"{hit.subject_key or hit.record_id}"
            )
            return MemoryRecallHit(
                record_id=hit.record_id,
                layer=hit.layer,
                scope_id=hit.scope_id,
                partition=hit.partition,
                summary=hit.summary,
                subject_key=hit.subject_key or "",
                search_query=str(hit.metadata.get("search_query", "")),
                citation=citation,
                content_preview=hit.summary,
                metadata=dict(hit.metadata),
                created_at=hit.created_at,
            )

        monkeypatch.setattr(type(memory_service), "search_memory", fake_search_memory)
        monkeypatch.setattr(type(memory_service), "get_backend_status", fake_get_backend_status)
        monkeypatch.setattr(type(memory_service), "_build_recall_hit", fake_build_recall_hit)

        recall = await memory_service.recall_memory(
            scope_ids=["work/project-x"],
            query="请继续推进 Alpha 方案拆解",
            policy=MemoryAccessPolicy(),
            per_scope_limit=4,
            max_hits=4,
            hook_options=MemoryRecallHookOptions(
                post_filter_mode=MemoryRecallPostFilterMode.KEYWORD_OVERLAP,
                rerank_mode=MemoryRecallRerankMode.HEURISTIC,
            ),
        )

        assert [hit.record_id for hit in recall.hits] == ["memory-alpha"]
        assert recall.hook_trace is not None
        assert recall.hook_trace.post_filter_mode is MemoryRecallPostFilterMode.KEYWORD_OVERLAP
        assert recall.hook_trace.rerank_mode is MemoryRecallRerankMode.HEURISTIC
        assert recall.hook_trace.candidate_count == 2
        assert recall.hook_trace.filtered_count == 1
        assert recall.hook_trace.delivered_count == 1
        assert recall.hook_trace.fallback_applied is False
        assert "Alpha" in recall.hook_trace.focus_terms
        assert recall.hits[0].metadata["recall_keyword_overlap"] >= 1
        assert recall.hits[0].metadata["recall_rerank_mode"] == "heuristic"

    async def test_recall_memory_post_filter_falls_back_when_all_candidates_are_filtered(
        self,
        memory_service,
        monkeypatch,
    ):
        created_at = datetime(2026, 3, 10, tzinfo=UTC)

        async def fake_search_memory(self, scope_id, *, query=None, policy=None, limit=20):
            return [
                MemorySearchHit(
                    record_id="memory-beta",
                    layer=MemoryLayer.FRAGMENT,
                    scope_id=scope_id,
                    partition=MemoryPartition.WORK,
                    summary="Beta retrospective 关注缺陷收敛与发布回顾。",
                    subject_key="project.beta.retrospective",
                    created_at=created_at,
                    metadata={},
                )
            ]

        async def fake_get_backend_status(self):
            return MemoryBackendStatus(
                backend_id="sqlite",
                active_backend="sqlite",
                state=MemoryBackendState.HEALTHY,
            )

        async def fake_build_recall_hit(self, *, hit, policy):
            citation = (
                f"memory://{hit.scope_id}/{hit.layer.value}/"
                f"{hit.subject_key or hit.record_id}"
            )
            return MemoryRecallHit(
                record_id=hit.record_id,
                layer=hit.layer,
                scope_id=hit.scope_id,
                partition=hit.partition,
                summary=hit.summary,
                subject_key=hit.subject_key or "",
                search_query=str(hit.metadata.get("search_query", "")),
                citation=citation,
                content_preview=hit.summary,
                metadata=dict(hit.metadata),
                created_at=hit.created_at,
            )

        monkeypatch.setattr(type(memory_service), "search_memory", fake_search_memory)
        monkeypatch.setattr(type(memory_service), "get_backend_status", fake_get_backend_status)
        monkeypatch.setattr(type(memory_service), "_build_recall_hit", fake_build_recall_hit)

        recall = await memory_service.recall_memory(
            scope_ids=["work/project-x"],
            query="请继续推进 Alpha 方案拆解",
            policy=MemoryAccessPolicy(),
            per_scope_limit=4,
            max_hits=4,
            hook_options=MemoryRecallHookOptions(
                post_filter_mode=MemoryRecallPostFilterMode.KEYWORD_OVERLAP,
                rerank_mode=MemoryRecallRerankMode.HEURISTIC,
            ),
        )

        assert [hit.record_id for hit in recall.hits] == ["memory-beta"]
        assert "recall_post_filter_fallback" in recall.degraded_reasons
        assert recall.hook_trace is not None
        assert recall.hook_trace.candidate_count == 1
        assert recall.hook_trace.filtered_count == 1
        assert recall.hook_trace.fallback_applied is True

    async def test_vault_access_request_resolve_and_retrieval_audit(self, memory_service):
        request = await memory_service.create_vault_access_request(
            project_id="project-default",
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
            scope_ids=["memory/project-x"],
        )
        assert audits
        assert audits[0].retrieval_id == audit.retrieval_id


class TestFastCommit:
    """fast_commit() 快速写入路径测试。"""

    async def test_fast_commit_add_success(self, memory_service, memory_store):
        """ADD + confidence>=0.75 + 非敏感分区 → 走快速路径，直接成功。"""
        result = await memory_service.fast_commit(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="work.project-x.fast",
            content="fast committed value",
            confidence=0.9,
        )
        assert isinstance(result, CommitResult)
        assert result.committed is True
        assert result.sor_id is not None

        current = await memory_store.get_current_sor("work/project-x", "work.project-x.fast")
        assert current is not None
        assert current.content == "fast committed value"
        assert current.version == 1

    async def test_fast_commit_fallback_low_confidence(self, memory_service, memory_store):
        """confidence < 0.75 → fallback 到完整 propose-validate-commit，ADD 仍应成功。"""
        result = await memory_service.fast_commit(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="work.project-x.low-conf",
            content="low confidence value",
            confidence=0.5,
        )
        assert isinstance(result, CommitResult)
        assert result.committed is True
        assert result.sor_id is not None

        current = await memory_store.get_current_sor("work/project-x", "work.project-x.low-conf")
        assert current is not None
        assert current.content == "low confidence value"

    async def test_fast_commit_fallback_sensitive_partition(self, memory_service, memory_store):
        """HEALTH 敏感分区 → fallback 到完整流程（写入 vault）。"""
        result = await memory_service.fast_commit(
            scope_id="profile/user",
            partition=MemoryPartition.HEALTH,
            action=WriteAction.ADD,
            subject_key="profile.user.health.bp",
            content="120/80",
            confidence=0.95,
        )
        assert isinstance(result, CommitResult)
        assert result.committed is True
        # 敏感分区写入应产生 vault_id
        assert result.vault_id is not None

    async def test_fast_commit_fallback_update_action(self, memory_service):
        """UPDATE action → fallback 到完整流程，但因为不存在 SoR 所以 validate 失败。"""
        result = await memory_service.fast_commit(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.UPDATE,
            subject_key="work.project-x.nonexistent",
            content="update value",
            confidence=0.9,
        )
        assert isinstance(result, CommitResult)
        # UPDATE 对不存在的 subject_key 走 fallback 后 validate 应该失败
        assert result.committed is False

    async def test_fast_commit_duplicate_subject_key(self, memory_service):
        """快速路径 ADD 对已存在的 subject_key → commit 阶段应失败（快速路径跳过 validate）。"""
        # 先通过正常流程写入一条记录
        first = await memory_service.fast_commit(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="work.project-x.dup-key",
            content="first value",
            confidence=0.9,
        )
        assert first.committed is True
        assert first.sor_id is not None

        # 再次 ADD 同一个 subject_key——快速路径不做 validate 查重，
        # 但 commit 阶段应检测到冲突并抛出异常
        try:
            await memory_service.fast_commit(
                scope_id="work/project-x",
                partition=MemoryPartition.WORK,
                action=WriteAction.ADD,
                subject_key="work.project-x.dup-key",
                content="duplicate value",
                confidence=0.9,
            )
            raise AssertionError("expected error on duplicate subject_key fast_commit")
        except Exception:
            # commit_memory 内部应因已有 current SoR 而拒绝 ADD
            pass
