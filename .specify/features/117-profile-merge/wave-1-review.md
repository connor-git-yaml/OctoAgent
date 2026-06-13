# F117 Wave 1 — 评审记录（review log）

> Wave 1 = 镜像 populate（加性）：两个镜像 builder 复制 worker 静态配置 9 字段进统一 agent_profiles(kind=worker) 行。
> 评审：Opus 对抗评审（agent a1d35d7）。回归 4135 = baseline + e2e_smoke 8/8。

## 评审结论：0 HIGH，Wave 1 纯加性；2 MEDIUM + 3 LOW 均为 **Wave 2 前瞻 gate**（非 Wave 1 bug）

### 验证通过
- **真加性 / 零运行时变更**：逐 consumer grep 确认无任何现有路径从 **AgentProfile** 读这 9 字段（capability_pack:434 agent_profile 分支只读 profile_id/version/model_alias/tool_profile/name 且因 mirror 与 worker 同 id 实际不可达；agent_service:99 AgentProfileItem 不含这 9 字段；chat:262 只读 model_alias）。populate 不可观测。
- **忠实复制**：两 builder 对 default_tool_groups/selected_tools/runtime_kinds 用 `list(...)` 拷贝（无源 list 别名）；status/origin_kind 枚举原样；scalar 直拷。9 源字段均 WorkerProfile 模型字段有默认 → 无 AttributeError 风险。
- **builder 间一致性**：Wave 1 对 9 字段未引入新分歧（`summary` 现两 builder 一致）。

### MEDIUM-1（Wave 2 强制 gate）：archive 路径不 sync 镜像 → 镜像 status 陈旧
- `worker_service.py:848 _handle_worker_profile_archive` 更新 worker_profiles（status=ARCHIVED）但**不调 `_sync_worker_profile_agent_profile`**；entity_ensure（:943）对 ARCHIVED worker 返回 None 不刷新镜像。→ publish→archive 后镜像 row 仍 `status=active`。
- **Wave 1 无害**（无消费方读 status）。但 **Wave 2 切读路径读 mirror status/archived_at 前必须闭合此 gap**，否则 archived worker 被误判 active。
- **闭合方式（Wave 2 自然解决）**：Wave 2 authoring 改写后，archive handler 直写统一 agent_profiles 行 status=ARCHIVED（统一行即权威，无 sync gap）。**Wave 2 验收：archive→读 liveness 端到端用 archived worker 断言 status 正确。**

### MEDIUM-2：migration vs runtime status 默认分歧 —— **核验为非问题**
- 评审担心 migration 默认 'active' vs builder 拷贝真实 DRAFT。**实测**：migration apply 用 `wd.get("status","active")` 读 worker_profiles **真实 status**（'active' 仅列缺失兜底，worker_profiles 恒有 status 列 → 不触发）。migration 忠实拷贝真实 status。非问题。Wave 2 仍以"统一行 status = worker 真实 status"为准。

### LOW（记录，Wave 2 处理）
- **LOW-1**：entity_ensure 对 ARCHIVED 返回 None，故 materialize-on-read 永远写不出 status=ARCHIVED 的镜像。与 MEDIUM-1 同源，Wave 2 authoring 直写统一行闭合。
- **LOW-2**：两 builder 的 created_at/version 处理不同（pre-existing，非 9 字段）。Wave 2 若按 version/created_at tie-break 需统一。inert today。
- **LOW-3**：entity_ensure:984 注释前瞻引用 Wave 2 行为。仅 intent doc。
- **persona_summary 分歧**（pre-existing）：lifecycle `=profile.summary` / entity_ensure `=""`。Wave 1 加 `summary` 后 entity_ensure 镜像 `summary` 有值但 `persona_summary` 空。Wave 2 镜像塌缩时统一。

## 状态
- 0 HIGH；2 MEDIUM（M1→Wave 2 gate / M2 核验非问题）+ 3 LOW（Wave 2 处理）。
- 回归 4135 = baseline + e2e_smoke 8/8。可 commit Wave 1。
