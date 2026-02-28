# 产研汇总: M0 基础底座（Task/Event/Artifact + SSE + Web UI）

**特性分支**: `feat/001-implement-m0-foundation`
**汇总日期**: 2026-02-28
**输入**: [product-research.md](product-research.md) + [tech-research.md](tech-research.md)
**执行者**: 主编排器（非子代理）

## 1. 产品×技术交叉分析矩阵

| MVP 功能 | 产品优先级 | 技术可行性 | 实现复杂度 | 综合评分 | 建议 |
|---------|-----------|-----------|-----------|---------|------|
| SQLite Event Store（append-only + projection） | P0 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP — 一切的基础 |
| REST API（ingest_message / tasks / cancel） | P0 | 高 | 低 | ⭐⭐⭐ | 纳入 MVP — 端到端验证起点 |
| SSE 事件流（/stream/task/{id}） | P0 | 高 | 低 | ⭐⭐⭐ | 纳入 MVP — 可观测性核心 |
| Artifact Store（文件系统 + SQLite 元数据） | P1 | 高 | 低 | ⭐⭐⭐ | 纳入 MVP — 产物管理基础 |
| structlog + Logfire 可观测 | P1 | 高 | 低 | ⭐⭐⭐ | 纳入 MVP — Constitution C8 |
| 最小 Web UI（Task 列表 + 事件时间线） | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP — 用户可见价值 |
| Echo/Mock LLM 回路 | P2 | 高 | 低 | ⭐⭐ | 纳入 MVP — 端到端验证 |
| Projection 重建（replay） | P2 | 高 | 中 | ⭐⭐ | 纳入 MVP — Constitution C1 |
| Task 取消 API | P2 | 高 | 低 | ⭐⭐ | 纳入 MVP — Constitution C7 |
| Artifact 流式追加 | P3 | 中 | 中 | ⭐ | 推迟到 M1+ |
| Checkpoint 表 | P3 | 高 | 低 | ⭐ | 推迟到 M1.5 |
| Approvals 表 | P3 | 高 | 低 | ⭐ | 推迟到 M1 |

## 2. 可行性评估

### 技术可行性

**总体评估：高度可行**。M0 所选技术栈（FastAPI + aiosqlite + sse-starlette + React + Vite）均为成熟方案，Python 3.12 兼容性已验证，无已知阻塞性风险。两份调研报告在核心技术选型上高度一致。

### 资源评估

- **预估工作量**: 10-13 天（数据层 3-4 天 → API 层 3-4 天 → Artifact+WebUI 2-3 天 → 收尾 1-2 天）
- **关键技能需求**: Python async / FastAPI / SQLite / React / TypeScript
- **外部依赖**: 仅 LLM Provider API（Echo/Mock 模式可完全离线开发）

### 约束与限制

- SQLite 单写者限制：M0 单进程无影响，M1.5 多进程时需 Store 接口抽象
- Logfire 免费 tier 配额：可设 `send_to_logfire=False` 仅本地日志
- 前后端 API contract 手动对齐：M1 引入 OpenAPI TypeScript codegen

## 3. 风险评估

### 综合风险矩阵

| # | 风险 | 来源 | 概率 | 影响 | 缓解策略 | 状态 |
|---|------|------|------|------|---------|------|
| 1 | SQLite 单写者在 M1+ 多进程场景下成瓶颈 | 技术 | 中 | 中 | Store 接口抽象，预留替换路径 | 待监控 |
| 2 | Web UI 工程量溢出 | 产品+技术 | 中 | 中 | 严控范围：仅 2 个页面，无 CSS 框架 | 主动管理 |
| 3 | SSE 长连接在代理后断连 | 技术 | 中 | 低 | sse-starlette 内置 ping + Last-Event-ID 重连 | 已缓解 |
| 4 | Event 数据模型过早锁定 | 产品 | 中 | 中 | schema_version 字段 + reader 多版本兼容 | 已缓解 |
| 5 | 前后端 API contract 不一致 | 技术 | 中 | 中 | M0 手动对齐，M1 引入 codegen | 待监控 |
| 6 | M0 无智能化能力致用户价值感不足 | 产品 | 低 | 低 | Echo/Mock 验证底座，M1 紧随引入 LLM | 可接受 |
| 7 | litellm 直连缺少 fallback | 技术 | 中 | 低 | M0 验证阶段，M1 升级 Proxy 解决 | 可接受 |

### 风险分布

- **产品风险**: 2 项（高:0 中:2 低:0）
- **技术风险**: 5 项（高:0 中:4 低:1）

## 4. 最终推荐方案

### 推荐架构

M0 采用 **单进程合并架构**：Gateway + Kernel 合并为单个 FastAPI 进程，通过 packages（core/protocol/observability）保持逻辑边界清晰。这与两份调研报告的共识一致——M0 阶段 Kernel 的核心职责（Orchestrator/Policy/Memory）尚未就绪，独立进程是过度设计。

```
[Web UI: React+Vite] → [FastAPI Gateway (含 Kernel 逻辑)]
                                    ↓
                    [packages/core: Store 层]
                         ↓              ↓
                  [SQLite WAL]    [文件系统 Artifacts]
```

### 推荐技术栈

