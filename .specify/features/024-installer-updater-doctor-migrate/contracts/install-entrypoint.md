# Contract: Install Entrypoint — `scripts/install-octo.sh`

**Feature**: `024-installer-updater-doctor-migrate`
**Created**: 2026-03-08
**Traces to**: FR-001, FR-002, FR-003

---

## 契约范围

本文定义 024 的官方一键安装入口。

目标不是发布多种安装器，而是提供一条**单机/单实例可重复执行**的官方入口，让用户完成：

1. 基础依赖检查
2. 本地 workspace 依赖准备
3. managed runtime descriptor 初始化
4. 后续 `octo config init` / `octo doctor` / 启动指引

---

## 1. 入口签名

```bash
./scripts/install-octo.sh [--project-root PATH] [--force] [--skip-frontend]
```

### 参数说明

| 参数 | 必填 | 说明 |
|---|---|---|
| `--project-root PATH` | 否 | 指定项目根目录，默认当前脚本所在仓库根 |
| `--force` | 否 | 允许覆盖已有 managed runtime descriptor |
| `--skip-frontend` | 否 | 跳过前端依赖安装/构建 |

---

## 2. 最小行为

安装入口必须按以下顺序执行：

1. 检查 Python/uv 是否可用
2. 检查项目根目录与关键文件是否存在
3. 执行 `uv sync`
4. 在未 `--skip-frontend` 时执行前端依赖准备（若前端目录存在）
5. 生成 `ManagedRuntimeDescriptor`
6. 输出结构化 `InstallAttempt`

---

## 3. 幂等要求

- 若 descriptor 已存在且与当前项目根一致：
  - 默认不破坏现有配置；
  - 可返回 `ACTION_REQUIRED` 或“已安装”摘要；
  - `--force` 才允许重写。
- 重复执行不得删除现有数据目录或覆盖用户配置文件。

---

## 4. 输出语义

至少展示：
- `status`
- `project_root`
- 完成了哪些动作
- descriptor 路径
- 下一步动作（如 `octo config init`、`octo doctor`、启动 gateway）

---

## 5. 禁止行为

- 不允许把 secrets/token 写入 descriptor 或脚本
- 不允许 installer 自动删除用户已有 `data/` 目录
- 不允许 installer 假装成功但未生成 runtime descriptor
