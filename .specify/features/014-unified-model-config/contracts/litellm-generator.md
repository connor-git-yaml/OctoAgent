# Contract: LiteLLM 配置生成器接口

**Feature**: 014-unified-model-config
**Created**: 2026-03-04
**Traces to**: FR-005, FR-006, FR-007, NFR-001, NFR-003, EC-2, EC-3, EC-4

---

## 契约范围

本文档定义 `litellm_generator.py` 模块的公共接口契约，包括：
- 函数签名与语义
- 生成的 `litellm-config.yaml` 格式规范
- `.env.litellm` 更新规范
- 同步状态检测接口（供 `octo doctor` 调用）

---

## 1. `generate_litellm_config`

### 签名

```python
def generate_litellm_config(
    config: OctoAgentConfig,
    project_root: Path,
) -> Path:
    """从 OctoAgentConfig 推导并原子写入 litellm-config.yaml。

    前置校验（任一失败则抛出异常，现有文件保持不变）：
    - OctoAgentConfig schema 完整（已由调用方 load_config 保证）
    - 至少有 1 个 enabled Provider
    - 至少有 1 个 model_alias 且其 provider 为 enabled 状态

    写入流程：
    1. 生成 YAML 内容字符串
    2. 若目标文件存在且不含标记注释，打印 WARN（EC-3）
    3. 写入临时文件（同目录 litellm-config.yaml.tmp）
    4. os.replace(tmp, target)（原子替换，NFR-003）
    5. 返回写入路径

    Args:
        config: 已校验的 OctoAgentConfig 实例
        project_root: 项目根目录（litellm-config.yaml 写入此目录）

    Returns:
        写入的文件绝对路径

    Raises:
        LiteLLMGeneratorError: 无 enabled Provider 或无 enabled alias 时
        OSError: 文件写入失败时
    """
```

### 生成的 `litellm-config.yaml` 格式规范

**头部标记**（机器生成标识，供 EC-3 检测使用）:
```yaml
# 由 octo config sync 自动生成，请勿手动修改
# 数据源: octoagent.yaml
# 生成时间: 2026-03-04T12:00:00
```

**model_list 生成规则**:
- 仅包含 `model_aliases` 中 `provider` 对应 `ProviderEntry.enabled=True` 的条目
- `model_name`: alias key（如 `main`、`cheap`）
- `litellm_params.model`: `ModelAlias.model`（如 `openrouter/auto`）
- `litellm_params.api_key`: `os.environ/{ProviderEntry.api_key_env}`

**general_settings**:
- `master_key`: `os.environ/{RuntimeConfig.master_key_env}`

**示例输出**（对应 `octoagent.yaml` 示例配置）:
```yaml
# 由 octo config sync 自动生成，请勿手动修改
# 数据源: octoagent.yaml
# 生成时间: 2026-03-04T12:00:00
model_list:
  - model_name: "main"
    litellm_params:
      model: "openrouter/auto"
      api_key: "os.environ/OPENROUTER_API_KEY"

  - model_name: "cheap"
    litellm_params:
      model: "openrouter/auto"
      api_key: "os.environ/OPENROUTER_API_KEY"

general_settings:
  master_key: "os.environ/LITELLM_MASTER_KEY"
```

**多 Provider 场景**（main → anthropic，cheap → openrouter）:
```yaml
model_list:
  - model_name: "main"
    litellm_params:
      model: "claude-opus-4-20250514"
      api_key: "os.environ/ANTHROPIC_API_KEY"

  - model_name: "cheap"
    litellm_params:
      model: "openrouter/auto"
      api_key: "os.environ/OPENROUTER_API_KEY"

general_settings:
  master_key: "os.environ/LITELLM_MASTER_KEY"
```

---

## 2. `generate_env_litellm`

### 签名

