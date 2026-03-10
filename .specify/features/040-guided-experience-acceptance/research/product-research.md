# Product Research: Feature 040 M4 Guided Experience Integration Acceptance

## 调研目标

寻找最适合 OctoAgent 当前阶段吸收的“整体验收 / 用户旅程闭环”做法，重点关注 onboarding、dashboard、approval、task control、memory/status integration。

## OpenClaw

### 值得吸收

1. `onboard` 必须共享 canonical onboarding/config 协议，而不是 CLI/Web 各维护一套状态机。
   - 参考：`_references/opensource/openclaw/docs/experiments/onboarding-config-protocol.md`
2. `onboard` 完成后直接把用户带到 dashboard，而不是停在命令输出。
   - 参考：`_references/opensource/openclaw/src/commands/onboard.ts`
3. Dashboard 文档明确把控制面定义为受保护的 admin surface，并给出 reopen / auth / SSH tunnel 的连续路径。
   - 参考：`_references/opensource/openclaw/docs/web/dashboard.md`
4. readiness 应拆成 `doctor / status / security audit`，把“能不能用”“现在状态如何”“是否有风险”分开表达。
   - 参考：`_references/opensource/openclaw/docs/cli/doctor.md`、`status.md`、`security.md`
5. approval / pairing / task control / config apply 应处于同一条工作台路径里，而不是 scattered pages。
   - 参考：`_references/opensource/openclaw/docs/web/control-ui.md`

### 不该照抄

1. Control UI 以 admin surface 为中心，适合 operator，不适合直接作为小白首页。
2. token 放在 `localStorage` 的方式只能在其 threat model 下成立，OctoAgent 不能无条件照搬。

## Agent Zero

### 值得吸收

1. Welcome/UI 强调“先告诉用户系统现在能不能用、哪里有风险、能否直接开始”。
   - 参考：`_references/opensource/agent-zero/README.md`
2. 顶层 agent -> subordinate agents 的层级关系在架构文档里非常明确，便于做用户解释。
   - 参考：`_references/opensource/agent-zero/docs/developer/architecture.md`
3. Memory、projects、settings、scheduler 都被组织成日常入口，而不是 operator 资源树。
   - 参考：`_references/opensource/agent-zero/README.md`
4. Memory 需要显式展示当前 scope、来源和内容，而不是只做“自动记住”。
   - 参考：`_references/opensource/agent-zero/docs/guides/projects.md`、`python/api/memory_dashboard.py`

### 不该照抄

1. Agent Zero 允许更强的“计算机即工具”默认权限；OctoAgent 仍要保留 safer-by-default 和 approval。
2. 其很多 UI/README 描述依赖容器内单体运行时和项目布局，不能直接映射到 OctoAgent 的 control-plane canonical contract。
3. UI 直接覆盖全量 settings 或直接删除 durable memory 的做法不适合 OctoAgent；040 仍应坚持 `review / apply / provenance`。
