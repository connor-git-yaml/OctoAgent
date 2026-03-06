# Contract: Channel Onboarding Verifier

**Feature**: `015-octo-onboard-doctor`
**Created**: 2026-03-07
**Traces to**: FR-007, FR-008, FR-009, FR-015

---

## 契约范围

本文定义 015 与 016 的并行边界。015 只提供 verifier protocol、registry 和缺位 fallback；任何 Telegram pairing / ingress / session routing 细节都不属于本契约。

---

## 1. Protocol

```python
class ChannelOnboardingVerifier(Protocol):
    channel_id: str
    display_name: str

    def availability(self, project_root: Path) -> VerifierAvailability:
        ...

    async def run_readiness(
        self,
        project_root: Path,
        session: OnboardingSession,
    ) -> ChannelStepResult:
        ...

    async def verify_first_message(
        self,
        project_root: Path,
        session: OnboardingSession,
    ) -> ChannelStepResult:
        ...
```

---

## 2. 行为语义

### `availability()`

职责：
- 判断 verifier 是否已注册且依赖已满足
- 不做实际网络/消息发送
- 不得抛出“未安装实现”这类原始异常

返回规则：
- `available=True`：允许进入 readiness
- `available=False`：必须携带至少一条 remediation action

### `run_readiness()`

职责：
- 检查 channel 基础前置条件是否满足
- 例如：必要配置已就绪、plugin 已安装、凭证存在、pairing 入口可用

返回规则：
- `status=COMPLETED`：允许进入 `verify_first_message()`
- `status=ACTION_REQUIRED/BLOCKED`：必须带 remediation

### `verify_first_message()`

职责：
- 执行“首条消息闭环已成功”的验证
- 可依赖外部轮询/回包确认，但超时和失败必须结构化返回

返回规则：
- `status=COMPLETED`：onboarding 可进入 `READY`
- `status=ACTION_REQUIRED/BLOCKED`：summary 必须明确缺什么，不能误报成功

---

## 3. Registry

```python
class ChannelVerifierRegistry:
    def register(self, verifier: ChannelOnboardingVerifier) -> None: ...
    def get(self, channel_id: str) -> ChannelOnboardingVerifier | None: ...
    def list_ids(self) -> list[str]: ...
```

### 缺位 fallback

当 `get(channel_id)` 返回 `None` 时，015 必须生成如下语义的 blocked action：
- `action_type="blocked_dependency"`
- 说明当前 channel verifier 尚未注册或对应 Feature 未交付
- 提示重新运行 `octo onboard --channel <id>`

---

## 4. 015 / 016 边界

### 015 In Scope

- protocol
- registry
- fake verifier 测试桩
- unavailable/missing verifier 的 blocked summary

### 016 In Scope

- Telegram verifier 的真实实现
- pairing token / QR / allowlist / routing 细节
- readiness / first-message 的真实网络交互

### 明确禁止

015 不得在 contract 中引入以下专属字段：
- Telegram bot token 持久化 schema
- pairing session state machine
- thread routing metadata
- webhook / polling transport 细节
