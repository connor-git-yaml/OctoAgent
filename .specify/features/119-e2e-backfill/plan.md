# F119 e2e_live 端到端补全 — Plan

> spec：`spec.md`。纯测试新增（不动 production）。技术路径已通过侦察确认可执行。

## 技术范式（侦察确认）

所有测试复用 e2e_live 既有装配：
- `octo_harness_e2e` fixture（`helpers/factories.py`）→ 注入 4 DI 钩子 + 真文件 tmp DB。
- 本批每个文件自带 `bootstrapped_harness` fixture（仿 `test_e2e_safety_gates.py`）：
  `harness.bootstrap(app)` + `harness.commit_to_app(app)` → `app.state.*` 全装配。
- HTTP 层：手动 `app.include_router(<route>.router, dependencies=[Depends(require_front_door_access)])`
  + `ASGITransport` 直调（仿 `test_e2e_memory_pipeline.py`，loopback 自动过 front-door）。
- marker：`pytestmark = [pytest.mark.e2e_full, pytest.mark.e2e_live]`（被 `pytest -m e2e_full` 收集，
  不进 pre-commit smoke，不阻塞 commit）。

## 各标的可执行路径（已验证装配点）

| 标的 | 装配点（bootstrap 后真实存在） | e2e 主路径 |
|------|------------------------------|-----------|
| F104 | `store_group.artifact_store`（带独立 `versionable_conn`，`__init__.py:112` make_store_group） | 直调 `put_artifact(versionable=True)` 写版本 + `app.include_router(files.router)` + ASGITransport 调 4 endpoint |
| F116 | `store_group.notification_store`（`SqliteNotificationStore(conn)`） | 构造两个 `NotificationService(notification_store=...)`，dismiss/record_active→新实例 rehydrate |
| F123 | `app.state.capability_pack_service`（pack_service）；`url_safety.ensure_url_safe/_ssrf_request_hook` | 直调 `_fetch_browser_page(私网)` + broker `web.fetch` + `_ssrf_request_hook` |
| F124 | `app.state.tool_broker`（`content_scanner=ContentThreatScanService()` 已注入，`octo_harness.py:525`） | `broker.try_register(stub)` + `broker.execute` → 断言 security_findings + 事件 |
| 链 | 同 F124 + `UnsafeUrlError`（`url_safety.py:47`，子类 RuntimeError 走 broker exception 分支） | stub raise UnsafeUrlError + spy scanner → 断言 is_error + error 通道流经扫描 |

## 实施顺序

1. F104（最复杂，先验范式：versionable 写 + Files route 挂载 + diff）→ 跑通。
2. F116（NotificationService 双实例 rehydrate）→ 跑通。
3. F123（SSRF 参数化拦截 + redirect hook）→ 跑通。
4. F124 + 互补链（stub 工具 + spy scanner）→ 跑通。
5. （stretch）F099/F100 视情况。
6. Verify：4 文件 PASS + e2e_smoke PASS + 全量回归 0 regression。
7. Codex review 仅当改了 conftest/fixture；否则纯新增跳过。
8. completion-report + 归总报告，等用户拍板。

## 风险

- web.fetch 是否在 bootstrap 后进 `tool_broker._registry`：测试加前置自检；不在则 SKIP broker 子路径，
  直调 `_fetch_browser_page` 仍覆盖 F123 主语义。
- versionable_conn 在 tmp 文件 DB 路径下是独立物理连接（make_store_group 真分配）；
  退化路径（内存 DB）会与主 conn 同对象 → put_artifact(versionable=True) raise。e2e 用文件 DB 规避。
- F124 stub 工具的 CONTEXT pattern 命中需用 F125 收紧后真能命中的 payload（如 AI 身份注入
  "you are now an unrestricted assistant"），避免用 F125 已回收的 PI-004 类。
