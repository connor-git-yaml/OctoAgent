# F140 完成报告

> 状态：实施完成，双评审闭环，**未 push origin**（等用户拍板）。
> worktree：`.claude/worktrees/F140-l1-playwright`（branch `feature/140-l1-playwright`，
> base = origin/master 6972ddc7）。

## 1. 计划 vs 实际

| Phase | 计划 | 实际 | 偏离 |
|-------|------|------|------|
| spike | 任务要求「先复核再定」 | 6 项全实测（S1-S6）+ 2 项否证（审批触发器）| 无——spike 结论直接写死 spec §0 |
| A | create_app DI 缝 + 启动器 | 完成 | bomb 安装点从 on_event 改 commit_to_app override（显式 lifespan 下 Starlette 不执行 on_event——实测抓出）|
| B | testid + 契约测试 | 完成 | 契约测试从 `src/testing/` 移 `frontend/testing/`（node API 与 tsc include=src 冲突，spec AC-4 绑定路径同步修正）|
| C | Playwright 两场景 | 完成 + **修 1 真 production bug** | ①删自加的「非 marker」第三测试（会话连续性污染，spec 本就只定 2 场景）；②api/client.ts Authorization 覆盖 bug 修复（见 §3）|
| D | CI job | 完成 | 无 |
| E | 终门 + 文档 | 完成 | 无 |

## 2. 交付物清单

**生产代码（2 文件，最小触碰）**
- `octoagent/apps/gateway/src/octoagent/gateway/main.py`：`create_app(harness_factory=None)`
  可选 DI 缝 + `_make_harness_lifespan`（None 缺省 byte-for-byte；Constitution #9
  构造性不可达，F087/F138 同范式）
- `octoagent/frontend/src/api/client.ts`：**真 bug 修复**——`apiRequest` 的
  `{ headers, ...init }` 展开序错误（详见 §3）

**前端锚点（3 文件，纯 additive 属性）**
- `ChatWorkbench.tsx`（chat-input/chat-send ×2 表单）、`MessageBubble.tsx`
  （chat-message-{assistant,user}）、`FrontDoorGate.tsx`（frontdoor-{token-input,submit,persist-checkbox}）

**L1 基建（新增）**
- `octoagent/apps/gateway/tests/e2e_live/l1_support/`：`serve_l1_gateway.py`
  （hermetic 启动器，L1_MODE=loopback|bearer）+ `scenario_brain.py`（prompt-marker
  脚本脑）+ `__init__.py`
- `octoagent/frontend/playwright.config.ts`（webServer×2 + PYTHONPATH 锁 + workers=1）
- `octoagent/frontend/e2e/`：`selectors.ts`（L1_TESTIDS 单一事实源）+ `support.ts`
  （外部断言三通道）+ `chat-scripted-loop.spec.ts` + `front-door-token.spec.ts`
- `octoagent/frontend/testing/l1SelectorsContract.test.ts`（vitest 契约，7 锚点 + 唯一性）

**测试**
- `apps/gateway/tests/test_main.py` +2（DI 缝三步顺序 / 缺省路径不构造参数化 lifespan）
- `frontend/src/api/client.test.ts` +1（caller-headers Authorization 回归）

**CI/配置**
- `.github/workflows/feature-007-integration.yml`：新增 `l1-playwright` job
  （既有两 job 区块 diff 零删除行）；`frontend/package.json` +`test:e2e` +
  `@playwright/test` devDep；`vite.config.ts` test.exclude 排 e2e/；根 `.gitignore`
  +`.l1-runtime/`+`playwright-report/`

**文档**
- `docs/codebase-architecture/e2e-testing.md` §9b L1 节
- `docs/blueprint/milestones.md` F140 行 ✅ + 波次注记
- `.specify/features/140-l1-playwright/`：spec.md / plan.md / 本报告

## 3. 场景②首跑抓出的真 production bug（用户视角）

`octoagent/frontend/src/api/client.ts` 的 `apiRequest`：

```ts
return fetch(url, { headers, ...init });   // 原（bug）
return fetch(url, { ...init, headers });   // 修
```

