# Tasks: Feature 029 — WeChat Import + Multi-source Import Workbench

**Input**: `.specify/features/029-wechat-import-workbench/`  
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/*.md`  
**Created**: 2026-03-08  
**Status**: Implemented

**实现说明**: 最终实现复用了现有 026 的 provider-scoped control-plane document 生产路径，`ImportWorkbenchDocument / ImportSourceDocument / ImportRunDocument` 落在 `packages/provider/src/octoagent/provider/dx/import_workbench_models.py`，并由 gateway control-plane producer 对外发布；下列任务已按这一实现落点完成。

**Task Format**: `- [x] T{三位数} [P0/P1] [P?] [USN?] 描述 -> 文件路径`

- `[P0]` / `[P1]`: 交付优先级（P0 为 029 主闭环阻塞项）
- `[P]`: 可并行执行
- `[B]`: 阻塞后续关键路径
- `[USN]`: 所属 User Story（US1-US4）；Setup/Foundational 阶段不标注
- `[SKIP]`: 明确不在 029 落地

---

## Phase 1: Docs / Design Lock

**目标**: 锁定 021/025/026/027/028/031 的边界，避免实现时 scope 漂移

- [x] T001 [P0] [B] 回写 `.specify/features/029-wechat-import-workbench/*` 制品，冻结 029 的 adapter/workbench/attachment pipeline 范围
- [x] T002 [P0] [P] 回读 `docs/blueprint.md`、`docs/m3-feature-split.md`、Feature 021/025/026/027 制品，确认 029 只做 source adapter + workbench，不重做导入内核
- [x] T003 [P0] [P] 固化 4 份 contract：source adapter、workbench API、mapping、attachment pipeline -> `.specify/features/029-wechat-import-workbench/contracts/`

**Checkpoint**: 范围与边界冻结，可进入实现分解

---

## Phase 2: Foundational Models & Durable State (Blocking)

**目标**: 先建立 029 的 durable workbench state、source adapter contract 和 control-plane import documents  
**警告**: 本阶段未完成前，不得开始 WeChat parser 或 Web workbench UI

- [x] T004 [P0] [B] 在 `octoagent/packages/provider/src/octoagent/provider/dx/import_workbench_models.py` 定义 `ImportWorkbenchDocument`、`ImportSourceDocument`、`ImportRunDocument`、`ImportResumeEntry`、`ImportMemoryEffectSummary`
- [x] T005 [P0] [B] 在 gateway control-plane producer 与 frontend type 层导出/消费 029 新增 import canonical document
- [x] T006 [P0] [B] 在 `octoagent/packages/memory/src/octoagent/memory/imports/` 新增 `source_adapters/base.py` 与 adapter registry 基线
- [x] T007 [P0] [B] 在 `octoagent/packages/provider/src/octoagent/provider/dx/import_mapping_store.py` 实现 mapping profile durable store
- [x] T008 [P0] [B] 在 `octoagent/packages/provider/src/octoagent/provider/dx/import_source_store.py` 实现 source detect state / recent run / resume projection store
- [x] T009 [P0] [P] 在 `octoagent/packages/provider/tests/test_import_workbench_service.py`、`octoagent/packages/provider/tests/test_chat_import_commands.py` 与 `octoagent/apps/gateway/tests/test_control_plane_api.py` 新增 mapping store / source state / control-plane 基线测试

**Checkpoint**: 029 的 durable state 与 canonical model 已冻结

---

## Phase 3: User Story 1 — 直接导入 WeChat 导出物并先看 dry-run（Priority: P1）

**Independent Test**: 对 WeChat 离线导出样本执行 detect + preview，不做真实导入，也能得到会话、附件、计数和 warnings/errors

- [x] T010 [P0] [US1] [B] 在 `octoagent/packages/memory/src/octoagent/memory/imports/source_adapters/wechat.py` 实现 WeChat 输入 detect（目录/HTML/JSON/SQLite snapshot）
- [x] T011 [P0] [US1] [B] 在同文件实现 conversation/account/media roots 提取与 detect warnings/errors
- [x] T012 [P0] [US1] [B] 在同文件实现 preview -> `ImportRunDocument(dry_run=true)`，输出 mapping preview、counts、附件摘要、warnings/errors
- [x] T013 [P0] [US1] 在 `octoagent/packages/provider/src/octoagent/provider/dx/import_workbench_service.py` 编排 detect/preview，并把结果持久化为 workbench projection
- [x] T014 [P0] [US1] 在 `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_commands.py` 增加 CLI detect/preview 子路径或等价主路径参数
- [x] T015 [P0] [US1] 为 WeChat detect/preview 编写单元与集成测试 -> `octoagent/packages/provider/tests/test_import_workbench_service.py`、`octoagent/apps/gateway/tests/test_control_plane_api.py`

**Checkpoint**: 普通用户已能对 WeChat 导出物做无副作用预览

---

## Phase 4: User Story 2 — 修正 mapping、查看 dedupe，并从中断点继续（Priority: P1）

**Independent Test**: 一个带多 conversation 和重复消息的 source，能持久化 mapping，并在失败后稳定 resume

- [x] T016 [P0] [US2] [B] 定义 `ImportMappingProfile` 与 `ImportConversationMapping` 的校验规则 -> `octoagent/packages/provider/src/octoagent/provider/dx/import_mapping_store.py`
- [x] T017 [P0] [US2] [B] 在 `octoagent/packages/provider/src/octoagent/provider/dx/import_workbench_service.py` 实现 mapping save/load/validate
- [x] T018 [P0] [US2] 在 `octoagent/packages/provider/src/octoagent/provider/dx/import_workbench_service.py` 实现 dedupe detail、recent runs、resume entries projection
- [x] T019 [P0] [US2] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 注册 `import.mapping.save`、`import.preview`、`import.resume`
- [x] T020 [P0] [US2] 在 `octoagent/apps/gateway/tests/test_control_plane_api.py` 覆盖 mapping save / preview / resume API 语义
- [x] T021 [P0] [US2] 为 recent runs / resume projection 编写 provider DX 测试 -> `octoagent/packages/provider/tests/test_import_workbench_service.py`

**Checkpoint**: mapping/dedupe/resume 成为正式工作台能力

---

## Phase 5: User Story 3 — 附件和事实提案进入统一治理链（Priority: P1）

**Independent Test**: 带附件和 `fact_hints` 的导入样本执行后，附件进入 artifact，proposal/commit 仍走 021/020 治理，MemU unavailable 时仅 degraded

- [x] T022 [P0] [US3] [B] 在 `octoagent/packages/memory/src/octoagent/memory/imports/source_adapters/base.py` 定义 attachment descriptor / provenance contract
- [x] T023 [P0] [US3] [B] 在 `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py` 接入 attachment materialization，生成 artifact-first 附件路径
- [x] T024 [P0] [US3] [B] 在 `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py` 汇总 `ImportMemoryEffectSummary`（fragment/proposal/commit/vault/memu）
- [x] T025 [P0] [US3] 在 `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_service.py` 对接 MemU integration point 的 best-effort sync，并在不可用时记录 degraded warning
- [x] T026 [P0] [US3] 在 `octoagent/packages/provider/tests/test_chat_import_service.py` 与 `octoagent/packages/provider/tests/test_import_workbench_service.py` 增加附件 artifact / attachment fragment / resume 回归
- [x] T027 [P0] [US3] 复用 `octoagent/packages/provider/tests/test_chat_import_service.py` 验证 fact/proposal/commit 与 fragment 同步，不绕过 021/020 治理

**Checkpoint**: 029 的附件与 memory effects 已落入统一治理链

---

## Phase 6: User Story 4 — Control Plane 中查看导入报告、错误、warnings 和 resume（Priority: P2）

**Independent Test**: Web Control Plane 能展示 workbench、recent runs、warnings/errors、resume 和报告详情

- [x] T028 [P0] [US4] [B] 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 生产 `import_workbench`、`import_source`、`import_run` canonical resources
- [x] T029 [P0] [US4] [B] 在 `octoagent/apps/gateway/src/octoagent/gateway/routes/control_plane.py` 发布 import resources 路由
- [x] T030 [P0] [US4] 在 `octoagent/frontend/src/types/index.ts` 扩展 import workbench 类型
- [x] T031 [P0] [US4] 在 `octoagent/frontend/src/api/client.ts` 增加 import workbench resources/actions client
- [x] T032 [P0] [US4] 在 `octoagent/frontend/src/pages/ControlPlane.tsx` 实现 Import Workbench section，包含 source detect、mapping、preview、recent runs、resume、warnings/errors
- [x] T033 [P0] [US4] 在 `octoagent/frontend/src/pages/ControlPlane.test.tsx` 覆盖 workbench 主交互与刷新行为

**Checkpoint**: Web Control Plane 导入工作台可用

---

## Phase 7: CLI Parity & Polish

**目标**: 保持 CLI 为高级路径，同时与 Web 共用同一后端语义

- [x] T034 [P1] 在 `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_commands.py` 补齐 detect/preview/run/resume 等价命令路径
- [x] T035 [P1] 在 `octoagent/packages/provider/tests/test_chat_import_commands.py` 覆盖 detect/preview/resume 的 CLI 输出与错误码
- [x] T036 [P1] 复用现有 `ControlPlane` 样式层完成 Import Workbench 桌面/移动端布局，无需新增平行样式文件

**Checkpoint**: Web/CLI 共语义成立

---

## Phase 8: Verification / Docs / Sync

- [x] T037 [P0] [B] 运行 targeted `pytest`：WeChat adapter、provider DX workbench、chat import service、gateway control-plane API
- [x] T038 [P0] [P] 运行 frontend `npm test` / `npm run build`
- [x] T039 [P0] [P] 增加必要 e2e：detect -> mapping -> preview -> run/resume 的主路径
- [x] T040 [P0] 更新 `verification/verification-report.md`
- [x] T041 [P1] 复核 `docs/blueprint.md` 与 `docs/m3-feature-split.md`；本次实现未改变 M3 边界口径，因此无需额外回写
- [x] T042 [P0] 使用 `/review` 思维做一次全面自查，优先检查：跨 project 污染、resume 重复写入、附件 provenance 丢失、MemU 降级误报

---

## Deferred / Boundary Tasks

- [x] T043 [P1] [SKIP] 在线抓取 WeChat 服务端聊天历史
  **SKIP 原因**: 029 主路径明确锁定为离线导出物输入

- [x] T044 [P1] [SKIP] 新建平行 Import Console 或重做 026 控制台框架
  **SKIP 原因**: 029 必须消费现有 control plane

- [x] T045 [P1] [SKIP] 定义 M3 全量 acceptance matrix / 全量样本库
  **SKIP 原因**: 这属于 Feature 031

---

## Dependencies & Execution Order

- Phase 2 是所有实现的阻塞前提
- Phase 3 依赖 Phase 2
- Phase 4 依赖 Phase 2 与 WeChat adapter detect/preview 基线
- Phase 5 依赖 Phase 4 的 source attachment contract 和既有 021 import core
- Phase 6 依赖 Phase 3/4/5 的 backend canonical producer 稳定
- Phase 7 可与 Phase 6 局部并行
- Phase 8 在所有故事完成后执行

## Parallel Opportunities

- T007/T008/T009 可并行
- T010/T011/T012 可并行推进同一 adapter 文件中的 detect/preview 子块
- T020/T021 可并行
- T030/T031/T033 可并行
- T037/T038 可并行
