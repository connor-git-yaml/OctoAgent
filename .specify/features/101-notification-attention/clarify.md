# F101 Spec Clarify 报告

**扫描日期**: 2026-05-15
**扫描基于**: spec.md（已就地修复 BLOCKER）+ tech-research.md

---

## Summary

- **Blocker 修复**: 4 项（已就地修改 spec.md）
- **Clarify 待澄清**: 5 项（等用户在 GATE_DESIGN 决议）
- **Suggest 改进**: 3 项（plan 阶段参考）

---

## Blocker 修复列表

### B-1：FR-B1 悬空 AC 引用（行 147）

**Before**:
```
- 可追踪到：User Story 1 AC-A3、User Story 3 AC-A3。
```

**After**:
```
- 可追踪到：AC-B5（WAITING_APPROVAL 通知推送）、AC-C3（超时清理联动）。
```

**理由**: spec §5 中无任何 "AC-A" 编号系列（用户场景内嵌的验收场景是散文，非正式 AC 编号）。正确对应：WAITING_APPROVAL 触发通知 → AC-B5；超时清理联动 → AC-C3。

---

### B-2：FR-B3 与 AC-B4 语义矛盾（行 153）

**Before**:
```
在 quiet hours 期间（默认 23:00-07:00）仅推送 `approval_pending` 级别通知
```

**After**:
```
当 USER.md `active_hours` 字段存在且格式合法（`HH:MM-HH:MM`）时，NotificationService MUST 在 quiet hours 期间（`active_hours` 范围外的时段）仅推送 `approval_pending` 级别通知，过滤其他级别。若 `active_hours` 字段不存在或格式非法，NotificationService MUST 回退到全时段推送（无过滤）。
```

**理由**: 原文"默认 23:00-07:00"与 AC-B4"字段不存在时无过滤"直接矛盾——"默认值"暗示字段不存在时系统仍会按 23:00-07:00 过滤。AC-B4 的语义（不存在时无过滤）是正确的向后兼容行为，FR-B3 应与之对齐。修复后移除"默认 23:00-07:00"表述，明确字段不存在时的回退语义。

---

### B-3：FR-B4 悬空 AC 引用（行 158）

**Before**:
```
- 可追踪到：User Story 2 AC-A4。
```

**After**:
```
- 可追踪到：AC-B4（字段不存在时无过滤）。
```

**理由**: "User Story 2 AC-A4" 是散文里的第 4 条验收场景，对应正式 AC 编号是 AC-B4。

---

### B-4：FR-B6 悬空 AC 引用（行 165）

**Before**:
```
- 可追踪到：User Story 3 AC-A1/A2。
```

**After**:
```
- 可追踪到：AC-B1（Worker 完成通知精确一次）。
```

**理由**: "User Story 3 AC-A1/A2" 是散文里的第 1、2 条验收场景，对应正式 AC 编号是 AC-B1（覆盖 SUCCEEDED 和 FAILED 两种终态）。

---

## Clarify 待澄清（用户 GATE_DESIGN 确认）

### Clarify-1：dismiss 跨通道同步方向（FR-B5）

**上下文**: FR-B5 规定"任一通道 dismiss 后，通知标记为已处理"。AC-B6 验证的是"Telegram dismiss 后 Web dismiss 幂等"。但 spec 没有说明：

1. **反向同步**：Telegram dismiss 后，Web UI 的通知角标/列表是否实时更新为"已处理"状态？还是 Web 下次刷新时才反映？
2. **并发冲突**：用户在 Telegram 和 Web 几乎同时点 dismiss（两个请求并发到达），是否需要加锁或乐观并发控制？

**推荐**（AUTO-RESOLVED 候选，非 CRITICAL）：Telegram dismiss 后 Web 下次轮询/刷新时反映（不做实时 SSE 推送）；并发 dismiss 采用 "last write wins" 语义（dismiss 操作是幂等的，谁先到谁生效，结果相同）。

**用户需确认**：是否接受"Telegram dismiss 后 Web 不实时反馈"？如要求实时同步，需增加 SSE 推送通道复杂度（接近新功能）。

---

### Clarify-2：FR-B7 attention_work_count 是否需要独立 AC

**上下文**: FR-B7 是 SHOULD 级别（可选），spec 正文补充了"若实施则通过 AC-B1 的 event_store 事件记录间接验证"，但没有明确独立的 AC-B7。

**选项**:
| 选项 | 描述 |
|------|------|
| A | 不增加 AC-B7，FR-B7 通过 AC-B1 的 event_store 记录间接验证，GATE_DESIGN 时决定是否实施 |
| B | 新增 AC-B7：Given Worker 开始/结束，When task 状态变更，Then attention_work_count 字段递增/递减 |

**推荐**: 选项 A，理由：FR-B7 是 SHOULD 级别，如果用户在 GATE_DESIGN 决议实施，补充 AC-B7 放在 plan 阶段更合适（不阻塞 spec 关门）。

---

### Clarify-3：FR-D4 API 显式 force_full_recall 参数是否纳入 F101 范围

**上下文**: FR-D4 是 SHOULD 级别，允许用户/admin 通过请求体显式传入 `force_full_recall: bool = False`，优先级高于长度自动检测。spec 没有对应 AC。

**选项**:
| 选项 | 描述 | F101 影响 |
|------|------|---------|
| A | 纳入 F101，补充 AC-D4 | 增加 ChatSendRequest schema 改动 + 验收 |
| B | 推迟到 F107 或作为 F101 Phase 末尾顺手清 | 不改 schema，F101 只做自动检测 |

**推荐**: 选项 B（推迟），理由：F100 handoff §6.1 列为 candidate，但自动检测（FR-D1/D2/D3）已覆盖主要场景；显式 API 参数涉及 ChatSendRequest schema 变更和文档更新，单独作为小 follow-up 更干净。

