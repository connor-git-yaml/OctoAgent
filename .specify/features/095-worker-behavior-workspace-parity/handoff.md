# F095 → F096 / F107 Handoff

## 给 F096 (Worker Recall Audit & Provenance) 的接口点

### 1. BEHAVIOR_PACK_LOADED 事件 EventStore 接入（必做）

F095 已就位 schema + helper + cache miss 标记，**F096 只需在 async caller 处接入 EventStore**：

**接入位置（推荐）**：`worker_service.py:168` / `agent_service.py:111` 等调用 `build_behavior_system_summary` 的处。但由于这些 caller 自身也是 sync，最干净的路径是：

```python
# 把 build_behavior_system_summary 改 async（如果 F096 顺手做 async refactor），或
# 在更上层的 async 入口（如 control_plane API handler）处接入 emit
async def some_async_handler(...):
    pack = resolve_behavior_pack(...)  # sync 函数仍 sync
    if pack.metadata.get("cache_state") == "miss":
        payload = make_behavior_pack_loaded_payload(
            pack, agent_profile=agent_profile, load_profile=load_profile,
        )
        event = build_event(EventType.BEHAVIOR_PACK_LOADED, payload.model_dump())
        await event_store.append_event_committed(event, update_task_pointer=False)
```

**API**：
- `BehaviorPackLoadedPayload` 在 `octoagent.core.models.BehaviorPackLoadedPayload`
- `make_behavior_pack_loaded_payload` 在 `octoagent.gateway.services.agent_decision`
- `EventType.BEHAVIOR_PACK_LOADED` 在 `octoagent.core.models.enums`

**不做的事（F096 自决）**：
- F095 不规定接入位置——F096 按 dispatch 主路径决定
- F095 不规定 emit 频率上限——cache hit 不 emit 已守住单进程内重复

### 2. BEHAVIOR_PACK_USED 事件（F096 范围）

F096 自定义 USED 事件（每次 LLM 决策环 emit），通过 `pack_id` 关联到 LOADED 实例形成完整可审计链路。

**关联键**：`BehaviorPack.pack_id`（hash 化，格式 `behavior-pack:{profile_id}:{load_profile}:{16-char hex}`）

**字段建议**（F096 自决，仅参考）：
- `pack_id`（关联到 LOADED）
- `agent_runtime_id`（与 F094 RecallFrame 同维度）
- `task_id` / `decision_id`（每次决策环唯一标识）
- `message_count` / `context_token_count`（每次实际使用的指标）

### 3. AC-7b 完整集成测（F096 范围）

F095 实施了 partial 单测（`payload.agent_id == AgentProfile.profile_id`）。F096 实施完整端到端：

```
F095 BehaviorPackLoadedPayload.agent_id (= AgentProfile.profile_id)
    ↓
AgentRuntime.profile_id（worker dispatch 时创建 AgentRuntime 表行）
    ↓
F094 RecallFrame.agent_runtime_id（recall 持久化时填充）
```

集成测覆盖 `delegate_task → worker_service.create_worker → AgentRuntime 创建 → workspace 初始化 → BEHAVIOR_PACK_LOADED emit → memory recall → RecallFrame 持久化` 完整路径，用 AgentRuntime 表的 (profile_id, runtime_id) 映射做对齐断言。

### 4. F095 推迟测试可复用 fixture

`apps/gateway/tests/services/test_agent_decision_envelope.py` 中：
- `test_end_to_end_worker_pack_to_envelope_with_worker_variants` 端到端 worker 创建路径
- `test_mtime_invalidation_re_marks_miss_with_new_pack_id` cache 失效流程
- `make_behavior_pack_loaded_payload` 调用范例

`packages/core/tests/test_behavior_workspace.py`:
- `test_worker_profile_e2e_filesystem_with_worker_variants` Worker filesystem e2e
- `test_worker_variants_via_kind_attribute` kind="worker" production 路径

F096 实施 dispatch 集成测时可参考这些范例。

---

## 给 F107 (Capability Layer Refactor) 的接口点

### 1. 已 materialize 主 Agent 版 SOUL/HEARTBEAT 的 worker 目录迁移

