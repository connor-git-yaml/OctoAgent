# F149 Code Smell Baseline / Review Gate

> 扫描基线与 current 都是 `origin/master@9d5e1e4`；当前没有生产改动，所以 `current=baseline`。该文件记录真实 source/import/call facts，不以对本报告关键词做 `rg` 作为 Gate。

下表前端短路径均相对 `octoagent/frontend/src/`，后端路径均相对 `octoagent/`；未来 Review 输出必须展开为完整 repo-relative path。短路径只在当前表内用于避免重复前缀，不改变 `path:line` 定位。

## 1. 可机械扫描项

| smell | source path + line | baseline → current | 判定 | 处置 / owner |
|---|---|---|---|---|
| direct transport | `SkillCenter.tsx:17,23,32,45`；`AgentCenter.tsx:273,295`；`api/approval-center.ts:112,136,192`；`api/memory-candidates-types.ts:34` | F149 closure 10 calls → 10 | F149 MUST FIX | 迁入 `api/client` 唯一 transport + adapter；owner F149 |
| token/header 分叉 | `approval-center.ts:103-105`；`memory-candidates-types.ts:26-32` | 2 helper implementations → 2 | F149 MUST FIX | 删除 helper 自建鉴权；404/409 保留 error mapper；owner F149/F150 boundary |
| 重复 transport/state 主路径 | Workbench snapshot/action 与 Agent direct override fetch、Skill direct REST、approval helper、memory helper并存 | 1 canonical + 4 bypass clusters → same | F149 MUST FIX | 单一 transport，不新增 store/optional fallback；owner F149 |
| import 方向 | `approvalModels.ts:8,13` 的 pure-ish model 同时 import UI `DiffBody` 与 API helper；`BehaviorVersionHistory.tsx` 是 UI，允许 import component/application | 2 questionable edges → 2 | F149 MUST FIX only touched seam | projection 改为自身 row/error VM；checker 按文件职责，不按 `domains/` 目录误判；owner F149 |
| complexity/God file | `SettingsPage.tsx` 997 wc/998 checker lines；`AgentCenter.tsx` 843/844；`chatStreamHelpers.ts` 477/500；`index.css` 4476 wc/4477 checker、上限4480 | same | RATCHET；Settings/stream 接近阈值 | 仅按真实 seam 拆 mapper/decoder/state；维护归位必须用窄 `MaintenanceRecoverySection` 一类组件组合，禁止 big-bang；owner F149 + future Fix |
| compat/fallback layering | `SettingsPage.tsx:269-290,615-617` 仍组装已退役 LiteLLM runtime fallback；`MemoryFiltersSection.tsx:12` 有历史 scope compat | 2 clusters → 2 | Settings touched cluster MUST FIX；Memory compat RATCHET | 不新增 fallback；Settings 等 F151 contract 后删 touched stale path；Memory另 Fix |
| dead/unreachable | `MemoryActionsSection.tsx:28` 无生产 importer；`MemoryPage.tsx:46` 与该 section `:155` 链接不存在的 `/advanced` | 1 dead component + 2 dead links → same | F149 MUST FIX | 不纳入 Memory contract；移除/替换死链；dead file 是否删除由 Tasks 精确判断；owner F149 |
| 过宽 DTO/raw leakage | `types/index.ts:1855-1877` snapshot 漏 envelope；`:72` event raw；`:1797-1831` action schemas/data raw；`McpInstallWizard.tsx:15-28` 手写 wire result | 4 touched clusters → 4 | F149 MUST FIX finite slice | raw boundary + decoder；generated consumed DTO；不迁全 types hub；owner F149/F150/F151 contract |
| secret egress | `mcp_service.py:123-151` catalog env；`McpProviderCenter.tsx:56-63` 回填；`SettingsPage.tsx:372-405` apply 后未清空 | 3 leaks → 3 | SECURITY MUST FIX | §contract keep/replace/remove + full egress oracles；Settings/MCP 窄 HTTP/application slice owner 均为 F149，复用现有 store/boundary，不依赖独立 Fix |
| circular dependency | 2026-07-20 relative import graph scan：111 production TS/TSX files、239 edges、0 SCC cycles | 0 → 0 | RATCHET | future AST checker 必须保持 0；owner F149 architecture gate |
| style/theme drift | `index.css` 4476 wc；`SkillCenter.tsx` modal 使用硬编码浅色（既有 recon） | index 0 growth，1 touched palette cluster | F149 MUST FIX touched / RATCHET global | `--cp-*` only、index zero growth；owner F149 |

Import cycle baseline 使用真实 production `.ts/.tsx` relative import graph（排除 tests），Tarjan SCC 结果为 0。未来 checker 必须用 parser/AST 复现并加入 alias resolution；本次 regex graph 是 baseline evidence，不冒充最终 Gate。

## 2. 需要 adversarial review 的判断项

| smell | source evidence | baseline/current | Review 问题 | 判定/owner |
|---|---|---|---|---|
| 职责漂移 | `SettingsPage.tsx:293-405` 同时构造 wire draft、保存 secret state、解释 response、控制 modal；`TaskDetail.tsx:54-65` 直接摘要 raw payload | 2 high-risk clusters → same | 是否可把 decoder/projection/state 抽为纯逻辑，而不新增 per-page service？ | F149 touched seam MUST FIX；Review owner |
| 命名失真/概念泄漏 | Settings 仍生成 `litellm_proxy_url/master_key_env`；普通 Task Raw Data、MCP command/env | ≥2 clusters → same | 用户文案是否仍出现退役/内部协议概念？Advanced 是否真正净化？ | F149 MUST FIX；product+security review |
| 隐藏全局状态 | F149 helpers直接读取 F150 token；MCP `setInterval`/refs 内部轮询；Settings secret React state | 3 clusters → same | 是否由 owner seam 注入/清理？是否存在第二主路径？ | token bypass/secret MUST FIX；polling RATCHET |
| mock-self test | Approval test `vi.mock` API（18-53）并多处只断言 call；Memory test 多处只断言 `submitAction`；Agent test stub global fetch | 3 candidate suites → same | 每个用例是否还断言 DOM/view-state/decoder contract，而不是只验证 mock 回传/被调用？ | 尚未逐用例判死刑；Tasks/Review 必须逐 test 分类，owner F149 Review |
| 不可达分支 | MCP poll `catch {}`（`McpInstallWizard.tsx:149`）、Agent多个 silent catch（286/309/378/398） | 5 silent branches → same | 分支是否真实可触达？若可达是否有用户 state/oracle；若不可达是否删除？ | touched branches MUST FIX/证明；owner F149 |

## 3. Gate 输出格式

未来 `review/code-smell-audit.md` 每行必须继承本表并填：`baseline`、`current`、`delta`、`source_path:line`、`mechanical_or_adversarial`、`must_fix|ratchet|future_fix`、`owner`、`evidence_command/output`。本 Feature MUST FIX 必须清零；ratchet 不得回退；future Fix 必须写 exact seam，禁止借机全站重写。

AST boundary/style/coverage checker 当前不存在，创建纪律见 `gate-prerequisites.md`。在 checker behavior RED→GREEN 前，不得报告 architecture Gate PASS。
