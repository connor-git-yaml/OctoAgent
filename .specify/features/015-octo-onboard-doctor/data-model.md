# Data Model: Feature 015 — Octo Onboard + Doctor Guided Remediation

**Feature**: `015-octo-onboard-doctor`
**Created**: 2026-03-07
**Source**: `spec.md` FR-001 ~ FR-015，Key Entities 节

---

## 实体总览

| 实体 | 对应模型 | 持久化位置 | 说明 |
|---|---|---|---|
| Onboarding Session | `OnboardingSession` | `data/onboarding-session.json` | 项目级 onboarding 恢复状态 |
| Onboarding Step State | `OnboardingStepState` | `OnboardingSession.steps{}` | 单个阶段的状态、摘要和下一步动作 |
| Next Action | `NextAction` | `OnboardingStepState.actions[]` / `DoctorGuidance.groups[]` | 面向用户的可执行动作 |
| Doctor Guidance | `DoctorGuidance` | 运行时结果 | doctor 和 onboard 共享的 remediation 结构 |
| Channel Step Result | `ChannelStepResult` | 运行时结果 / session step snapshot | verifier 返回的 readiness / first-message 结果 |
| Onboarding Summary | `OnboardingSummary` | `OnboardingSession.summary` | 统一输出 `READY` / `ACTION_REQUIRED` / `BLOCKED` |

---

## 1. OnboardingStep / OnboardingStepStatus

```python
class OnboardingStep(StrEnum):
    PROVIDER_RUNTIME = "provider_runtime"
    DOCTOR_LIVE = "doctor_live"
    CHANNEL_READINESS = "channel_readiness"
    FIRST_MESSAGE = "first_message"


class OnboardingStepStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    ACTION_REQUIRED = "action_required"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
```

**约束**:
- 执行顺序固定：`provider_runtime -> doctor_live -> channel_readiness -> first_message`
- `completed` 之后重复运行允许重新校验，但不得在无确认情况下回退到 `pending`
- `blocked` 优先级高于 `action_required`

---

## 2. NextAction — 下一步动作

```python
class NextAction(BaseModel):
    action_id: str = Field(min_length=1)
    action_type: Literal[
        "command",
        "manual",
        "config",
        "retry",
        "blocked_dependency",
    ]
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    command: str = ""
    manual_steps: list[str] = Field(default_factory=list)
    blocking: bool = True
    sort_order: int = 100
```

**设计说明**:
- `command`：推荐直接执行的 CLI 命令，如 `octo config sync`
- `manual`：需要用户手工完成的动作，如“启动 Docker Desktop”
- `config`：提示补齐/修正配置，但不一定是单条命令
- `retry`：表示外部依赖修复后可重新运行 `octo onboard`
- `blocked_dependency`：表示缺少当前 Feature 不拥有的实现，如 verifier 未注册

**约束**:
- `blocking=True` 的 action 必须出现在最终摘要的“优先处理”区域
- `action_type="command"` 时，`command` 不得为空
- `manual_steps` 仅用于 `manual` / `blocked_dependency`

---

## 3. OnboardingStepState — 单阶段状态

```python
class OnboardingStepState(BaseModel):
    step: OnboardingStep
    status: OnboardingStepStatus = OnboardingStepStatus.PENDING
    summary: str = ""
    actions: list[NextAction] = Field(default_factory=list)
    last_checked_at: datetime | None = None
    completed_at: datetime | None = None
    detail_ref: str | None = None
```

**说明**:
- `summary` 用于终端展示短句，例如“provider 配置已存在，跳过初始化”
- `detail_ref` 预留给未来 artifact/log 引用；015 可为空
- `actions` 允许一个步骤给出多个动作，但最终摘要只优先展示 blocking action

---

## 4. DoctorRemediation / DoctorGuidance

```python
class DoctorRemediation(BaseModel):
    check_name: str
    stage: Literal["system", "config", "connectivity"]
    severity: Literal["blocking", "warning"]
    reason: str
    action: NextAction


class DoctorGuidanceGroup(BaseModel):
    stage: Literal["system", "config", "connectivity"]
    title: str
    items: list[DoctorRemediation] = Field(default_factory=list)


class DoctorGuidance(BaseModel):
    overall_status: Literal["ready", "action_required", "blocked"]
    groups: list[DoctorGuidanceGroup] = Field(default_factory=list)
    blocking_actions: list[NextAction] = Field(default_factory=list)
    generated_at: datetime
```