---

### Clarify-4：LONG_PROMPT_THRESHOLD 单位是字符数还是 token 数

**上下文**: FR-D3 说"建议 2000 字符，不得 hardcode"，但 spec 没有明确"字符"是 Unicode 字符数（`len(message)`）还是 token 数（需要分词）。

**影响**: 
- Unicode 字符数：实现简单，但中文每字约等于 1-1.5 token，英文每词约 1-2 token，2000 字符对应 token 数差异较大
- Token 数：准确但需要引入分词器（依赖 LLM provider）

**推荐**（AUTO-RESOLVED）：Unicode 字符数（`len(message)`）。理由：F101 的 producer 目标是过滤"明显长的 prompt"，字符数精度足够；token 分词引入外部依赖，实现复杂度不合算。GATE_DESIGN 时确认即可。

---

### Clarify-5：AC-B2/B3 中 quiet hours 的时间边界含义

**上下文**: AC-B2/B3 说"当前时间在 quiet hours 内（active_hours 范围外的时段）"，但 active_hours 格式是 "09:00-23:00"（活跃时段），quiet hours 是其补集（23:00-09:00 次日）。边界时刻（恰好 23:00 或 09:00）的归属未定义（闭区间还是半开区间）。

**推荐**（AUTO-RESOLVED）：active_hours 为左闭右开区间 `[start, end)`，即 `active_hours: "09:00-23:00"` 时，09:00 属于活跃时段，23:00 属于 quiet hours。实现用 `start <= now < end` 判断，与大多数调度系统惯例一致。

---

## Suggest 改进（plan 阶段参考）

### S-1：§12 不确定性章节补充第 3 项

当前 §12 只有 2 项待确认（SSEHub per-session 广播 + NotificationService 注入状态）。建议 plan Phase 0 侦察同时确认第 3 项：

**新增第 3 项**: dismiss 跨通道同步方向（对应 Clarify-1），plan 阶段实测 SSENotificationChannel 现有 dismiss 接口是否有状态持久化（内存 set vs 持久化），决定实现方案。

已在 spec.md §12 补充第 3 条不确定性条目。

---

### S-2：FR-C4 integration test 范围明确化

FR-C4 要求"有集成层测试覆盖（非纯 mock-based）"，但未定义"集成层"边界：是 in-process（service layer 真实调用）还是需要 HTTP 端到端（有 FastAPI TestClient）？

**建议**: plan 阶段将 AC-C4 细化为"service layer integration test（不跑 LLM，但真实 task_runner + event_store + ask_back_tools 调用链）"，与 e2e_smoke 区分，避免实施时对"集成"有歧义。

---

### S-3：M-1 broad-catch 实际影响位置数核实

tech-research §A-2-6 说"共 4 处 broad-catch"，其中 3 处是有问题的 `except Exception: pass`（ask_back:194, request_input:282, escalate_permission:376），1 处是合理降级（ask_back 外层 :219）。

spec §4 FR-C9（实际对应 User Story 5 AC-A1）和 spec 散文引用的是"3 处"，与 tech-research "4 处"表述不一致——两者都正确（问题的 3 处 vs 总计 4 处），但措辞容易让 plan 阶段遗漏 `request_input:282` 和 `escalate_permission:376`（只盯 ask_back:194 那一处）。

**建议**: plan 阶段在 Phase 任务中明确列出 3 个文件行号（194/282/376），避免遗漏。

---

## User Story 5W1H 检查表（P1 User Story）

### US 1 — 审批请求真正到达用户

| W/H | 描述 | 评估 |
|-----|------|------|
| **Who** | Worker 调用 escalate_permission | OK |
| **What** | 任务进 WAITING_APPROVAL + SSE 推审批事件 | OK |
| **When** | Worker 执行高风险操作时 | OK |
| **Where** | Web UI / Telegram | OK |
| **Why** | 宪法规则 7 User-in-Control | OK |
| **How** | ApprovalGate.sse_push_fn 注入 → escalate_permission_handler → WAITING_APPROVAL | OK，路径清晰 |

### US 2 — 通知有优先级，夜间只推关键通知

| W/H | 描述 | 评估 |
|-----|------|------|
| **Who** | 任意 Worker 任务触发通知 | OK |
| **What** | NotificationService 按优先级过滤，quiet hours 内只推 critical | OK |
| **When** | 通知触发时，读取 USER.md active_hours 决定是否推送 | OK |
| **Where** | USER.md（SoT）→ NotificationService | OK |
| **Why** | 减少噪声，夜间睡眠不被普通通知打扰 | OK |
| **How** | user_profile.update 更新 active_hours → NotificationService 读取 → 过滤决策 | OK，但 user_profile.update 是否支持 active_hours 字段更新未实测（见 Clarify 章节） |

### US 3 — Worker 完成/失败时可靠推送一次

| W/H | 描述 | 评估 |
|-----|------|------|
| **Who** | task_runner 处理 Worker 终态 | OK |
| **What** | NotificationService.notify_task_state_change 精确一次调用 | OK |
| **When** | Worker SUCCEEDED / FAILED 时 | OK |
| **Where** | task_runner._notify_completion | OK |
| **Why** | 用户需要知道任务完成结果 | OK |
| **How** | WAITING_APPROVAL → 终态路径也覆盖（修复 task_runner.py:404-406）| 路径清晰，已有 AC-B5 覆盖进入 WAITING_APPROVAL，但**从 WAITING_APPROVAL 退出到终态时的通知**（US 3 独立测试方法第③条）spec §5 AC 中无明确条目——SUGGEST: plan 阶段补充此场景的验收条目 |
