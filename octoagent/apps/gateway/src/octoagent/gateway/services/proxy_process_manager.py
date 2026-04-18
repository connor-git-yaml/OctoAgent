"""LiteLLM Proxy 进程生命周期管理。

负责启动、停止、重启和健康检查 LiteLLM Proxy，
支持 Docker Compose 和直接进程两种运行模式，遵循降级安全原则。
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog
from octoagent.provider.dx.docker_daemon import ensure_docker_daemon

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_HEALTH_ENDPOINT = "/health/liveliness"
_HEALTH_TIMEOUT_S = 5.0
_POLL_INTERVAL_S = 1.0
_STOP_GRACE_S = 5.0

_COMPOSE_FILENAME = "docker-compose.litellm.yml"
_DEFAULT_CONFIG_NAME = "litellm-config.yaml"
_DEFAULT_ENV_NAME = ".env.litellm"
_PID_RELPATH = Path("data/ops/litellm.pid")
_LOG_RELPATH = Path("data/ops/litellm.log")


class ProxyProcessManager:
    """管理 LiteLLM Proxy 进程的启动、停止、重启和健康检查。"""

    def __init__(
        self,
        instance_root: Path,
        proxy_url: str = "http://localhost:4000",
        config_path: Path | None = None,
        env_file: Path | None = None,
    ) -> None:
        self._instance_root = instance_root.expanduser().resolve()
        self._proxy_url = proxy_url.rstrip("/")
        self._config_path = (
            config_path.expanduser().resolve()
            if config_path is not None
            else self._instance_root / _DEFAULT_CONFIG_NAME
        )
        self._env_file = (
            env_file.expanduser().resolve()
            if env_file is not None
            else self._instance_root / _DEFAULT_ENV_NAME
        )
        self._port = self._parse_port(self._proxy_url)
        try:
            self._docker_autostart_timeout_s = float(
                os.environ.get("OCTOAGENT_DOCKER_DAEMON_TIMEOUT", "60")
            )
        except ValueError:
            self._docker_autostart_timeout_s = 60.0

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    async def ensure_running(self, timeout_s: float = 25.0) -> bool:
        """检查 Proxy 是否存活，不存活则启动。返回是否成功。"""
        if await self.health_check():
            log.info("proxy_already_running", proxy_url=self._proxy_url)
            return True
        return await self._start(timeout_s=timeout_s)

    async def restart(self, timeout_s: float = 25.0) -> bool:
        """停止旧进程 + 启动新进程。返回是否成功。"""
        await self.stop()
        return await self._start(timeout_s=timeout_s)

    async def stop(self) -> None:
        """优雅停止 Proxy。"""
        compose_file = self._find_compose_file()
        if compose_file is not None and await self._docker_available():
            await self._stop_docker(compose_file)
        else:
            await self._stop_process()

    async def health_check(self) -> bool:
        """Proxy 是否可达。"""
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_S) as client:
                resp = await client.get(f"{self._proxy_url}{_HEALTH_ENDPOINT}")
                return resp.is_success
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 启动：策略选择
    # ------------------------------------------------------------------

    async def _start(self, *, timeout_s: float) -> bool:
        compose_file = self._find_compose_file()

        # 策略 1: Docker Compose（先尝试自动启动 Docker daemon）
        if compose_file is not None:
            daemon_status = await ensure_docker_daemon(
                timeout_s=self._docker_autostart_timeout_s,
            )
            if daemon_status.available:
                log.info(
                    "proxy_docker_daemon_ready",
                    auto_started=daemon_status.auto_started,
                    elapsed_s=daemon_status.elapsed_s,
                    platform=daemon_status.platform,
                )
                log.info(
                    "proxy_start_docker",
                    compose_file=str(compose_file),
                    proxy_url=self._proxy_url,
                )
                ok = await self._start_docker(compose_file)
                if ok:
                    return await self._wait_for_proxy(timeout_s)
                # Docker Compose 失败（daemon 未运行等），fallback 到直接进程
                log.warning("proxy_docker_failed_fallback_to_direct")
            else:
                log.warning(
                    "proxy_docker_daemon_unavailable",
                    platform=daemon_status.platform,
                    attempts=daemon_status.attempts,
                    error=daemon_status.error,
                )

        # 策略 2: 直接进程
        if self._config_path.exists():
            log.info(
                "proxy_start_process",
                config_path=str(self._config_path),
                port=self._port,
            )
            ok = await self._start_process()
            if not ok:
                return False
            return await self._wait_for_proxy(timeout_s)

        # 策略 3: 降级
        log.warning(
            "proxy_start_no_strategy",
            instance_root=str(self._instance_root),
            compose_found=compose_file is not None,
            config_exists=self._config_path.exists(),
        )
        return False

    # ------------------------------------------------------------------
    # Docker Compose 启停
    # ------------------------------------------------------------------

    async def _start_docker(self, compose_file: Path) -> bool:
        env = os.environ.copy()
        env["OCTOAGENT_INSTANCE_ROOT"] = str(self._instance_root)
        env["LITELLM_PORT"] = str(self._port)

        cmd = [
            "docker",
            "compose",
            "--env-file",
            str(self._env_file),
            "-f",
            str(compose_file),
            "up",
            "-d",
            "--force-recreate",
        ]
        return await self._run_subprocess(cmd, env=env, label="docker_compose_up")

    async def _stop_docker(self, compose_file: Path) -> None:
        env = os.environ.copy()
        env["OCTOAGENT_INSTANCE_ROOT"] = str(self._instance_root)
        env["LITELLM_PORT"] = str(self._port)

        cmd = [
            "docker",
            "compose",
            "-f",
            str(compose_file),
            "down",
        ]
        await self._run_subprocess(cmd, env=env, label="docker_compose_down")

    # ------------------------------------------------------------------
    # 直接进程启停
    # ------------------------------------------------------------------

    async def _start_process(self) -> bool:
        env = self._load_env_file()

        pid_path = self._instance_root / _PID_RELPATH
        log_path = self._instance_root / _LOG_RELPATH
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # 直接进程模式下 LiteLLM 不解析 os.environ/ 引用，
        # 需要生成一份已替换的临时 config
        resolved_config = self._resolve_config_env_refs(env)

        try:
            log_file = log_path.open("a", encoding="utf-8")
            proc = await asyncio.create_subprocess_exec(
                "uv",
                "run",
                "litellm",
                "--config",
                str(resolved_config),
                "--port",
                str(self._port),
                stdout=log_file,
                stderr=log_file,
                env=env,
            )
            pid_path.write_text(str(proc.pid), encoding="utf-8")
            log.info("proxy_process_started", pid=proc.pid, log=str(log_path))
            return True
        except Exception as exc:
            log.warning(
                "proxy_process_start_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return False

    async def _stop_process(self) -> None:
        pid_path = self._instance_root / _PID_RELPATH
        if not pid_path.exists():
            return

        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError) as exc:
            log.warning("proxy_pid_read_failed", error=str(exc))
            self._cleanup_pid_file()
            return

        # SIGTERM -> 等待 -> SIGKILL
        try:
            os.kill(pid, signal.SIGTERM)
            log.info("proxy_sigterm_sent", pid=pid)
        except ProcessLookupError:
            log.info("proxy_process_already_gone", pid=pid)
            self._cleanup_pid_file()
            return
        except OSError as exc:
            log.warning("proxy_sigterm_failed", pid=pid, error=str(exc))
            self._cleanup_pid_file()
            return

        if await self._wait_process_exit(pid, timeout_s=_STOP_GRACE_S):
            log.info("proxy_process_stopped", pid=pid)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
                log.warning("proxy_sigkill_sent", pid=pid)
            except ProcessLookupError:
                pass
            except OSError as exc:
                log.warning("proxy_sigkill_failed", pid=pid, error=str(exc))

        self._cleanup_pid_file()

    # ------------------------------------------------------------------
    # 健康轮询
    # ------------------------------------------------------------------

    async def _wait_for_proxy(self, timeout_s: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while loop.time() < deadline:
            if await self.health_check():
                log.info("proxy_ready", proxy_url=self._proxy_url)
                return True
            await asyncio.sleep(_POLL_INTERVAL_S)
        log.warning(
            "proxy_wait_timeout",
            proxy_url=self._proxy_url,
            timeout_s=timeout_s,
        )
        return False

    # ------------------------------------------------------------------
    # Docker 可用性检测
    # ------------------------------------------------------------------

    async def _docker_available(self) -> bool:
        """检查 Docker daemon 是否可用（委派给 ensure_docker_daemon 单一事实源）。"""
        status = await ensure_docker_daemon(auto_start=False)
        return status.available

    # ------------------------------------------------------------------
    # compose 文件搜索
    # ------------------------------------------------------------------

    def _find_compose_file(self) -> Path | None:
        candidates = [
            self._instance_root / "app" / "octoagent" / _COMPOSE_FILENAME,
            self._instance_root / _COMPOSE_FILENAME,
        ]
        for path in candidates:
            if path.exists():
                return path
        return None

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _load_env_file(self) -> dict[str, str]:
        """从 env_file 加载环境变量并合并到当前 env。"""
        env = os.environ.copy()
        if not self._env_file.exists():
            return env
        try:
            for line in self._env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key:
                    env[key] = value
        except OSError as exc:
            log.warning("proxy_env_file_read_failed", error=str(exc))
        return env

    def _resolve_config_env_refs(self, env: dict[str, str]) -> Path:
        """生成一份将 os.environ/XXX 引用替换为实际值的临时 config。

        LiteLLM 的 os.environ/ 语法仅在 Docker 容器模式下可靠，
        直接进程模式需要将 api_key 等字段替换为实际值。
        同时加 openai/ 前缀给有 api_base 的自定义 provider（SiliconFlow 等）。
        """
        import re

        resolved_path = self._instance_root / "data" / "ops" / "litellm-config-resolved.yaml"
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            content = self._config_path.read_text(encoding="utf-8")
            # 替换 os.environ/XXX 引用
            def _replace_env_ref(match: re.Match) -> str:
                env_name = match.group(1)
                return env.get(env_name, f"MISSING_{env_name}")

            content = re.sub(r"os\.environ/(\w+)", _replace_env_ref, content)
            resolved_path.write_text(content, encoding="utf-8")
            resolved_path.chmod(0o600)
            return resolved_path
        except Exception as exc:
            log.warning(
                "proxy_config_resolve_failed",
                error=str(exc),
            )
            return self._config_path  # fallback 用原文件

    @staticmethod
    def _parse_port(url: str) -> int:
        parsed = urlparse(url)
        if parsed.port is not None:
            return parsed.port
        return 443 if parsed.scheme == "https" else 80

    async def _run_subprocess(
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        label: str,
    ) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                log.warning(
                    f"proxy_{label}_failed",
                    returncode=proc.returncode,
                    stderr=stderr.decode(errors="replace").strip(),
                )
                return False
            return True
        except Exception as exc:
            log.warning(
                f"proxy_{label}_error",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return False

    @staticmethod
    async def _wait_process_exit(pid: int, *, timeout_s: float) -> bool:
        """轮询检测进程是否已退出。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while loop.time() < deadline:
            try:
                os.kill(pid, 0)  # 检测进程是否存在
            except ProcessLookupError:
                return True
            except OSError:
                return True
            await asyncio.sleep(0.3)
        return False

    def _cleanup_pid_file(self) -> None:
        pid_path = self._instance_root / _PID_RELPATH
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
