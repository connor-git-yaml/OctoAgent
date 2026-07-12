"""F140 L1 gateway 启动器——Playwright webServer 拉起的 hermetic 真 gateway。

职责（spec D2）：
1. 环境卫生：清凭证 env + ``OCTOAGENT_*`` 路径 env 全指进实例 root（仿 e2e_live
   conftest 清单——本进程独立于 pytest，须自带同款隔离）
2. 实例骨架 + ``tests/fixtures/local-instance`` 模板复制（每次启动 wipe 重建，
   保证 run 间干净——Playwright 断言依赖「最新 task 即本场景」）
3. ``create_app(harness_factory=...)``：完整路由 + SPA mount 的真 app，harness
   带空凭证 store + L1 场景脚本脑（零宿主 OAuth）
4. 零真 LLM 三重防御（F138 keystone 同款）：F137 gate=deny（env 自证）+ 空凭证
   store + bootstrap 后 ``resolve_for_alias`` bomb
5. uvicorn 127.0.0.1 起服务

env 契约（Playwright config 注入）：
- ``L1_ROOT``   实例 root 绝对路径（必填）
- ``L1_PORT``   监听端口（必填）
- ``L1_MODE``   ``loopback``（默认）| ``bearer``（场景②：额外置 FRONTDOOR 三件）
- ``L1_FD_TOKEN`` bearer 模式的 token 值（bearer 时必填；测试专用假值非真凭证）

用法：``uv run --project <octoagent> --no-sync python \
apps/gateway/tests/e2e_live/l1_support/serve_l1_gateway.py``（cwd=octoagent，
脚本自身目录进 sys.path[0] → 直接 import 同目录 scenario_brain）。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
# parents: [0]=l1_support [1]=e2e_live [2]=tests [3]=gateway [4]=apps [5]=<octoagent>
# Codex final review P2 闭环：原 parents[4] 差一层（解析到 apps/），致
# local-instance 模板复制静默 no-op（fixture 目录不存在 → if 分支跳过）。
_OCTOAGENT_ROOT = _HERE.parents[5]  # <octoagent>/
assert (_OCTOAGENT_ROOT / "pyproject.toml").exists(), (
    f"L1 launcher 根目录解析漂移：{_OCTOAGENT_ROOT} 下无 pyproject.toml"
)


def _clean_env() -> None:
    """清凭证 env（与 e2e_live conftest 对齐：静态清单 + 通配 sweep）。

    Opus 评审 MED-2 闭环：conftest ``_hermetic_environment`` 除静态清单外还有
    ``endswith("_API_KEY"/"_TOKEN")`` 通配兜底——launcher 独立于 pytest 跑，
    须搬全两层，否则宿主非标准 provider key（GEMINI/GROQ/HF_TOKEN 等）会
    进 bootstrap（真调用虽被三重防御挡住，但 hermetic 纯度要名实相符）。
    """
    for key in (
        "OPENAI_API_KEY",
        "SILICONFLOW_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "SLACK_SIGNING_SECRET",
        "SLACK_BOT_TOKEN",
        "DISCORD_BOT_TOKEN",
    ):
        os.environ.pop(key, None)
    for key in list(os.environ):
        if (
            (key.endswith("_API_KEY") or key.endswith("_TOKEN"))
            and key != "L1_FD_TOKEN"  # bearer 场景自身的测试假 token（非真凭证）
        ):
            os.environ.pop(key, None)

    # Opus 评审 MED-1 闭环：中和宿主可能残留的 host/暴露 env——launcher 硬编码
    # bind 127.0.0.1，但 create_app 的 _enforce_front_door_exposure 读 env
    # OCTOAGENT_HOST 判定裸奔组合；宿主 export 过 0.0.0.0 会误 exit(78) 全挂。
    os.environ["OCTOAGENT_HOST"] = "127.0.0.1"


def _redirect_paths(root: Path) -> None:
    """OCTOAGENT_* 路径 env 全指进实例 root（同 conftest ``_OCTOAGENT_PATH_ENVS``）。"""
    os.environ["OCTOAGENT_PROJECT_ROOT"] = str(root)
    os.environ["OCTOAGENT_DATA_DIR"] = str(root / "data")
    os.environ["OCTOAGENT_DB_PATH"] = str(root / "data" / "octoagent.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(root / "artifacts")
    os.environ["OCTOAGENT_PLUGINS_DIR"] = str(root / "plugins")
    os.environ["PYTHONHASHSEED"] = "0"


def _apply_mode(mode: str) -> None:
    """front door 模式：loopback（缺省即是）/ bearer（env 三件切换，spike S6）。"""
    if mode == "bearer":
        token = os.environ.get("L1_FD_TOKEN", "").strip()
        if not token:
            print("[L1-FATAL] L1_MODE=bearer 需要 L1_FD_TOKEN", file=sys.stderr, flush=True)
            raise SystemExit(2)
        os.environ["OCTOAGENT_FRONTDOOR_MODE"] = "bearer"
        os.environ["OCTOAGENT_FRONTDOOR_TOKEN_ENV"] = "L1_FD_TOKEN"
    else:
        # loopback 是 FrontDoorConfig 缺省；显式清掉宿主可能残留的覆盖
        os.environ.pop("OCTOAGENT_FRONTDOOR_MODE", None)
        os.environ.pop("OCTOAGENT_FRONTDOOR_TOKEN_ENV", None)


def _build_instance(root: Path) -> None:
    """wipe 重建实例骨架 + local-instance 模板。

    rmtree 前缀守卫（Opus 评审 LOW 闭环）：本脚本 docstring 允许手跑，
    误设 ``L1_ROOT=$HOME`` 不能变成删家目录——只允许删 ``.l1-runtime``
    下的实例目录。
    """
    if ".l1-runtime" not in root.parts:
        print(
            f"[L1-FATAL] L1_ROOT 必须位于 .l1-runtime/ 下（拒绝对 {root} 做 wipe）",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
    if root.exists():
        shutil.rmtree(root)
    (root / "behavior" / "system").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "mcp-servers").mkdir(parents=True, exist_ok=True)
    tmpl = _OCTOAGENT_ROOT / "tests" / "fixtures" / "local-instance"
    if (tmpl / "octoagent.yaml.template").exists():
        shutil.copy(tmpl / "octoagent.yaml.template", root / "octoagent.yaml")
    src_behavior = tmpl / "behavior" / "system"
    for name in ("USER.md", "MEMORY.md"):
        if (src_behavior / f"{name}.template").exists():
            shutil.copy(src_behavior / f"{name}.template", root / "behavior" / "system" / name)


def main() -> None:
    root_env = os.environ.get("L1_ROOT", "").strip()
    port_env = os.environ.get("L1_PORT", "").strip()
    if not root_env or not port_env:
        print("[L1-FATAL] 需要 L1_ROOT 与 L1_PORT env", file=sys.stderr, flush=True)
        raise SystemExit(2)
    root = Path(root_env).resolve()
    port = int(port_env)
    mode = os.environ.get("L1_MODE", "loopback").strip() or "loopback"

    # 零真 LLM 防御 #1：F137 gate=deny（漏网真调用 → ModelRequestsNotAllowedError 炸，
    # 不会被 FallbackManager 吞成 Echo 假成功）。spike S5 实测 deny 下全链路通。
    os.environ["OCTOAGENT_ALLOW_MODEL_REQUESTS"] = "0"

    _clean_env()
    _redirect_paths(root)
    _apply_mode(mode)
    _build_instance(root)

    # env 全就位后再 import（config/路径解析发生在 import 后的 create_app 内）
    import octoagent.gateway.main as gateway_main  # noqa: PLC0415
    from octoagent.gateway.harness.octo_harness import OctoHarness  # noqa: PLC0415
    from octoagent.provider.auth.store import CredentialStore  # noqa: PLC0415

    sys.path.insert(0, str(_HERE.parent))
    from scenario_brain import L1ScenarioModelClient  # noqa: PLC0415

    def _resolve_for_alias_bomb(*_a: object, **_k: object) -> object:
        raise AssertionError(
            "F140 L1: 真 provider 解析被触发——脚本化 L1 服务器不允许任何真 LLM 调用"
        )

    class _L1Harness(OctoHarness):
        """零真 LLM 防御 #3：commit_to_app（lifespan startup 内 bootstrap 完成后
        执行）末尾在两条 LLM 路径的共同咽喉点装 bomb（keystone 同款时序）。

        注意不能用 ``@app.on_event("startup")``——显式 ``lifespan=`` 下 Starlette
        不再执行 on_event 处理器。"""

        def commit_to_app(self, app) -> None:  # type: ignore[override]
            super().commit_to_app(app)
            app.state.provider_router.resolve_for_alias = _resolve_for_alias_bomb
            print(f"[L1-READY] mode={mode} port={port} root={root}", flush=True)

    def _harness_factory() -> OctoHarness:
        return _L1Harness(
            project_root=root,
            # 零真 LLM 防御 #2：空 tmp CredentialStore（load 返回空 store，零宿主 OAuth）
            credential_store=CredentialStore(store_path=root / "creds" / "auth-profiles.json"),
            mcp_servers_dir=root / "mcp-servers",
            data_dir=root / "data",
            model_client=L1ScenarioModelClient(),
        )

    app = gateway_main.create_app(harness_factory=_harness_factory)

    import uvicorn  # noqa: PLC0415

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
