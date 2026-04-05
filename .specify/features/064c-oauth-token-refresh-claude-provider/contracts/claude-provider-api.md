# Contract: Claude 订阅 Provider API

**Feature**: 064-oauth-token-refresh-claude-provider
**Date**: 2026-03-19
**对齐需求**: FR-008, FR-009, FR-010

---

## SS1: CLI paste-token 命令契约

### 命令签名

```
octo auth paste-token --provider anthropic-claude
```

### 交互流程

```
$ octo auth paste-token --provider anthropic-claude

Claude 订阅凭证导入
====================

注意: 此功能使用 Claude Code CLI 的 setup-token 机制，
属于"技术兼容性"范畴，非 Anthropic 官方支持的用法。
Anthropic 可能在未来限制此类使用。建议同时配置 API Key 作为备选。

请按以下步骤操作:
1. 确保已安装 Claude Code CLI
2. 运行: claude setup-token
3. 将输出的 token 粘贴到下方

粘贴 access_token (sk-ant-oat01-...):
> [用户输入]

粘贴 refresh_token (sk-ant-ort01-...):
> [用户输入]

验证中...
凭证已保存为 Claude (Subscription) profile。
access_token 有效期约 8 小时，系统将自动刷新。
```

### 输入验证

```python
def validate_claude_setup_token(
    access_token: str,
    refresh_token: str,
) -> tuple[bool, str]:
    """验证 Claude setup-token 格式

    Args:
        access_token: access token 字符串
        refresh_token: refresh token 字符串

    Returns:
        (is_valid, error_message)
    """
    if not access_token.startswith("sk-ant-oat01-"):
        return False, "access_token 格式无效（应以 sk-ant-oat01- 开头）"
    if not refresh_token.startswith("sk-ant-ort01-"):
        return False, "refresh_token 格式无效（应以 sk-ant-ort01- 开头）"
    if len(access_token) < 20:
        return False, "access_token 长度不足"
    if len(refresh_token) < 20:
        return False, "refresh_token 长度不足"
    return True, ""
```

### 存储行为

导入成功后，凭证存储为 `OAuthCredential` 类型的 Profile：

```python
profile = ProviderProfile(
    name="anthropic-claude-default",
    provider="anthropic-claude",
    credential=OAuthCredential(
        provider="anthropic-claude",
        access_token=SecretStr(access_token),
        refresh_token=SecretStr(refresh_token),
        expires_at=datetime.now(tz=UTC) + timedelta(seconds=28800),  # 8h
        account_id=None,  # Claude token 不是 JWT
    ),
    is_default=False,  # 不自动设为默认 Provider
)
store.set_profile(profile)
```

### 对齐需求

- FR-008: 通过 CLI 导入 setup-token，存储为 OAuthCredential
- FR-010: 导入时展示政策风险提示

---

## SS2: Claude Provider 刷新适配

### 刷新端点

- **URL**: `https://console.anthropic.com/api/oauth/token`
- **Method**: `POST`
- **Content-Type**: `application/x-www-form-urlencoded`

### 刷新请求

```
grant_type=refresh_token
refresh_token=sk-ant-ort01-...
client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e
```

### 刷新响应

```json
{
  "access_token": "sk-ant-oat01-NEW...",
  "refresh_token": "sk-ant-ort01-NEW...",
  "token_type": "Bearer",
  "expires_in": 28800
}
```

### 适配说明

`refresh_access_token()` 函数无需修改即可处理此响应：
- `access_token` 提取 -> 成功
- `extract_account_id_from_jwt()` -> 返回 `None`（非 JWT 格式，可接受）
- `refresh_token` 更新 -> `data.get("refresh_token", old_refresh_token)`
- `expires_in` -> 28800（8 小时）

---

## SS3: Anthropic 政策拒绝处理

### 错误场景

当 Anthropic 拒绝 setup-token 用于非 Claude Code 应用时，可能返回：

```
HTTP 403
{
  "error": {
    "type": "permission_error",
    "message": "This credential is only authorized for use with Claude Code and cannot be used for other API requests."
  }
}
```

### 处理行为

1. `LiteLLMClient.complete()` 捕获 403 响应
2. 触发 refresh-then-retry 逻辑（一次）
3. 如果 retry 后仍为 403，构建用户友好的错误消息：

```python
error_message = (
    "Claude 订阅凭证被 Anthropic 拒绝。\n"
    "此凭证可能仅授权用于 Claude Code 应用，不支持第三方调用。\n"
    "建议: 使用 Anthropic API Key 替代订阅凭证。\n"
    "配置方法: octo auth setup -> 选择 Anthropic -> 输入 API Key"
)
```

### 对齐需求

- FR-010: 向用户展示清晰的错误提示并建议 API Key 替代

---

## SS4: Claude Provider LiteLLM 配置集成

### 直连模式（推荐）

Claude 订阅走直连模式，不经过 LiteLLM Proxy：

```python
# 在 Kernel 调用 LLM 时
chain_result = await handler_chain.resolve(provider="anthropic-claude")

await litellm_client.complete(
    messages=messages,
    model_alias="anthropic/claude-sonnet-4-5",  # LiteLLM Anthropic provider 格式
    api_base=None,  # 使用 LiteLLM SDK 内置的 Anthropic API 端点
    api_key=chain_result.credential_value,  # setup-token access_token
    # extra_headers 为空（Claude 不需要额外 headers）
)
```

### 与 Codex 路径的差异

| 维度 | OpenAI Codex | Claude 订阅 |
|------|-------------|------------|
| API 路径 | `chatgpt.com/backend-api/codex` (Responses API) | 标准 Anthropic API |
| 认证方式 | JWT Bearer token | `sk-ant-oat01-*` Bearer token |
| 额外 headers | `chatgpt-account-id` 等 | 无 |
| LiteLLM model 格式 | 自定义 alias | `anthropic/claude-*` |
| `api_base_url` | 必须覆盖 | `None`（使用默认） |
| `account_id` | 从 JWT 提取 | `None` |
