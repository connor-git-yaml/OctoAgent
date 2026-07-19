# F134 completion-report

> M10 首波（F145∥F134∥F146 三路并行之一）。baseline：本地 master `5311e250`。
> worktree `F134-bearer` / 分支 `feature/134-bearer-hardening`。**未 push，等用户拍板。**

## 1. 交付 vs 计划（spec 三范围全兑现，零 deferred 主体）

| 范围 | 计划（spec） | 实际 | 偏离 |
|------|--------------|------|------|
| ① 认证失败限流 | `_FailureRateLimiter` + verify-first + 429 | 如设计落地；attest 负向断言 {401,429} 同步 | 无 |
| ② 强 token 自动生成 | serve 成功后生成写 .env（0600）+ 零明文 | 如设计落地；删除既有 stdout 明文建议行为 | 无 |
| ③ SSE 泄露收敛 | 选 (b)：uvicorn.access filter + 钉住 + (a) 归档 | 如设计落地；前端零改动 | 无 |

## 2. 改动文件与规模

生产代码 4 文件：
- `octoagent/apps/gateway/src/octoagent/gateway/services/frontdoor_auth.py`（+~175：limiter 类 + `_reject_invalid_credential` + 两分支接线 + reset）
- `octoagent/apps/gateway/src/octoagent/gateway/middleware/logging_config.py`（+~45：`_UvicornAccessRedactionFilter` + 幂等挂载）
- `octoagent/packages/provider/src/octoagent/provider/dx/remote_commands.py`（`_token_hint_lines` 明文建议 → `_write_generated_token`/`_token_generated_lines` + enable 编排 + dry-run 预览 + docstring 红线翻转归档）
- `octoagent/packages/provider/src/octoagent/provider/dx/attest_commands.py`（负向断言 401→{401,429}）

测试 5 文件：
- `test_frontdoor_auth.py`：+限流矩阵 9 格 + limiter 单元 7 + 阈值钉住 1（**F144 既有 17 格逐字未动**）
- `test_remote_commands.py`：+4 新格（AC-T1 零明文/AC-T2 dry-run/AC-T3 写失败即止/AC-T4 追加保内容）+ 3 既有测试适配（root `/fake/instance` → tmp_path，新行为下须真实可写）+ 2 语义演进（prompts→generates；shell-only 提示→自动写入）
- `test_logging_file_sink.py`：+AC-S1 三格 + autouse fixture 补 uvicorn.access filters 保存/恢复
- `test_log_redaction.py`：+AC-S2 契约钉住（规则零改动）
- `test_attest_commands.py`：+AC-A1（429 负向通过）

docs：`remote-access.md`（§5 表 + 新 §5c + §6 #5 翻转 + §7 limitation 收敛）+ `milestones.md` F134 行 + 本制品目录。

**不动清单兑现**：`client.ts` / `useSSE` / `useChatStream` / F140 L1 场景 / F144 17 格 / `log_redaction.py` 规则 / FrontDoorConfig schema / `.env.litellm` —— 与 F145（frontend）/F146（services+core）零文件交集。

## 3. 关键决策留档（评审挑战点的回答）

- **限流为何不锁正确凭证**：serve 场景 TCP 源恒 127.0.0.1（共享桶），OpenClaw 式 check-before-verify 会让攻击者 10 次错误尝试把唯一用户锁在门外 5 分钟（DoS 可用性 > 爆破增益）；本实例 token 256-bit 熵 + `compare_digest` 常时间，限流是纵深非主防线。攻击者在超限后仍被验证（每次 compare_digest 开销可忽略），但只能得到 429（无 valid/invalid 反馈差异）。
- **为何 loopback 源不豁免（与 OpenClaw 默认差异）**：serve 主入口就是 loopback，豁免=serve 路径限流形同虚设；本地 CLI 不会自锁（正确凭证恒放行）。
- **为何 key 不用 XFF**：直连 LAN 场景（`OCTOAGENT_HOST` 非 loopback + bearer）XFF 可被伪造成每请求换桶绕过限流；TCP 源地址不可伪造。serve 场景牺牲 per-远端粒度（共享桶）是私网单用户下可接受的取舍。
- **缺凭证为何不计数**：SPA 首屏并发裸请求 401 渲染 FrontDoorGate 是 F140 L1 场景②的正常路径；爆破必然带凭证。超限后缺凭证仍得 401 TOKEN_REQUIRED（无爆破信息增量）。
- **SSE 为何选 (b)**：取证三分——唯一实锤泄露面是 uvicorn access log 绕过 F129 脱敏链落盘（`uvicorn.access` 自带 handler + launchd fd 级 StandardOutPath）；Referer/history 面否证（EventSource URL 非导航 URL）；Tailscale 面理论（tailscaled 标准日志无 per-request URL）。(a) ticket 化会破坏 EventSource 自动重连语义（弱网手机重连是常态）、改动横跨三端超 S 预算，在私网下 ROI 为负。完整设计归档 spec §6 备走出私网时启用。
- **F130「绝不写 token 到文件」红线翻转**：原句意图是防 token 进 config/版本管理面；实测原"提示"路径把建议 token 明文打进 stdout（终端 scrollback / service 落盘），比 CLI 代写 0600 `.env` 更差。翻转归档于 remote_commands 模块 docstring + remote-access.md §6。

## 4. 验证（终门数字）

- 相关域聚焦：157 passed（frontdoor 42 + remote/attest 59+ + logging/redaction）
- 全量回归：见 §6 终门记录
- e2e_smoke / e2e_scripted：见 §6
- F144 17 格矩阵：全绿且逐字未改（`git diff` 可证既有格零触碰）
- serve 兼容语义：bearer 分支不检 XFF 逐字未变；A2 五格（bearer 正确 token × proxy header → 200）继续绿

## 5. 双评审闭环

见 §6 终门记录（Codex final + Opus 自审，0 HIGH 收敛）。

## 6. 终门记录

（实施尾声填写）

## 7. 已知 limitations / follow-up

- 限流参数为常量（60s/10/300s/256）不进配置面——单用户实例合理；若未来多实例形态需要调参再立 env。
- `uvicorn.access` filter 只覆盖 logger 级——若未来切换非 uvicorn server（hypercorn 等）需同步其 access log 通道（logging_config 注释已标）。
- ticket 化 (a) 存 spec §6，触发条件=front_door 走出 Tailscale 私网。