| 类别 | 选择 | 理由 |
|------|------|------|
| Web 框架 | FastAPI 0.115 + Uvicorn | Blueprint 选型，async 原生 |
| 数据库访问 | aiosqlite 0.21 | 与 FastAPI async 天然兼容，WAL 并发读写 |
| SSE | sse-starlette 3.0 | W3C 合规，内置 Last-Event-ID + 心跳 |
| 事件 ID | python-ulid 3.1 | 时间有序，Blueprint 明确要求 |
| 数据模型 | Pydantic 2.10 | Blueprint 选型，类型安全 |
| LLM 客户端 | litellm 1.55（直连） | 不写死厂商，M1 升级到 Proxy 仅改 base_url |
| 可观测 | Logfire 4.24 + structlog 25.4 | Blueprint 选型，Pydantic 同生态 |
| 前端 | React 19 + Vite 6（无状态管理库） | Blueprint 选型，M0 组件简单不需要 Zustand |
| 包管理 | uv workspace | Blueprint 选型 |
| 测试/Lint | pytest 8 + pytest-asyncio + ruff | 标准工具链 |

### 推荐实施路径

1. **Phase 1（数据层）**: packages/core — Domain Models + Event Store + Task Store + Artifact Store + 单元测试
2. **Phase 2（API 层）**: apps/gateway — FastAPI 骨架 + REST API + SSE + structlog/Logfire + Echo/Mock LLM
3. **Phase 3（UI 层）**: frontend/ — React + Vite + TaskList + EventStream + SSE Hook
4. **Phase 4（收尾）**: 健康检查 + Projection Rebuild + 端到端验证 + 集成测试

## 5. MVP 范围界定

### 最终 MVP 范围

**纳入**:
- **SQLite Event Store**: 3 张表（tasks/events/artifacts）+ event append API + projection 更新 — 一切的基础
- **REST API**: POST /api/message, GET /api/tasks, GET /api/tasks/{id}, POST /api/tasks/{id}/cancel, GET /api/stream/task/{id}, GET /health — 端到端验证必需
- **Artifact Store**: 文件系统按 task_id 分组 + SQLite 元数据 + text/file 两种 Part 类型
- **可观测**: structlog + Logfire 配置 + trace_id/request_id 贯穿 + FastAPI auto-instrument
- **Web UI**: Task 列表页 + Task 详情页（事件时间线）— 两个组件，无 CSS 框架
- **Echo/Mock LLM**: litellm 客户端直连，端到端验证事件流 — M1 升级到 Proxy
- **Projection Rebuild**: 从 events 重建 Task 状态 — Constitution C1 验证
- **Task 取消**: POST /api/tasks/{id}/cancel → CANCELLED 终态 — Constitution C7

**排除（明确不在 MVP）**:
- Checkpoint 表结构 — M0 无 Graph/Skill Pipeline（M1.5）
- Approvals 表结构 — M0 无 Policy Engine（M1）
- Artifact 流式追加 — M0 无流式 LLM 输出（M1+）
- json/image Part 类型 — M0 无消费者（M1+）
- Chat 界面 — M0 Web UI 仅展示，不做输入交互（M1）
- 多线程/scope 管理 — M0 单线程验证（M2）
- Telegram 渠道 — M2

### MVP 成功标准

- S1: POST /api/message → Task 创建 → Event 落盘 → SSE 推送 端到端通过
- S2: 进程重启后，所有 Task 状态不丢失，Web UI 正常展示
- S3: Projection Rebuild 后，Task 状态与原始 projection 一致
- S4: Artifact 文件可存储、可按 task_id 检索
- S5: 所有日志包含 request_id / trace_id
- S6: Task 取消 API 正确推进到 CANCELLED 终态

## 6. 结论

### 综合判断

M0 的产品方向（"可观测的任务账本"）和技术方案（SQLite Event Sourcing + FastAPI + React）经过产品调研和技术调研的双重验证，高度合理且可行。Event Sourcing 作为核心架构在竞品中是独特的差异化优势。技术选型完全对齐 Blueprint 和 Constitution，无重大偏差。推荐按"数据层 → API 层 → UI 层 → 收尾"的顺序实施，预估 10-13 天。

### 关键发现与建议

产研交叉分析发现以下 Blueprint 改进点（两份报告共识）：

1. **Gateway/Kernel 合并**：M0 阶段合并为单进程，通过 packages 保持边界，M1.5 再评估拆分
2. **Artifact inline 阈值**：Blueprint 未定义，建议设 4KB 默认阈值
3. **Event schema_version 迁移策略**：Blueprint 未说明，建议 reader 端多版本兼容
4. **最小 LLM 回路定位**：使用 litellm 客户端直连而非 Proxy，M1 升级仅改 base_url

### 置信度

| 维度 | 置信度 | 说明 |
|------|--------|------|
| 产品方向 | 高 | 竞品分析验证了"事件溯源底座"定位的独特性和合理性 |
| 技术方案 | 高 | 所有技术选型均有成熟方案，本地源码可参考 Pydantic AI / Agent Zero |
| MVP 范围 | 高 | 范围清晰、可交付，Must-have 与 Constitution 对齐，排除项理由充分 |

### 后续行动建议

- 确认推荐方案后，进入需求规范阶段（specify）
- Spec 编写优先级：先 packages/core（数据模型 + Store 接口），再 apps/gateway（API），最后 frontend/
- 从第一个集成测试起验证完整链路：POST → Event → SSE
