# F140 L1 UI E2E：Playwright 薄输入 + 外部断言（v0.1 收窄版）

> M9 波② P1。用户拍板③：L1 走 **Playwright 薄输入 + 外部断言**——断言走 event_store /
> 文件系统 diff，不做脆弱 DOM 断言。仿 cc-haha desktop-smoke（UI 仅输入通道 + 文件系统
> 验证 + 失败 marker 定性扫描）。**依赖 F138**（脚本化 model_client 使 UI E2E 全确定性，
> 零真 LLM 可进 CI）。
>
> 本 spec 为**收窄版**：架构方向已经 spike 实测复核（见 §0），不再有开放岔路。

## 0. Spike 复核结论（2026-07-12，全部实测通过，写死架构）

按任务要求「先复核再定」，以下 6 项在真 gateway + 真浏览器上逐项验证：

| # | 复核项 | 结论 |
|---|--------|------|
| S1 | gateway serve SPA 方式 | `main.create_app()` 尾部 mount `SpaStaticFiles(frontend/dist, html=True)` 到 `/`（main.py:499-504），带 BrowserRouter index fallback。**最简形态成立：build 一次 frontend dist → gateway 单进程 serve → Playwright 直打 gateway 端口**，不起独立 vite server |
| S2 | OctoHarness DI 起真 HTTP | `create_app()` 的 lifespan 硬连无 DI 的 `OctoHarness(project_root=env)`（main.py:425）——**需要一条显式 DI 缝**（见 D1）。spike 用等价 lifespan 替换实测：空凭证 + `model_client=脚本脑` 全 11 段 bootstrap + uvicorn 起服务 OK |
| S3 | UI 全链路 | 真浏览器输入「请把笔记写进文件 L1-WRITE」→ POST /api/chat/send → task_runner → orchestrator 自动写 `selected_tools_json`（profile_first_core 稳定 15 工具集，含 `filesystem.write_text`）→ SkillRunner(脚本脑) 2 轮决策 → `filesystem.write_text` 真执行 → 文件真落盘 `projects/default/l1_e2e/note.md` → SSE 流式回复「文件已写好」渲染进消息气泡。**逐环节验证通过** |
| S4 | 外部断言通道 | `GET /api/tasks/{task_id}` 返回完整事件链（TOOL_CALL_STARTED/COMPLETED 含 tool_name、MODEL_CALL_*、STATE_TRANSITION 等 31 事件实测可读）；文件系统直读实例 root。两通道都在 UI 外 |
| S5 | F137 gate 兼容 | `OCTOAGENT_ALLOW_MODEL_REQUESTS=0`（deny）下同一场景全链路照常通过——脚本路径 B 不触发 gate，路径 A 零触发。**L1 服务器常态跑 deny**，构造性零真 LLM |
| S6 | bearer 模式 | env `OCTOAGENT_FRONTDOOR_MODE=bearer` + `OCTOAGENT_FRONTDOOR_TOKEN_ENV` 切换后：API 无 token 401 `FRONT_DOOR_TOKEN_REQUIRED`、带 token 200、SPA 静态资源不受 guard（FrontDoorGate 由 SPA 收到 401 后渲染）。场景②成立 |

**Spike 否证记录（写死边界）**：审批流场景在 chat 主路径**没有确定性触发器**——
① `behavior.write_file confirmed=true`（F136 服务端审批）在 chat inline SkillRunner 路径抛
"execution runtime context is not available"（F136 绑定 execution session，chat inline 不绑）；
② `cron.delete`（IRREVERSIBLE）经 broker `check_permission` 实测**直接放行**（默认 policy
对 owner chat 不拦），无 APPROVAL 事件产生。→ **审批点击场景（原候选③）降级 deferred**，
见 §6。

## 1. 目标与范围

**一句话**：给 OctoAgent 建立第一条真浏览器 L1 UI E2E 通道——Playwright 驱动真 Chromium
打真 gateway（真 bootstrap + 脚本决策环 + 真工具执行），UI 只做输入通道，断言全部在 UI
外（REST 事件链 + 文件系统），全程零真 LLM 调用、零宿主 OAuth，可进 CI。

