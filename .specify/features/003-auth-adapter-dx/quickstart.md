# Feature 003 快速上手指南

**Feature**: Auth Adapter + DX 工具
**适用对象**: 开发者（实现本 Feature 的工程师）

---

## 前置条件

- Feature 002（LiteLLM Proxy 集成）已交付并可用
- Python 3.12+ 和 uv 已安装
- 项目 workspace 已同步（`uv sync`）

---

## 1. 实现顺序建议

按照依赖关系，建议的实现顺序：

```
Phase A: 数据模型 + 异常体系
  ├── credentials.py（凭证类型: ApiKey / Token / OAuth）
  ├── profile.py（ProviderProfile）
  ├── masking.py（凭证脱敏工具）
  ├── validators.py（格式校验）
  └── exceptions.py 扩展（CredentialError 体系）

Phase B: 存储层
  ├── store.py（CredentialStore -- 文件读写 + filelock）
  └── EventType 枚举扩展（core/models/enums.py）

Phase C: Adapter 层
  ├── adapter.py（AuthAdapter ABC）
  ├── api_key_adapter.py（ApiKeyAuthAdapter）
  ├── setup_token_adapter.py（SetupTokenAuthAdapter）
  ├── codex_oauth_adapter.py（CodexOAuthAdapter + Device Flow）
  └── chain.py（HandlerChain）

Phase D: DX 工具
  ├── dx/models.py（CheckResult / DoctorReport）
  ├── dx/init_wizard.py（octo init 流程）
  ├── dx/doctor.py（octo doctor 检查器）
  ├── dx/cli.py（click CLI 入口）
  └── dx/dotenv_loader.py（dotenv 加载封装）

Phase E: 集成
  ├── Gateway main.py 集成 dotenv 加载
  ├── __init__.py 更新公开接口
  └── pyproject.toml 更新依赖和 scripts 入口
```

---

## 2. 关键文件路径

```
octoagent/packages/provider/
├── src/octoagent/provider/
│   ├── auth/                    # 新增目录
│   │   ├── __init__.py
│   │   ├── adapter.py          # AuthAdapter ABC
│   │   ├── api_key_adapter.py  # API Key 适配器
│   │   ├── setup_token_adapter.py  # Setup Token 适配器
│   │   ├── codex_oauth_adapter.py  # Codex OAuth 适配器
│   │   ├── chain.py            # Handler Chain
│   │   ├── credentials.py      # 凭证数据模型
│   │   ├── profile.py          # ProviderProfile
│   │   ├── store.py            # Credential Store
│   │   ├── masking.py          # 凭证脱敏
│   │   ├── validators.py       # 格式校验
│   │   ├── events.py           # 凭证事件发射
│   │   └── oauth.py            # Device Flow 实现
│   ├── dx/                      # 新增目录
│   │   ├── __init__.py
│   │   ├── cli.py              # click CLI 入口
│   │   ├── init_wizard.py      # octo init 流程
│   │   ├── doctor.py           # octo doctor 检查器
│   │   ├── dotenv_loader.py    # dotenv 加载封装
│   │   └── models.py           # DX 数据模型
│   └── exceptions.py           # 扩展凭证异常
├── tests/
│   ├── test_credentials.py     # 凭证模型测试
│   ├── test_store.py           # Credential Store 测试
│   ├── test_adapters.py        # AuthAdapter 测试
│   ├── test_chain.py           # Handler Chain 测试
│   ├── test_masking.py         # 脱敏测试
│   ├── test_validators.py      # 校验测试
│   ├── test_doctor.py          # octo doctor 测试
│   └── test_oauth.py           # Device Flow 测试
└── pyproject.toml              # 更新依赖
```

---

## 3. 新增依赖

在 `packages/provider/pyproject.toml` 中新增：

```toml
dependencies = [
    # ... 原有依赖
    "click>=8.1,<9.0",         # CLI 框架
    "rich>=13.0,<14.0",        # 终端格式化输出
    "questionary>=2.0,<3.0",   # 交互式提示（select/confirm/text）
    "python-dotenv>=1.0,<2.0", # .env 自动加载
    "filelock>=3.12,<4.0",     # 跨进程文件锁
]
```

---

## 4. 快速验证步骤

### 4.1 凭证模型验证

```python
from octoagent.provider.auth.credentials import ApiKeyCredential
from pydantic import SecretStr

cred = ApiKeyCredential(provider="openrouter", key=SecretStr("sk-or-v1-test"))
assert cred.type == "api_key"
assert cred.key.get_secret_value() == "sk-or-v1-test"
print(cred.model_dump())  # key 字段显示 "**********"
```

### 4.2 Credential Store 验证

```python
from octoagent.provider.auth.store import CredentialStore

store = CredentialStore()  # 使用默认路径 ~/.octoagent/auth-profiles.json
# 测试时使用 tmp_path fixture 覆盖路径
```

### 4.3 CLI 验证

```bash
# 安装后
uv run octo init
uv run octo doctor
uv run octo doctor --live
```

---

## 5. 测试策略

### 5.1 单元测试（Phase A-C）

- 凭证模型：Pydantic 序列化/反序列化、SecretStr 脱敏、Discriminated Union 反序列化
- CredentialStore：使用 `tmp_path` fixture 隔离文件系统；测试并发写入、文件损坏恢复
- AuthAdapter：Mock credential，验证 resolve/refresh/is_expired 行为
- HandlerChain：Mock store + env，验证优先级解析链
- 凭证脱敏：边界值测试（空字符串、短字符串、正常长度）
- 格式校验：正向和反向用例

### 5.2 集成测试（Phase D-E）

- `octo init`：模拟用户输入（使用 `click.testing.CliRunner`），验证文件生成
- `octo doctor`：模拟各种故障场景，验证诊断输出
- dotenv 加载：验证环境变量优先级

### 5.3 测试风格（对齐现有模式）

```python
# 参考 tests/test_config.py 的风格
class TestApiKeyCredential:
    def test_default_type(self):
        """type 字段默认值"""
        cred = ApiKeyCredential(provider="openai", key=SecretStr("sk-test"))
        assert cred.type == "api_key"

    def test_secret_str_masking(self):
        """SecretStr 序列化脱敏"""
        cred = ApiKeyCredential(provider="openai", key=SecretStr("sk-test"))
        dumped = cred.model_dump()
        assert dumped["key"] == "**********"
```

---

## 6. Constitution 合规清单

实现过程中请持续检查：

- [ ] **C2**: 凭证加载/过期/失败事件写入 Event Store
- [ ] **C5**: 凭证值不出现在日志、事件、LLM 上下文中
- [ ] **C5**: auth-profiles.json 文件权限 0o600
- [ ] **C5**: auth-profiles.json 在 .gitignore 中
- [ ] **C6**: 所有凭证失效时降级到 echo 模式
- [ ] **C7**: `octo init` 交互式确认，`octo doctor` 可视化诊断
- [ ] **C8**: 凭证脱敏，事件仅含元信息
