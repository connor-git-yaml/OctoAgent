# F144 验收自动吸收 — Completion Report

**Feature ID**: F144 / `attestation-absorb`
**Milestone**: M9 — P1（与 F140/F142 并行波次）
**分支**: `feature/144-attestation-absorb`（worktree `.claude/worktrees/F144-attest/`）
**Base**: origin/master `2bd8679b`
**Status**: ✅ 实施完成 + 双评审 0 HIGH，**未 push origin，等用户拍板**

---

## 1. 吸收对账（本 Feature 的存在理由）

| 手工验收项（原挂在用户身上）| 吸收到哪层 | 物理残余 |
|---|---|---|
| **F130 AC-1 语义半边**（serve 注入转发头时 bearer 放行/loopback 拒绝——此前仅 completion-report §2 人工复核记录）| **L4**：`test_frontdoor_auth.py::TestFrontDoorModeHeaderMatrix` 17 格 + 契约钉住（CI 常跑）| 无 |
| **F130 AC-1 链路半边**（手机经 tailnet 打开 Web UI）| **本机 live 探针**：`octo attest remote`（mode/tailscale/token/HTTP 五链检查，验活 published URL）| ATT-130-PHONE（optional，手机首屏人眼体验）|
| **F129 AC-1 崩溃自愈**（user-guide §6 手工脚本：记 pid→kill -9→等自愈→新 pid）| **本机 live 探针**：`octo attest service`（SIGKILL→poll status()→新 pid≠旧 pid，--dry-run 只检不杀）| ATT-129-BOOT（重启机器验开机自启，真 reboot 不可自动化）|
| **F135 gap-1**（USER.md 初始化引导闭环 + F136 审批，completion-report §8 真机步骤）| **L3 scripted**：`test_e2e_scripted_write_approval.py`（e2e_scripted marker，CI 可跑，零真 LLM 零 OAuth）| 无（全吸收）|

物理残余 → `docs/codebase-architecture/attestation-checklist.md`（机器可读 YAML，
仅 2 项，F141 release lane 消费；含增项纪律防清单膨胀）。

## 2. 实际做了什么 vs plan（Phase A→E，零跳步）

| Phase | 计划 | 实际 | 偏离 |
|-------|------|------|------|
| spec/plan | 收窄 spec → Codex 评审 | ✅ `codex review --base origin/master`：0 HIGH + 2 P2 全接受闭环 | 无 |
| A 矩阵 | 17 格 + guard 轻量 fixture | ✅ 26 passed（8 既有零修改 + 17 格 + 1 契约钉住）2s | 无 |
| B 探针 | attest_commands + 单测 | ✅ `octo attest remote/service` + 32 hermetic 单测 | json 模式闪断声明走 stderr（保 stdout 纯 JSON，实施期补强）|
| C gap-1 | scripted × F136 审批全链 | ✅ approve/reject 双路径 2 case 1.4s | **补生产同款 ctx 绑定**（见 §4 发现 1）|
| D 文档 | 清单 + living-docs + handoff | ✅ attestation-checklist / e2e-testing §11 / remote-access §5b / service-and-logging §4b / milestones F144 行 / handoff-to-F141 | 无 |
| E 终门 | 全量 + 双评审 | ✅ 见 §5/§6 | 无 |

## 3. 关键设计决策（含一处对任务书的显式偏离）

1. **service 探针 SIGKILL（偏离任务书的 SIGTERM，spec §D-3）**：launchd
   `KeepAlive{SuccessfulExit=false}` 只在非成功退出时拉起；uvicorn 收 SIGTERM
   优雅退出 exit 0 → **不拉起**（这正是 `octo stop` 的设计语义，
   service-and-logging.md §4 明文）。SIGTERM 探针在健康系统上必假失败；
   SIGKILL 是 F129 user-guide §6 钦定的崩溃模拟。
2. **enabled 信号 = mode==bearer 单独判定（Codex spec P2-1）**：bearer 下
   tailscale 断链归 **fail** 而非 not_enabled——防 F141 lane 把已启用链路故障
   当「未启用可忽略」漏掉 F130 回归。
