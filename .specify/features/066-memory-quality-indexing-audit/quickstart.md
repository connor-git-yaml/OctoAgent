# Quick Start: Feature 066 Memory 提取质量、索引利用与审计优化

---

## 前置知识

本 feature 涉及 OctoAgent Memory 系统的以下核心概念：

- **SoR（Source of Record）**: 经确认的长期记忆。位于 `packages/memory/src/octoagent/memory/models/sor.py`
- **Proposal 流程**: 记忆写入治理（propose → validate → commit）。位于 `packages/memory/src/octoagent/memory/service.py`
- **Consolidation Pipeline**: Fragment → SoR 的自动整理流程。位于 `packages/provider/src/octoagent/provider/dx/consolidation_service.py`
- **Control Plane**: 前端操作的后端处理层。位于 `apps/gateway/src/octoagent/gateway/services/control_plane.py`
- **Agent 工具**: Agent 可调用的 memory 工具集。位于 `apps/gateway/src/octoagent/gateway/services/capability_pack.py`

---

## 变更概览

### 后端变更

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `packages/memory/.../enums.py` | 修改 | +ARCHIVED, +SOLUTION, +MERGE 枚举值 |
| `packages/memory/.../models/browse.py` | 新增 | BrowseItem, BrowseGroup, BrowseResult 模型 |
| `packages/memory/.../store/memory_store.py` | 修改 | +browse_sor(), 扩展 search_sor() |
| `packages/memory/.../store/protocols.py` | 修改 | 新增 browse_sor 签名 |
| `packages/provider/.../consolidation_service.py` | 修改 | 全生活域 prompt + Solution 阶段 + MERGE |
| `packages/provider/.../profile_generator_service.py` | 修改 | 放宽 Profile 密度限制 |
| `packages/provider/.../memory_console_service.py` | 修改 | +browse_memory() |
| `packages/core/.../control_plane.py` | 修改 | 新增请求模型 |
| `apps/gateway/.../capability_pack.py` | 修改 | +memory_browse, 扩展 memory_search |
| `apps/gateway/.../control_plane.py` | 修改 | +sor.edit/archive/restore/browse handlers |

### 前端变更

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `frontend/src/domains/memory/MemoryDetailModal.tsx` | 修改 | +编辑/归档按钮 |
| `frontend/src/domains/memory/MemoryEditDialog.tsx` | 新增 | 编辑对话框 |
| `frontend/src/domains/memory/MemoryFiltersSection.tsx` | 修改 | +status 筛选 |
| `frontend/src/domains/memory/MemoryResultsSection.tsx` | 修改 | +已归档标签 |

---

## 实施步骤

### Step 1: 枚举与模型（Phase A）

从 `enums.py` 开始：
```python
# SorStatus 新增 ARCHIVED
# MemoryPartition 新增 SOLUTION
# WriteAction 新增 MERGE
```

然后创建 `models/browse.py`，实现 `browse_sor()` 存储方法。

**验证**: `pytest packages/memory/tests/` 全部通过

### Step 2: Agent 工具（Phase B）

在 `capability_pack.py` 中：
1. 新增 `memory_browse` 工具函数
2. 扩展 `memory_search` 参数

**验证**: 在 Agent 对话中调用 `memory.browse(group_by="partition")` 返回正确分组

### Step 3: 审计后端（Phase C）

在 `control_plane.py` 中新增 4 个 action handler。关键：编辑操作必须走 Proposal 流程。

**验证**: 通过 Control Plane API 执行编辑/归档/恢复操作

### Step 4: 审计前端（Phase D）

修改 Memory UI 组件，新增编辑/归档按钮和筛选选项。

**验证**: 在 Memory UI 中完成完整的编辑/归档/恢复操作流

### Step 5: Consolidation 扩展（Phase E + F）

扩展 prompt + 新增 Solution 检测阶段。

**验证**: 触发 consolidate 后检查输出覆盖多个生活维度

### Step 6: Profile 密度（Phase G）

修改 Profile prompt，放宽信息密度限制。

**验证**: profile_generate 输出包含多段详细描述

---

## 关键注意事项

1. **编辑走 Proposal 流程**: 绝不直接修改 SoR 数据库记录。必须经过 propose → validate → commit
2. **乐观锁**: 编辑/归档请求必须携带 `expected_version`，服务端验证匹配
3. **向后兼容**: 所有新参数必须可选，默认值保持现有行为
4. **Vault 授权**: `SENSITIVE_PARTITIONS`（HEALTH/FINANCE）的编辑/归档需额外授权检查
5. **MERGE 与 REPLACE**: MERGE 是新的 WriteAction 枚举值；REPLACE 复用 UPDATE + metadata

---

## 测试策略

| 层级 | 覆盖范围 | 工具 |
|------|---------|------|
| 单元 | 枚举值、browse_sor SQL、search_sor 扩展参数 | pytest |
| 集成 | 编辑 Proposal 流程、归档/恢复状态转换、browse 分组统计 | pytest |
| Contract | memory.browse 工具 schema 一致性 | pytest |
| 前端 | 编辑对话框交互、归档确认流程、筛选状态切换 | vitest |
| E2E | Agent 调用 browse → read 完整流程 | 手动验证 |
