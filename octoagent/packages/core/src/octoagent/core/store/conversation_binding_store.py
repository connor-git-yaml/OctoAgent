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


def _runtime_activity_at(binding: ConversationBinding) -> Any | None:
    """提取 runtime 活跃证据时间（F105 v0.2 D17b）。

    - ``last_runtime_active_at`` 非 None → 直接用（runtime upsert 恒写；
      configured 升级保留原值——配置动作不抹除活跃证据）
    - 否则 RUNTIME kind 兜底用 ``last_active_at``（v0.1 存量行/手工构造行
      尚无新列值，其 last_active 即 inbound 活跃，语义等价）
    - CONFIGURED 且无活跃证据 → None（配置时间不是活跃证据）
    """
    if binding.last_runtime_active_at is not None:
        return binding.last_runtime_active_at
    if binding.binding_kind is ConversationBindingKind.RUNTIME:
        return binding.last_active_at
    return None


def resolve_outbound_route(
    bindings: list[ConversationBinding],
    *,
    explicit: tuple[str, str] | None = None,
) -> ConversationBinding | None:
    """OC-6 last-route 出站渠道选择（纯函数，spec v0.1 FR-E4 / v0.2 D17b）。

    三级策略：
    1. explicit=(platform, conversation_id) 精确命中 → 该 binding。
       **list-order 契约（v0.2 FR-D4 / OPUS2-L2 文档化）**：explicit 元组
       不含 project_id——多 project 同 conversation 时命中传入 list 的
       首条；调用方若按 project 维度出站，须自行预过滤 bindings 或在
       引入首个 explicit 生产消费者时扩三元组（v0.2 生产消费者均不使用
       explicit）。
    2. **runtime 活跃证据**最新者（v0.2 D17b：按 ``_runtime_activity_at``
       排序、不分 kind——CONFIGURED 升级后的会话凭保留的活跃证据继续
       参与排序，修复"配置动作把活跃会话踢出路由"缺陷 CODEX-H3）。
    3. 唯一 CONFIGURED binding（已配置但尚无流量、且不歧义时才可用）。
    全不命中 → None。
    """
    if explicit is not None:
        explicit_platform, explicit_conversation = explicit
        for binding in bindings:
            if (
                binding.platform == explicit_platform
                and binding.conversation_id == explicit_conversation
            ):
                return binding

    active = [
        (activity_at, binding)
        for binding in bindings
        if (activity_at := _runtime_activity_at(binding)) is not None
    ]
    if active:
        return max(active, key=lambda pair: pair[0])[1]

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
                last_active_at, last_runtime_active_at, metadata,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, account_id, conversation_id, project_id)
            DO UPDATE SET
                scope_id = excluded.scope_id,
                metadata = excluded.metadata,
                last_active_at = excluded.last_active_at,
                last_runtime_active_at = excluded.last_runtime_active_at,
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
                now,  # F105 v0.2 D17b：runtime 活跃证据恒写（与 kind 解耦）
                metadata_json,
                now,
                now,
            ),
        )
        await self._conn.commit()
        stored = await self.get(platform, conversation_id, project_id=project_id)
        if stored is None:  # upsert 后必存在；不用 assert（-O 下被剥离，F124 同类 LOW）
            raise RuntimeError(
                f"conversation binding upsert 后读取失败: {platform}/{conversation_id}"
            )
        return stored

    async def upsert_configured_binding(
        self,
        platform: str,
        conversation_id: str,
        *,
        scope_id: str = "",
        project_id: str = "",
        metadata: dict[str, Any] | None = None,
        agent_profile_id: str = "",
    ) -> ConversationBinding:
        """登记/升级一条 CONFIGURED 绑定（F105 v0.2 FR-D1，**单一 CONFIGURED
        写入口**——handoff §2.3 约束：加配置面必须收敛到 store 单一入口）。

        H1 单点校验：``agent_profile_id`` 非空即 raise——非主 Agent 绑定
        必须经"显式用户拍板"的配置面（未来 Feature），v0.2 物理上没有该面，
        应用层写入面构造性收敛（spec §7 / OPUS-M3 措辞口径）。

        双向 kind 规则（与 upsert_runtime_binding 并存）：
        - 本入口把已存在的 runtime 行**升级**为 configured（用户显式意图
          优先于 inbound 痕迹）；
        - runtime 入口不降级 configured（v0.1 既有语义）；
        - **不触碰 last_active_at / last_runtime_active_at**（D17b：配置
          动作不伪造也不抹除活跃证据——升级后该会话凭保留的活跃证据继续
          参与 last-route 排序，CODEX-H3 闭环）。
        """
        if agent_profile_id != "":
            raise ValueError(
                "H1 校验：CONFIGURED 绑定不得指向非主 Agent"
                f"（agent_profile_id={agent_profile_id!r}）；非主 Agent 绑定"
                "需要未来显式用户拍板的配置面，v0.2 不存在该写入路径"
            )
        now = datetime.now(UTC).isoformat()
        binding_id = f"convb-{ULID()}"
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        await self._conn.execute(
            """
            INSERT INTO conversation_bindings (
                binding_id, platform, account_id, conversation_id,
                scope_id, project_id, agent_profile_id, binding_kind,
                last_active_at, last_runtime_active_at, metadata,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(platform, account_id, conversation_id, project_id)
            DO UPDATE SET
                scope_id = excluded.scope_id,
                metadata = excluded.metadata,
                binding_kind = excluded.binding_kind,
                updated_at = excluded.updated_at
            """,
            (
                binding_id,
                platform,
                DEFAULT_ACCOUNT_ID,
                conversation_id,
                scope_id,
                project_id,
                ConversationBindingKind.CONFIGURED.value,
                now,  # 新建行的 last_active_at（NOT NULL 列）=配置时间
                metadata_json,
                now,
                now,
            ),
        )
        await self._conn.commit()
        stored = await self.get(platform, conversation_id, project_id=project_id)
        if stored is None:
            raise RuntimeError(
                f"configured binding upsert 后读取失败: {platform}/{conversation_id}"
            )
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
        raw_runtime_active = row["last_runtime_active_at"]
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
            last_runtime_active_at=(
                datetime.fromisoformat(raw_runtime_active)
                if raw_runtime_active
                else None
            ),
            metadata=metadata if isinstance(metadata, dict) else {},
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
