# Verification Report: 077-fix-mcp-tool-permission

**Feature**: 修复 MCP 工具权限一致性 + Responses API call_id 配对防御
**Branch**: `claude/affectionate-hellman-d84725`
**Verified at**: 2026-04-18
**Mode**: fix
**Overall**: ✅ PASS（0 CRITICAL，5 WARNING，2 INFO）

---

## Phase 4a — Spec 合规审查

**结果**: ✅ PASS
**13/13 修复点已实现**（100%）
**Constitution 评估**：原则 3（Tools are Contracts）、原则 10（Policy-Driven Access）、原则 8（Observability）、原则 13A（上下文优先）全部 PASS

唯一 INFO：实现者主动把旧 type-based 分支的 `function_call_output` 也加了孤立过滤（超出 fix-report 的"保留现状"表述，但方向正确，防御一致性更好）。

## Phase 4b — 代码质量审查

**结果**: GOOD（0 CRITICAL，5 WARNING，2 INFO）

### WARNING 清单

| # | 维度 | 位置 | 描述 |
|---|------|------|------|
| W1 | 边界/错误处理 | `runner.py:415-418` | `get_tool_meta` 若抛异常透传；`tool_broker` 无 None 检查。最坏情况 broker 故障被误判为权限拒绝 |
| W2 | 死条件 | `litellm_client.py:160` | `if tool_meta.name in allowed_tool_names or is_mcp:` 永远为 True（上一 `continue` 已 skip 反面），可直接删 |
| W3 | 边界 | `models.py:464-468` | `tool_name = "mcp..evil"` 会通过（双点情况），因为 `tool_name[4:]` 为 `.evil`，`"." in ".evil"` 为 True |
| W4 | 可观测性 | `litellm_client.py:351-357` | `call_id` 为空的 tool 消息被静默 skip，无 debug 日志 |
| W5 | 测试健壮性 | `test_runner.py:176-211` | 未显式断言 `echo_manifest.permission_mode == RESTRICT`，若未来 fixture 默认变 INHERIT 则变为假阳性通过 |

### INFO 清单

| # | 描述 |
|---|------|
| I1 | 旧 type-based 格式分支注释"不应再出现"但仍完整维护，长期是技术债，建议注明弃用版本 |
| I2 | `runner.py:412` `if allowed_tool_names and ...` 空列表跳过校验是既有 INHERIT fallback 行为，建议加行注释说明 MCP 豁免不需要在此分支介入 |

## Phase 4c — 工具链验证

### 测试结果
- ✅ 5 个新增测试全部通过（`pytest: 5 passed in 1.52s`）
  - `test_runner_mcp_tool_exempt_from_allowlist` — 正向修问题 1
  - `test_runner_non_mcp_tool_not_exempted` — 回归保护（非 mcp 不得误放行）
  - `test_history_to_responses_input_paired_tool_included` — 正常配对场景
  - `test_history_to_responses_input_orphan_tool_filtered` — 防御修问题 2（新格式）
  - `test_history_to_responses_input_legacy_orphan_filtered` — 防御修问题 2（旧格式）
- ✅ skills 包全量 **334 passed**，3 个失败**预先存在**（`log.isEnabledFor(10)` structlog API 误用，stash 对比已验证与本次无关）

### Ruff Lint
- 本次改动行**无任何新增 lint 错误**
- 仓库既有 14 处 ruff 问题（long line / SIM / unused import 等预先存在技术债），不属于本次范围

### Python 语法
- 3 个修改的源文件 AST 解析全部 OK

---

## 独立发现（本次 fix 范围外）

**`litellm_client.py:187` `log.isEnabledFor(10)` structlog API 误用**
- `structlog.BoundLogger` 没有 `isEnabledFor` 方法（那是 stdlib `logging.Logger` 的 API）
- 实际影响：3 个 test_litellm_client.py 测试失败（跑到 DEBUG 分支时 AttributeError）
- Stash 对比确认**预先存在**，与本次 fix 无关
- 建议后续单独开 fix 修复（把 `log.isEnabledFor(10)` 改为 `log.is_enabled_for(10)` 或直接去掉 DEBUG 守卫）

---

## GATE_VERIFY 决策

```
[GATE] GATE_VERIFY | policy=on_failure | override=无 | decision=AUTO_CONTINUE | reason=0 CRITICAL，5 WARNING 均为非阻断性改进建议
```

**验收标准**：
- [x] 新增测试 5/5 通过
- [x] 包级回归无新增失败
- [x] Spec 合规 PASS
- [x] 代码质量无 CRITICAL
- [x] 修改行 lint 干净
- [x] 保留边界（runner 非 mcp 工具依旧拒绝，测试 `test_runner_non_mcp_tool_not_exempted` 验证）

## WARNING 补修（用户决策后执行）

### W2 — litellm_client.py:157-180 控制流简化
- 删除永真守卫 `if tool_meta.name in allowed_tool_names or is_mcp:`
- 保留原 `continue` 分支行为不变
- 简化控制流，减少后续维护误读风险

### W3 — is_runtime_exempt_tool 边界加固（真实安全边界 bug）
- 原逻辑 `"." in tool_name[4:]` 会让 `"mcp..evil"`（`tool_name[4:]==".evil"`）通过豁免
- 新逻辑：`remainder` 不能以 `"."` 开头（server 段非空），仍需含 `"."`
- **新增 parametrize 单元测试 `test_is_runtime_exempt_tool_edge_cases`（12 条）**覆盖正向、反向、双点、空段等边界

### W5 — test_runner_mcp_tool_exempt_from_allowlist 断言前提
- 在测试用例开头加 `assert echo_manifest.permission_mode == SkillPermissionMode.RESTRICT`
- 若未来 fixture 默认值变化，测试会立即失败而非假阳性通过

### 未补修（保留记录）
- W1（runner.py broker 异常捕获）：改动异常语义风险较高，建议独立 fix
- W4（空 call_id 静默 skip）：debug 日志加固，价值有限，独立 fix

## 最终测试结果（W2/W3/W5 补修后）

- ✅ 17 个新增测试全部通过（12 parametrize + 2 runner + 3 litellm_client）
- ✅ skills 包全量 **346 passed**（基线 334 + 新增 12）
- ✅ 3 个失败依旧是**预先存在**的 `log.isEnabledFor(10)` structlog API 误用（stash 对比确认）

## 结论

**修复干净，两个主 bug + 3 个 WARNING 一并修复；W1/W4 和独立发现 structlog 误用留待后续。**

建议下一步：
1. 提交 2 个 commit（主修 + WARNING 补修）
2. Rebase 并推送到 origin master
3. 独立 fix 跟进 `log.isEnabledFor(10)` structlog 误用