```python
def generate_env_litellm(
    provider_id: str,
    api_key: str,
    env_var_name: str,
    project_root: Path,
) -> Path:
    """追加或更新 .env.litellm 中指定 Provider 的 API Key 条目。

    行为：
    - 若 .env.litellm 不存在：创建（含头部注释）
    - 若 env_var_name 已存在于文件中：更新该行
    - 若 env_var_name 不存在：追加新行
    - 所有操作使用原子写入（tmp + os.replace）

    安全：
    - api_key 参数在此函数内接触明文，不得传递到 OctoAgentConfig
    - 函数不打印 api_key 内容（避免日志泄露）

    Args:
        provider_id: Provider ID（仅用于注释，如 'openrouter'）
        api_key: API Key 明文
        env_var_name: 环境变量名（如 'OPENROUTER_API_KEY'）
        project_root: 项目根目录

    Returns:
        写入的 .env.litellm 文件绝对路径

    Raises:
        CredentialLeakError: api_key 为空时（防止写入空值）
        OSError: 文件写入失败时
    """
```

### 生成的 `.env.litellm` 格式

```bash
# LiteLLM Proxy 凭证配置（由 octo config 自动管理）
# 此文件包含明文凭证，请勿纳入版本管理（已在 .gitignore）
LITELLM_MASTER_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**更新语义**（"追加或更新"实现逻辑）:
```
读取现有 .env.litellm 内容（不存在则视为空）
遍历行，若发现 `{env_var_name}=` 前缀的行，替换该行
若无匹配行，在末尾追加 `{env_var_name}={api_key}`
写入临时文件 → os.replace
```

---

## 3. `check_litellm_sync_status`

### 签名

```python
def check_litellm_sync_status(
    config: OctoAgentConfig,
    project_root: Path,
) -> tuple[bool, list[str]]:
    """检查 octoagent.yaml 与 litellm-config.yaml 是否一致。

    用途：供 octo doctor 的新检查项调用（FR-013，EC-4）。
    不修改任何文件，纯读取+比对操作。

    检查项：
    1. litellm-config.yaml 是否存在
    2. litellm-config.yaml 是否含机器生成标记（EC-3）
    3. model_list 中的 model_name 集合是否与有效 aliases 一致
    4. 每个 model_list 条目的 model 和 api_key 是否与 octoagent.yaml 对应

    Args:
        config: 已加载的 OctoAgentConfig
        project_root: 项目根目录

    Returns:
        (is_in_sync, diff_messages)
        - is_in_sync: True 表示一致
        - diff_messages: 差异描述列表（空列表表示一致）
    """
```

### 使用示例（doctor.py 中）

```python
async def check_litellm_sync(self) -> CheckResult:
    """octoagent.yaml 与 litellm-config.yaml 一致性检查"""
    config = load_config(self._root)
    if config is None:
        return CheckResult(
            name="litellm_sync",
            status=CheckStatus.SKIP,
            level=CheckLevel.RECOMMENDED,
            message="octoagent.yaml 不存在，跳过同步检查",
        )

    is_in_sync, diffs = check_litellm_sync_status(config, self._root)
    if is_in_sync:
        return CheckResult(
            name="litellm_sync",
            status=CheckStatus.PASS,
            level=CheckLevel.RECOMMENDED,
            message="octoagent.yaml 与 litellm-config.yaml 一致",
        )
    return CheckResult(
        name="litellm_sync",
        status=CheckStatus.WARN,
        level=CheckLevel.RECOMMENDED,
        message=f"配置不一致: {'; '.join(diffs)}",
        fix_hint="运行 octo config sync 同步配置",
    )
```

---

## 4. 异常类型

```python
class LiteLLMGeneratorError(RuntimeError):
    """litellm-config.yaml 生成失败

    场景：
    - 无 enabled Provider（无法生成有效 model_list）
    - 所有 model_aliases 的 provider 均为 disabled 状态
    """
```

---

## 5. 性能约束（NFR-001）

所有函数在正常路径下 MUST 在 1 秒内完成（本地文件操作，无网络调用）。

`check_litellm_sync_status` 是纯内存对比操作，不涉及网络，SHOULD 在 100ms 内完成。

---

## 6. 原子写入实现规范（NFR-003）

所有写文件操作 MUST 使用以下模式：

```python
import os
import tempfile
from pathlib import Path

def atomic_write(content: str, target: Path) -> None:
    """原子写入：先写临时文件，再 os.replace"""
    # 临时文件与目标文件在同一目录（保证同分区，os.replace 原子性依赖此条件）
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(str(tmp_path), str(target))
    except Exception:
        # 清理临时文件
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
```

**POSIX 保证**: `os.replace` 在同文件系统内为原子操作，写入中断不产生部分内容文件。
