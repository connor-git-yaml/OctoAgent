# F119 已合入功能 e2e_live 端到端补全 — Completion Report

> 模式：spec-driver story。baseline：master HEAD `02e139fd`。
> 制品：`spec.md` / `plan.md` / `tasks.md` / 本报告。
> 性质：**纯测试新增**（production 零改动），命中 CLAUDE.local.md "纯测试新增可跳过 Codex review"。

## 1. 做了什么 vs 计划

| 标的 | 计划 | 实际 | 实测 |
|------|------|------|------|
| F104 文件工作台 | 4 AC | `test_e2e_file_workbench.py` 4 test | 4 passed |
| F116 通知持久化 | 4 AC | `test_e2e_notification_persist.py` 4 test | 4 passed |
| F123 出站 SSRF | 4 AC | `test_e2e_ssrf_guard.py` 8 test（含 5 参数化） | 8 passed |
| F124 工具结果威胁扫描 + F123↔F124 链 | 4 AC | `test_e2e_tool_result_threat_scan.py` 4 test | 4 passed |
| **合计** | 16 AC | **20 test（4 文件）** | **20 passed** |

F099/F100：**显式不补**（见 §5 归档）。

## 2. AC↔test 绑定实测（SDD 强化：每条 AC 机械校验）

### F104（`test_e2e_file_workbench.py`）
- AC-104-1 版本内容可取回（非计数器）→ `test_file_workbench_versions_retrievable` ✅
  current.content/previous.content 各自等于写入的对应版本真实内容。
- AC-104-2 两级导航 → `test_file_workbench_two_level_navigation` ✅
  `/api/files/tasks` 含 task；`/logical-files` 只返回 version_count≥2（单版本隐藏）。
- AC-104-3 diff 无技术字段泄漏 → `test_file_workbench_diff_no_technical_field_leak` ✅
  diff 含 current+previous 内容；响应原文不含 artifact_id/storage_ref/hash。
- AC-104-4 并发不串版本 → `test_file_workbench_concurrent_writes_no_version_clash` ✅
  `asyncio.gather` 5 并发写 → 版本号 1..5 连续唯一。

### F116（`test_e2e_notification_persist.py`）
- AC-116-1 dismiss 跨重启 → `test_notification_dismiss_survives_restart` ✅
  service A dismiss durable=True → 新 service B rehydrate → is_dismissed True。
- AC-116-2 active 跨重启 → `test_notification_active_survives_restart` ✅
  落盘 active → 新实例 rehydrate → list_active 恢复该条。
- AC-116-3 已读不重现 → `test_notification_dismissed_filtered_after_restart` ✅
  active 后 dismiss → rehydrate → list_active 不含该条。
- AC-116-4 跨通道统一 → `test_notification_dismiss_cross_channel` ✅
  dismiss(source=web) → 共享表落盘 → 另一实例 rehydrate 看到同一 dismiss。

### F123（`test_e2e_ssrf_guard.py`）
- AC-123-1 内网拦截 → `test_ssrf_fetch_browser_page_blocks_internal[5 参数化]` ✅
  云元数据/loopback/IPv6 loopback/私网/CGNAT 各抛 UnsafeUrlError（发包前预检）。
- AC-123-2 broker 真路径拦 → `test_ssrf_web_fetch_via_broker_blocked` ✅
  web.fetch 真注册 + broker.execute 私网 → is_error=True + error 含"拒绝"。
- AC-123-3 DNS→私网拦 → `test_ssrf_hostname_resolving_to_private_blocked` ✅
  monkeypatch `_resolve_host` 返回私网 IP → ensure_url_safe 抛异常含该 IP。
- AC-123-4 302 逐跳 re-validate → `test_ssrf_redirect_hook_revalidates_each_hop` ✅
  `_ssrf_request_hook` 对内网 request 抛异常、对公网 request 放行。

### F124 + 链（`test_e2e_tool_result_threat_scan.py`）
- AC-124-1 标注不 block → `test_threat_scan_flags_but_does_not_block` ✅
  前置自检 broker._content_scanner 非 None；finding 非空 scope=CONTEXT；is_error=False 且 raw 不改。
- AC-124-2 emit 事件 → `test_threat_scan_emits_flagged_event` ✅
  events 含 1 条 TOOL_RESULT_THREAT_FLAGGED；payload 有 pattern_id + content_hashes，无原文。
- AC-124-3 真实文档 0 误报 → `test_threat_scan_false_positive_clean_on_real_docs` ✅
  k8s/安全/CDN 文档 → findings 为空 + 无 flagged 事件（F125 反"狼来了"）。
