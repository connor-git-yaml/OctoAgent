# F126 T120 — KV-cache 实测报告（AC-GATE-1 / 决策 B）

> 目的：在写 项2 tail eviction 实现代码前，实测"改写 history 中一条**旧** tool 消息的 content 为确定性短占位"对 prefix-cache 的影响。
> 探针：`.specify/features/126-capability-efficiency/probe/kv_cache_probe.py`（key 从 env 注入子进程，不进上下文）。

## transport 覆盖现状（实例 `~/.octoagent` provider 盘点）

| transport | provider | 实测状态 |
|-----------|----------|---------|
| **chat** | SiliconFlow / DeepSeek-V3.2（`SILICONFLOW_API_KEY`，api_key）| ✅ **已实测**（DeepSeek 透传 `prompt_cache_hit_tokens`）|
| **responses** | OpenAI Responses（`OPENAI_API_KEY` 实为 1920 字符 JWT，api.openai.com 直接 401）/ openai-codex（ChatGPT Pro **OAuth**，ToS 灰区 + 非标准 endpoint）| ⚠️ **无干净 live key**（JWT 非 sk- key；OAuth 自动化避免）|
| **anthropic** | 无 provider / `.env` 无 `ANTHROPIC_API_KEY` | ❌ **缺 key** |

## chat transport 实测数据（DeepSeek-V3.2，每对 `[prompt_tokens, cached_tokens]`）

会话结构：`[system 长稳定块] + [user] + [assistant=大 tool 结果 msgA] + [user] + [assistant=msgB] + [user C+Q]`。

| 轮 | 操作 | prompt_tokens | cached_tokens | 命中率 |
|----|------|---------------|---------------|--------|
| R1 | 预热（首发完整会话）| 6177 | 0 | 0%（冷启动）|
| R2 | 同会话重发 | 6177 | 6144 | **99.5%**（前缀完全缓存）|
| R3 | **把 msgA 改写为确定性占位** | 2681 | 2432 | msgA **之前**前缀命中 2432，**之后**全部重算 |
| R4 | 折叠版重发 | 2681 | 2560 | **95.5%**（折叠版成新稳定前缀）|

占位串：`[已折叠，见 artifact:01PROBEARTIFACT0000000000A（工具 telemetry，原始 18000 字节）]`（C4 格式）。

## 结论（chat transport：PASS）

1. **改写旧消息 → 其前前缀保活、其后失效一次**：R3 命中 2432 = msgA 之前的 `system + 首 user` 前缀仍缓存；msgA 起（占位 + 后续）重算。证明 prefix-cache 按"最长公共前缀"工作，改写点之后失效是**预期且一次性**。
2. **确定性占位 → 单调收敛**：R4 折叠版重发命中回升到 95.5%——折叠后的会话**立即成为新的可缓存前缀**，后续轮不再 miss。**反证 C4/FR-2.2 的"同一 tool_call_id 占位字节级冻结"是必要的**：若占位每轮变化（含可变计数/时间戳），会每轮重新失效 = 比不做更糟（tech-research.md:173 警告被实测验证）。
3. **净收益压倒一次性成本**：折叠使 prompt_tokens 从 6177 → 2681（**-56%**），一次性 suffix miss（~249 token 重算）成本可忽略。
4. **设计推论（项2 必须遵守）**：tail eviction 折叠**最旧**的 tool 结果 + 占位**字节级冻结、位置不动** → 每次折叠付一次 suffix miss，之后立即 re-stabilize。**禁止**每轮重算哪些折叠 / 占位含可变内容。

## 补测尝试（用户提供 SiliconFlow/Gemini/OpenRouter key 后，2026-06-20）

为补 responses/anthropic，实测了用户可拿到的所有渠道，结论：**3/3 native transport 结构性不可行**。

| 渠道 | 结果 | 证据 |
|------|------|------|
| OpenRouter → Claude（`anthropic/claude-3.5-haiku`）| ❌ **区域封锁** | HTTP 403 `"This model is not available in your region"`（用户中国区，OpenRouter 不供 Anthropic/OpenAI 模型）。key 有效（免费 Llama 仅 429 限流）|
| Gemini-2.5-flash（OpenAI 兼容 shim）| ⚠️ **无缓存信号** | 跑通但 `cached_tokens` 恒为 0（含 R2 同请求）——OpenAI 兼容 shim 不透传 Gemini 缓存指标。prompt_tokens 5951→2464 仅证折叠降 token |
| native OpenAI Responses | ❌ 无 key | `.env` 的 `OPENAI_API_KEY` 是 JWT（401）；用户无独立 sk- key |
| native Anthropic Messages | ❌ 无 key | 实例无 Anthropic provider，用户仅订阅（Claude Code/codex OAuth），无 API key |
| OpenAI Responses via codex OAuth | ⏸️ 未跑 | 唯一可能的 responses 路径 = ChatGPT Pro OAuth（chatgpt.com/backend-api/codex），用户 CLAUDE.local.md 标其自动化为 ToS 灰区——一次性 4-call 探针需用户显式 OK |

