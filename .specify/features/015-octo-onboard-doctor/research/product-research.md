# Feature 015 产品调研：Octo Onboard + Doctor Guided Remediation

**特性分支**: `codex/feat-015-octo-onboard-doctor`
**调研日期**: 2026-03-07
**调研模式**: full（产品 + 技术 + 在线补充）

## 1. 目标重述

Feature 015 的目标不是“再加一个 CLI 命令”，而是把 OctoAgent 的首次使用路径从离散的命令集合，收敛成一条可恢复、可诊断、可完成的 onboarding 流程：

- 从 `octo config` 开始，而不是让用户猜应该先跑哪个命令；
- 在 `octo doctor --live` 失败时，直接给出下一步可执行动作，而不是只打印检查项；
- 当用户中途中断时，能从上次步骤继续，而不是重新走一遍；
- 当渠道能力暂未就绪时，明确标记阻塞点和后续依赖，而不是让用户误以为系统已经可用。

## 2. 用户价值

### 新用户（Owner / 首次安装者）

- 降低首次启动门槛：不需要理解 `config`、`doctor`、`pairing`、`first message` 的先后关系。
- 降低失败焦虑：当 provider、Docker、Proxy 或 channel 不可用时，能得到明确的修复动作。
- 降低中断成本：中途退出终端、重启机器或修完环境后，可以继续而不是重来。

### 日常维护者

- 可以用 `octo onboard` 重新跑“健康恢复”路径，而不是只能人工拼命令排查。
- 可以快速区分“provider 未配置”“doctor 未通过”“channel 未接入”“首条消息未验证”四类状态。

## 3. 竞品体验启示

### 3.1 OpenClaw

- `openclaw onboard` 被明确定位为推荐安装路径，用户不用在 wizard、doctor、dashboard 之间自己编排顺序。
- `openclaw doctor` 不只报错，还提供 `--repair`、`--deep`、`pairing list` 等可执行修复路径。
- pairing 默认是安全前置，不会让未授权消息直接进入执行链路。
- dashboard 可以作为“快速进入控制面”的补充，而不是替代 onboarding 本身。

### 3.2 Agent Zero

- 安装文档聚焦“从零到 first working chat”，目标非常明确。
- 交互式终端和 Web UI 都强调“可以随时 intervene”，用户不会陷入黑盒执行。
- chat save/load 与 backup/restore 让用户感知到“出错了也能回来”，这本质上也是 onboarding 信心的一部分。

### 3.3 对 OctoAgent 的直接要求

1. 015 必须先解决“路径连续性”，而不是继续增加单点命令。
2. 015 的输出必须是 action-oriented remediation，而不是纯状态表。
3. 015 需要把“系统可用”定义成一个清晰终态，而不是让用户自己判断 doctor 通过后是否已经能发首条消息。

## 4. 当前 OctoAgent 用户缺口

### 4.1 入口仍然分裂

当前代码已具备：

- `octo config`（Feature 014）
- `octo doctor`
- 历史路径 `octo init`

但仍缺少：

- 将三者串成一次性向导的统一入口；
- 在失败时根据当前状态跳到下一步动作；
- 将“已经完成到哪一步”持久化。

### 4.2 doctor 仍偏“检查报告”，不够“修复助手”

`DoctorRunner` 当前返回的是平铺的 `CheckResult` 列表，虽然已有 `fix_hint`，但仍存在三个问题：

1. 缺少结构化动作类型，无法驱动向导自动跳到下一步；
2. 缺少按阶段归组，用户很难知道“我到底卡在 provider、runtime 还是 channel”；
3. 缺少“通过定义”，不能明确宣布“现在系统已可用”。

### 4.3 缺少 onboarding 的恢复语义

当前 CLI 没有任何 `OnboardingSession` 一类的进度持久化。用户如果：

- 配完 provider 后退出；
- 修完 Docker 再回来；
- 准备晚点再做 Telegram pairing；

系统无法告诉他“下次该从哪一步继续”。

### 4.4 渠道步骤必须和 016 解耦

Feature 015 需要覆盖“channel 接入”和“首条消息验证”的用户旅程，但真正的 Telegram ingress/egress、pairing、thread routing 属于 Feature 016。

产品上必须满足两个条件：

1. 015 要给用户看见完整路径；
2. 015 不能自己重新定义 Telegram 语义，避免破坏并行开发边界。

因此 015 更适合定义 channel onboarding contract，并在 channel 未就绪时给出明确阻塞说明。

## 5. 范围边界

### In Scope（本 Feature 必做）

- `octo onboard` 统一入口
- 可恢复的 onboarding session / step checkpoint
- doctor 的 action-oriented remediation 输出
- provider/runtime/channel/first-message 的阶段性状态汇总
- 当 channel verifier 可用时，执行首条消息验证；当不可用时，明确给出阻塞项和下一步动作

### Out of Scope（本 Feature 不做）

- 不实现 Telegram 渠道 transport、pairing 状态机、thread routing 本身（归 Feature 016）
- 不实现统一 operator inbox（归 Feature 017）
- 不实现 backup/restore 产品入口（归 Feature 022）
- 不引入新的 Web UI 向导；MVP 以 CLI 为主

## 6. 成功标准（产品视角）

1. 新用户可以通过单一命令进入完整上手路径，而不是记忆多条命令。
2. 用户在任一步失败后，都能得到“下一条命令或操作”，而不是纯错误文本。
3. 中断后恢复不要求用户重做已完成步骤。
4. 如果 channel 能力尚未接入，系统会明确告知阻塞原因和依赖 Feature，而不是伪装成成功。
5. 当所有步骤都通过时，系统明确输出“系统已可用”。

## 7. 产品风险

- 风险 1：015 把 016 的 Telegram 实现吞进来，导致范围膨胀
  - 策略：015 只拥有 channel onboarding contract 和 verifier 调度，不拥有 Telegram transport 实现。
- 风险 2：只做 wizard，不做恢复，导致体验仍然脆弱
  - 策略：session checkpoint 必须是 MVP 一部分。
- 风险 3：doctor 仍只是换皮输出，无法指导下一步动作
  - 策略：把 remediation 作为结构化结果建模，而不是字符串备注。
- 风险 4：系统“部分通过”时用户误判为已可用
  - 策略：显式定义 onboarding overall status：blocked / actionable / ready。

## 8. 结论

Feature 015 合理且必要，但它的核心不是“CLI 新命令”，而是“首次使用闭环 + 中断恢复 + 动作化诊断”。

建议的 MVP 是：

1. 以 `octo config` 和 `octo doctor --live` 为既有能力基线；
2. 新增 `octo onboard` 做流程编排与进度恢复；
3. 通过 channel verifier contract 为 Feature 016 留出并行接入口；
4. 把“系统已可用”定义成显式终态并在 CLI 摘要中输出。
