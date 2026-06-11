# F119 e2e_live 端到端补全 — Tasks

> 纯测试新增（production 零改动）。每个 task = 1 个 e2e_live 文件，写完即跑通。

## 实施 tasks

- [x] **T1** `test_e2e_file_workbench.py`（F104，AC-104-1~4）：versionable 写多版本 →
  get_current_and_previous 取回真实内容 + Files API 两级导航 + diff 无技术字段泄漏 +
  并发不串版本。**4 passed**。
- [x] **T2** `test_e2e_notification_persist.py`（F116，AC-116-1~4）：NotificationService 双实例
  跨"重启" rehydrate 恢复 dismiss/active + 已读不重现 + 跨通道统一。**4 passed**。
- [x] **T3** `test_e2e_ssrf_guard.py`（F123，AC-123-1~4）：_fetch_browser_page 参数化拦内网
  （云元数据/loopback/私网/CGNAT/IPv6）+ broker web.fetch 拦 + DNS→私网拦 + redirect hook
  逐跳 re-validate。**8 passed**（5 参数化 + 3）。
- [x] **T4** `test_e2e_tool_result_threat_scan.py`（F124 + 链，AC-124-1~4）：stub 注入 payload →
  finding 挂载不 block + TOOL_RESULT_THREAT_FLAGGED 事件 + 真实文档负样本 0 误报 +
  F123↔F124 链（SSRF error 经 spy 验证流经扫描器 + error 通道产 finding）。**4 passed**。

## Verify tasks

- [x] **V1** 4 新文件单跑全 PASS（20 passed）。
- [x] **V2** `pytest -m e2e_smoke` → 8 passed（未破坏 pre-commit 关口）。
- [ ] **V3** 全量回归 0 regression vs `02e139fd`（后台跑中）。
- [ ] **V4** completion-report 标注每条 AC↔test 实测结果 + 漂移闸 + 归档项。

## 归档（显式不做 + 理由）

- **F099 ask_back / F100 force_full_recall e2e_live 不补**：
  - 二者**已有专门 e2e/集成测试**（F099 `test_phase_e_ask_back_e2e.py` 15 单测 + e2e 框架；
    F100 `test_runtime_control_f100.py` + `test_chat_force_full_recall.py` 链路集成测）——
    不属于集成 review 标记的"有单测无 e2e_live"硬缺口（那是 F104/F116/F123/F124）。
  - 干净 e2e_live 需大量 mock（ExecutionRuntimeContext patch / LLMService 消费 spy），
    偏离 e2e_live"真 bootstrap 主路径绕 LLM"精神，增量价值≈重复单测。
  - prompt 明确标二者为"(可选)"。控制本批范围（避免范围蔓延 + 维护面膨胀）。