**问题**：`materialize_agent_behavior_files` 是 write-if-missing。F095 合并前已存在的 Worker AgentProfile，其 `behavior/agents/{worker_slug}/SOUL.md` / `HEARTBEAT.md` 可能是主 Agent 通用版本（如 F095 改动前的代码路径，或 worker_service 之前用 `is_worker_profile=False` 误调用）。F095 合并后这些 worker 仍读旧文件，违反 H1 守护。

**人工迁移指引**（completion-report §5.2 已提供）：
```bash
for f in ~/.octoagent/behavior/agents/*/SOUL.md; do
    if ! grep -q "服务对象 = 主 Agent" "$f"; then
        echo "需迁移: $f"
        # 删除触发下次 worker 创建时重新 materialize 为 worker variant
    fi
done
```

**F107 自动化方案建议**：
- **方案 A** migrate-095/107 CLI：扫描 `behavior/agents/*/{SOUL,HEARTBEAT}.md`，按内容 hash 检测旧版（不含 worker variant 特征短语），覆盖为 worker variant
- **方案 B** 运行时检测：`materialize_agent_behavior_files` 增 `force_overwrite_if_outdated` 参数，调用方按需启用
- **方案 C** Worker AgentProfile.kind="worker" 触发 F107 capability layer 重 materialize

### 2. share_with_workers 字段彻底删除

F095 保留字段（UI / behavior_commands.py:113,165 / models/behavior.py:78,114 仍读），envelope 去过滤。F107 capability layer refactor 时彻底删除：
- `BehaviorPackFile.share_with_workers` 字段
- `BehaviorWorkspaceFile.share_with_workers` 字段
- behavior_commands.py 显示逻辑改为按 `BehaviorLoadProfile` 显示（"main only" / "shared" / "worker only"）
- SQL schema 迁移（如有持久化）

### 3. WorkerProfile 完全合并 AgentProfile

F095 用 `AgentProfile.kind="worker"` + metadata fallback；F107 完全合并时 F095 测试 fixture `_make_worker_profile` 可继续使用 kind 路径，metadata fallback 路径（用于历史数据）可在 F107 删除。

### 4. tooling/harness/capability_pack 三层职责（D9）

F095 不改这层；F107 处理时如重构 `apps/gateway/src/octoagent/gateway/services/agent_decision.py`，注意 F095 引入的：
- `_generate_behavior_pack_id` （pack_id hash 函数）
- `_make_pack_with_loaded_metadata` (cache miss metadata 标记)
- `make_behavior_pack_loaded_payload` (sync helper)
- `BehaviorPackLoadedPayload` (`packages/core/src/octoagent/core/models/behavior.py`)

这些是 F095 → F096 接口，F107 重构时需保留 API 兼容（或同步改 F096 调用方）。

---

## 不在 F096/F107 范围（长期跟踪）

- pack_id 长度（16 hex 在单用户单 worktree 足够；F096 跨用户审计场景如需要可扩到 32/64 hex）
- BEHAVIOR_PACK_LOADED 事件的 retention / cleanup 策略（按 EventStore 通用策略）
- Worker 真正与用户对话的 H3-B Ask-Back 通道（F099 范围）

---

## docs/codebase-architecture/harness-and-context.md 同步建议

F095 没改 Harness 结构（中央 ToolRegistry / SnapshotStore / ApprovalGate 等不动），但改了 behavior 加载行为：

- **Worker LLM context**：4 文件 (AGENTS/TOOLS/PROJECT/KNOWLEDGE) → 8 文件 (加 IDENTITY/USER/SOUL/HEARTBEAT)
- **share_with_workers 字段语义降级**：从过滤源 → UI 显示提示
- **BehaviorPack.pack_id 改造**：从 `f"behavior-pack:{profile_id}"` → hash 化（含 load_profile + content）
- **BEHAVIOR_PACK_LOADED 事件 schema** 已就位（emit 接入由 F096 完成）

如 harness-and-context.md 有"Worker behavior 加载"章节，需更新；如无，本 handoff 已留接口。F107 capability layer refactor 时统一同步更稳。
