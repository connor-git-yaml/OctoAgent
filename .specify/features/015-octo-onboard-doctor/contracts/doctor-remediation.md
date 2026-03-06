# Contract: Doctor Remediation Guidance

**Feature**: `015-octo-onboard-doctor`
**Created**: 2026-03-07
**Traces to**: FR-005, FR-006, FR-013

---

## 契约范围

本文定义 `DoctorRunner` 之上的 remediation planner 契约。它不替代现有 `DoctorReport`，而是把 `CheckResult` 映射成统一的 action-oriented guidance，供 `octo doctor` 和 `octo onboard` 共享。

---

## 1. 输入与输出

### 输入

```python
DoctorReport(
    checks: list[CheckResult],
    overall_status: CheckStatus,
    timestamp: datetime,
)
```

### 输出

```python
DoctorGuidance(
    overall_status: Literal["ready", "action_required", "blocked"],
    groups: list[DoctorGuidanceGroup],
    blocking_actions: list[NextAction],
    generated_at: datetime,
)
```

---

## 2. 映射规则

### 2.1 blocking 判定

以下情况必须被映射为 `severity=blocking`：
- `CheckLevel.REQUIRED` 且 `status=FAIL`
- `check.name == "live_ping"` 且 `status=FAIL`
- `check.name == "octoagent_yaml_valid"` 且 `status=FAIL`

以下情况映射为 `severity=warning`：
- `WARN`
- `RECOMMENDED + FAIL`，但不阻断进入下一阶段的场景

### 2.2 action 生成优先级

1. 若存在明确修复命令，生成 `action_type="command"`
2. 否则若需要用户手工动作，生成 `action_type="manual"`
3. 若问题来自缺少其他 Feature/插件，实现 `action_type="blocked_dependency"`
4. 每个失败检查至少生成一条 action；不得只返回原始异常文本

---

## 3. 规范化映射表示例

| CheckResult.name | 典型状态 | Guidance stage | 默认 action |
|---|---|---|---|
| `env_file` | FAIL | `config` | `octo config init` |
| `env_litellm_file` | WARN | `config` | `octo config sync` |
| `llm_mode` | FAIL | `config` | `octo config init` 或检查 `octoagent.yaml.runtime.llm_mode` |
| `docker_running` | WARN | `system` | 启动 Docker Desktop |
| `proxy_reachable` | WARN/FAIL | `connectivity` | 启动 LiteLLM Proxy |
| `credential_valid` | WARN | `connectivity` | 重新配置 provider 凭证 |
| `credential_expiry` | WARN | `connectivity` | 刷新 token / 更换 API Key |
| `octoagent_yaml_valid` | FAIL | `config` | 修复 YAML 或重新初始化配置 |
| `litellm_sync` | WARN | `config` | `octo config sync` |
| `live_ping` | FAIL | `connectivity` | 检查 proxy key / provider key / 网络 |

---

## 4. CLI 展示要求

### `octo doctor`

必须继续打印现有 table，同时追加 remediation 摘要，例如：

```text
Remediation
────────────────────────────────
[config]
- 运行: octo config sync

[connectivity]
- 启动 Docker Desktop
- 修复后重试: octo doctor --live
```

### `octo onboard`

- 直接消费 `DoctorGuidance`
- blocking action 必须写入 `OnboardingSession.last_remediations`
- summary 的 next actions 应优先复用 `DoctorGuidance.blocking_actions`

---

## 5. 兼容性保证

- `DoctorRunner.run_all_checks()` 的现有返回类型不变
- `format_report()` 现有表格输出不删除、不改列名
- 旧测试仍可只断言 `DoctorReport.checks` / `overall_status`
- 新增 guidance 的测试单独放在 `test_doctor_remediation.py`
