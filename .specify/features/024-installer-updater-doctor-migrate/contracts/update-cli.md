# Contract: CLI API — `octo update` / `octo restart` / `octo verify`

**Feature**: `024-installer-updater-doctor-migrate`
**Created**: 2026-03-08
**Traces to**: FR-004 ~ FR-014, FR-017, FR-018

---

## 契约范围

本文定义 024 的最小 operator CLI：

- `octo update`
- `octo restart`
- `octo verify`

---

## 1. `octo update`

### 命令签名

```bash
octo update [--dry-run] [--wait/--no-wait]
```

### 参数说明

| 参数 | 必填 | 说明 |
|---|---|---|
| `--dry-run` | 否 | 只执行 preflight/migrate preview，不产生 destructive 副作用 |
| `--wait/--no-wait` | 否 | 真实 update 时是否等待 detached worker 完成；默认 CLI 等待 |

### 最小行为

真实 update 必须遵循固定阶段：

1. `preflight`
2. `migrate`
3. `restart`
4. `verify`

### dry-run 约束

- dry-run 不得重启当前实例
- dry-run 不得执行 destructive migrate
- dry-run 必须返回 `UpdateAttemptSummary`

### 返回码

| 返回码 | 含义 |
|---|---|
| `0` | update 成功或 dry-run 通过 |
| `1` | update 失败 / verify 失败 / action required |
| `2` | 参数错误或项目根无效 |

---

## 2. `octo restart`

### 命令签名

```bash
octo restart
```

### 最小行为

- 读取 `ManagedRuntimeDescriptor`
- 对 managed runtime 执行真实 restart
- 对 unmanaged runtime 返回结构化失败并退出码 `1`

---

## 3. `octo verify`

### 命令签名

```bash
octo verify
```

### 最小行为

1. 读取 `ManagedRuntimeDescriptor` 或 `RuntimeStateSnapshot`
2. 轮询 `verify_url`
3. 必要时补本地 doctor/diagnostics 摘要
4. 返回结构化 `UpdateAttemptSummary` 或 verify summary

### 返回码

| 返回码 | 含义 |
|---|---|
| `0` | verify 通过 |
| `1` | verify 失败或超时 |
| `2` | 参数错误或缺失 runtime 配置 |

---

## 4. 共享语义

- CLI 与 Web 必须共享同一 `UpdateAttemptSummary`
- CLI 文本输出只是 summary 的呈现层，不得成为唯一结果
- `octo update` / `octo restart` / `octo verify` 都必须尊重并发保护；已有 active attempt 时不得静默并发执行

---

## 5. 禁止行为

- 不允许 `octo update --dry-run` 写 `active-update.json`
- 不允许 CLI 在 unmanaged runtime 上假装 restart 成功
- 不允许只输出原始堆栈而没有结构化失败报告
