# Contract: CLI API — `octo onboard`

**Feature**: `015-octo-onboard-doctor`
**Created**: 2026-03-07
**Traces to**: FR-001, FR-002, FR-003, FR-006, FR-007, FR-010, FR-011, FR-014

---

## 契约范围

本文定义 `octo onboard` 的命令签名、阶段行为、输出语义和返回码。目标是保证它成为首次使用主入口，而不是另一个需要用户记忆的附加命令。

---

## 1. 命令签名

```bash
octo onboard [--channel CHANNEL_ID] [--restart] [--status-only]
```

### 选项

| 选项 | 类型 | 默认值 | 描述 |
|---|---|---|---|
| `--channel` | `str` | `telegram` | 指定目标 verifier ID |
| `--restart` | `flag` | `False` | 清空当前 onboarding session 并从头重新开始；必须确认 |
| `--status-only` | `flag` | `False` | 只显示当前 session summary，不推进任何步骤 |

### 项目根解析

与 `octo config` 保持一致：
1. `OCTOAGENT_PROJECT_ROOT`
2. `Path.cwd()`

---

## 2. 基本行为

### 2.1 无参数执行

`octo onboard` 必须按以下顺序执行：
1. 加载或创建 `OnboardingSession`
2. 检查 provider/runtime 步骤
3. 运行 `octo doctor --live` 等级的诊断
4. 执行 channel readiness
5. 执行 first-message verification
6. 输出统一 summary

### 2.2 resume 语义

若已存在 session 且未传 `--restart`：
- 必须从第一个非 `COMPLETED` 步骤继续
- 已完成步骤默认只做轻量重校验或直接跳过
- 不得清空已有配置或 session 数据

### 2.3 `--restart`

- 若存在 session：必须提示用户确认
- 确认后归档/删除旧 session，再从 `provider_runtime` 开始
- 未确认时，命令退出且不改动任何配置或 session

### 2.4 `--status-only`

- 只读取 session 并输出 summary
- 不执行 doctor live、不调用 verifier、不修改配置
- 若 session 不存在，则输出“尚未开始 onboarding”的友好提示

---

## 3. 阶段行为约定

### 3.1 Provider / Runtime 阶段

- 若 `octoagent.yaml` 缺失或不完整，命令必须进入共享 config bootstrap 路径
- 配置写入必须继续走 `save_config()` / `generate_litellm_config()` 现有链路
- 该阶段完成前，不得进入 doctor 阶段

### 3.2 Doctor 阶段

- 必须执行 `DoctorRunner.run_all_checks(live=True)`
- 必须调用 shared remediation planner 生成 `DoctorGuidance`
- 存在 blocking remediation 时，命令必须：
  1. 持久化当前 session
  2. 输出 grouped remediation
  3. 以非 0 返回码退出

### 3.3 Channel / First Message 阶段

- 通过 `ChannelVerifierRegistry` 解析 verifier
- registry miss、依赖缺失、verifier unavailable 时，不得抛出原始异常；必须输出 `BLOCKED` summary
- `first_message` 只可在 `channel_readiness=COMPLETED` 后执行

---

## 4. 输出格式

### 4.1 正常流程输出

至少包含三个区块：
1. 当前阶段 / resume 命中信息
2. 若有 remediation，则按 stage 分组展示
3. 最终 summary

### 4.2 Summary 语义

最终 summary 至少展示：
- `READY` / `ACTION_REQUIRED` / `BLOCKED`
- 已完成步骤列表
- 待完成步骤列表
- 下一步动作（最多优先展示 3 条）

示例：

```text
Onboarding Summary
────────────────────────────────
状态: BLOCKED
已完成: provider_runtime, doctor_live
待完成: channel_readiness, first_message
下一步动作:
  1. 安装或启用 telegram verifier（Feature 016）
  2. 修复后重新运行: octo onboard --channel telegram
```

---

## 5. 返回码

| 返回码 | 含义 |
|---|---|
| `0` | 全部步骤完成，summary=`READY` |
| `1` | 存在 `ACTION_REQUIRED` 或 `BLOCKED`，需要用户后续动作 |
| `2` | 内部错误、参数错误或用户拒绝 `--restart` 覆盖 |

说明：`status-only` 若当前不是 `READY`，也返回 `1`，便于 CI/自动化识别尚未可用状态。

---

## 6. 禁止行为

- 不得在默认运行中静默重置已完成 session
- 不得把 verifier 缺位场景输出为成功
- 不得要求用户必须离开 `octo onboard` 才能完成主路径配置
- 不得在 015 中实现 Telegram pairing/transport/thread routing 逻辑
