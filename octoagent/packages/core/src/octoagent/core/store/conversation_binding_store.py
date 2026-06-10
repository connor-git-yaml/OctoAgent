"""SqliteConversationBindingStore -- F105 ConversationBinding 持久化（OC-2/OC-6）。

H1 构造性保证（spec D5）：``upsert_runtime_binding`` 签名**不含 agent_profile_id**
——v0.1 的唯一写入路径物理上写不进非主 Agent 绑定（列恒 ''）。v0.2 引入
CONFIGURED 配置面时，写入必须收敛到本 store 的单一入口并在该入口做 H1 校验。

binding 是路由缓存态（可由 inbound 重建），无 task FK，不绑 task 生命周期
（对齐 notification_store / memory_extraction_ledger 的无 FK 设计）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite
from ulid import ULID

from ..models.conversation_binding import ConversationBinding, ConversationBindingKind

DEFAULT_ACCOUNT_ID = "default"


def resolve_outbound_route(
    bindings: list[ConversationBinding],
    *,
    explicit: tuple[str, str] | None = None,
) -> ConversationBinding | None:
    """OC-6 last-route 出站渠道选择（纯函数，spec FR-E4）。

    三级策略：
    1. explicit=(platform, conversation_id) 精确命中 → 该 binding
    2. RUNTIME binding 中 last_active_at 最新者（"用户最后说话的地方"——
       仅 runtime 的 last_active 代表真实 inbound 流量；CONFIGURED 的时间戳
       是配置时间不是活跃证据，混入会让新配置压过真实活跃会话）
    3. 唯一 CONFIGURED binding（已配置但尚无流量、且不歧义时才可用）
    全不命中 → None。

    v0.1 仅供单测与 v0.2 接线消费，不接入任何现有出站路径（行为零变更）。
    """
    if explicit is not None:
        explicit_platform, explicit_conversation = explicit
        for binding in bindings:
            if (
                binding.platform == explicit_platform
                and binding.conversation_id == explicit_conversation
            ):
                return binding

    runtime_bindings = [
        b for b in bindings if b.binding_kind is ConversationBindingKind.RUNTIME
    ]
    if runtime_bindings:
        return max(runtime_bindings, key=lambda b: b.last_active_at)

    configured = [
        b for b in bindings if b.binding_kind is ConversationBindingKind.CONFIGURED
    ]
    if len(configured) == 1:
        return configured[0]
    return None


class SqliteConversationBindingStore:
    """conversation_bindings 表访问层。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def upsert_runtime_binding(
        self,
        platform: str,
        conversation_id: str,
        *,
        scope_id: str = "",
        project_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ConversationBinding:
        """登记/touch 一条 runtime 绑定（FR-E2）。

        - 不存在 → 新建（binding_kind=runtime，agent_profile_id=''=主 Agent）
        - 已存在（UNIQUE 四元组冲突，project_id 参与身份——Codex pre-impl H3：
          web thread_id 不跨 project 唯一）→ touch last_active_at + 更新
          scope_id/metadata/updated_at；binding_kind 与 agent_profile_id
          **不被 runtime 路径覆盖**（CONFIGURED 绑定的配置语义不被 inbound
          流量降级）
        """
        now = datetime.now(UTC).isoformat()
        binding_id = f"convb-{ULID()}"
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        await self._conn.execute(
            """
            INSERT INTO conversation_bindings (
                binding_id, platform, account_id, conversation_id,
                scope_id, project_id, agent_profile_id, binding_kind,
                last_active_at, metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)
            ON CONFLICT(platform, account_id, conversation_id, project_id)
            DO UPDATE SET
                scope_id = excluded.scope_id,
                metadata = excluded.metadata,
                last_active_at = excluded.last_active_at,
                updated_at = excluded.updated_at
            """,
            (
                binding_id,
                platform,
                DEFAULT_ACCOUNT_ID,
                conversation_id,
                scope_id,
                project_id,
                ConversationBindingKind.RUNTIME.value,
                now,
                metadata_json,
                now,
                now,
            ),
        )
        await self._conn.commit()
        stored = await self.get(platform, conversation_id, project_id=project_id)
        assert stored is not None  # upsert 后必存在
        return stored

    async def get(
        self,
        platform: str,
        conversation_id: str,
        account_id: str = DEFAULT_ACCOUNT_ID,
        *,
        project_id: str = "",
    ) -> ConversationBinding | None:
        cursor = await self._conn.execute(
            """
            SELECT * FROM conversation_bindings
            WHERE platform = ? AND account_id = ? AND conversation_id = ?
              AND project_id = ?
            """,
            (platform, account_id, conversation_id, project_id),
        )
        row = await cursor.fetchone()
        return self._row_to_binding(row) if row is not None else None

    async def list_by_platform(self, platform: str) -> list[ConversationBinding]:
        cursor = await self._conn.execute(
            """
            SELECT * FROM conversation_bindings
            WHERE platform = ?
            ORDER BY last_active_at DESC
            """,
            (platform,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_binding(row) for row in rows]

    async def list_recent(self, limit: int = 50) -> list[ConversationBinding]:
        cursor = await self._conn.execute(
            """
            SELECT * FROM conversation_bindings
            ORDER BY last_active_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_binding(row) for row in rows]

    @staticmethod
    def _row_to_binding(row: aiosqlite.Row) -> ConversationBinding:
        try:
            metadata = json.loads(row["metadata"])
        except (TypeError, ValueError):
            metadata = {}
        return ConversationBinding(
            binding_id=row["binding_id"],
            platform=row["platform"],
            account_id=row["account_id"],
            conversation_id=row["conversation_id"],
            scope_id=row["scope_id"],
            project_id=row["project_id"],
            agent_profile_id=row["agent_profile_id"],
            binding_kind=ConversationBindingKind(row["binding_kind"]),
            last_active_at=datetime.fromisoformat(row["last_active_at"]),
            metadata=metadata if isinstance(metadata, dict) else {},
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
