# 产品调研报告：Feature 016 Telegram Channel + Pairing + Session Routing

**特性分支**: `codex/feat-016-telegram-channel`
**调研日期**: 2026-03-07
**调研模式**: 离线 + 本地参考实现

## 1. 需求概述

**需求描述**: 基于 `docs/m2-feature-split.md` 的 Feature 016，交付 Telegram 作为首个真实外部渠道，并打通 pairing、allowlist、webhook/polling、session routing 和基础回传语义。

**核心功能点**:
- Telegram 入站消息稳定进入 `NormalizedMessage -> Task` 链路
- 未授权私聊默认进入 pairing / allowlist 流程，而不是静默执行
- DM、群聊、forum topic、reply thread 的 `scope_id` / `thread_id` 稳定映射
- Telegram 回传文本、审批提示、错误提示与重试结果

**目标用户**:
- 主要用户是 OctoAgent 的 owner / operator，需要在手机上直接驱动系统
- 次级用户是被允许进入群聊协作的可信成员

## 2. 参考产品现状

### OpenClaw 的成熟做法

- Telegram 已是生产级入口，默认 DM 策略就是 `pairing`
- 群聊和私聊的授权边界明确分离，不让群聊继承 DM pairing 结果
- 回传不仅是文本发送，还包含 reply、topic/thread、inline actions 和失败降级

### OctoAgent 当前缺口

- `octo onboard --channel telegram` 已有 verifier contract，但默认因 verifier 未注册而阻塞
- Gateway 只有 Web/API 入站，没有 Telegram transport 运行面
- 配置体系还没有 `channels.telegram` 这类 channel 级单一事实源

## 3. 用户痛点与价值判断

### 当前痛点

1. 用户已经完成 `octo config` 与 `octo doctor`，但仍然无法真正从 Telegram 发第一条消息
2. 缺少 pairing / allowlist 时，系统无法明确表达“谁可以发消息、谁被拦截”
3. 没有稳定的群聊与 topic 路由规则，后续 approval / retry / cancel 无法在移动端可靠落同一线程

### 价值判断

- 016 是 M2 “第一次真实外部可用” 的门槛，不是锦上添花
- 如果 016 只做 webhook 接入、不做授权与路由，用户得到的仍然是不安全且不可回放的半成品
- 如果 016 吞掉 inbox / mobile controls，会与 017 混线，反而拖慢交付

## 4. 用户场景验证

### 核心角色

**Persona 1: Owner / Operator**
- 背景：已经配置好 LiteLLM、Doctor，通过手机管理个人 AI OS
- 目标：从 Telegram 直接发起任务、查看结果、完成配对和审批
- 痛点：不希望在 Web UI、CLI、日志之间频繁切换

**Persona 2: Trusted Group Member**
- 背景：在授权的群聊中与 owner 协作
- 目标：在群里向 Agent 提问，得到稳定回复
- 痛点：群消息噪音高，必须明确哪些消息会触发 Agent，哪些不会

### 关键用户旅程

1. **首次配对旅程**：用户在 Telegram 私聊 bot，若未授权则拿到 pairing code；owner 审批后，重新发送即可进入任务链路
2. **群聊协作旅程**：bot 被加入授权群组，消息根据 group policy、topic/thread 与提及规则进入固定 `scope_id/thread_id`
3. **结果回传旅程**：Agent 在 Telegram 中按原会话回复文本、错误或审批提示，用户无需回到 Web 才知道当前状态

## 5. MVP 范围建议

### Must-have（MVP）

- 单 bot Telegram 渠道配置与健康校验
- webhook / polling 双模式，默认优先 webhook
- DM pairing / allowlist 与群组 allowlist
- `scope_id` / `thread_id` 稳定映射（DM、群聊、forum topic、reply thread）
- 文本回传、审批提示、错误提示、重试结果回传
- 与 `octo onboard --channel telegram` 的真实 verifier 闭环

### Nice-to-have（二期）

- 更丰富的 Telegram UI 组件（复杂卡片、多按钮布局、媒体回传）
- 群聊 mention policy 与更细颗粒 topic allowlist
- 更丰富的 outbound retry / dead-letter 诊断面板

### Future（后续 Feature）

- 统一 operator inbox（Feature 017）
- 多 bot account / 多渠道一致操作控制
- 更复杂的媒体、poll、文件上传回传

## 6. 结论与建议

### 总结

Feature 016 的产品本质是“把 Telegram 变成 OctoAgent 的真实入口”，不是“提供一个能收消息的 webhook”。因此 pairing、授权边界、session routing 和基础回传语义都必须进入 MVP。

### 对技术调研的建议

- 优先评估如何在现有 Gateway 生命周期内接入 Telegram runtime，而不破坏 WebChannel
- 优先冻结 Telegram 的 canonical routing 规则，避免后续事件与 approval thread 漂移
- 把 `octo onboard` / `octo doctor` 所需的 Telegram readiness 检查纳入同一设计，而不是做两套逻辑

### 风险与不确定性

- 风险 1：如果 pairing 状态不 durable，Gateway 重启后授权边界会丢失
- 风险 2：如果 webhook/polling 切换不做幂等，重复投递会制造重复 Task
- 风险 3：如果把 operator inbox 一并纳入 016，会直接破坏 M2 并行拆分边界

