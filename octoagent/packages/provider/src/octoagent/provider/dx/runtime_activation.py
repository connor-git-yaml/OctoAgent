"""统一的真实模型激活服务。

负责：
1. 加载实例根目录下的 .env / .env.litellm
2. 为 home-instance / repo 开发态定位 docker-compose.litellm.yml
3. 拉起 LiteLLM Proxy 并等待 liveliness 就绪
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config_wizard import load_config
from .dotenv_loader import load_project_dotenv
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
        """解析 docker compose 所在源码目录。"""
        descriptor = self._status_store.load_runtime_descriptor()
        if descriptor is not None:
            candidate = Path(descriptor.project_root).expanduser().resolve()
            if (candidate / "docker-compose.litellm.yml").exists():
                return candidate

        if (self._root / "docker-compose.litellm.yml").exists():
            return self._root

        home_source_root = self._root / "app" / "octoagent"
        if (home_source_root / "docker-compose.litellm.yml").exists():
            return home_source_root

        raise RuntimeActivationError("未找到 docker-compose.litellm.yml，无法启动 LiteLLM Proxy")

    def resolve_proxy_url(self) -> str:
        """解析当前实例期望使用的 LiteLLM Proxy 地址。"""
        config = load_config(self._root)
        if config is not None:
            return config.runtime.litellm_proxy_url
        return os.environ.get("LITELLM_PROXY_URL", "http://localhost:4000")

    def build_compose_up_command(self) -> str:
        """生成可直接复制执行的 LiteLLM Proxy 启动命令。"""
        source_root = self.resolve_source_root()
        compose_file = source_root / "docker-compose.litellm.yml"
        env_file = self._root / ".env.litellm"
        return (
            f'OCTOAGENT_INSTANCE_ROOT="{self._root}" '
            f'docker compose --env-file "{env_file}" '
            f'-f "{compose_file}" up -d --force-recreate'
        )

    async def start_proxy(self, *, timeout_seconds: float = 25.0) -> RuntimeActivationSummary:
        """拉起 LiteLLM Proxy 并等待 liveliness。

        .. deprecated::
            Gateway 主流程已改用 ``ProxyProcessManager`` 管理 Proxy 生命周期。
            本方法仅保留供 CLI 等旧路径使用，后续版本将移除。
        """
        warnings.warn(
            "start_proxy() 已弃用，Gateway 主流程改用 ProxyProcessManager。",
            DeprecationWarning,
            stacklevel=2,
        )
        self.load_runtime_env(override=True)

        env_file = self._root / ".env.litellm"
        if not env_file.exists():
            raise RuntimeActivationError("缺少 .env.litellm，无法启动 LiteLLM Proxy")

        source_root = self.resolve_source_root()
        compose_file = source_root / "docker-compose.litellm.yml"
        await asyncio.to_thread(
            self._compose_up,
            source_root=source_root,
            compose_file=compose_file,
        )

        proxy_url = self.resolve_proxy_url()
        await self._wait_for_proxy(proxy_url, timeout_seconds=timeout_seconds)
        return RuntimeActivationSummary(
            project_root=str(self._root),
            source_root=str(source_root),
            compose_file=str(compose_file),
            proxy_url=proxy_url,
            managed_runtime=self.has_managed_runtime(),
        )

    def _compose_up(self, *, source_root: Path, compose_file: Path) -> None:
        env = os.environ.copy()
        env.setdefault("OCTOAGENT_INSTANCE_ROOT", str(self._root))
        env.setdefault("OCTOAGENT_PROJECT_ROOT", str(self._root))
        env_file = self._root / ".env.litellm"
        command = [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(compose_file),
            "up",
            "-d",
            "--force-recreate",
        ]
        result = subprocess.run(
            command,
            cwd=source_root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip()
            raise RuntimeActivationError(details or "docker compose up -d 执行失败")

    async def _wait_for_proxy(self, proxy_url: str, *, timeout_seconds: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_error = ""
        async with httpx.AsyncClient(timeout=3.0) as client:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    response = await client.get(f"{proxy_url}/health/liveliness")
                    if response.status_code == 200:
                        return
                    last_error = f"status={response.status_code}"
                except Exception as exc:
                    last_error = str(exc)
                await asyncio.sleep(1.0)
        raise RuntimeActivationError(
            f"LiteLLM Proxy 在 {int(timeout_seconds)} 秒内未就绪：{last_error or proxy_url}"
        )