**直接服务的用户主诉求**：减手工测 UI。审计实证 L1 层绝对零（无 playwright/cypress，
前端 28 测试全 vi.mock 屏蔽 API），真 EventSource 语义 / storage token 持久化 /
FrontDoorGate 流程只能手工验。

### 范围内（v0.1）

- FR-1 **create_app DI 缝**：`create_app(harness_factory=...)` 可选参数（默认 None =
  现有 lifespan byte-for-byte；生产 `app = create_app()` 不传 → Constitution #9 构造性不可达）
- FR-2 **L1 gateway 启动器**：Python 脚本（Playwright webServer 拉起），组装
  hermetic 实例 root + 空凭证 + prompt-marker 脚本脑 + F137 gate=deny + resolve bomb
- FR-3 **场景①聊天决策环**（P0）：UI 输入 → 脚本决策环 → `filesystem.write_text` 真执行
  → 回复气泡稳定信号 → 外部断言（文件内容逐字节 + 事件链 TOOL_CALL_*/MODEL_CALL_*）
- FR-4 **场景② FrontDoorGate token 流程**（P0）：bearer 模式 server → UI 渲染 gate →
  输 token 保存重试 → 聊天界面解锁 → **再发一条消息走场景①同款断言**（顺带覆盖
  bearer 下 SSE `access_token` query 鉴权真浏览器路径）→ 外部断言 storage 持久化模式
- FR-5 **data-testid 选择器契约**：场景涉及控件补最小 data-testid 锚点 + 锚点清单
  单一事实源 + 机器可校验契约测试（vitest，防选择器腐烂——审计实证全库仅 2-3 处 testid）
- FR-6 **CI 独立 playwright job**：现有 workflow 新增 job（不动 backend/frontend 既有
  job），build frontend + 装 chromium（缓存）+ 跑 L1，失败上传 trace/screenshot artifact
- FR-7 **失败 marker 定性扫描**（cc-haha 技巧）：等待回复超时前扫 UI 已知失败文案
  （「刚才没有发送成功」等），把裸超时降级成可定性失败
- FR-8 **living-docs**：e2e-testing.md 加 L1 节；milestones.md F140 行标 ✅

### 范围外（显式排除）

