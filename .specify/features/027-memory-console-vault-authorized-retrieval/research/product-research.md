# Feature 027 产品调研：Memory Console + Vault Authorized Retrieval

**日期**: 2026-03-08  
**调研模式**: full / product  
**核心参考**:
- `docs/m3-feature-split.md`
- `docs/blueprint.md`
- `.specify/features/020-memory-core/spec.md`
- `.specify/features/025-project-workspace-migration/spec.md`
- `.specify/features/026-control-plane-contract/spec.md`
- `_references/opensource/agent-zero/python/api/memory_dashboard.py`
- `_references/opensource/openclaw/src/cli/memory-cli.ts`
- `_references/opensource/openclaw/docs/reference/session-management-compaction.md`

## 1. 产品问题

当前 OctoAgent 已经具备 Memory 治理内核与正式 Control Plane，但 Memory 对操作者仍然是“系统内部能力”，不是“可理解、可授权、可审计的产品对象”：

- 020 已交付 `SoR / Fragments / Vault / WriteProposal`，但只有 service/store contract，没有 operator-facing 浏览与审计面。
- 026 已交付统一控制台，但 Memory/Vault 仍只留入口，没有详细视图、授权检索和证据链。
- 025-B 已经把 project/workspace/secret/wizard 收拢成正式主路径，用户现在会期待 Memory 也能按 project/workspace 被解释和管理。
- Vault 当前只有 default deny skeleton，尚未形成“申请授权 -> 查看授权记录 -> 执行检索 -> 追踪证据”的产品闭环。

因此 027 的产品目标不是“做一个记忆搜索框”，而是把 Memory 变成 operator 真能理解和信任的控制面对象。

## 2. 参考产品信号

### Agent Zero

- `memory_dashboard.py` 把 memory 作为 operator 可浏览、可删除、可搜索的对象暴露出来，而不是完全藏在运行时内部。
- 它强调“按目录/区域浏览 + 搜索 + 查看原始元数据”，说明 Memory 产品面首先要解决可理解性和可检查性。
- 但它对治理和授权链较弱，更多是 dashboard/maintenance 工具，这意味着 OctoAgent 不能简单照搬。

### OpenClaw

- `memory-cli.ts` 和 compaction 文档把 memory 看成可检查的系统对象，强调 session/context hygiene、compaction hooks、operator diagnostics。
- 它说明 Memory 产品面要和 session/project/runtime 管理视图连在一起，而不是做一张孤立页面。
- 它同样没有完整的 Vault 授权产品面，因此 OctoAgent 需要把自己的 Vault/WriteProposal 治理优势显式做成产品能力。

## 3. 从用户视角的真实需求

### 3.1 我想知道“系统为什么记住了这件事”

仅有 `search_memory()` 结果不够。用户需要看到：

- 这是 SoR 还是 Fragment 还是 Vault 引用
- 它属于哪个 project/workspace/scope/partition
- 当前版本是不是 `current`
- 被谁、基于什么 evidence 提案出来
- 是否已经被 supersede

### 3.2 我想知道“敏感内容为什么现在看不到、什么时候能看”

Vault 默认 deny 是正确的，但如果系统只返回“拒绝访问”，用户并不知道：

- 应该向谁申请
- 授权是否已经存在
- 授权的范围/时效/证据是什么
- 本次检索结果为何被 redact 或为何允许明细展开

### 3.3 我想知道“WriteProposal 有没有真正落成事实”

对长期记忆来说，真正重要的是：

- proposal 来自哪个 ingest/worker/import/compaction
- validate 是否通过，为什么拒绝
- commit 是否成功
- 最终写入的是 Fragment、SoR 还是 Vault skeleton

这决定了 027 必须提供 proposal 审计视图，而不只是 record 列表。

## 4. 对 027 的产品结论

- Memory Console 的主心智应是“浏览 + 审计 + 授权”，不是“全文搜索替代品”。
- 默认视图必须先给出安全摘要、分层标签和证据引用，再按授权状态决定能否展开敏感原文。
- Memory 必须挂在现有 Control Plane 里，保持和 `Projects / Sessions / Diagnostics / Automation` 同一导航层级，避免再造第二套管理台。
- Vault 授权不能只是一个布尔开关；它必须是带 request/decision/result/evidence 的正式记录。
- WriteProposal 审计要把“提案、验证、提交、落盘结果”串成一条 operator 可解释链，而不是只有底层 SQLite 行。

## 5. 范围建议

### In Scope

- Memory 浏览器：按 `project / workspace / partition / scope / layer` 浏览
- `subject_key` 当前版本与 superseded 历史
- evidence refs 与来源摘要
- Vault 授权申请、授权记录、授权检索结果
- WriteProposal 审计视图
- Memory export / inspect / restore 校验入口
- control plane 内的正式导航与 resource/action 接线

### Out of Scope

- MemU 深度召回、分类、ToM、多模态推理
- 旁路写 SoR 的“快捷修记忆”能力
- 新造独立 Memory 管理台框架
- 把 Vault 原文默认暴露给 Web/Telegram

## 6. 产品风险

- 如果直接把底层 `content` 暴露到 UI，会破坏 Vault default deny 和敏感信息最小暴露原则。
- 如果只做搜索结果列表，不做 `subject_key` 历史与 proposal 审计，用户仍无法理解“哪条才是当前事实”。
- 如果授权链没有单独记录，Vault 检索会变成一次性临时开关，后续无法审计谁在什么时候看过什么。
- 如果 Memory 仍不按 project/workspace 展示，在 025-B 已引入 project 语义后会直接造成用户理解错位。
