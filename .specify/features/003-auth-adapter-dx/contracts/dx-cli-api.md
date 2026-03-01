# DX CLI 工具 API 契约

**Feature**: 003 - Auth Adapter + DX 工具
**Date**: 2026-03-01
**Status**: Draft
**对齐**: Blueprint SS12.9 + spec FR-007 ~ FR-009

---

## SS1. CLI 入口点

```toml
# packages/provider/pyproject.toml 新增
[project.scripts]
octo = "octoagent.provider.dx.cli:main"
```

```python
# packages/provider/src/octoagent/provider/dx/cli.py

import click

@click.group()
def main() -> None:
    """OctoAgent CLI 工具"""

@main.command()
def init() -> None:
    """交互式引导配置 -- FR-007"""

@main.command()
@click.option("--live", is_flag=True, help="发送真实 LLM 调用验证端到端连通性")
def doctor(live: bool) -> None:
    """环境诊断 -- FR-008"""
```

---

## SS2. `octo init` 流程契约

### 2.1 流程步骤

```
步骤 1: 检测运行模式
  输入: 用户选择 echo / litellm
  输出: OCTOAGENT_LLM_MODE 值

步骤 2: Provider 选择
  输入: 用户选择 Provider（OpenRouter / OpenAI / Anthropic）
  输出: provider 标识

步骤 3: 认证模式选择
  输入: 根据 Provider 列出可用认证模式（API Key / Setup Token / OAuth）
  输出: auth_mode 值

步骤 4: 凭证输入/获取
  - API Key: 文本输入 + 格式校验
  - Setup Token: 文本输入 + 前缀校验（sk-ant-oat01-）
  - Codex OAuth: 触发 Device Flow -> 浏览器授权 -> 轮询获取 token

步骤 5: 凭证存储
  输入: Credential 对象
  输出: 写入 ~/.octoagent/auth-profiles.json

步骤 6: Master Key 生成
  输出: 随机 LITELLM_MASTER_KEY（32 字节 hex）

步骤 7: Docker 检测
  输出: Docker 可用性状态

步骤 8: 配置文件生成
  输出: .env / .env.litellm / litellm-config.yaml

步骤 9: 输出摘要
  输出: 生成文件列表 + 下一步操作提示
```

### 2.2 函数签名

```python
# packages/provider/src/octoagent/provider/dx/init_wizard.py

class InitConfig(BaseModel):
    """init 配置结果"""
    llm_mode: Literal["echo", "litellm"]
    provider: str
    auth_mode: Literal["api_key", "token", "oauth"]
    credential: Credential
    master_key: str
    docker_available: bool

async def run_init_wizard() -> InitConfig:
    """执行交互式引导配置

    流程:
    1. 检测运行模式（echo/litellm）
    2. 选择 Provider
    3. 选择认证模式
    4. 输入/获取凭证
    5. 生成 Master Key
    6. 检测 Docker
    7. 返回配置结果

    Returns:
        InitConfig 实例
    """

def generate_env_file(config: InitConfig, project_root: Path) -> Path:
    """生成 .env 文件

    Args:
        config: init 配置结果
        project_root: 项目根目录

    Returns:
        生成的 .env 文件路径
    """

def generate_env_litellm_file(config: InitConfig, project_root: Path) -> Path:
    """生成 .env.litellm 文件"""

def generate_litellm_config(config: InitConfig, project_root: Path) -> Path:
    """生成 litellm-config.yaml 文件"""
```

### 2.3 中断恢复（EC-3）

```python
def detect_partial_init(project_root: Path) -> bool:
    """检测是否存在半成品配置

    检查 .env 是否存在但 .env.litellm 不存在等不一致状态。
    """

def prompt_overwrite() -> bool:
    """提示用户是否覆盖已有配置"""
```

---

## SS3. `octo doctor` 流程契约

### 3.1 检查项列表

| 检查项 | 级别 | 检查内容 | 修复建议 |
|--------|------|----------|----------|
| python_version | REQUIRED | Python >= 3.12 | 安装 Python 3.12+ |
| uv_installed | REQUIRED | `uv` 命令可用 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| env_file | REQUIRED | `.env` 文件存在 | `octo init` |
| env_litellm_file | RECOMMENDED | `.env.litellm` 文件存在 | `octo init` |
| llm_mode | REQUIRED | `OCTOAGENT_LLM_MODE` 有值 | 检查 `.env` |
| proxy_key | RECOMMENDED | `LITELLM_PROXY_KEY` 非空 | 检查 `.env` |
| master_key_match | RECOMMENDED | `LITELLM_MASTER_KEY == LITELLM_PROXY_KEY` | 重新运行 `octo init` |
| docker_running | RECOMMENDED | Docker daemon 运行中 | 启动 Docker Desktop |
| proxy_reachable | RECOMMENDED | LiteLLM Proxy `/health/liveliness` 返回 200 | `docker compose -f docker-compose.litellm.yml up -d` |
| db_writable | REQUIRED | SQLite DB 可写 | 检查 `data/` 目录权限 |
| credential_valid | RECOMMENDED | credential store 中有有效凭证 | `octo init` |
| credential_expiry | RECOMMENDED | Token 类凭证未过期 | 重新获取 Token |
| live_ping | RECOMMENDED | cheap 模型调用成功（仅 --live） | 检查 Provider 可达性和凭证有效性 |

### 3.2 函数签名

```python
# packages/provider/src/octoagent/provider/dx/doctor.py

class DoctorRunner:
    """诊断运行器"""

    def __init__(self, project_root: Path) -> None: ...

    async def run_all_checks(self, live: bool = False) -> DoctorReport:
        """执行所有检查项

        Args:
            live: 是否执行 --live 检查（真实 LLM 调用）

        Returns:
            DoctorReport 实例
        """

    async def check_python_version(self) -> CheckResult: ...
    async def check_uv_installed(self) -> CheckResult: ...
    async def check_env_file(self) -> CheckResult: ...
    async def check_env_litellm_file(self) -> CheckResult: ...
    async def check_llm_mode(self) -> CheckResult: ...
    async def check_proxy_key(self) -> CheckResult: ...
    async def check_master_key_match(self) -> CheckResult: ...
    async def check_docker_running(self) -> CheckResult: ...
    async def check_proxy_reachable(self) -> CheckResult: ...
    async def check_db_writable(self) -> CheckResult: ...
    async def check_credential_valid(self) -> CheckResult: ...
    async def check_credential_expiry(self) -> CheckResult: ...
    async def check_live_ping(self) -> CheckResult: ...

def format_report(report: DoctorReport) -> str:
    """格式化诊断报告为终端输出

    使用 rich 格式化：
    - PASS: 绿色 checkmark
    - WARN: 黄色 warning
    - FAIL: 红色 cross
    """
```

---

## SS4. dotenv 自动加载契约

```python
# apps/gateway/src/octoagent/gateway/main.py（修改）

from dotenv import load_dotenv

def create_app() -> FastAPI:
    # 自动加载 .env（不覆盖已设置的环境变量）
    load_dotenv(override=False)
    # ... 原有代码
```

**行为规范**:
- `override=False`: 已设置的环境变量优先级 > `.env` 文件值
- `.env` 不存在时静默跳过
- `.env` 语法错误时记录 warning 日志，不阻塞启动（EC-7）
