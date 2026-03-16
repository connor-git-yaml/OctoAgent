"""MCP server 安装与生命周期管理服务。

负责 npm/pip MCP server 的安装、卸载、注册表持久化和安装任务管理。
McpInstallerService 不直接访问 McpSessionPool，通过 McpRegistryService 间接交互。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from .mcp_registry import McpRegistryService, McpServerConfig

log = structlog.get_logger()

# ── 安装目录 ──────────────────────────────────────────────────

_DEFAULT_MCP_SERVERS_DIR = Path.home() / ".octoagent" / "mcp-servers"
_DEFAULT_INSTALLS_PATH = Path("data/ops/mcp-installs.json")

# ── 子进程超时 ────────────────────────────────────────────────

_SUBPROCESS_TIMEOUT_S = 120
_VERIFY_TIMEOUT_S = 15

# ── 子进程 env 安全基线（Constitution #5: Least Privilege）──────
# 仅保留子进程运行所必需的环境变量，不继承宿主进程完整 env
_SAFE_ENV_KEYS = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR")


def _build_safe_env(user_env: dict[str, str] | None = None) -> dict[str, str]:
    """构造安全的子进程环境变量。仅包含最小必需基线 + 用户指定的 per-server env。"""
    env: dict[str, str] = {}
    for key in _SAFE_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    if user_env:
        env.update(user_env)
    return env


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


# ── 数据模型（T014） ─────────────────────────────────────────

class InstallSource(StrEnum):
    """安装来源类型。"""
    NPM = "npm"
    PIP = "pip"
    DOCKER = "docker"
    MANUAL = "manual"


class InstallStatus(StrEnum):
    """安装状态。"""
    INSTALLING = "installing"
    INSTALLED = "installed"
    FAILED = "failed"
    UNINSTALLING = "uninstalling"


class McpInstallRecord(BaseModel):
    """描述一个已安装 MCP server 的元数据。"""
    server_id: str
    install_source: InstallSource
    package_name: str
    version: str = ""
    install_path: str = ""
    integrity: str = ""
    installed_at: datetime
    updated_at: datetime
    status: InstallStatus
    auto_generated_config: bool = True
    error: str = ""


class InstallTaskStatus(StrEnum):
    """安装任务执行状态。"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class InstallTask(BaseModel):
    """描述一个进行中的安装任务。"""
    task_id: str
    server_id: str
    install_source: InstallSource
    package_name: str
    status: InstallTaskStatus = InstallTaskStatus.PENDING
    progress_message: str = ""
    error: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


# ── 工具函数（T023-T025） ────────────────────────────────────

# npm 包名正则：@scope/name 或 name，允许连字符和数字
_NPM_PACKAGE_RE = re.compile(r"^(@[a-z0-9\-_.]+/)?[a-z0-9\-_.]+$", re.IGNORECASE)
# pip 包名正则：PEP 508 简化版
_PIP_PACKAGE_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9._\-]*[a-zA-Z0-9])?$")


def _validate_package_name(source: InstallSource, name: str) -> None:
    """校验包名格式，防注入。"""
    if not name or not name.strip():
        raise ValueError("包名不能为空")
    name = name.strip()
    if source == InstallSource.NPM and not _NPM_PACKAGE_RE.match(name):
        raise ValueError(f"npm 包名格式不合法: {name}")
    elif source == InstallSource.PIP and not _PIP_PACKAGE_RE.match(name):
        raise ValueError(f"pip 包名格式不合法: {name}")
    # 额外检查：不允许包含路径分隔符（防注入）
    if ".." in name or "/" in name.replace("@", "", 1).split("/", 1)[-1]:
        raise ValueError(f"包名包含危险字符: {name}")


def _slugify_server_id(source: InstallSource, package_name: str) -> str:
    """将包名转为安全的 server_id。"""
    slug = package_name.strip().lower()
    # 替换 @scope/name 中的 @ 和 /
    slug = slug.replace("@", "").replace("/", "_")
    # 替换其他不安全字符
    slug = re.sub(r"[^a-z0-9_\-]", "_", slug)
    slug = slug.strip("_")
    if not slug:
        slug = f"{source}_server"
    return slug


def _validate_install_path(path: Path, base_dir: Path) -> None:
    """路径安全检查：防路径遍历。"""
    resolved = path.resolve()
    base_resolved = base_dir.resolve()
    if not resolved.is_relative_to(base_resolved):
        raise ValueError(
            f"安装路径 {resolved} 不在允许的基础目录 {base_resolved} 内"
        )


