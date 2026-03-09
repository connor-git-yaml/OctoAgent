# 技术调研报告：Feature 033 Agent Profile + Bootstrap + Context Continuity

## 1. 本地代码现状

### 1.1 主 Agent 没有正式上下文装配层

已确认的真实运行链：

- `TaskService.process_task_with_llm()` 最终调用 `_call_llm_service()`
- `_call_llm_service()` 直接把 `user_text` 传给 `llm_service.call()`
- `LLMService._try_call_with_tools()` 只在 `selected_tools_json` 非空时创建一个临时 `SkillManifest`
- 这个临时 manifest 的 prompt 仍然只是 `user_text`

结论：

- 目前主 Agent 不会主动读取 project instructions、owner profile、bootstrap、recent summary 或 memory hits
- 030 的 dynamic tool selection 解决了“带什么工具”，没有解决“带什么上下文”

### 1.2 Memory 已有 read/write core，但未进入主对话路径

`packages/memory` 当前已经提供：

- `search_memory()`
- `get_memory()`
- `WriteProposal -> validate -> commit_memory()`
- SoR / Fragments / Vault / MemU degrade path

但在生产代码中，`search_memory()` / `get_memory()` 基本只出现在：

- import / chat import
- memory console / control plane
- tests

结论：

- Memory Core 已经存在，真正缺的是 runtime consumer

### 1.3 030 只落了 worker-facing profile，不是 main-agent profile

`packages/core/models/capability.py` 当前只有：

- `WorkerCapabilityProfile`
- `WorkerBootstrapFile`
- `BundledCapabilityPack`

没有：

- `AgentProfile`
- project default agent profile binding
- session / automation / work 的 `agent_profile_id`
- effective config snapshot resolver

### 1.4 短期上下文只存在进程内

`LiteLLMSkillClient` 当前维护 `_histories`：

- key = `{task_id}:{trace_id}`
- 仅存进程内
- 不 durable
- 不 project-aware
- 不可由 control plane 审计

结论：

- 当前 continuity 只能维持单进程内的少量 skill loop
- 对用户真正关心的“下次聊天还能接上吗”几乎没有帮助

## 2. 技术设计方向

### 2.1 分三层处理上下文

1. **Canonical profile layer**
   - AgentProfile
   - OwnerProfile
   - BootstrapSession

2. **Short-term continuity layer**
   - SessionContextState
   - recent turn refs
   - rolling summary
   - recent artifact refs

3. **Long-term retrieval layer**
   - MemoryService.search_memory()
   - MemoryService.get_memory()
   - evidence refs

这三层共同汇总到 `ContextFrame`。

### 2.2 `ContextFrame` 必须是 durable snapshot

原因：

- worker/delegation/automation 需要引用同一次上下文
- control plane 需要解释 provenance
- 重启恢复需要找到最近一次 context assembly 结果

因此 `ContextFrame` 不应该只是内存中的 prompt string，而应是可查询、可回放、可审计的结构化对象。

### 2.3 Memory 只做 retrieval / evidence，不替代 profile truth

技术边界建议：

- `AgentProfile` / `OwnerProfile` 存在 core store
- bootstrap 更新它们时，如需长期检索，可额外生成 memory proposals
- 主 Agent 读取 profile 时优先读 canonical profile，不从 Memory 拼 persona

这样可以避免：

- 把 profile 变成无法版本化的自然语言片段
- 把 owner preferences 混成不可控的 memory soup

### 2.4 接线点必须明确

建议的最小接线点：

- `TaskService`：resolve `ContextFrame`
- `LLMService`：消费 `ContextFrame.system_blocks`
- `DelegationPlaneService`：继承 `agent_profile_id` / `context_frame_id`
- `AutomationSchedulerService`：run-now / schedule 都走同一 resolver
- `ControlPlaneService`：读取 context resources + provenance

## 3. 技术风险

### 风险 1：只在 control-plane 做展示，不改 runtime

这是最危险的伪实现路径。必须直接由 integration test 阻断。

### 风险 2：把 recent summary 全部写进 Memory

会把短期 continuity 和长期事实治理混为一谈，导致 summary 噪声污染长期检索。

### 风险 3：bootstrap 只写 markdown 文件

虽然借鉴 OpenClaw 的形态，但这会再次退化为不可继承、不可审计的文本约定。

## 4. 技术结论

033 应新增一个正式的 `AgentContextService + ContextFrame durability` 层，把 025 的 project/wizard、027 的 memory console 背后的 retrieval core、030 的 bootstrap/capability/delegation 统一接进主 Agent 运行链。  
如果只做 Profile 模型或只做控制台页面，都会继续停留在“有能力、没用起来”的状态。
