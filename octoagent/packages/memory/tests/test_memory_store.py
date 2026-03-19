"""MemoryStore 测试。"""

from datetime import UTC, datetime, timedelta

import aiosqlite
from octoagent.memory import (
    BrowseResult,
    EvidenceRef,
    MemoryPartition,
    SorRecord,
    SorStatus,
    VaultAccessGrantRecord,
    VaultAccessGrantStatus,
    VaultAccessRequestRecord,
    VaultAccessRequestStatus,
    VaultRecord,
    VaultRetrievalAuditRecord,
    WriteAction,
    WriteProposal,
)
from octoagent.memory.store import SqliteMemoryStore, init_memory_db


class TestSqliteMemoryStore:
    async def test_append_fragment_and_query(self, memory_store):
        now = datetime.now(UTC)
        fragment = await _seed_fragment(memory_store, now)
        found = await memory_store.get_fragment(fragment.fragment_id)
        assert found is not None
        assert found.content == "Project X kicked off"

        listing = await memory_store.list_fragments("work/project-x", query="kicked")
        assert len(listing) == 1

    async def test_sor_history(self, memory_store):
        now = datetime.now(UTC)
        current = SorRecord(
            memory_id="01JSOR_100000000000000001",
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            subject_key="work.project-x.status",
            content="running",
            version=1,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            created_at=now,
            updated_at=now,
        )
        await memory_store.insert_sor(current)
        await memory_store.update_sor_status(
            current.memory_id,
            status="superseded",
            updated_at=now.isoformat(),
        )
        await memory_store.insert_sor(
            current.model_copy(
                update={
                    "memory_id": "01JSOR_100000000000000002",
                    "content": "done",
                    "version": 2,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        )
        history = await memory_store.list_sor_history(
            "work/project-x",
            "work.project-x.status",
        )
        assert [item.version for item in history] == [2, 1]

    async def test_proposal_round_trip(self, memory_store):
        now = datetime.now(UTC)
        proposal = WriteProposal(
            proposal_id="01JPROP_10000000000000001",
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="work.project-x.status",
            content="running",
            rationale="sync current state",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            created_at=now,
        )
        await memory_store.save_proposal(proposal)
        loaded = await memory_store.get_proposal(proposal.proposal_id)
        assert loaded is not None
        assert loaded.subject_key == proposal.subject_key

    async def test_list_proposals_with_empty_scope_ids_returns_empty(self, memory_store):
        now = datetime.now(UTC)
        await memory_store.save_proposal(
            WriteProposal(
                proposal_id="01JPROP_10000000000000003",
                scope_id="work/project-x",
                partition=MemoryPartition.WORK,
                action=WriteAction.ADD,
                subject_key="work.project-x.status",
                content="running",
                rationale="sync current state",
                confidence=0.9,
                evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
                metadata={"source": "worker"},
                created_at=now,
            )
        )

        proposals = await memory_store.list_proposals(scope_ids=[])

        assert proposals == []

    async def test_store_sets_row_factory_for_named_column_access(self, tmp_path):
        db_path = tmp_path / "memory-store-row-factory.db"
        conn = await aiosqlite.connect(str(db_path))
        try:
            await init_memory_db(conn)
            store = SqliteMemoryStore(conn)
            proposal = WriteProposal(
                proposal_id="01JPROP_10000000000000002",
                scope_id="work/project-y",
                partition=MemoryPartition.WORK,
                action=WriteAction.ADD,
                subject_key="work.project-y.status",
                content="running",
                rationale="sync current state",
                confidence=0.9,
                evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
                created_at=datetime.now(UTC),
            )
            await store.save_proposal(proposal)
            await conn.commit()

            loaded = await store.get_proposal(proposal.proposal_id)
            assert loaded is not None
            assert loaded.subject_key == proposal.subject_key
        finally:
            await conn.close()

    async def test_vault_round_trip(self, memory_store):
        now = datetime.now(UTC)
        vault = VaultRecord(
            vault_id="01JVAULT_100000000000001",
            scope_id="profile/user",
            partition=MemoryPartition.HEALTH,
            subject_key="profile.user.health.note",
            summary="health note updated",
            content_ref="vault://proposal/123",
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            created_at=now,
        )
        await memory_store.insert_vault(vault)
        loaded = await memory_store.get_vault(vault.vault_id)
        assert loaded is not None
        assert loaded.partition == MemoryPartition.HEALTH

    async def test_vault_authorization_round_trip(self, memory_store):
        now = datetime.now(UTC)
        request = VaultAccessRequestRecord(
            request_id="01JVREQ_100000000000001",
            project_id="project-default",
            workspace_id="workspace-primary",
            scope_id="memory/project-x",
            partition=MemoryPartition.HEALTH,
            subject_key="profile.user.health.note",
            reason="排障",
            requester_actor_id="user:web",
            requester_actor_label="Owner",
            status=VaultAccessRequestStatus.PENDING,
            requested_at=now,
        )
        await memory_store.create_vault_access_request(request)
        loaded_request = await memory_store.get_vault_access_request(request.request_id)
        assert loaded_request is not None
        assert loaded_request.scope_id == request.scope_id

        grant = VaultAccessGrantRecord(
            grant_id="01JVGRANT_10000000000001",
            request_id=request.request_id,
            project_id=request.project_id,
            workspace_id=request.workspace_id,
            scope_id=request.scope_id,
            partition=request.partition,
            subject_key=request.subject_key,
            granted_to_actor_id="user:web",
            granted_to_actor_label="Owner",
            granted_by_actor_id="user:web",
            granted_by_actor_label="Owner",
            granted_at=now,
            status=VaultAccessGrantStatus.ACTIVE,
        )
        await memory_store.insert_vault_access_grant(grant)
        loaded_grant = await memory_store.get_vault_access_grant(grant.grant_id)
        assert loaded_grant is not None
        assert loaded_grant.status is VaultAccessGrantStatus.ACTIVE

        audit = VaultRetrievalAuditRecord(
            retrieval_id="01JVRET_100000000000001",
            project_id=request.project_id,
            workspace_id=request.workspace_id,
            scope_id=request.scope_id,
            partition=request.partition,
            subject_key=request.subject_key,
            query="health",
            grant_id=grant.grant_id,
            actor_id="user:web",
            actor_label="Owner",
            authorized=True,
            reason_code="VAULT_RETRIEVE_AUTHORIZED",
            result_count=1,
            retrieved_vault_ids=["vault-1"],
            created_at=now,
        )
        await memory_store.append_vault_retrieval_audit(audit)
        requests = await memory_store.list_vault_access_requests(project_id=request.project_id)
        grants = await memory_store.list_vault_access_grants(project_id=request.project_id)
        audits = await memory_store.list_vault_retrieval_audits(project_id=request.project_id)
        assert requests[0].request_id == request.request_id
        assert grants[0].grant_id == grant.grant_id
        assert audits[0].retrieval_id == audit.retrieval_id


async def _seed_fragment(memory_store, now):
    from octoagent.memory import FragmentRecord

    fragment = FragmentRecord(
        fragment_id="01JFRAG_10000000000000001",
        scope_id="work/project-x",
        partition=MemoryPartition.WORK,
        content="Project X kicked off",
        evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        created_at=now,
    )
    await memory_store.append_fragment(fragment)
    return fragment


async def _seed_sor_batch(memory_store, scope_id: str = "memory/global") -> list[SorRecord]:
    """创建一批测试用 SoR 记录，覆盖多个 partition 和 subject_key。"""
    now = datetime.now(UTC)
    records: list[SorRecord] = []
    configs = [
        ("01JSOR_BROWSE_000000000001", MemoryPartition.CORE, "用户偏好/编程语言", "偏好 Python", 1),
        ("01JSOR_BROWSE_000000000002", MemoryPartition.CORE, "用户偏好/编辑器", "使用 VS Code", 1),
        ("01JSOR_BROWSE_000000000003", MemoryPartition.WORK, "项目决策/选型", "选用 FastAPI", 1),
        ("01JSOR_BROWSE_000000000004", MemoryPartition.WORK, "项目决策/部署", "Docker 部署", 1),
        ("01JSOR_BROWSE_000000000005", MemoryPartition.WORK, "技术选型/数据库", "SQLite WAL", 1),
        ("01JSOR_BROWSE_000000000006", MemoryPartition.HEALTH, "健康/运动", "每周跑步三次", 1),
        ("01JSOR_BROWSE_000000000007", MemoryPartition.CORE, "家庭/妈妈", "妈妈喜欢园艺", 1),
    ]
    for i, (mid, part, sk, content, ver) in enumerate(configs):
        ts = now + timedelta(seconds=i)
        rec = SorRecord(
            memory_id=mid,
            scope_id=scope_id,
            partition=part,
            subject_key=sk,
            content=content,
            version=ver,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            created_at=ts,
            updated_at=ts,
        )
        await memory_store.insert_sor(rec)
        records.append(rec)
    return records


class TestBrowseSor:
    """T014: browse_sor 测试。"""

    async def test_browse_empty_scope(self, memory_store):
        """空 scope 返回空列表不报错。"""
        result = await memory_store.browse_sor("nonexistent/scope")
        assert isinstance(result, BrowseResult)
        assert result.groups == []
        assert result.total_count == 0
        assert result.has_more is False

    async def test_browse_by_partition(self, memory_store):
        """按 partition 分组。"""
        await _seed_sor_batch(memory_store)
        result = await memory_store.browse_sor("memory/global", group_by="partition")
        assert result.total_count == 7
        # 应有 core、work、health 三个分组
        keys = {g.key for g in result.groups}
        assert "core" in keys
        assert "work" in keys
        assert "health" in keys
        # core 有 3 条
        core_group = next(g for g in result.groups if g.key == "core")
        assert core_group.count == 3

    async def test_browse_by_prefix_filter(self, memory_store):
        """按 subject_key 前缀筛选。"""
        await _seed_sor_batch(memory_store)
        result = await memory_store.browse_sor(
            "memory/global", prefix="用户偏好/", group_by="partition"
        )
        assert result.total_count == 2
        # 所有条目的 subject_key 都以 "用户偏好/" 开头
        for g in result.groups:
            for item in g.items:
                assert item.subject_key.startswith("用户偏好/")

    async def test_browse_by_partition_filter(self, memory_store):
        """按 partition 筛选。"""
        await _seed_sor_batch(memory_store)
        result = await memory_store.browse_sor(
            "memory/global", partition="work", group_by="prefix"
        )
        assert result.total_count == 3

    async def test_browse_pagination(self, memory_store):
        """分页：has_more 和 total_count。"""
        await _seed_sor_batch(memory_store)
        result = await memory_store.browse_sor("memory/global", limit=3, offset=0)
        assert result.total_count == 7
        assert result.has_more is True
        assert result.limit == 3

        result2 = await memory_store.browse_sor("memory/global", limit=3, offset=3)
        assert result2.total_count == 7
        assert result2.has_more is True

        result3 = await memory_store.browse_sor("memory/global", limit=3, offset=6)
        assert result3.total_count == 7
        assert result3.has_more is False

    async def test_browse_group_has_latest_updated_at(self, memory_store):
        """每个分组都有 latest_updated_at。"""
        await _seed_sor_batch(memory_store)
        result = await memory_store.browse_sor("memory/global", group_by="partition")
        for g in result.groups:
            assert g.latest_updated_at is not None

    async def test_browse_items_have_summary(self, memory_store):
        """每个 item 都有 summary（content 前 100 字符）。"""
        await _seed_sor_batch(memory_store)
        result = await memory_store.browse_sor("memory/global", limit=100)
        for g in result.groups:
            for item in g.items:
                assert item.summary != ""
                assert len(item.summary) <= 100


class TestSearchSorExtended:
    """T015: search_sor 扩展参数测试。"""

    async def test_search_backward_compatible(self, memory_store):
        """不传新参数时行为不变。"""
        await _seed_sor_batch(memory_store)
        results = await memory_store.search_sor("memory/global", query="Python")
        assert len(results) >= 1
        assert all(r.status == SorStatus.CURRENT for r in results)

    async def test_search_by_partition(self, memory_store):
        """按 partition 筛选。"""
        await _seed_sor_batch(memory_store)
        results = await memory_store.search_sor("memory/global", partition="health")
        assert len(results) == 1
        assert results[0].partition == MemoryPartition.HEALTH

    async def test_search_by_status(self, memory_store):
        """按 status 筛选。"""
        await _seed_sor_batch(memory_store)
        # 归档一条记忆
        await memory_store.update_sor_status(
            "01JSOR_BROWSE_000000000001",
            status="archived",
            updated_at=datetime.now(UTC).isoformat(),
        )
        # 默认不含 archived
        results = await memory_store.search_sor("memory/global")
        ids = [r.memory_id for r in results]
        assert "01JSOR_BROWSE_000000000001" not in ids

        # 显式查 archived
        results = await memory_store.search_sor(
            "memory/global", status="archived", include_history=True
        )
        assert any(r.memory_id == "01JSOR_BROWSE_000000000001" for r in results)

    async def test_search_by_time_range(self, memory_store):
        """按更新时间范围筛选。"""
        await _seed_sor_batch(memory_store)
        now = datetime.now(UTC)
        future = (now + timedelta(hours=1)).isoformat()
        past = (now - timedelta(hours=1)).isoformat()

        # 全部在范围内
        results = await memory_store.search_sor(
            "memory/global", updated_after=past, updated_before=future
        )
        assert len(results) == 7

        # 未来之后无结果
        results = await memory_store.search_sor(
            "memory/global", updated_after=future
        )
        assert len(results) == 0


class TestUpdateSorStatus:
    """T016: update_sor_status 测试。"""

    async def test_current_to_archived(self, memory_store):
        """current -> archived 转换。"""
        await _seed_sor_batch(memory_store)
        now_str = datetime.now(UTC).isoformat()
        await memory_store.update_sor_status(
            "01JSOR_BROWSE_000000000001", status="archived", updated_at=now_str
        )
        rec = await memory_store.get_sor("01JSOR_BROWSE_000000000001")
        assert rec is not None
        assert rec.status == SorStatus.ARCHIVED

    async def test_archived_to_current(self, memory_store):
        """archived -> current 恢复。"""
        await _seed_sor_batch(memory_store)
        now_str = datetime.now(UTC).isoformat()
        # 先归档
        await memory_store.update_sor_status(
            "01JSOR_BROWSE_000000000001", status="archived", updated_at=now_str
        )
        # 再恢复
        await memory_store.update_sor_status(
            "01JSOR_BROWSE_000000000001", status="current", updated_at=now_str
        )
        rec = await memory_store.get_sor("01JSOR_BROWSE_000000000001")
        assert rec is not None
        assert rec.status == SorStatus.CURRENT

    async def test_status_change_preserves_version(self, memory_store):
        """状态变更不改变 version。"""
        await _seed_sor_batch(memory_store)
        rec_before = await memory_store.get_sor("01JSOR_BROWSE_000000000001")
        assert rec_before is not None

        now_str = datetime.now(UTC).isoformat()
        await memory_store.update_sor_status(
            "01JSOR_BROWSE_000000000001", status="archived", updated_at=now_str
        )
        rec_after = await memory_store.get_sor("01JSOR_BROWSE_000000000001")
        assert rec_after is not None
        assert rec_after.version == rec_before.version