- 审批按钮点击场景（spike 否证确定性触发器，见 §6 deferred）
- DiffBody jsdiff 场景（需 artifact 版本数据铺垫，v0.2）
- Playwright 并行 workers（v0.1 串行 workers=1，确定性优先）
- iOS/WebKit/Firefox 多浏览器（只 chromium）
- 前端 vitest 存量 / ChatWorkbench 拆分（F143 地盘）
- packages/*/tests 护栏、pyproject markers、backend CI job（F142/F141 地盘）

## 2. 架构决策（全部写死，无开放岔路）

### D1 create_app DI 缝（唯一生产代码触碰）

```python
def create_app(*, harness_factory: Callable[[], OctoHarness] | None = None) -> FastAPI
```

- `None`（缺省）→ 现有模块级 `lifespan` 原样使用，生产行为 byte-for-byte 等价
- 非 None → lifespan 改为调 factory 拿 harness（bootstrap/commit/shutdown 三步不变）
- 先例：F087 4 DI 钩子 / F138 model_client+clock——同一「默认 None 生产不可达」范式
- **不做** env 通道注入 model_client（脚本脑是对象，env 表达不了；也不做模块级全局钩子）

### D2 启动器（launcher）

- 位置：`octoagent/apps/gateway/tests/e2e_live/l1_support/serve_l1_gateway.py`
  （非 test_* 文件不被 pytest 收集；e2e_live 目录本就被 CI backend job --ignore）
- 职责：①环境卫生（清凭证 env + OCTOAGENT_* 路径 env 全指进实例 root，仿 e2e conftest
  清单）②实例骨架 + local-instance 模板复制（复用 fixtures）③组装
  `OctoHarness(project_root, credential_store=空 tmp store, mcp_servers_dir, data_dir,
  model_client=L1ScenarioModelClient())` ④bootstrap 后装 `resolve_for_alias` bomb
  （keystone 三重防御同款）⑤uvicorn 127.0.0.1:env 端口
- 模式由 env 区分：`L1_MODE=loopback|bearer`（bearer 时额外设 FRONTDOOR env 三件）
- 零真调用三重防御 = F137 gate deny（env）+ 空凭证 store + resolve_for_alias bomb

### D3 脚本脑：prompt-marker 路由（memU FakeChatClient 范式 + pydantic-ai FunctionModel 等价）

- `L1ScenarioModelClient` 实现 `StructuredModelClientProtocol.generate`（与 F138
  `ScriptedModelClient` 同协议；不复用队列版——**长驻 server 跨多测试消费，队列会被
  后台 LLM 调用desync，prompt-marker 按输入路由才稳**；两件并存不冲突，F138 Echo
  并存先例）
- 路由键 = 用户消息里的 marker token（如 `L1-WRITE`）；轮次判定用 `feedback` 空/非空
  （spike 实测决策环第 2 轮带 tool feedback）
- 位置：launcher 同目录 `l1_support/scenario_brain.py`（test-support，不进 skills 包
  ——它是 F140 场景专用编排，非通用件；若 v0.2 第三方要用再上提）

### D4 Playwright 侧

- 位置：`octoagent/frontend/e2e/`（specs + selectors.ts），`playwright.config.ts` 在
  frontend 根；`@playwright/test` 进 devDependencies
- webServer 数组两条：loopback server（场景①）+ bearer server（场景②），各自独立
  实例 root + 端口（默认 8151/8152，env 可覆盖）；`reuseExistingServer: !CI`
- 启动命令统一走 `uv run --project <octoagent> --no-sync python <launcher>` + 显式
  PYTHONPATH 锁（TS config 内计算绝对路径——worktree 防 venv 漂移假绿；CI 下同树无害）
- workers=1 串行；trace `retain-on-failure` + screenshot `only-on-failure`
- 实例 root：`frontend/e2e/.l1-runtime/{loopback,bearer}/`（gitignore；launcher 启动时
  wipe 重建，保证每 run 干净）

### D5 断言纪律（薄输入原则的机械化）

UI 内**只允许**两类操作：
1. 输入：fill textarea / click 发送 / fill token / click 保存
2. 等待稳定信号：`getByTestId` 等待回复气泡出现 / gate 消失（Playwright 自带
   auto-wait，**禁止裸 sleep 堆时序**）

断言**全部在 UI 外**（node 上下文）：
- 文件系统：实例 root 下工具写入的文件逐字节比对（sha256/字符串全等，复用
  state_diff 语义）
- REST：`GET /api/tasks/{task_id}` 事件链断言（TOOL_CALL_STARTED.tool_name /
  TOOL_CALL_COMPLETED / MODEL_CALL_* 计数）——task_id 从 `GET /api/tasks` 列表取
  （每 run 实例干净，最新 task 即本场景）
- storage：`page.evaluate` 读 sessionStorage/localStorage 仅取值，断言在 node 侧
  （token 持久化模式验证——这是「读状态」不是「断 DOM 结构」，且正是审计判定的
  必须留浏览器项）

允许的例外（显式声明）：等待信号本身用 testid 定位回复气泡属 UI 依赖，但只依赖
**锚点存在性**（受 FR-5 契约测试保护），不断言样式/结构/文案全文。

### D6 data-testid 契约

- 锚点最小集（只加场景真用到的）：`chat-input` / `chat-send`（两处表单同名，同时
  只渲染一个）/ `chat-message-assistant`（MessageBubble agent 侧）/
  `frontdoor-token-input` / `frontdoor-submit`
- 单一事实源：`frontend/e2e/selectors.ts` 导出 `L1_TESTIDS` 清单；specs 只经它引用
- 契约测试：vitest 一条（`frontend/testing/l1SelectorsContract.test.ts`——放
  src 外因其用 node API 而 `tsc -b` include=["src"] 无 node types，vitest
  esbuild 转译不受影响；实施期修正）遍历清单，机械校验
  每个 testid 在 src/**.tsx 源码中字面出现 ≥1 次；防「组件重构删锚点，Playwright
  在 CI 才炸」

### D7 CI job（只加区块，不碰既有 job）

- workflow 仍是 `.github/workflows/feature-007-integration.yml`（F137 已改写为 ci，
  job：backend-deterministic + frontend）；新增第三个 job `l1-playwright`
- 步骤：checkout → setup-python/uv → `uv sync --dev`（launcher 依赖）→ setup-node +
  npm ci → `npm run build`（产 dist 给 gateway serve）→ `npx playwright install
  --with-deps chromium`（`~/.cache/ms-playwright` 以 package-lock hash 缓存）→
  `npx playwright test` → 失败上传 `playwright-report/` + test-results artifact
- 目标 <5min；timeout-minutes 15 兜底
- push 该 workflow 需 SSH（OAuth 无 workflow scope——运维备忘已记，**本 Feature 不
  push，留给用户**）

## 3. 验收标准（AC ↔ test 显式绑定，SDD 强化）

| AC | 内容 | 绑定测试 |
|----|------|---------|
| AC-1 | 场景①：真浏览器输入触发脚本决策环 + 真工具执行；文件逐字节命中 + 事件链含 `TOOL_CALL_STARTED/COMPLETED(filesystem.write_text)` + `MODEL_CALL_*`≥2 对；断言全在 UI 外 | `frontend/e2e/chat-scripted-loop.spec.ts` |
| AC-2 | 场景②：bearer 模式 gate 渲染 → 输 token 解锁 → 发消息全链路成功（SSE query 鉴权路径）→ storage 持久化模式正确（默认 session / 勾选 persistent） | `frontend/e2e/front-door-token.spec.ts` |
| AC-3 | 零真 LLM：L1 服务器 gate=deny + 空凭证 + resolve bomb 下两场景全绿 | launcher env + spec 内断言（回复 content=脚本值） |
| AC-4 | testid 契约机器可校验：删除任一锚点 → vitest 契约测试红 | `frontend/testing/l1SelectorsContract.test.ts` |
| AC-5 | 生产零行为变更：`create_app()` 不传 factory 路径 byte-for-byte；后端全量 0 regression | 后端全量回归 + gateway app 冒烟（现有测试隐式覆盖 create_app） |
| AC-6 | 反 flaky 出生：本地 L1 套件连跑 3 次全绿 | 终门执行记录（completion-report） |
| AC-7 | CI job 语法正确 + 不改既有 job 行为 | workflow diff review（既有 job 区块零改动） |

## 4. 成功度量

- L1 从绝对零 → 2 条真浏览器场景进 CI（audit gap-high L1 首次有落点）
- 覆盖审计「真正 UI-only」3 项中的 1 项完整（FrontDoorGate）+ 必留浏览器清单中的
  真 EventSource 消费 + storage 持久化 2 项
- 手工测收窄：聊天冒烟 + 远程 token 首屏两条日常手工路径自动化

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 服务器 bootstrap ~10s，webServer 超时误杀 | webServer timeout 60s + launcher 就绪打印（uvicorn 起 = 端口可探） |
| profile_first_core 选择集漂移致 `filesystem.write_text` 掉出 | 事件链断言会显式红（TOOL_CALL_STARTED 缺失），failure marker 扫描给定性原因；选择集是 stable core 非 query 相关（spike 实测两条不同消息同集） |
| chromium 下载抖动 | CI 缓存 ms-playwright；失败重跑一次由 PW `retries: CI?1:0` 吸收（trace 保留可查） |
| 首跑 CI triage | 预算一轮（F137 先例）；completion-report 显式列预期项 |

## 6. Deferred（v0.2 候选，带前置条件）

1. **审批点击场景**：前置 = 给 chat 主路径找到/建一条确定性审批触发器（候选：
   ①F136 execution runtime context 在 chat inline 绑定的缺口修复；②policy profile
   对 IRREVERSIBLE 的 owner-chat 默认放行语义确认后换 preset）。spike 证据见 §0
2. **DiffBody jsdiff 场景**：需 files API 版本数据铺垫
3. **失败注入场景**（脚本脑 raise → UI 错误文案）：低成本高价值，v0.1 时间盒外
4. **scenario_brain 上提共享**：第三方消费出现时再议（F138 ScriptedModelClient 先例）