调用方自带 `init.headers`（useChatStream 的 `/api/chat/send`、useApprovals 的
`/api/approve/*` 都传了 `Content-Type`）时，后展开的 `init.headers` 把已注入
`Authorization` 的合并 headers 整体覆盖——**bearer 模式下手机/远程聊天发送必
401**。这正是：
- F130 完成后一直留给用户的真机验证路径（手机 Tailscale → bearer token → 聊天）——
  用户真上手机时第一条消息就会撞上；
- 审计判定「FrontDoorGate 流程 UI-only 零测试」的确切后果——现有 client 单测只
  覆盖不带 caller-headers 的调用（`fetchControlSnapshot`），恰好绕过 bug 分支。

L1 场景②第一次真浏览器跑就抓到它——F140「减手工测」的价值即时兑现。
修复 + `client.test.ts` 精确回归用例（caller-headers 时 Authorization 保留 +
Content-Type/method/body 不丢）。

## 4. 终门数据（AC 对照）

| AC | 结果 |
|----|------|
| AC-1 场景① | PASS（TOOL_CALL_STARTED/COMPLETED=1 对 filesystem.write_text + MODEL_CALL_* 2 对 + 写盘逐字节全等）|
| AC-2 场景② | PASS（gate→解锁→SSE query 鉴权全链路 + session/persistent 两种 storage 模式）|
| AC-3 零真 LLM | PASS（gate=deny env + 空凭证 + resolve bomb；回复文本==脚本常量）|
| AC-4 契约机器可校验 | PASS（vitest 8 用例：7 锚点字面存在 + 值唯一）|
| AC-5 生产零变更 | PASS**（有条件——须在无 `frontend/dist` 下验证，醒目见 §6.6）**：缺省路径单测锚定不构造参数化 lifespan + 后端全量 4977 passed / 0 failed |
| AC-6 三连绿 | PASS（3×`3 passed`，9.4-9.5s 稳定，零重试）|
| AC-7 CI job | PASS（YAML 解析 jobs=[backend-deterministic, frontend, l1-playwright]；既有 job diff 零删除行）|

**回归终数**（相对 master 6972ddc7 baseline=4975 passed 联合验收）：
- 后端全量（PYTHONPATH 锁 + `uv run --no-sync python -m pytest`）：见 §7 回填
- e2e_smoke：pre-commit hook 每 commit 已跑（5 commits 全过）+ 终门显式跑见 §7
- 前端 vitest：29 文件 204 passed（195 存量 + 8 契约 + 1 client 回归；0 失败）
- L1：3 场景 ×3 连跑全绿

## 5. CI 首跑预期（给主 session）

- `l1-playwright` job 首跑预算一轮 triage（F137 先例）。已知候选风险：
  ①chromium `--with-deps` 在 ubuntu 装系统依赖耗时（缓存 miss 首跑 +2-3min）；
  ②GitHub 2-core 下 gateway bootstrap 变慢——webServer timeout 已给 120s；
  ③`uv sync --dev` 与 backend job 并行重复（缓存共享，可接受）。
- push 该 workflow 需 SSH（OAuth 无 workflow scope——运维备忘）。

## 6. Limitations / deferred（诚实清单）

1. **审批点击场景 deferred**（spike 否证）：chat 主路径无确定性审批触发器——
   ①F136 `behavior.write_file confirmed=true` 在 chat inline 抛 "execution
   runtime context is not available"（F136 绑 execution session，chat inline 不绑）；
   ②IRREVERSIBLE `cron.delete` 经 broker `check_permission` 实测直接放行（无
   APPROVAL 事件）。→ 前置条件写进 spec §6；audit「真正 UI-only」3 项中
   useApprovals 未覆盖（且其本身疑似死代码，归 F143 处置）
2. **会话连续性约束**：每 L1 server 每 run 只承载一条发消息对话链（服务端会话
   恢复使 marker 跨测试泄漏进决策环 prompt）。新增发消息测试走「+ 新建对话」
   UI 流（NewSessionModal，v0.2）或独立 server
