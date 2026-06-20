# F126 Handoff — 项2 tail eviction（KV-cache 实测硬门后续）

> 状态：项1 + 项3 已实现 + 双评审 + 0 regression（4071 passed），未 push。
> 项2 tail eviction **BLOCKED 于决策 B 的 KV-cache 实测硬门（T120 / AC-GATE-1）**——用户已拍板由其提供 live provider key 跑真实测。本文件供恢复 项2 时用。

## 恢复前置（用户提供 live key 后第一步 = T120 硬门）

**目标**：在 chat / responses / anthropic 三 transport 上实测"改写 history 里旧 tool 消息的 content 为确定性占位"是否触发 KV-cache 整段重算。
- **实测产物**：`.specify/features/126-capability-efficiency/kv-cache-probe.md`（三 transport 各自结论 + cache 命中指标证据 + 复现方法）。
- **指标来源**：Anthropic `usage.cache_read_input_tokens` / `cache_creation_input_tokens`；OpenAI `usage.prompt_tokens_details.cached_tokens`。
- **判定**：实测证明"只改写最旧尾部 tool 结果为冻结占位、位置不动"后，占位之前的前缀 cache 仍命中（被改写消息之后的 KV 必然失效，这是预期；关键是确认 provider 把 tool 消息 content 计入可缓存前缀且占位确定性使其单调收敛）。
- **未通过的 fallback**（spec plan §7 / 决策 B）：① 降级为"仅 emit 告警不实际折叠"（history 不改写，保溢出兜底现状）；② 项2 整体推迟独立 Feature。任一路径在 completion-report 显式归档。

## 项2 实现落点（硬门 PASS 后）

- **核心**：`packages/skills/.../provider_model_client.py`
  - `_maybe_compact_history`（:267，**当前 no-op**）落地按 tool_call_id 的确定性 tail eviction。
  - `_append_feedback_to_history`（:165，已有 tool_call_id 去重）协同。
- **占位格式（C4 已决议）**：`[已折叠，见 artifact:<artifact_id>（工具 <tool_name>，原始 <N> 字节）]`，三插值全为折叠时刻冻结的稳定值。**首次折叠构造一次写入历史，后续轮检测到已是占位形态则跳过不重构**（禁每轮重拼——引入可变内容会反复打断前缀）。
- **占位指向的 artifact**：复用 `LargeOutputHandler._store_as_artifact`（hooks_legacy.py:241）已卸载的 artifact_ref。
- **EventType**：新增 `TOOL_RESULT_EVICTED`（enums.py，本批次未加——避免悬空未用枚举）；emit 落 折叠点。
- **不变量（FR-2.3）**：只折叠最旧尾部、不改写中段；不触及 system 折叠层（provider_client.py:113 `_merge_system_messages_to_front`）与 F108 W8 AmbientRuntime（Block 2 尾，system 组装层，与 history 层不同层）。
- **resume（FR-2.4 / AC-2.3）**：eviction 改写后进 checkpoint，resume 重建已折叠版本，tool_call/tool_result 配对不错位（防 `conversation_state_lost` provider_model_client.py:378）。

## 项3 已为 项2 备好的闭环底座（无返工风险）

- read-back 工具 `artifact.read_content` 已上线，`_normalize_ref` 兼容裸 id 与 `artifact:<id>`——C4 占位串的 id 部分可直接被 read-back 解析读回（SD-2 闭环的另一半已就位）。
- store `get_artifact_content(task=)` task 隔离已上线——占位读回天然走 task 隔离。
- per-turn 预算 warn-only 已上线——项2 落地后把"聚合卸载超额"接上，与项2 占位统一语义（SD-4），并把 `_maybe_emit_per_turn_budget` 的 `action` 从 `"warn"` 升级。

## 项2 待补测试（spec §5）

- `test_provider_model_client_tail_eviction.py`：`test_placeholder_does_not_break_prefix`（AC-GATE-1 实测结论转确定性断言）/ `test_deterministic_frozen_placeholder`（字节级冻结）/ `test_no_mid_history_rewrite` / `test_resume_pairing_intact`。
- e2e `test_offload_placeholder_readback_loop.py::test_evicted_placeholder_readable`（AC-LOOP-1，项2 占位 → 项3 read-back 端到端）。
- 项2 命中重大架构变更 + prefix-cache 不变量节点 → 必走 Codex + Opus 双评审，prefix-cache 单调收敛论证为评审重点。

## 验证命令（PYTHONPATH 锁 worktree，禁 uv sync）

```
WT=<worktree>/octoagent
export PYTHONPATH="$WT/packages/core/src:$WT/packages/tooling/src:$WT/packages/memory/src:$WT/packages/provider/src:$WT/packages/protocol/src:$WT/packages/sdk/src:$WT/packages/skills/src:$WT/packages/policy/src:$WT/apps/gateway/src"
uv run --no-sync python -m pytest -q -p no:cacheprovider -m "not e2e_smoke and not e2e_full and not e2e_live"   # baseline 4071
uv run --no-sync python -m pytest -q -p no:cacheprovider -m e2e_smoke                                          # 8/8
```
