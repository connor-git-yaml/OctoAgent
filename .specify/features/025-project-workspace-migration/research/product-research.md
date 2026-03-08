# Product Research: Feature 025 第二阶段 — Secret Store + Unified Config Wizard

**Date**: 2026-03-08  
**Mode**: full  
**Scope**: Secret Store 分层、统一 wizard session 的 CLI 主路径、`octo project create/select/edit/inspect`、`octo secrets audit/configure/apply/reload/rotate`

---

## 1. 产品问题重新定义

Feature 025 第一阶段已经把 `Project / Workspace` 与 default project migration 打通，但普通用户视角的配置路径仍然是碎片化的：

1. provider auth 还分散在 `auth-profiles.json`、`.env`、`.env.litellm` 和 `octoagent.yaml` 的 env name 引用中。
2. Telegram / Gateway / model alias / runtime secret 没有统一入口，用户仍要记住“该改 YAML、该 export env、还是该跑 init/onboard/config”。
3. `Project` 虽然已经是一等公民数据模型，但用户还不能把 project 当作正式操作对象来创建、选择、编辑和检查。
4. `octo init` 仍是早期一次性引导，不是可恢复、可重入、可多端复用的 wizard session。
5. Web 配置中心尚未交付，因此 025-B 必须先把 **CLI 路径做完整**，不能把关键主路径留给未来页面。

这意味着 025-B 的产品目标不是“再加几个配置命令”，而是把当前零散的配置/密钥/项目管理面收敛成一条连续的 operator 主路径。

---

## 2. 参考产品结论

### 2.1 OpenClaw：secret 生命周期应该是独立的 operator loop

从 [_references/opensource/openclaw/docs/cli/secrets.md](/Users/connorlu/Desktop/.workspace2.nosync/AgentsStudy-parallel-1/_references/opensource/openclaw/docs/cli/secrets.md) 可以看出，比较成熟的做法不是“编辑配置时顺便塞几个 secret 字段”，而是把 secret 生命周期拆成：

- `audit`: 只读检查明文残留、未解析 ref、优先级漂移
- `configure`: 交互式生成计划
- `apply`: 执行计划并可 `--dry-run`
- `reload`: 只做 runtime 重解析和原子切换

这对 OctoAgent 的直接启发是：

- `audit`、`apply`、`reload` 必须解耦，不能把“写配置”“注入运行时”“热重载”揉成一个黑箱命令
- CLI 第一阶段就要有完整 loop，不能等 Web 配置中心
- `rotate` 应被建模为正式 operator 动作，而不是“重新跑一遍 configure”

### 2.2 OpenClaw：wizard session 应先成为协议对象，再做 UI

从 [_references/opensource/openclaw/docs/experiments/onboarding-config-protocol.md](/Users/connorlu/Desktop/.workspace2.nosync/AgentsStudy-parallel-1/_references/opensource/openclaw/docs/experiments/onboarding-config-protocol.md) 可见，`wizard.start / next / status / cancel` 和 `config.schema + uiHints` 被先定义成共享协议。

这意味着 025-B 的 CLI 向导也不应直接沿用 `init_wizard.py` 的一次性脚本体验，而应满足：

- 可创建 / 恢复 / 取消 / 检查状态
- step model 来自已冻结的 026-A `WizardSessionDocument`
- CLI 只决定如何渲染交互，不重新定义 step 语义

### 2.3 Agent Zero：project 必须是用户可理解的正式工作单元

从 [_references/opensource/agent-zero/docs/guides/projects.md](/Users/connorlu/Desktop/.workspace2.nosync/AgentsStudy-parallel-1/_references/opensource/agent-zero/docs/guides/projects.md) 能看到，project 对用户来说不是隐藏的数据库对象，而是一个统一承载 instructions、memory、secrets、files、git workspace 的工作单元。

对 OctoAgent 来说，这要求 025-B 的 CLI 至少提供：