3. **token 值只从实例 .env 读（Codex spec P2-2）**：`read_instance_effective_env`
   仅用于 mode/变量名解析；防自定义变量 shell-only 值让探针假通过（服务重启后
   不继承 → 实际 503）。
4. **gap-1 用 permission_preset=full 是最强断言不是绕过**：F136 gate 在 handler
   内部、REVIEW_REQUIRED 无条件触发——最宽 policy preset 下审批仍拦住 =
   「服务端审批绑定不可被 policy 放宽绕过」。
5. **三态报告协议**：`pass/not_enabled/fail`（exit 0/0/1）+ `--json`；
   `not_enabled` 是能力未启用非失败（探针不越权替 lane 定策略）。

## 4. 实施中发现（对后续 Feature 有用）

1. **misc_tools.py:218 裸调 `get_current_execution_context()`**（未绑即 raise）：
   `behavior.write_file` 依赖生产路径 `task_service.py:745` 的
   `bind_execution_context` 包裹——**任何绕过 task_service 直驱 llm_service 的
   测试/调用面都必须自补绑定**（user_profile_tools 是 try/except 软化，两 handler
   风格不一致；未立 fix——生产唯一路径恒绑定，不构成缺陷，记录供 F138 Phase 2
   / F140 参考）。
2. **httpx MockTransport 陷阱**：`Response(content=bytes)` 是预载响应，消费端
   `iter_raw()` 抛 `StreamConsumed`；模拟 SSE 必须 `content=iter([...])`（迭代器
   才是流式语义）。
3. **`octo remote status` 不验活**（只打印预期 URL）——attest remote 正是补这半边，
   两命令职责边界已写进 remote-access.md §5b。

## 5. 改动文件清单 + 回归

| 文件 | 类型 | 说明 |
|------|------|------|
| `packages/provider/src/octoagent/provider/dx/attest_commands.py` | 新 +793 | 探针主体（纯逻辑 DI + Click 呈现分离）|
| `packages/provider/src/octoagent/provider/dx/cli.py` | 改 +2 | 挂 attest_group（master 既有 I001 保留 0 新增）|
| `packages/provider/tests/dx/test_attest_commands.py` | 新 +680 | 32 hermetic 单测 |
| `apps/gateway/tests/test_frontdoor_auth.py` | 改 +201 | 矩阵 17 格 + 契约钉住（既有 8 格零修改）|
| `apps/gateway/tests/e2e_live/test_e2e_scripted_write_approval.py` | 新 +337 | gap-1 scripted 双路径 |
| `docs/codebase-architecture/attestation-checklist.md` | 新 | 机器可读残余清单（2 项）|
| `docs/codebase-architecture/{e2e-testing,remote-access,service-and-logging}.md` | 改 | living-docs（探针节/矩阵引用）|
| `docs/blueprint/milestones.md` | 改 | F144 行 ✅ |
| `.specify/features/144-attestation-absorb/*` | 新 | spec/plan/completion-report/handoff-to-F141 |

