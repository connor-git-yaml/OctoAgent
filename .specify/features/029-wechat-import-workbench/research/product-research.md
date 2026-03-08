# Feature 029 产品调研：WeChat Import + Multi-source Import Workbench

**日期**: 2026-03-08  
**调研模式**: full / product  
**核心参考**:
- `docs/m3-feature-split.md`
- `docs/blueprint.md`
- `.specify/features/021-chat-import-core/spec.md`
- `.specify/features/025-project-workspace-migration/spec.md`
- `.specify/features/026-control-plane-contract/spec.md`
- `.specify/features/027-memory-console-vault-authorized-retrieval/spec.md`
- `octoagent/frontend/src/pages/ControlPlane.tsx`
- `octoagent/packages/provider/src/octoagent/provider/dx/chat_import_commands.py`
- 在线补充：WeChat Help Center、PyWxDump / Python-Wxid-Dump README、Airbyte connector docs（见 `research/online-research.md`）

## 1. 产品问题

当前 OctoAgent 已经具备 021 的 Chat Import Core、025 的 Project/Workspace 主路径、026 的 Control Plane、027 的 Memory/Vault 可视面，以及 028 的 MemU integration point，但导入体验仍然停留在“工程入口”：

- 021 已经提供 `octo import chats`、`--dry-run`、`cursor/resume`、`ImportReport`，但输入仍然是单一 `normalized-jsonl` 文件。
- 026 已经把 `import.run` 接到 Control Plane，但当前 Web 端只有一个 `Import Path + Source Format` 的简易触发区，没有 source-specific 向导、mapping 预览、dedupe 结果和 resume 入口。
- 027 已经让 Memory/Proposal/Vault 可被审计，因此用户会自然期待“导入会写入哪些 fragment / proposal / SoR / Vault ref”也能先看清楚，再决定执行。
- M3 明确要求“普通用户 Ready”，这意味着导入不能再要求用户手工把不同来源先转成内部 `normalized-jsonl`，否则 029 只是在 021 外面再包一层 CLI。

所以 029 的产品目标不是“再加一个 WeChat 命令”，而是把导入升级成一个**可预览、可修正、可恢复、可审计**的正式工作台。

## 2. 外部产品/项目信号

### 2.1 WeChat 官方与社区现实

- WeChat 官方个人数据导出面向账户数据，不提供“从服务器拉取完整聊天历史”的产品路径；聊天记录主要在本地设备侧。
- 社区项目如 `PyWxDump`、`Python-Wxid-Dump`、`WechatExport-iOS` 普遍采用“解析本地数据库 / 本地导出包 / HTML/CSV/JSON 导出”的路线，且都把媒体文件路径解析视为一等能力。
- 这意味着 029 的 WeChat 适配器不应设计成“依赖在线 API”，而应围绕**用户提供的本地导出物**来建模。

### 2.2 导入工作台的产品模式

来自 Airbyte 一类连接器产品的成熟模式很一致：

- source-specific 配置与验证先于真正导入
- dry-run / test connection / preview 先建立用户信任
- cursor state / resume 是一等对象，不是失败后再想办法
- dedupe / typing / warnings 不应静默吞掉，而应在报告里显式呈现

这对 029 的直接启发是：

- 工作台必须分出“准备态”和“执行态”
- mapping 与 dedupe 结果必须是用户可见的正式输出
- warnings/errors 不能只落日志

## 3. 从用户视角的真实需求

### 3.1 我不想先学内部格式，才有资格导入

对普通用户来说，“先自己写一份 normalized-jsonl 再调用 CLI”不是产品路径。用户真正理解的是：

- 这是微信导出目录 / HTML / JSON / SQLite snapshot
- 这是这个来源对应的 project/workspace
- 这些会被导进哪些 chat scope、哪些 memory partition

所以 source-specific adapter 是 029 的产品核心，而不是技术附属。

### 3.2 我需要先看影响，再决定要不要执行

用户想提前知道：

- 会导入多少消息 / 会跳过多少重复
- 会生成多少附件 artifact / fragment
- 会产生多少 proposal / commit 候选
- 哪些 source conversation 会映射到哪些 project/workspace/scope
- 当前有哪些 warnings / parse errors / attachment 缺失

这要求 dry-run 不能只返回 counts，而要成为工作台的第一视图。

### 3.3 我需要中断后能继续，而不是重头来过

WeChat 和多源导入天然是批量、长耗时、容易半路中断的操作。用户真正需要的是：

- “上次导到哪里了”
- “哪些 conversation 已经完成 / 还没完成”
- “哪些错误是可修复后继续的”
- “resume 以后会不会重复污染 memory”

所以 resume/cursor 不是技术细节，而是工作台的核心体验。

### 3.4 我需要看懂导入最终如何影响 Memory

在 027 已经把 proposal/vault 做成可见产品对象之后，029 不能只说“导入成功”：

- 导入是 fragment-only，还是提出了 facts
- 哪些 proposal 被 validate/commit
- 哪些敏感附件仅进入 artifact/Vault ref，未暴露原文
- MemU 是否参与了附件或 fragment 的索引，同步是否 degraded

## 4. 对 029 的产品结论

- 029 的主路径应该是**Import Workbench**，而不是“多几个 import flag”。
- WeChat 只是首个 source-specific adapter，但工作台要按“多源通用壳 + source-specific step/hints”设计。
- 用户主心智应是：
  1. 选择来源
  2. 校验/解析
  3. 预览 mapping + dedupe + 影响
  4. 执行或修正
  5. 查看报告 / warnings / errors / resume
- 附件必须进入 artifact-first 路径，并把摘要/引用继续送往 fragment 与 MemU integration point，而不是直接把二进制内容塞进 Memory 文本层。
- 导入结果必须和 027 的 Memory/Proposal/Vault 视图打通，用户才能真正理解“导入后系统记住了什么”。

## 5. 范围建议

### In Scope

- WeChat source adapter（基于用户提供的本地导出物）
- 多源导入工作台
- dry-run / mapping / dedupe / warnings / errors / cursor / resume
- 附件进入 artifact / fragment / MemU 管线
- 导入提案与 Memory proposal/commit 打通
- Control Plane 中的导入报告、resume 入口和运行状态

### Out of Scope

- 直接从在线 WeChat 服务端拉取历史消息
- 新造一套独立 import console framework
- 把 031 的全量验收矩阵、真实样本库和用户 Ready 验收偷带进 029
- 越过 WriteProposal/SoR/Vault 治理直接落权威事实
- 在 029 内解决全部多源 connector 生态，只需冻结 adapter contract 并落首个 WeChat adapter

## 6. 产品风险

- 如果 029 只做 WeChat CLI adapter，不做 workbench，普通用户仍无法安全操作批量导入。
- 如果 mapping/dedupe/warnings 不可见，用户会把导入视为黑箱，很难信任 Memory 结果。
- 如果附件只是当作“消息文本附注”处理，会损失 artifact provenance，并让 028 的 MemU 多模态入口变得不可追溯。
- 如果 import 报告没有进入 Control Plane，运维与恢复路径会再次分叉回 CLI-only。
