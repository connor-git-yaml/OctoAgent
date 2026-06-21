# Plugin Loader（F106 User Plugin Loader）

> 用户/社区插件装载子系统。把自定义 **skill + behavior pack + 新工具/hooks** 装进 `~/.octoagent/plugins/`，
> 主 Agent 自动发现并可用。在 SkillDiscovery + 中央 ToolRegistry + ApprovalGate 之上升级，非从零。
> 状态：**Phase A（declarative spine）+ Phase B（code 执行安全核心）已实现**；Phase C（watchdog/git）+
> behavior overlay 推迟（见 `.specify/features/106-plugin-loader/handoff.md`）。

## 1. 两类 plugin + 信任模型

| 类 | 判定（纯 stat） | 装载 |
|----|----------------|------|
| **declarative** | 无 `.py/.so/...` 等可执行文件 | 数据（skill 指令 + KNOWLEDGE 知识），自由装，过威胁扫描 |
| **code-capable** | 含任一可执行触发文件 | **启用须用户显式审批**（human-initiated）+ 整树 code_hash 绑定 |

**v0.1 信任模型 = trust-on-install + 审批门控（无沙箱）**：
- code plugin 代码 **未审批前绝不 import**（`pending_approval`）。
- 审批 = 用户独立 `POST /api/plugins/{name}/approve`，记 plugin **整树**内容 hash。
- 任一文件变（手改/git/未来 watchdog）→ hash 不匹配 → 强制重新审批（闭合"审批后换码"洞）。
- **诚实残余（无沙箱）**：已审批 = 进程内任意 Python，full 访问（secrets/网络/可 monkeypatch Policy）。审批面明示风险、禁"已扫描/安全"措辞。真隔离 = v0.2 沙箱。详见 spec §0.3。

## 2. 目录约定

```
~/.octoagent/plugins/<name>/
  plugin.yaml          # PluginManifest（name 须 kebab + 与目录名一致；provides: skills/behavior/tools/hooks）
  skills/<skill>/SKILL.md   # declarative skill（注册进 SkillDiscovery，PLUGIN source）
  behavior/KNOWLEDGE.md     # behavior overlay（仅 KNOWLEDGE.md allowlist；overlay 接入推迟）
  tools.py             # code: PLUGIN_TOOLS: list[ToolEntry]（经专用 loader 注册）
  hooks.py             # code: on_load()/on_unload() lifecycle
  .disabled            # toggle marker（存在=禁用，loader 管理，不计入 code_hash）
  .approved            # 审批 marker（内容=code_hash，loader 管理，不计入 code_hash）
```

## 3. 模块

| 模块 | 职责 |
|------|------|
| `packages/skills/.../plugins/manifest.py` | PluginManifest/Capability/State/RejectedReason/Record + code 触发文件集 + behavior allowlist（纯，无 gateway 依赖）|
| `packages/skills/.../plugins/discovery.py` | 纯 stat 发现 + 能力分类（**绝不 import**）+ manifest 校验（yaml.safe_load + provides 制品存在 + 路径不逃逸）|
| `packages/skills/.../plugins/code_hash.py` | 整树 code_hash（全文件排序 path+content，排除 .git/__pycache__/marker）|
| `packages/skills/.../plugins/approval.py` | `.approved` marker 读写（code_hash 绑定，跨重启）|
| `apps/gateway/.../services/plugin_registry.py` | **编排器**：discover→威胁扫描→注册→toggle/approve/refresh/remove，asyncio.Lock 串行 + 单 plugin 隔离降级 + 审计事件 |
| `apps/gateway/.../services/plugin_loader.py` | **code 专用 importlib path**（非 scan_and_register）：namespaced 模块 + staging 冲突预检 + 事务回滚 + MED-1 全局 register 篡改检测 + hooks lifecycle |
| `apps/gateway/.../routes/plugins.py` | `/api/plugins` REST（list/get/toggle/approve/delete/refresh，front-door protected）|

## 4. 装配（bootstrap 段 7.5）

`OctoHarness._bootstrap_user_plugins`（`octo_harness.py`）在段 7（capability_pack 构造 SkillDiscovery）之后、段 9（executors）之前：构造 `PluginRegistry`（plugins_dir DI / SkillDiscovery / ContentThreatScanService / 中央 ToolRegistry / event+task store）→ `discover_and_register()` → `app.state.plugin_registry`。整段 try/except 降级（plugin 子系统坏不拖垮 gateway，#6）。

`plugins_dir` 解析：DI（e2e hermetic）> `OCTOAGENT_PLUGINS_DIR` env（full-lifespan 隔离）> `~/.octoagent/plugins`（生产）。

## 5. 关键安全不变量（dual-review 0 HIGH 实测守护）

1. **未审批不执行**：code plugin `pending_approval` → 模块名不入 `sys.modules`、工具不注册（契约测试）。
2. **换码重审**：reconcile 在锁下 `_unload_all_code`（deregister 工具 + evict 模块）再重建；code_hash 变 → pending，旧工具不残留、新码不自动跑。
3. **名冲突不覆盖**：plugin skill/工具与内置同名 → 拒该项 + 审计（SkillDiscovery reject-on-collision；loader staging 预检）。
4. **MED-1**：plugin import 期直接调全局 `register()` 篡改 → loader 快照 diff 检测 → 还原 + 拒载。
5. **隔离降级**：单 plugin 任一步失败 → try/except + `PLUGIN_REJECTED(reason)` + 继续，不拖垮 bootstrap。
6. **声明式制品威胁扫描**：manifest+SKILL.md+KNOWLEDGE.md 过 `scan_memory`；blocked→拒，scanner-raise→fail-open，oversize-degraded→拒。**代码 .py 不扫**（靠审批+provenance，不制造假信心）。

## 6. 审计事件

`PLUGIN_LOADED`（含 pending_approval）/ `PLUGIN_REJECTED`（reason∈PluginRejectedReason）/ `PLUGIN_TOGGLED` / `PLUGIN_REMOVED` / `PLUGIN_APPROVED` / `PLUGIN_CODE_CHANGED`（Phase C producer）。payload 无原文明文（#5）。

## 7. 与 Constitution / 哲学

- **#4 两阶段**：code plugin 启用=运行任意代码=高风险 side-effect → 发现→审批→执行（门控首次执行；不门控已审批代码之后做什么，§0.3）。
- **#6 降级**：单 plugin 隔离；plugin 子系统/EventStore 不可用降级。
- **#9/#10**：declarative=数据 LLM 自选；plugin 工具走中央 ToolRegistry + Policy（cooperative-only 契约，非对恶意已审批代码的边界）。
- **H1**：declarative 结构性不绕主 Agent；code plugin 靠 trust+audit（channel-as-plugin 推迟，enforcement=v0.2 沙箱）。

详见 `.specify/features/106-plugin-loader/spec.md`（§0.2/§0.3 信任模型 + §5 FR）。
