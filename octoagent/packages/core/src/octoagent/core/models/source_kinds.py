"""F099: source_runtime_kind 枚举常量 + control_metadata_source 操作字符串常量。

两套常量语义严格区分，**绝对不得混用**：

1. ``SOURCE_RUNTIME_KIND_*``：**Caller 身份枚举**
   - 用于 ``envelope.metadata["source_runtime_kind"]`` 和
     ``runtime_metadata["source_runtime_kind"]``
   - 标识"是谁（哪类 runtime）发起了这次 dispatch/spawn"
   - dispatch_service._resolve_a2a_source_role 读取此字段推断 source role
   - F099 新增：``"automation"`` / ``"user_channel"`` 两个新 caller 类型

2. ``CONTROL_METADATA_SOURCE_*``：**事件来源操作字符串**
   - 用于 ``ControlMetadataUpdatedPayload.source`` 字段
   - 标识"是哪个工具/操作触发了这次 CONTROL_METADATA_UPDATED emit"
   - F098 已有：``subagent_delegation_init`` / ``subagent_delegation_session_backfill``
   - F099 新增：``worker_ask_back`` / ``worker_request_input`` / ``worker_escalate_permission``

GATE_DESIGN G-4 落实（命名约定显式化 + 常量化，防止跨 Phase 命名混淆）。
OD-F099-3 落实（扩展 source_runtime_kind 枚举，不新增 A2AConversation 字段）。
"""

# ---------------------------------------------------------------------------
# Caller 身份枚举（source_runtime_kind）
# 注：这些是字符串常量，不是 StrEnum，保持与现有 metadata.get() 读取模式一致
# ---------------------------------------------------------------------------

SOURCE_RUNTIME_KIND_MAIN = "main"
"""主 Agent 发起的 dispatch（baseline 默认路径）。"""

SOURCE_RUNTIME_KIND_WORKER = "worker"
"""Worker 发起的 dispatch/spawn（F098 LOW §3 修复：spawn 路径注入）。"""

SOURCE_RUNTIME_KIND_SUBAGENT = "subagent"
"""Subagent 发起的 dispatch（F097）。"""

SOURCE_RUNTIME_KIND_AUTOMATION = "automation"
"""Automation/Routine 发起的 dispatch（F099 新增，GATE_DESIGN G-2 要求）。"""

SOURCE_RUNTIME_KIND_USER_CHANNEL = "user_channel"
"""用户渠道（Telegram/Web）直接触发的 dispatch（F099 新增，GATE_DESIGN G-2 要求）。"""

# 已知值集合（用于 _resolve_a2a_source_role 的有效性验证和降级判断）
KNOWN_SOURCE_RUNTIME_KINDS: frozenset[str] = frozenset({
    SOURCE_RUNTIME_KIND_MAIN,
    SOURCE_RUNTIME_KIND_WORKER,
    SOURCE_RUNTIME_KIND_SUBAGENT,
    SOURCE_RUNTIME_KIND_AUTOMATION,
    SOURCE_RUNTIME_KIND_USER_CHANNEL,
})

# ---------------------------------------------------------------------------
# 事件来源操作字符串（control_metadata_source）
# 用于 ControlMetadataUpdatedPayload.source 字段
# F098 已有值（向后兼容，不修改已有字符串）
# ---------------------------------------------------------------------------

CONTROL_METADATA_SOURCE_SUBAGENT_DELEGATION_INIT = "subagent_delegation_init"
"""F098 Phase B-1: Subagent delegation 初始化时 emit（SubagentDelegation 建模）。"""

CONTROL_METADATA_SOURCE_SUBAGENT_DELEGATION_BACKFILL = "subagent_delegation_session_backfill"
"""F098 Phase B-1: Subagent session backfill 阶段 emit（历史消息补填）。"""

# F099 新增操作字符串（三工具触发）
CONTROL_METADATA_SOURCE_ASK_BACK = "worker_ask_back"
"""F099 Phase B: worker.ask_back 工具触发 emit。"""

CONTROL_METADATA_SOURCE_REQUEST_INPUT = "worker_request_input"
"""F099 Phase B: worker.request_input 工具触发 emit。"""

CONTROL_METADATA_SOURCE_ESCALATE_PERMISSION = "worker_escalate_permission"
"""F099 Phase B: worker.escalate_permission 工具触发 emit。"""