- AC-124-4 F123↔F124 链 → `test_ssrf_error_flows_through_threat_scan` ✅
  链A：stub raise UnsafeUrlError + spy → is_error=True + scanner 经 error 通道被调用；
  链B：error 文本含注入 → security_findings 非空 + source_field='error'。

## 3. 验证结果

- 4 新文件单跑：**20 passed**（实测）。
- `pytest -m e2e_smoke`：**8 passed**（pre-commit 关口未破坏，实测）。
- e2e_live 全套（4 新文件 + 现有 18 文件，排除 3 个真打 LLM 文件）：**85 passed / 3 skipped / 9.05s**
  （核心 regression 面——新增与现有 e2e_live 同跑零 FAIL、无状态泄漏污染；3 skipped 为 Perplexity manual gate）。
- 单元 + 集成全量（排除 e2e_live）：**3898 passed / 1 failed / 10 skipped / 1 xfailed / 1 xpassed**。
  唯一 failed = `tests/integration/test_sc3_projection.py::test_rebuild_preserves_task_state` —— **F083 已知 flaky**
  （aiosqlite event loop 关闭顺序 race，CLAUDE.md/memory 记录在案，套跑偶发、单跑必过；已单独重跑确认 PASS）。
  **与本批改动无关**（本批零生产改动，不触及 sc3_projection / event store / task store 路径）。
- **结论：0 真 regression vs `02e139fd`**。

> 环境注记：worktree `.venv` symlink 主仓；主仓 venv editable `.pth` 当前指向 `F105-gateway`
> worktree。裸 `python -m pytest` 全量收集子包 conftest 时 `import octoagent` 解析失败/解析到
> 错误 worktree（memory `project_worktree_venv_symlink` 已记录）。**修复**：`PYTHONPATH` 锁
> F119-e2e 的 9 个子包 src，确保测的是 worktree 自身 baseline + 新增，防假 0 regression。

## 4. 约束达成

- ✅ 零 production 改动（`git status` 仅 4 新测试文件 + `.specify/features/119-e2e-backfill/`）。
- ✅ 零 conftest/fixture 改动 → 纯测试新增，跳过 Codex review（CLAUDE.local.md 例外条款）。
- ✅ 守 H1：断言走主 Agent 可见路径（broker.execute / Files API / NotificationService 公共方法 /
  pack_service / url_safety 公共函数）。
- ✅ 复用 F087 范式（octo_harness_e2e + bootstrap + 直调主路径绕 LLM 不确定性）。
- ✅ 不进 13 域 DOMAIN_REGISTRY（回归补全测试，非新能力域；`e2e_full` marker 被 `-m e2e_full` 自动收集）。

## 5. 归档（显式不做 + 理由）

- **F099 ask_back / F100 force_full_recall e2e_live 不补**：
  1. 二者**已有专门测试**——F099 `test_phase_e_ask_back_e2e.py`（e2e 框架）+ `test_ask_back_tools.py`
     （15 单测）；F100 `test_runtime_control_f100.py` + `test_chat_force_full_recall.py`（链路集成）。
     不属于集成 review 标记的"有单测无 e2e_live"硬缺口（硬缺口是 F104/F116/F123/F124）。
  2. 干净 e2e_live 需大量 mock（ExecutionRuntimeContext patch / LLMService 消费 spy），
     偏离 e2e_live "真 bootstrap 主路径绕 LLM" 精神，增量价值≈重复单测。
  3. prompt 明确标二者为"(可选)"；控制本批范围。

## 6. living-docs 漂移闸

- `docs/codebase-architecture/e2e-testing.md`：本批 4 文件用 `e2e_full` marker，**不进 13 域
  DOMAIN_REGISTRY**（属回归补全，非能力域）。该文档 §2 "13 能力域清单" 仍准确，无需改；
  §6 "module 单例 reset 维护指南" 未触发（本批未引入新 module 单例）。**无漂移**。
- 无 Blueprint 架构变更（纯测试）。

## 7. 已知 limitations

- 本批是 e2e_live 集成层补全，沿用 GATE_P3_DEVIATION（直调主路径，非真打 LLM agent loop）。
- F123↔F124 链用 spy 验证"error 通道流经扫描器"——真 SSRF 经真 httpx 发包到 302 内网的端到端
  （需本地恶意 redirect server）未覆盖；预检在发包前，spy + 字面量私网 IP 已充分验证拦截语义。
