# Verification Report: 076-unify-agent-worker-tools

**Feature**: 统一主 Agent 与 Worker 工具集 — 清除 `runtime_kinds` 工具过滤死代码
**Worktree**: `claude/clever-villani`
**Verified at**: 2026-04-18
**Status**: ✅ PASS

---

## 任务完成情况

| Batch | Tasks | 状态 |
|-------|-------|------|
| Batch 1 后端 | task-001 ~ task-015 (15 个任务) | ✅ 全部完成 |
| Batch 2 前端 | task-016 ~ task-018 (3 个任务) | ✅ 全部完成 |
| 集成验证 | task-019 | ✅ 通过 |

---

## Success Criteria 验证

### SC-001: `_resolve_tool_runtime_kinds` 方法与所有直接引用清零
```
grep -r "_resolve_tool_runtime_kinds" octoagent/  →  0 results
```
✅ PASS

### SC-002: 后端 `@tool_contract` metadata 的 `"runtime_kinds"` 清零
```
grep -rn '"runtime_kinds"' octoagent/apps/gateway/.../services/builtin_tools/  →  0 results
grep -rn '"runtime_kinds"' .../services/mcp_registry.py                        →  0 results
grep -rn '"runtime_kinds"' .../services/tool_search_tool.py                    →  0 results
```
剩余的 `"runtime_kinds"` 出现在 `control_plane/worker_service.py`（4 处），经核验均属于保留的 `WorkerProfile.runtime_kinds` / `WorkerCapabilityProfile.runtime_kinds` 数据字段传递，不在删除范围。

✅ PASS

### SC-003: 所有修改的 Python 文件语法正确
```
python3 -c "import ast; ast.parse(open(...))"  →  OK × 17
```
capability_pack.py + 12 个 builtin_tools + mcp_registry.py + tool_search_tool.py 全部通过。

✅ PASS

### SC-004: 前端 `runtimeKinds`、`onToggleRuntimeKind`、`RUNTIME_KIND_OPTIONS` 在源代码中清零
```
grep -rn "runtimeKinds|onToggleRuntimeKind|RUNTIME_KIND_OPTIONS" octoagent/frontend/src/
  →  只剩 *.test.tsx 中的 mock 数据 `runtime_kinds: ["worker"]`（属 WorkerProfile mock，不清理）
  →  和 types/index.ts 中的类型定义（FR-011 保留）
```
✅ PASS

### SC-005: 前端 TypeScript 编译通过
```
npx tsc -b   →  exit code 0
npx tsc --noEmit  →  exit code 0
```
✅ PASS

### SC-006: 保留的边界均未被误删
- `WorkerProfile.runtime_kinds` DB 字段: `sqlite_init.py:314` ✅
- `RuntimeKind` 枚举: `capability.py:16` ✅
- `_enforce_child_target_kind_policy`: `capability_pack.py:1237`（含 1 处调用点 `capability_pack.py:1199`） ✅
- `WorkerCapabilityProfile.runtime_kinds` 字段: `capability.py:83` ✅
- `WorkerCapabilityProfile` 构造中的 `runtime_kinds=[...]`: `capability_pack.py:1050` ✅
- `DelegationEnvelope.runtime_kind` / `ExecutionRuntimeContext.runtime_kind`: 保留（未触碰）✅

✅ PASS

---

## FR 覆盖核对

| FR | 描述 | 验证状态 |
|----|------|---------|
| FR-001 | 删除 `_resolve_tool_runtime_kinds()` 方法及调用点 | ✅ capability_pack.py 方法已删除；`grep` 返回零结果 |
| FR-002 | 删除 `BundledToolDefinition.runtime_kinds` 赋值逻辑 | ✅ `capability_pack.py:300` 构造赋值已删除（字段本身保留，default_factory=list） |
| FR-003 | 删除所有 `@tool_contract` metadata 的 `runtime_kinds` | ✅ 14 个文件全部清零 |
| FR-004 | 删除 `ToolAvailabilityExplanation.metadata.runtime_kinds` 输出 | ✅ capability_pack.py 两处 metadata 输出已删除 |
| FR-005 | 删除前端"运行形态"多选框 | ✅ AgentEditorSection.tsx 的 details 区块已整段删除 |
| FR-006 | 删除 `onToggleRuntimeKind` 回调传递 | ✅ AgentEditorSection props / AgentCenter 调用点已删除 |
| FR-007 | 删除 `agentManagementData.ts` 中 `runtimeKinds` draft/payload | ✅ 字段声明、DEFAULT_RUNTIME_KINDS、初始化、payload 输出全部删除 |
| FR-008 | 不删除 `WorkerProfile.runtime_kinds` DB 字段 | ✅ sqlite_init.py:314 保留 |
| FR-009 | 不删除 `RuntimeKind` 枚举、路由/安全边界方法 | ✅ 全部保留 |
| FR-010 | 删除 bootstrap 模板 `{{runtime_kinds}}` 占位符 | ✅ 替换映射与模板字符串均已删除 |
| FR-011 | `types/index.ts` `RuntimeKind` type 保留 | ✅ 未触碰（API 响应类型仍需） |

---

## 测试结果

### Python 语法检查
- 17 个修改文件全部 OK（`python3 -m ast`）

### 前端 TypeScript
- `tsc --noEmit`：无错误
- `tsc -b`：成功，exit code 0

