# F134 plan

baseline：本地 master `5311e250`（origin/master f011f361 + M10 立项 docs）。worktree `F134-bearer`，分支 `feature/134-bearer-hardening`。

## 改动文件清单

| 文件 | 改动 | 范围 |
|------|------|------|
| `octoagent/apps/gateway/src/octoagent/gateway/services/frontdoor_auth.py` | +`_FailureRateLimiter`（clock DI）+ guard 三分支接线（verify-first 语义）+ 429 error helper | ① |
| `octoagent/apps/gateway/src/octoagent/gateway/middleware/logging_config.py` | +uvicorn.access logger 级脱敏 filter（幂等挂载，setup_logging 内） | ③ |
| `octoagent/packages/provider/src/octoagent/provider/dx/remote_commands.py` | `_token_hint_lines` 明文建议删除 → `_ensure_bearer_token`（生成/append .env/0600/幂等/失败即止）+ enable 编排接线 + dry-run 文案 | ② |
| `octoagent/packages/provider/src/octoagent/provider/dx/attest_commands.py` | 负向断言 401 → {401,429}（detail 文案同步） | ① |
| `octoagent/apps/gateway/tests/test_frontdoor_auth.py` | +`TestFrontDoorRateLimitMatrix`（AC-R1..R8）+ limiter 单元段（AC-L1）；既有 17 格零触碰 | ① |
| `octoagent/packages/provider/tests/dx/test_remote_commands.py` | +AC-T1..T4 | ② |
| gateway logging_config 测试（既有文件或新增） | +AC-S1 | ③ |
| core log_redaction 测试 | +AC-S2 钉住 | ③ |
| `octoagent/packages/provider/tests/dx/test_attest_commands.py` | AC-A1 | ① |
| `docs/blueprint/milestones.md` M10 表 F134 行 | ✅ 收口 | docs |
| `docs/codebase-architecture/remote-access.md` | +限流/自动 token/SSE 泄露收敛段 | docs |
| `.specify/features/134-bearer-hardening/{spec,plan,completion-report}.md` | 制品 | docs |

不动：`client.ts` / `useSSE` / `useChatStream` / L1 spec / F144 既有 17 格 / `log_redaction.py` 规则 / FrontDoorConfig schema。

## 顺序

1. spec/plan commit → `codex review --base master`（spec 评审）→ finding 闭环
2. 范围①限流（frontdoor_auth + 矩阵扩格 + attest 判定）→ 聚焦回归
3. 范围②token 生成（remote_commands + 测试）→ 聚焦回归
4. 范围③SSE 收敛（logging filter + 钉住测试）→ 聚焦回归
5. 终门：全量 0 regression + e2e_smoke/scripted + frontdoor 矩阵全绿（17 既有 + 新格）
6. 双评审：Codex final（挑战面：限流被绕/锁自己/DoS/熵/矩阵真扩格）+ Opus 自审（#5 零泄漏 sweep / #10 单入口 / serve 兼容）0 HIGH
7. completion-report + living-docs（milestones F134 行 + remote-access.md）

## 验证命令（worktree PYTHONPATH 锁，禁 uv sync）

```
cd <worktree>/octoagent && uv run --project . --no-sync python -m pytest apps/gateway/tests/test_frontdoor_auth.py -q
uv run --project . --no-sync python -m pytest packages/provider/tests/dx/ -q
uv run --project . --no-sync python -m pytest -q            # 全量终门
uv run --project . --no-sync python -m pytest -m e2e_smoke -q
```

## 风险与预案

- 限流状态挂 guard 单例：hermetic 测试间泄漏 → 矩阵每格独立 guard_app fixture（既有范式天然隔离，fixture 每次新建 FrontDoorGuard）。
- `uvicorn.access` filter 在测试环境无 uvicorn：getLogger 幂等无副作用（FR-3b 显式测试）。
- `.env` append 半写：单行 write ≤ PIPE_BUF 原子；写前 flush 判尾字节补 `\n`。
- attest 判定改动影响 F144 探针测试：同步改 test_attest_commands 对应格。
