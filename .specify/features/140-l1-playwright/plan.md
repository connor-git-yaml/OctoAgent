# F140 实施计划

> spec 已收窄零岔路（spike 全验证）。Phase 划分按依赖序，小步 commit。

## Phase A：create_app DI 缝 + L1 启动器

1. `main.py`：`create_app(*, harness_factory=None)`——None 路径 byte-for-byte（现有
   模块级 `lifespan` 原样）；非 None 走参数化 lifespan（bootstrap/commit/shutdown 同
   三步）。生产 `app = create_app()` 零改动
2. `apps/gateway/tests/e2e_live/l1_support/`：
   - `scenario_brain.py`：`L1ScenarioModelClient`（prompt-marker 路由；`L1-WRITE`
     场景 + 默认回复；feedback 空/非空判轮次）
   - `serve_l1_gateway.py`：env 卫生 → 实例骨架+模板 → harness 组装（空凭证 store /
     model_client / gate deny 自证 / bomb）→ `create_app(harness_factory=...)` →
     uvicorn（`L1_PORT` / `L1_ROOT` / `L1_MODE` env 驱动）
3. 单测：l1_support 不进任何 pytest marker 收集面（非 test_* 自然豁免）；create_app
   缝加一条单测（factory 传入时 lifespan 用它——ASGITransport 冒烟即可，放
   `apps/gateway/tests/test_main_app.py` 既有文件或新建）

Commit：`feat(l1): create_app harness_factory DI 缝 + L1 gateway 启动器`

## Phase B：data-testid 锚点 + 契约测试

1. `ChatWorkbench.tsx` 两处表单：textarea `data-testid="chat-input"`、提交按钮
   `chat-send`；`MessageBubble.tsx` agent 侧根 div `chat-message-assistant`
2. `FrontDoorGate.tsx`：token input `frontdoor-token-input`、提交 `frontdoor-submit`
3. `frontend/e2e/selectors.ts`：`L1_TESTIDS` 单一事实源
4. `frontend/src/testing/l1SelectorsContract.test.ts`：vitest 遍历清单 grep src 源码
   ≥1 命中（node fs 扫 .tsx，机械校验）
5. 现有 vitest 全绿确认（testid 是纯 additive 属性，不动断言）

Commit：`feat(frontend): L1 data-testid 锚点 + selectors 契约测试`

## Phase C：Playwright 基建 + 两场景

1. `npm i -D @playwright/test`（lockfile 更新）
2. `playwright.config.ts`：webServer×2（loopback 8151 / bearer 8152，`uv run
   --project --no-sync python` + PYTHONPATH 锁 env）、workers=1、trace/screenshot
   on-failure、testDir `e2e/`
3. `e2e/support.ts`：外部断言 helper（fetchTaskEvents / readInstanceFile /
   scanFailureMarkers——已知失败文案清单）
4. `e2e/chat-scripted-loop.spec.ts`（AC-1）+ `e2e/front-door-token.spec.ts`（AC-2，
   含 storage 模式断言 + 解锁后发消息）
5. `.gitignore`：`e2e/.l1-runtime/` + playwright-report/ + test-results/

Commit：`feat(l1): Playwright 薄输入两场景（chat 决策环 + FrontDoorGate）`

## Phase D：CI job

`.github/workflows/feature-007-integration.yml` 追加 `l1-playwright` job（不碰既有
两 job 区块）：uv sync → npm ci → build → playwright install chromium（缓存）→ test
→ 失败 artifact。

Commit：`ci(l1): 新增 l1-playwright job`

## Phase E：终门 + 文档

1. L1 本地 3 连绿（AC-6）
2. 后端全量回归（PYTHONPATH 锁 + `uv run --project octoagent --no-sync python -m
   pytest`）vs baseline 0 regression + e2e_smoke 8/8
3. 前端 vitest 全绿（不劣化）
4. living-docs：`docs/codebase-architecture/e2e-testing.md` 加 L1 节；
   `docs/blueprint/milestones.md` F140 标 ✅
5. completion-report.md

Commit：`docs(l1): e2e-testing L1 节 + milestones F140 ✅ + completion-report`

## 双评审

- Codex final：`codex review --base origin/master`（挑战点：断言是否真在 UI 外 /
  等待策略是否 sleep 堆 / testid 契约是否机器可校验 / CI job 正确性）
- Opus 对抗自审：薄输入原则 / 与 F138 keystone 重复建设检查 / 宪法逐条

## 基线

- baseline：origin/master 6972ddc7（F137+F138+vitest 清零已合入）
- 后端 baseline 数：终门跑一次 master 侧记录对照（预期 ~4975+）