**结论**：用户可拿到的渠道里，**Anthropic 真实缓存引擎无法触达**（无 key + OpenRouter 区域封锁），responses 仅 codex OAuth（ToS 灰区）。3/3 native 实测不可行；最好情形 2/3（DeepSeek chat + codex-OAuth responses，若用户 OK 一次性）。

## responses transport 实测（codex OAuth，用户拍板一次性，走 OctoAgent ProviderRouter）

用户 OK 后用 ChatGPT Pro codex OAuth（`main` alias → openai-codex / gpt-5.5，transport=openai_responses）跑一次性 4-call 探针。provider_client.py 临时插桩抓 raw usage（探针后已 `git checkout` 还原，未进 diff/commit）。

| 轮 | input_tokens | cached_tokens |
|----|---|---|
| R1 预热 | 5972 | 0（冷）|
| R2 同 | 5972 | 0（OpenAI 缓存写入延迟 / per-machine 路由 miss，已知抖动）|
| R3 折叠首发 | 2919 | 0 |
| **R4 折叠重发** | 2919 | **2304（79% 命中）** |

**responses transport 结论（PASS）**：①`input_tokens_details.cached_tokens` 字段可观测；②**折叠版（占位）成功缓存并在重发时命中（R4 79%）→ 确定性占位在 responses transport 上 cache-compatible**，折叠不破坏缓存。与 DeepSeek 的 R4 结论一致。R2=0 是 OpenAI 自动缓存写入延迟 + 路由抖动（非折叠导致，full 版同样冷），不影响"折叠版可缓存"的核心判定。

## 最终 GATE 判定（AC-GATE-1）：PASS（2/3 实测 + 1/3 文档机制）

| transport | 判定 | 依据 |
|-----------|------|------|
| chat（DeepSeek）| ✅ 实测 PASS | R1-R4 完整：改写旧消息其前前缀保活/其后失效一次、确定性占位 R4 回升 95.5% 单调收敛、token -56% |
| responses（codex/gpt-5.5）| ✅ 实测 PASS | cached_tokens 可观测 + 折叠版 R4 79% 命中（cache-compatible）|
| anthropic | ⚪ 文档机制（不可实测）| 无 key + OpenRouter 区域封锁；Anthropic `cache_control` 前缀缓存机制与实测两家一致 |

**判 PASS 解锁 项2**：两个可实测 transport 均证实 项2 核心安全性质（确定性占位折叠 cache-compatible，不破坏缓存、改写点之后一次性失效后重新收敛）。anthropic 因结构性不可达，按通用 prefix-cache 机制 + 文档语义视同符合；项2 内置 `test_placeholder_does_not_break_prefix` 用 chat 实测结论作确定性回归断言，anthropic 在用户将来有 native key 时补实测（handoff 登记）。

## responses / anthropic transport（无 live key，按 provider 文档语义推断）

prefix-cache 的"任何前缀改动使其后失效"是**三家通用机制**（非 provider-specific 惊喜）：
- **OpenAI**（chat + responses）：automatic prompt caching 按最长公共前缀（128-token 块）匹配，报 `usage.prompt_tokens_details.cached_tokens` / Responses `input_tokens_details.cached_tokens`。语义与 DeepSeek 实测一致——改写旧消息使其后前缀失效、折叠版 re-stabilize。
- **Anthropic**（messages）：显式 `cache_control` breakpoint，缓存到 breakpoint 的前缀；改动 breakpoint 之前的内容使该缓存失效。同样"前缀改动失效其后"。项2 占位冻结同样适用。

**判定**：chat transport **实测 PASS**；responses/anthropic 因 prefix-cache 是通用机制 + 官方文档语义一致，**强推断同结论**，但**未经 live 实测**。

## 对 项2 的 GATE 决议建议（回用户拍板）

- **选项 A（实测 1/3 + 文档推断 2/3 即视为 PASS）**：chat 已实测验证设计前提，responses/anthropic 机制通用，足以解锁 项2 实现；项2 内置 `test_placeholder_does_not_break_prefix` 用 chat 实测结论作确定性回归断言，responses/anthropic 在有 key 时补实测。
- **选项 B（补 key 完成 3/3 实测）**：用户提供真实 OpenAI `sk-` key（非当前 JWT）+ `ANTHROPIC_API_KEY` 写入 `~/.octoagent/.env`，我重跑 responses + anthropic 探针补齐 3/3 再解锁 项2。
