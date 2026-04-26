"""统一的真实模型激活服务。

历史职责（Feature 080 之前）：
1. 加载实例根目录下的 .env / .env.litellm
2. 为 home-instance / repo 开发态定位 docker-compose.litellm.yml
3. 拉起 LiteLLM Proxy 并等待 liveliness 就绪

Feature 081 P3 退役：
- LiteLLM Proxy 已不再启动 → start_proxy() / build_compose_up_command() 全部
  改成返回 deprecation 提示（不再实际操作 docker compose）
- resolve_source_root() 改成 best-effort，缺失 docker-compose.litellm.yml 不抛错
  返回当前 instance root 即可（doctor / init_wizard 仍会调用，但展示文案改写）
- load_runtime_env() 保留，仍读取 .env.litellm（兼容窗口至 P4）
- 文件本身在 P4 拆分：保留 load_runtime_env 部分（重命名后），移除 docker-compose 部分
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path

# Feature 081 P3：移除 asyncio / subprocess / httpx / load_config 等 imports，
# 它们曾用于实际启动 LiteLLM Proxy + 等待 ready，本 Phase 后这些功能已退役。
from octoagent.gateway.services.config.dotenv_loader import load_project_dotenv
from .update_status_store import UpdateStatusStore


class RuntimeActivationError(RuntimeError):
    """真实模型激活失败。"""


@dataclass(slots=True)
class RuntimeActivationSummary:
    """激活结果摘要。"""

    project_root: str
    source_root: str
    compose_file: str
    proxy_url: str
    managed_runtime: bool
    warnings: list[str] = field(default_factory=list)


class RuntimeActivationService:
    """统一处理 LiteLLM Proxy 启动与就绪等待。"""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root.expanduser().resolve()
        self._status_store = UpdateStatusStore(self._root)

    def has_managed_runtime(self) -> bool:
        """当前实例是否存在托管 runtime 描述符。"""
        return self._status_store.load_runtime_descriptor() is not None

    def load_runtime_env(self, *, override: bool = True) -> None:
        """把实例根目录的运行时 env 注入当前进程。"""
        load_project_dotenv(self._root, override=override)
        env_litellm_path = self._root / ".env.litellm"
        if not env_litellm_path.exists():
            return
        try:
            from dotenv import load_dotenv
        except Exception as exc:  # pragma: no cover - 依赖缺失走安全降级
            raise RuntimeActivationError(
                "当前环境缺少 python-dotenv，无法加载 .env.litellm"
            ) from exc
        load_dotenv(dotenv_path=str(env_litellm_path), override=override)

    def resolve_source_root(self) -> Path:
        """解析当前实例的 source root。

        Feature 081 P3：不再要求 docker-compose.litellm.yml 存在。
        Provider 直连后没有 LiteLLM 容器需要管理，source root 仅用于其他诊断展示。
        """
        descriptor = self._status_store.load_runtime_descriptor()
        if descriptor is not None:
            candidate = Path(descriptor.project_root).expanduser().resolve()
            if candidate.exists():
                return candidate

        if self._root.exists():
            return self._root

        home_source_root = self._root / "app" / "octoagent"
        if home_source_root.exists():
            return home_source_root

        return self._root  # 兜底：返回 instance root 本身

    def resolve_proxy_url(self) -> str:
        """Feature 081 P3：保留接口，仅返回 deprecation 标记字符串。

        Provider 直连后已无 Proxy URL 概念。本方法仅供 doctor / init_wizard 等
        老路径继续调用不挂；返回值不应被用于实际 HTTP 调用。
        """
        return ""

    def build_compose_up_command(self) -> str:
        """Feature 081 P3：返回 deprecation 提示而非实际 compose 命令。

        Provider 直连后无需启动 LiteLLM 容器；调用方（doctor / init_wizard）
        展示此字符串时实际是给用户看 retire 状态。
        """
        return (
            "# Feature 081：LiteLLM Proxy 已退役，无需启动。"
            " 请运行 `octo config migrate-080` 升级配置；运行时已统一走 ProviderRouter 直连。"
        )

    async def start_proxy(self, *, timeout_seconds: float = 25.0) -> RuntimeActivationSummary:
        """Feature 081 P3：禁用启动逻辑，直接抛 deprecation 错误。

        Provider 直连后无 Proxy 需要启动；任何调用都说明上游路径需要更新。
        P4 中本方法会被整体删除。
        """
        warnings.warn(
            "RuntimeActivationService.start_proxy() 已退役（Feature 081 P3）。"
            " Provider 直连后 LiteLLM Proxy 不再启动；请移除调用方对该方法的依赖。",
            DeprecationWarning,
            stacklevel=2,
        )
        raise RuntimeActivationError(
            "LiteLLM Proxy 已退役（Feature 081）；运行时统一走 ProviderRouter 直连，"
            " 不再有 Proxy 子进程需要启动。请运行 `octo config migrate-080` 升级。"
        )