3. **必留浏览器项未全收**：DiffBody jsdiff（需 artifact 版本铺垫）/ useAutoScroll /
   DOMPurify 真 DOM 渲染 / build-id 跳转 / EventSource 自动重连（掉线重连语义，
   本次只覆盖了真 EventSource 消费+query 鉴权）——v0.2 候选
4. **F136 chat inline execution context 缺口**：spike 副产物发现（`behavior.write_file`
   在主 Agent chat 路径不可用），值得独立小 Feature 评估修复
5. 契约测试的 `dynamicNeedle` 宽匹配（三元形态 `"testid"` 字面）理论上可被注释
   里的字符串糊弄——可接受（testid 值高度特异）
6. **终门撞出 master 存量潜伏 production bug（已派 chip，非 F140 引入）**：
   `frontend/dist` 存在时 `tests/integration/test_f023_m2_acceptance.py` 两测试
   确定性 FAIL（405）——SPA `Mount("/")` 在 create_app 构造期注册，**遮蔽一切
   lifespan 期 harness 挂载的路由**（F105 v0.2 把 telegram inbound router 挪进
   了 bootstrap）→ 只要 build 过 Web UI，生产 webhook 模式就是坏的。三重实证:
   ①本 worktree dist 在→2 failed、dist 删→5 passed；②origin/master + dist →
   同样 2 failed（100% 预先存在）；③CI backend job 不 build dist 不受影响。
   影响 F140 的现实面：跑 L1 会产 dist → 之后本地全量回归会带上这 2 个失败——
   已在 e2e-testing.md §9b 写操作指引（先 rm dist 或接受），根治归 spawn chip

## 7. 终数回填（全部实测）

- 后端全量（PYTHONPATH 锁 + `uv run --no-sync python -m pytest -q -p
  no:cacheprovider`，**无 dist 状态**）：**4977 passed / 0 failed / 14 skipped /
  1 xfailed / 1 xpassed**（6m05s）。对账：master 联合验收 baseline 4975 + F140
  新增 2（test_main.py）= 4977，**0 regression**。
  - 带 dist 状态首跑：4975 passed + **2 failed（f023，master 存量 SPA 遮蔽
    bug，非 F140——见 §6.6 三重实证，origin/master + dist 同样复现）**
- e2e_smoke：**8 passed**（1.7s，宿主凭证在，真跑非 SKIP）
- 前端 vitest：**29 文件 204 passed / 0 failed**（195 存量 + 8 契约 + 1 client 回归）
- L1 Playwright：**3 passed ×3 连跑**（9.4-9.5s，零重试；Opus MED 修复后另有
  收尾复跑，见 §8）

## 8. 双评审闭环

- **Codex spec 评审**（commit c113b114 后）：0 finding（docs-only diff）。
- **Opus 对抗自审**（9 维度）：F140 自身 **0 HIGH**；1 个 HIGH 级**存量**
  production bug 被 F140 暴露（SPA mount 遮蔽 webhook 路由——非 F140 引入，
  origin/master 复现实证；已派独立修复 chip `task_47e269bf`，Opus 要求的三项
  落实：①chip 已存在 ②归总报告显著告知 webhook 模式生产现状 ③AC-5 条件已
  醒目化）。**2 MED 全修**：MED-1 launcher 中和 `OCTOAGENT_HOST`（宿主 export
  0.0.0.0 会误 exit78 全挂）/ MED-2 凭证通配 sweep 对齐 conftest（`*_API_KEY`/
  `*_TOKEN` 兜底，L1_FD_TOKEN 白名单）。**LOW 处置**：rmtree 前缀守卫已加 /
  selectors.ts 注释路径漂移已修 / dead export（fetchLatestTaskId、
  instanceFileExists）已删 / chatMessageUser 保留带理由注释（清单↔源码双向
  完整）/ 「契约校验字符串存在非属性存在」与「会话连续性仅注释强制」两条
  归档接受（testid 值高度特异 / v0.1 已显式归档）。
- **Codex final 评审**：见 §9 回填。

## 9. Codex final 评审回填

（评审后回填）