**生产代码净变化**：attest_commands.py 新增 + cli.py 2 行——**零改动既有生产逻辑**
（frontdoor_auth / write_approval / service_manager / tailscale_helper /
remote_commands 全部只读复用）。**红线核对**：未触碰 frontend/**、
`.github/workflows/`、pyproject、F142 标的 4 个 CI-skip 测试文件；探针零 sudo、
不改配置、不代跑 enable。

**回归**（PYTHONPATH 锁本 worktree + `uv run --no-sync python -m pytest`）：
- baseline（实施前实测）：`-m "not e2e_live"` **4872 passed / 11 skipped / 106 deselected / 1 xfailed / 1 xpassed**（173s）
- 终门（实施后）：**4922 passed / 11 skipped / 108 deselected / 1 xfailed / 1 xpassed（0 failed）** = baseline + 50 新增（矩阵 18 + 探针 32），**0 regression**（deselected +2 = 本 Feature 两个 e2e_live 标记的 gap-1 case，单独跑全绿）
- e2e_smoke：8/8（每 commit pre-commit hook 实跑全绿）
- scripted 域组合验证：F138 keystone + 新 gap-1 2 case + F136 单测 = 17 passed
- ruff：新增/触碰文件全 clean（cli.py 保留 master 既有 1 I001，0 新增）

## 6. 双评审闭环

- **Codex spec 评审**（`codex review --base origin/master`，spec/plan commit 后）：
  **0 HIGH + 2 P2，全接受闭环**（P2-1 enabled 信号错置 / P2-2 token 读取面），
  修正进 spec §D-4 + 实现 + 各自回归测试
  （`test_fail_when_bearer_but_tailscale_not_ready` / `TestDefaultTokenReader`）。
- **Codex final 评审**（全量 diff）：见 `codex-review-final.md`——0 HIGH，
  1 P2 接受闭环（SSE 真握手 `client.stream` 异常可能回显含 token URL →
  `_scrub` 兜底 + sentinel 用例加压）+ 1 P3 采纳（`--json` fail 时 hint 保留）。
- **Opus 对抗自审**（评审挑战面逐项）：
  - 「矩阵是否真补了 bearer×XFF 缺格」——A2 5 格直接命中（bearer+valid token+
    各转发头→200），且用生产 `_PROXY_HINT_HEADERS` 常量参数化 + 集合契约钉住；
  - 「探针 token 是否零泄漏」——值只入 header/query；report 只含布尔；异常路径
    只回显异常类名 + `_scrub` 兜底；sentinel 扫描 3 场景参数化机械断言；
  - 「gap-1 是否真走了 F136 审批没绕过」——批准前断言不落盘 + REST 真路由
    双 resolve + 记录终态 + `permission_preset=full` 反向加压；
  - 「探针 fake 是否掩盖真命令语义」——fake 只替执行层（CommandRunner 契约与
    F130 同款 / httpx MockTransport / kill 记录 / 虚拟时钟），断言面全是真参数
    （SIGKILL 常量、URL 路径、pid 值）；SIGKILL 语义由 F129/F130 文档证据链
    支撑非 fake 推断；SSE mock 修正为迭代器流式语义（发现 2）。
  - **0 HIGH 残留**。

## 7. 已知 limitations

- **attest remote 的 SSE 覆盖两档**：空实例只验认证判别（404），真流式握手
  依赖至少一条历史任务（detail 注明「streaming 未实测」）——lane 归档可区分。
- **恢复预算 90s 固定**：慢机器可能贴边；`run_service_probe(recovery_budget_s=…)`
  有参数缝，CLI flag 留给 F141 按需加（S 级）。
- **探针不进 CI 是设计而非欠账**：真副作用 + 依赖真实实例；探针逻辑回归由
  31 hermetic 单测在 CI 守。
- **两 handler ctx 获取风格不一致**（misc_tools 裸调 vs user_profile try/except）：
  非本 Feature 缺陷（生产路径恒绑定），记录见 §4.1。

## 8. 真机探针执行指引（主 session 替用户跑）

```bash
# 1) 服务崩溃自愈（会秒级闪断，先看 dry-run）
octo attest service --dry-run
octo attest service            # SIGKILL → 等 launchd 拉起 → 新 pid 判定

# 2) 远程链路（只读零副作用；未启用远程时输出指引不失败）
octo attest remote

# 机器可读（F141 lane 形态预演）
octo attest service --json ; octo attest remote --json
```

预期：两探针 `pass`（或 remote `not_enabled` 若尚未 `octo remote enable`）。
任一 `fail` 按 checks[].hint 排查（`octo service status` / `octo logs` /
`tailscale status`）。

## 9. 合入建议

**建议合入 origin/master**：①三条手工验收全部按验证吸收原则落位（2 吸收 +
2 物理残余落清单）；②0 regression + e2e_smoke 8/8 + 双评审 0 HIGH；③生产面
只增不改（唯一生产改动 = 新命令组 + CLI 挂载 2 行）；④与 F140/F142 红线零交集
（无 frontend/pyproject/workflows/库钉住文件触碰），合并时预期唯一交叠 =
milestones.md 文档行。合入后建议主 session 真机跑 §8 探针一轮（即完成 F129/F130
AC-1 的首次自动化验收 + 回填清单 ATT-129-BOOT 需真 reboot 时再签）。