**阻塞判定规则**:
- `CheckLevel.REQUIRED + FAIL` => `severity="blocking"`
- `live_ping FAIL` => 视为 `blocking`，因为 FR-006 要求 channel 步骤前必须完成 live 验证
- 其他 `WARN` => `severity="warning"`

**分组规则**:
- `system`: `python_version` / `uv_installed` / `docker_running` / `db_writable`
- `config`: `env_file` / `env_litellm_file` / `llm_mode` / `octoagent_yaml_valid` / `litellm_sync`
- `connectivity`: `proxy_reachable` / `credential_valid` / `credential_expiry` / `live_ping`

---

## 5. ChannelStepResult / VerifierAvailability

```python
class VerifierAvailability(BaseModel):
    available: bool
    reason: str = ""
    actions: list[NextAction] = Field(default_factory=list)


class ChannelStepResult(BaseModel):
    channel_id: str
    step: Literal["channel_readiness", "first_message"]
    status: OnboardingStepStatus
    summary: str
    actions: list[NextAction] = Field(default_factory=list)
    checked_at: datetime
```

**约束**:
- `availability.available=False` 时，`actions` 至少有一条 `blocked_dependency` 或 `manual` 动作
- `ChannelStepResult.status=COMPLETED` 时，不应再附带 `blocking=True` action
- `first_message` 不得在 `channel_readiness` 未完成时执行

---

## 6. OnboardingSummary — 最终状态摘要

```python
class OnboardingOverallStatus(StrEnum):
    READY = "READY"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    BLOCKED = "BLOCKED"


class OnboardingSummary(BaseModel):
    overall_status: OnboardingOverallStatus
    headline: str
    completed_steps: list[OnboardingStep] = Field(default_factory=list)
    pending_steps: list[OnboardingStep] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    generated_at: datetime
```

**优先级规则**:
1. 任一步骤为 `BLOCKED` => summary 为 `BLOCKED`
2. 否则只要存在 `ACTION_REQUIRED` 或 `PENDING` => summary 为 `ACTION_REQUIRED`
3. 四个步骤全 `COMPLETED` => summary 为 `READY`

---

## 7. OnboardingSession — 项目级恢复状态

```python
class OnboardingSession(BaseModel):
    session_version: int = 1
    project_root: str
    selected_channel: str = "telegram"
    current_step: OnboardingStep = OnboardingStep.PROVIDER_RUNTIME
    steps: dict[OnboardingStep, OnboardingStepState]
    last_remediations: list[DoctorRemediation] = Field(default_factory=list)
    summary: OnboardingSummary
    updated_at: datetime
```

**生命周期**:
- 第一次运行 `octo onboard` 时创建
- 每个阶段结束后更新 `current_step` / `steps` / `summary`
- `--restart` 时归档旧 session 并重建新 session
- 项目已经 `READY` 时继续保留，用于非破坏性重跑摘要

**约束**:
- `steps` 必须包含四个固定 key
- `current_step` 必须指向第一个非 `COMPLETED` 步骤；若全部完成，则保持 `FIRST_MESSAGE`
- `session_version` 不兼容时，store 层需备份旧文件并创建新 session

---

## 8. 存储格式与损坏恢复

- 文件路径：`project_root / data / onboarding-session.json`
- 写入方式：临时文件 + `os.replace()` 原子替换
- 并发保护：`filelock`
- 损坏恢复：
  - JSON 解析失败或 schema 校验失败时，备份为 `onboarding-session.json.corrupted`
  - 返回 `None`，由 `OnboardingService` 创建新 session，并在摘要里追加一条 warning action

---

## 9. 与 Feature 016 的边界

015 只定义以下稳定接口，不实现具体 Telegram 行为：
- `ChannelOnboardingVerifier`
- `VerifierAvailability`
- `ChannelStepResult`
- registry 注册与缺位 fallback

016 负责提供具体 adapter，并满足本文件的 contract；015 不在数据模型中引入 Telegram pairing token、thread routing、allowlist 等专属字段。