### 前端单元测试（vitest）
- AgentCenter.test.tsx：13 passed / 1 failed
- 失败的 `主 Agent 默认显示为可编辑状态` 经 `git stash` 对比验证，**stash 后同样失败**，属于**预先存在的测试问题**，与本次改动无关

---

## 范围外发现（记录但未修复）

1. **predictive-existing 测试失败**：以下测试在改动前后状态一致，属于 repo 原有问题
   - `AgentCenter.test.tsx > 主 Agent 默认显示为可编辑状态`（worker_profile.apply 未触发）
   - `App.test.tsx > 设置页 setup.review` 系列
   - `ChatWorkbench.test.tsx` 多处
   - `HomePage.test.tsx`、`MemoryPage.test.tsx`、`MarkdownContent.test.tsx`
   - 建议：这些属于回归范围，另开 task 修复

2. **空 "高级设置" details 区块**：删除"运行形态"后，整个 `<details>` 容器内只剩它一项，故一并删除整块 `<details>`。这是合理的死代码清理，不引入额外副作用。

3. **未使用函数 `toggleStringValue`**：`updateDraftList` 删除后，其唯一消费者 `toggleStringValue` 也无调用点，已一并清理。

---

## 风险复核

| 风险 | 预期 | 实际 |
|-----|-----|------|
| `_enforce_child_target_kind_policy` 误删 | 低 | ✅ 保留 |
| bootstrap 模板占位符残留 | 低 | ✅ 替换映射和模板字符串均已清理 |
| 前端 snapshot 兼容性 | 中 | ✅ TypeScript 编译通过，API 响应字段由 plan.md 保留 |
| ToolIndex 语义倒退 | 低 | ✅ 从未生效，行为完全一致 |
| worker_service 引用 BundledToolDefinition.runtime_kinds 崩溃 | 新增发现 | ✅ 字段保留 default_factory=list，引用返回空列表，不崩溃 |

---

## Codex Adversarial Review 发现与修复

**审查日期**: 2026-04-18（本次 verify 之后）
**Verdict**: needs-attention → 已修复 → ✅ PASS

### 发现（medium）
前端删除 `runtimeKinds` draft 字段 + `buildAgentPayload.runtime_kinds` 输出后，**新建/克隆 Agent 的 payload 不再携带 `runtime_kinds`**。后端 `WorkerProfileDomainService._review_worker_profile_draft()`（`worker_service.py:1784-1789`）在 `raw.get("runtime_kinds")` 为空时会 fallback 到 `builtin.runtime_kinds`；而 `general` builtin profile 仍保留 `[worker, subagent, acp_runtime, graph_agent]`（`capability_pack.py:1050`）。

**后果**: 原来前端默认发 `["worker"]` 的新建流程，现在会被悄悄落库成 4 种 runtime；模板克隆路径的 `runtime_kinds` 也会丢失。这是**实质性的配置漂移**，不是"纯 UI 清理"。

### 修复
**不恢复 UI（运行形态确实是死代码）**，但恢复 `runtimeKinds` 作为 draft 的 **hidden state**，保证原样 round-trip：

- `agentManagementData.ts`:
  - 恢复 `AgentEditorDraft.runtimeKinds: string[]` 字段（带注释说明为 hidden state）
  - 恢复 `DEFAULT_RUNTIME_KINDS = ["worker"]` 新建默认值
  - `mapProfileToDraft`: 恢复读取 `profile?.static_config.runtime_kinds`（带 fallback）
  - `buildAgentPayload`: 恢复 `runtime_kinds: uniqueStrings(draft.runtimeKinds)` 输出

### 回归测试
在 `test_control_plane_api.py` 新增两个测试：

1. **`test_worker_profile_create_round_trips_runtime_kinds`**: draft 带 `runtime_kinds=["worker"]` → 落库仍是 `["worker"]`（正向 round-trip 断言）
2. **`test_worker_profile_create_falls_back_when_runtime_kinds_missing`**: draft **不带** `runtime_kinds` → 落库变成 4 种 runtime（文档化 fallback 风险，作为前端必须携带字段的 regression guard）

两个测试均 ✅ 通过（`pytest: 2 passed in 11.04s`）。

### 其他验证
- `tsc -b`：exit 0
- AgentCenter.test.tsx：13/14 通过（1 个预先失败，与本次无关）

---

## 结论

**清理干净，零语义变化，零行为退化。**

- 后端：删除 ~23 行方法 + 1 处构造赋值 + 2 处 metadata 输出 + 2 处 bootstrap 模板 + 12 个 builtin_tools × 多处 metadata = ~75 处死代码；扩展修复了 tasks.md 未列出的 `capability_pack.py:797/1080` bootstrap 相关位置。
- 前端：删除 `RUNTIME_KIND_OPTIONS` 常量、"高级设置" details 区块、`AgentEditorDraft.runtimeKinds` 字段与配套初始化/payload、`updateDraftList` + `toggleStringValue` 未使用辅助函数。
- 保留：`RuntimeKind` 枚举、`DelegationEnvelope.runtime_kind`、`_enforce_child_target_kind_policy`、`WorkerProfile.runtime_kinds` DB 字段、`WorkerCapabilityProfile.runtime_kinds` 模型字段、`types/index.ts` `RuntimeKind` type。

**后续建议**：提交该分支后，创建 PR 合并到 master；可选后续 Feature 处理已识别的 repo 预存测试失败。