# ── McpInstallerService（T016-T020, T026-T034） ─────────────

class McpInstallerService:
    """MCP server 安装与注册表管理服务。"""

    def __init__(
        self,
        *,
        registry: McpRegistryService,
        project_root: Path,
        mcp_servers_dir: Path | None = None,
        installs_path: Path | None = None,
    ) -> None:
        self._registry = registry
        self._project_root = project_root
        self._mcp_servers_dir = mcp_servers_dir or _DEFAULT_MCP_SERVERS_DIR
        self._installs_path = installs_path or (project_root / _DEFAULT_INSTALLS_PATH)
        self._install_records: dict[str, McpInstallRecord] = {}
        self._install_tasks: dict[str, InstallTask] = {}
        self._running_tasks: dict[str, asyncio.Task[None]] = {}

    # ── 生命周期 ──────────────────────────────────────────────

    async def startup(self) -> None:
        """加载安装注册表，检查并清理不完整安装。"""
        self._load_installs()
        # 检测 status="installing" 的不完整安装并标记为 "failed"
        changed = False
        for record in list(self._install_records.values()):
            if record.status == InstallStatus.INSTALLING:
                log.warning(
                    "mcp_install_incomplete_detected",
                    server_id=record.server_id,
                    package_name=record.package_name,
                )
                record.status = InstallStatus.FAILED
                record.error = "安装因进程终止而中断"
                record.updated_at = _utc_now()
                changed = True
        if changed:
            self._save_installs()

    async def shutdown(self) -> None:
        """取消进行中的安装任务。"""
        for task_id, async_task in list(self._running_tasks.items()):
            if not async_task.done():
                async_task.cancel()
                log.info("mcp_install_task_cancelled", task_id=task_id)
        self._running_tasks.clear()

    # ── 注册表持久化（T017） ──────────────────────────────────

    def _load_installs(self) -> None:
        """从 mcp-installs.json 加载安装注册表。"""
        self._install_records = {}
        if not self._installs_path.exists():
            return
        try:
            raw = json.loads(self._installs_path.read_text(encoding="utf-8"))
            installs = raw.get("installs", {})
            for server_id, data in installs.items():
                try:
                    self._install_records[server_id] = McpInstallRecord.model_validate(data)
                except Exception as exc:
                    log.warning(
                        "mcp_install_record_parse_error",
                        server_id=server_id,
                        error=str(exc),
                    )
        except Exception as exc:
            log.warning("mcp_installs_load_error", error=str(exc))

    def _save_installs(self) -> None:
        """写入安装注册表到 mcp-installs.json。"""
        self._installs_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "installs": {
                server_id: record.model_dump(mode="json")
                for server_id, record in sorted(self._install_records.items())
            },
        }
        self._installs_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    # ── 查询方法（T020） ──────────────────────────────────────

    def list_installs(self) -> list[McpInstallRecord]:
        """列出所有安装记录。"""
        return list(self._install_records.values())

    def get_install(self, server_id: str) -> McpInstallRecord | None:
        """获取指定 server 的安装记录。"""
        return self._install_records.get(server_id)

    # ── 安装入口（T026-T027） ─────────────────────────────────

    async def install(
        self,
        *,
        install_source: str | InstallSource,
        package_name: str,
        env: dict[str, str] | None = None,
    ) -> str:
        """启动异步安装任务，立即返回 task_id。"""
        source = InstallSource(install_source)
        package_name = package_name.strip()

        # 包名校验
        _validate_package_name(source, package_name)

        # 计算 server_id
        server_id = _slugify_server_id(source, package_name)

        # 重复安装检测
        existing = self._install_records.get(server_id)
        if existing and existing.status == InstallStatus.INSTALLED:
            raise ValueError(f"MCP server '{server_id}' 已安装（包名: {existing.package_name}）")

        # 创建安装任务
        task_id = str(uuid.uuid4())
        task = InstallTask(
            task_id=task_id,
            server_id=server_id,
            install_source=source,
            package_name=package_name,
        )
        self._install_tasks[task_id] = task

        # 启动后台 asyncio 任务
        async_task = asyncio.create_task(
            self._run_install(task, env=env or {}),
        )
        self._running_tasks[task_id] = async_task

        log.info(
            "mcp_install_started",
            task_id=task_id,
            server_id=server_id,
            install_source=source,
            package_name=package_name,
        )
        return task_id

    def get_install_status(self, task_id: str) -> InstallTask | None:
        """查询安装任务进度。"""
        return self._install_tasks.get(task_id)

    # ── 安装任务执行（内部） ──────────────────────────────────

    async def _run_install(self, task: InstallTask, *, env: dict[str, str]) -> None:
        """后台安装任务主逻辑。"""
        try:
            task.status = InstallTaskStatus.RUNNING
            task.progress_message = "准备安装..."

            if task.install_source == InstallSource.NPM:
                await self._install_npm(task, env=env)
            elif task.install_source == InstallSource.PIP:
                await self._install_pip(task, env=env)
            else:
                raise ValueError(f"不支持的安装来源: {task.install_source}")

        except asyncio.CancelledError:
            task.status = InstallTaskStatus.FAILED
            task.error = "安装任务被取消"
            log.info("mcp_install_cancelled", task_id=task.task_id)
        except Exception as exc:
            task.status = InstallTaskStatus.FAILED
            task.error = str(exc)
            # 更新安装记录（如果已创建）
            record = self._install_records.get(task.server_id)
            if record:
                record.status = InstallStatus.FAILED
                record.error = str(exc)
                record.updated_at = _utc_now()
                self._save_installs()
            log.error(
                "mcp_install_failed",
                task_id=task.task_id,
                server_id=task.server_id,
                error=str(exc),
            )
        finally:
            self._running_tasks.pop(task.task_id, None)

    # ── npm 安装（T028-T031） ─────────────────────────────────

    async def _install_npm(self, task: InstallTask, *, env: dict[str, str]) -> None:
        """npm 安装核心逻辑。"""
        server_id = task.server_id
        package_name = task.package_name

        # 创建安装目录
        install_dir = self._mcp_servers_dir / server_id
        _validate_install_path(install_dir, self._mcp_servers_dir)
        install_dir.mkdir(parents=True, exist_ok=True)

        # 写入安装记录（status=installing）
        now = _utc_now()
        record = McpInstallRecord(
            server_id=server_id,
            install_source=InstallSource.NPM,
            package_name=package_name,
            install_path=str(install_dir),
            installed_at=now,
            updated_at=now,
            status=InstallStatus.INSTALLING,
        )
        self._install_records[server_id] = record
        self._save_installs()

        # 执行 npm install（env 隔离：仅传递安全基线 + 用户 per-server env）
        safe_env = _build_safe_env(env)
        task.progress_message = f"正在安装 npm 包 {package_name}..."
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "--prefix", str(install_dir), package_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(install_dir),
            env=safe_env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=_SUBPROCESS_TIMEOUT_S,
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"npm install 超时（{_SUBPROCESS_TIMEOUT_S}s）") from exc

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"npm install 失败 (exit {proc.returncode}): {error_msg}")

        # 提取版本号
        version = self._extract_npm_version(install_dir, package_name)
        record.version = version

        # 入口点检测
        task.progress_message = "正在检测入口点..."
        command, args = self._detect_npm_entrypoint(install_dir, package_name)

        # 安装后验证
        task.progress_message = "正在验证 MCP server..."
        tools = await self._verify_server(command, args, env=env)

        # 完成安装
        await self._finalize_install(
            task=task,
            record=record,
            command=command,
            args=args,
            env=env,
            tools=tools,
        )

    def _extract_npm_version(self, install_dir: Path, package_name: str) -> str:
        """从 node_modules 中提取 npm 包版本号。"""
        # 解析 @scope/name
        if package_name.startswith("@"):
            pkg_dir = install_dir / "node_modules" / package_name
        else:
            pkg_dir = install_dir / "node_modules" / package_name
        pkg_json = pkg_dir / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
                return str(data.get("version", ""))
            except Exception:
                pass
        return ""

    def _detect_npm_entrypoint(
        self, install_dir: Path, package_name: str
    ) -> tuple[str, list[str]]:
        """npm 入口点检测（分层策略）。"""
        if package_name.startswith("@"):
            pkg_dir = install_dir / "node_modules" / package_name
        else:
            pkg_dir = install_dir / "node_modules" / package_name

        pkg_json = pkg_dir / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
            except Exception:
                data = {}

            # 策略 1: bin 字段
            bin_field = data.get("bin")
            if isinstance(bin_field, str):
                bin_path = (pkg_dir / bin_field).resolve()
                return str(bin_path), []
            if isinstance(bin_field, dict) and bin_field:
                # 取第一个 bin 入口
                first_bin = next(iter(bin_field.values()))
                bin_path = (pkg_dir / first_bin).resolve()
                return str(bin_path), []

            # 策略 2: main 字段
            main_field = data.get("main")
            if main_field:
                main_path = str((pkg_dir / main_field).resolve())
                return "node", [main_path]

        # 策略 3: npx 回退
        return "npx", ["-y", "--prefix", str(install_dir), package_name]

    # ── pip 安装（T032-T034） ─────────────────────────────────

    async def _install_pip(self, task: InstallTask, *, env: dict[str, str]) -> None:
        """pip 安装核心逻辑。"""
        server_id = task.server_id
        package_name = task.package_name

        # 创建安装目录
        install_dir = self._mcp_servers_dir / server_id
        _validate_install_path(install_dir, self._mcp_servers_dir)
        install_dir.mkdir(parents=True, exist_ok=True)

        venv_dir = install_dir / "venv"

        # 写入安装记录（status=installing）
        now = _utc_now()
        record = McpInstallRecord(
            server_id=server_id,
            install_source=InstallSource.PIP,
            package_name=package_name,
            install_path=str(install_dir),
            installed_at=now,
            updated_at=now,
            status=InstallStatus.INSTALLING,
        )
        self._install_records[server_id] = record
        self._save_installs()

        # 创建虚拟环境（env 隔离：仅传递安全基线 + 用户 per-server env）
        safe_env = _build_safe_env(env)
        task.progress_message = "正在创建虚拟环境..."
        proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "venv", str(venv_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"创建虚拟环境失败: {stderr.decode('utf-8', errors='replace').strip()}"
            )

        # 记录 venv/bin 安装前的文件列表
        venv_bin = venv_dir / "bin"
        pre_install_bins = set(venv_bin.iterdir()) if venv_bin.exists() else set()

        # pip install（env 隔离：仅传递安全基线 + 用户 per-server env）
        pip_path = venv_dir / "bin" / "pip"
        task.progress_message = f"正在安装 pip 包 {package_name}..."
        proc = await asyncio.create_subprocess_exec(
            str(pip_path), "install", package_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(install_dir),
            env=safe_env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=_SUBPROCESS_TIMEOUT_S,
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"pip install 超时（{_SUBPROCESS_TIMEOUT_S}s）") from exc

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"pip install 失败 (exit {proc.returncode}): {error_msg}")

        # 提取版本号
        version = await self._extract_pip_version(venv_dir, package_name)
        record.version = version

        # 入口点检测
        task.progress_message = "正在检测入口点..."
        command, args = self._detect_pip_entrypoint(
            venv_dir, package_name, pre_install_bins
        )

        # 安装后验证
        task.progress_message = "正在验证 MCP server..."
        tools = await self._verify_server(command, args, env=env)

        # 完成安装
        await self._finalize_install(
            task=task,
            record=record,
            command=command,
            args=args,
            env=env,
            tools=tools,
        )

    async def _extract_pip_version(self, venv_dir: Path, package_name: str) -> str:
        """通过 pip show 提取版本号。"""
        pip_path = venv_dir / "bin" / "pip"
        try:
            proc = await asyncio.create_subprocess_exec(
                str(pip_path), "show", package_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_build_safe_env(),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            for line in stdout.decode("utf-8", errors="replace").splitlines():
                if line.lower().startswith("version:"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return ""

    def _detect_pip_entrypoint(
        self,
        venv_dir: Path,
        package_name: str,
        pre_install_bins: set[Path],
    ) -> tuple[str, list[str]]:
        """pip 入口点检测（分层策略）。"""
        venv_bin = venv_dir / "bin"
        if venv_bin.exists():
            post_install_bins = set(venv_bin.iterdir())
            new_bins = post_install_bins - pre_install_bins
            # 过滤掉 pip/python 等系统文件
            new_executables = [
                p for p in new_bins
                if p.is_file() and not p.name.startswith("python")
                and not p.name.startswith("pip")
                and not p.name.startswith("activate")
                and not p.name.startswith("Activate")
            ]
            if len(new_executables) == 1:
                return str(new_executables[0].resolve()), []
            if new_executables:
                # 尝试匹配包名
                slug = package_name.lower().replace("-", "_").replace(".", "_")
                for exe in new_executables:
                    if slug in exe.name.lower().replace("-", "_"):
                        return str(exe.resolve()), []
                # 取第一个
                return str(new_executables[0].resolve()), []

        # 回退: python -m
        python_path = venv_dir / "bin" / "python"
        module_name = package_name.replace("-", "_").replace(".", "_")
        return str(python_path.resolve()), ["-m", module_name]

    # ── 通用验证和完成逻辑（T030-T031, T034） ────────────────

    async def _verify_server(
        self,
        command: str,
        args: list[str],
        *,
        env: dict[str, str],
    ) -> list[dict[str, str]]:
        """尝试启动 server 执行 tools/list 验证。

        返回 tools 列表。验证失败不阻断安装，但记录警告。
        """
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        tools_list: list[dict[str, str]] = []
        try:
            # 验证时也使用安全基线 env（Constitution #5）
            params = StdioServerParameters(
                command=command,
                args=args,
                env=_build_safe_env(env),
            )
            async with (
                stdio_client(params) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await asyncio.wait_for(
                    session.initialize(),
                    timeout=_VERIFY_TIMEOUT_S,
                )
                result = await asyncio.wait_for(
                    session.list_tools(cursor=None),
                    timeout=_VERIFY_TIMEOUT_S,
                )
                for tool in result.tools:
                    tools_list.append({
                        "name": tool.name,
                        "description": tool.description or "",
                    })
        except Exception as exc:
            log.warning(
                "mcp_install_verify_warning",
                command=command,
                error=str(exc),
            )
            # 验证失败不阻断安装
        return tools_list

    async def _finalize_install(
        self,
        *,
        task: InstallTask,
        record: McpInstallRecord,
        command: str,
        args: list[str],
        env: dict[str, str],
        tools: list[dict[str, str]],
    ) -> None:
        """安装完成后：写入配置、刷新 registry、更新注册表。"""
        server_id = record.server_id

        # 生成 McpServerConfig
        config = McpServerConfig(
            name=server_id,
            command=command,
            args=args,
            env=env,
            cwd="",
            enabled=True,
        )

        # 通过 McpRegistryService 写入运行时配置并刷新
        task.progress_message = "正在注册配置..."
        self._registry.save_config(config)
        await self._registry.refresh()

        # 更新安装记录
        record.status = InstallStatus.INSTALLED
        record.error = ""
        record.updated_at = _utc_now()
        self._save_installs()

        # 更新任务结果
        task.status = InstallTaskStatus.COMPLETED
        task.progress_message = "安装完成"
        task.result = {
            "server_id": server_id,
            "version": record.version,
            "install_path": record.install_path,
            "command": command,
            "tools_count": len(tools),
            "tools": tools,
        }

        log.info(
            "mcp_install_completed",
            task_id=task.task_id,
            server_id=server_id,
            version=record.version,
            tools_count=len(tools),
        )

    # ── 卸载（US6，MVP 阶段先预留骨架） ──────────────────────

    async def uninstall(self, server_id: str) -> dict[str, Any]:
        """卸载已安装的 MCP server。"""
        record = self._install_records.get(server_id)
        if record is None:
            raise ValueError(f"MCP server '{server_id}' 未安装")

        install_source = record.install_source
        install_path = record.install_path

        # 手动配置仅删除配置
        if record.install_source == InstallSource.MANUAL:
            self._registry.delete_config(server_id)
            await self._registry.refresh()
            del self._install_records[server_id]
            self._save_installs()
            return {
                "server_id": server_id,
                "install_source": str(install_source),
                "cleaned_path": "",
            }

        # 更新状态
        record.status = InstallStatus.UNINSTALLING
        record.updated_at = _utc_now()
        self._save_installs()

        # 删除运行时配置
        self._registry.delete_config(server_id)
        await self._registry.refresh()

        # 删除安装目录
        cleaned_path = ""
        if install_path and Path(install_path).exists():
            shutil.rmtree(install_path, ignore_errors=True)
            cleaned_path = install_path

        # 删除安装记录
        del self._install_records[server_id]
        self._save_installs()

        log.info(
            "mcp_server_uninstalled",
            server_id=server_id,
            install_source=str(install_source),
            cleaned_path=cleaned_path,
        )

        return {
            "server_id": server_id,
            "install_source": str(install_source),
            "cleaned_path": cleaned_path,
        }