- `create`: 创建并初始化 project 主路径
- `select`: 切换当前 project
- `edit`: 修改 project 基础属性和入口配置
- `inspect`: 查看 project 的 bindings、workspace、secret 状态和 readiness

---

## 3. 推荐的用户主路径

### 3.1 普通用户路径

面向“想把 OctoAgent 配起来并跑起来”的用户，推荐主路径应收敛成：

1. `octo project create`
   - 创建 project，必要时选择是否立即设为当前 project
2. `octo project select`
   - 明确当前 project/workspace
3. `octo project edit --wizard`
   - 启动或恢复统一 wizard session
4. wizard 内完成：
   - provider auth/profile 选择
   - model alias / runtime 选择
   - Telegram / Gateway / webhook secret 绑定
   - config schema 消费与基本校验
5. `octo secrets apply`
   - 把计划落成 canonical bindings
6. `octo secrets reload`
   - 原子更新 runtime secret snapshot
7. `octo doctor` / `octo onboard`
   - 做最终 readiness 验证

对普通用户来说，关键不是记住命令多寡，而是命令之间的顺序和职责边界清晰：

- wizard 负责收集和规划
- apply 负责写入
- reload 负责让 runtime 生效
- audit/doctor 负责验证和收口

### 3.2 高级用户路径

高级用户、CI 或容器编排场景仍应保留快捷路径，但这些路径应被显式归类为“高级路径”：

- 直接使用 `SecretRef(env)`
- 直接使用 `SecretRef(file)`
- 直接使用 `SecretRef(exec)`
- 在支持时使用 `SecretRef(keychain)`
- 先 `audit --check` 再 `apply --dry-run`

也就是说，环境变量不会被禁用，但应从默认路径降级为高级路径。

---

## 4. 025-B 与 026-B 的清晰边界

### 4.1 025-B 必须先交付的 CLI 能力

以下能力必须在 025-B 交付，因为否则普通用户路径仍然不闭环：

- `octo project create/select/edit/inspect`
- `octo secrets audit/configure/apply/reload/rotate`
- 可恢复的 CLI wizard session
- `config schema + uiHints` 的 CLI 消费
- project-scoped provider/channel/gateway secret bindings
- runtime short-lived injection 与 reload

### 4.2 必须延后到 026-B 的 Web 能力

以下能力本阶段不应偷带实现承诺，统一留给 026-B：

- Web Config Center 页面
- Web Secrets 管理页面
- Web Project Selector 页面
- Web Wizard 会话页面
- Session Center、Scheduler、Runtime Console

025-B 只需要保证这些未来页面有可消费的 contract 和 CLI 主路径，不需要先做页面。

---

## 5. 产品分层建议

### 5.1 普通用户只应接触“对象”和“动作”

普通用户应该只需要理解以下概念：

- 当前 project 是谁
- 某个 secret 属于 provider / channel / gateway 的哪一类
- 这个 secret 是配置好了、待应用、待 reload 还是失效

不应要求普通用户直接理解：

- `.env` / `.env.litellm` 的优先级细节
- CredentialStore 与 channel token 的落盘差异
- Gateway runtime 的注入时机

### 5.2 高级用户才暴露 ref 细节

`env/file/exec/keychain` 这些 `SecretRef` source type 应在 CLI 和 contract 中存在，但默认文案应强调：

- 普通路径优先“选择存储位置/目标对象”
- 高级路径才显式选择“具体 ref source”

这样既满足高级可控，也不把普通用户路径复杂化。

---

## 6. 对 spec 的直接影响

1. 025-B 的 CLI 必须是第一公民，不能把完整体验延后给 Web。
2. `project` 与 `secret` 都必须成为可检查对象，不能继续只靠底层文件和 env 推断状态。
3. wizard session 必须是可恢复的正式对象，而不是一次性脚本流程。
4. secret 生命周期必须分解为 `audit -> configure -> apply -> reload -> rotate`，而不是单个黑箱命令。
5. 026-B 只消费 025-B 交付的 contract 和状态面，不应反向定义 025-B 的 wizard/project/secret 语义。
