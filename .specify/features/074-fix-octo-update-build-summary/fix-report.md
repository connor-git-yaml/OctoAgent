# 问题修复报告

## 问题描述

`octo update` 在 `migrate` 阶段失败，CLI 面板把失败原因显示成：

- `npm warn deprecated @types/dompurify@3.2.0 ...`

但本地复现 `npm install && npm run build` 后发现，真正失败的是前端 TypeScript 构建错误，而不是这条 npm warning。

## 根因分析

- **根因 1：`ChatWorkbench` 与 `useTaskLiveState` 的类型边界失配**
  - `useTaskLiveState` 把 `taskId` 定义成 `string | undefined`，但 `ChatWorkbench` 传入的是 `string | null`
  - `useTaskLiveState` 自己声明了一份本地 `SnapshotResourceRef`，其 `schema_version` 被写成 `string`
  - `ChatWorkbench` 传给 hook 的 `snapshotResourceRefs` 实际上是完整资源文档数组，而不是 resource ref 数组
  - `refreshResources` 的函数签名也与 `useWorkbench()` 暴露的不一致
- **根因 2：update 命令的失败摘要策略过于粗糙**
  - `_default_run_command()` 在命令返回非 0 时优先读取 `stderr`
  - `npm install && npm run build` 这类串联命令中，warning 常常先出现在 `stderr`，真正的 `tsc` 错误在 `stdout`
  - 导致升级失败被错误摘要“截胡”，误导 operator
- **附带噪音：前端仍保留了多余依赖 `@types/dompurify`**
  - `dompurify` 已自带类型，这条依赖只会持续制造 warning

## 引入原因

- `ChatWorkbench` 被拆分、`useTaskLiveState` 被抽成独立 hook 后，类型收口没有同步完成
- update 领域服务最初只考虑“失败时给一个字符串”，没有区分 warning 与真正失败上下文

## 影响范围

- 受影响文件
  - `octoagent/frontend/src/pages/ChatWorkbench.tsx`
  - `octoagent/frontend/src/hooks/useTaskLiveState.ts`
  - `octoagent/packages/provider/src/octoagent/provider/dx/update_service.py`
  - `octoagent/frontend/package.json`
  - `octoagent/frontend/package-lock.json`
- 受影响功能
  - `octo update` / Web recovery 的 `migrate` 阶段稳定性与错误可解释性
  - 前端生产构建
  - Chat 页面任务实时状态轮询的类型边界

## 相关上下文

- 关联既有 spec
  - `.specify/features/024-installer-updater-doctor-migrate/spec.md`
- 近期相关变更
  - `ce24098` `refactor(frontend): 提取 useTaskLiveState hook`
  - `6f54454` `fix(frontend): 修复 useTaskLiveState review 发现的 4 个 bug + 代码清理`
  - `d7938cd` `refactor(gateway): provider/dx 运行时服务上移到 gateway`

## 修复策略

### 方案 A（推荐）

1. 让 `useTaskLiveState` 直接复用统一的 `ControlPlaneResourceRef` 类型，并放宽 `taskId` 入参到 `string | null | undefined`
2. 在 `ChatWorkbench` 中明确把 snapshot resource document 映射成 resource ref 数组
3. 调整 `_default_run_command()`，失败时保留 `stdout + stderr` 的有效上下文，避免 warning 盖住真正错误
4. 移除 `@types/dompurify`

**优点**

- 改动集中，能同时修复 build、operator 可解释性和 warning 噪音
- 让 hook 与 workbench 类型边界重新统一

### 方案 B（备选）

1. 只在 `ChatWorkbench` 本地做类型断言绕过编译
2. 只在 update CLI 渲染层补一个“显示 stdout”分支

**缺点**

- 只是掩盖症状，类型债仍在
- 下次别的命令失败仍可能被 `stderr` warning 误导

## Spec 影响

- 需要更新的 spec：无需更新
- 原因：这是对既有 Feature 024 交付行为的实现修正，不引入新的需求边界

## 项目上下文与风险

- `project-context.yaml` 可读
- 官方 `resolve-project-context.mjs` 在当前插件缓存下因缺失 `zod` 无法运行，本次以手动回退方式继续，不阻断修复
