# F149 Code Smell Audit

## 结论

**PASS**。F149 范围内 MUST FIX=0，production relative-import cycle=0，page/domain direct transport=0，F149 token helper=0，secret egress=0，`index.css` 没有增长。保留的高行数文件和内部事件字面量均登记为 ratchet/future owner，不伪装成已消失。

## 机械与对抗审查

| smell | repo-relative path:line | baseline | current | delta | method | disposition / owner | evidence |
|---|---|---:|---:|---:|---|---|---|
| page/domain direct transport | baseline `SkillCenter.tsx`、`AgentCenter.tsx`、approval/memory helpers | 10 calls | 0 | -10 | mechanical | MUST FIX closed / F149 | `check-f149-boundaries.mjs` PASS；生产 `fetch(` 仅 canonical client 与 shell version watcher |
| token/header helpers | baseline approval/memory helpers | 2 | 0 | -2 | mechanical | MUST FIX closed / F149+F150 boundary | boundary checker PASS；auth header 只由 `src/api/client.ts:284-291` 组装 |
| duplicate transport/state bypass | baseline 1 canonical + 4 bypass clusters | 4 bypass | 0 | -4 | mechanical+adversarial | MUST FIX closed / F149 | pages consume `api/client` + platform queries/actions |
| questionable projection imports | baseline `approvalModels.ts` UI/API edges | 2 | 0 | -2 | mechanical | MUST FIX closed / F149 | boundary checker import graph PASS |
| Settings stale compatibility | baseline `SettingsPage.tsx` LiteLLM fallback cluster | 1 touched cluster | 0 | -1 | adversarial | MUST FIX closed / F149 | Settings consumes current generated/application contract |
| dead Memory component/link | baseline dead component + 2 `/advanced` links | 3 | 0 | -3 | mechanical | MUST FIX closed / F149 | no production importer/dead route remains |
| raw DTO leakage | baseline snapshot/event/action/MCP wire clusters | 4 | 0 | -4 | mechanical+adversarial | MUST FIX closed / F149 | generated adapter types + runtime decoders + pure VM |
| secret egress | baseline backend catalog/UI refill/uncleared apply | 3 | 0 | -3 | mechanical+adversarial | SECURITY MUST FIX closed / F149 | write-only keep/replace/remove；response/SSE/DOM/clipboard tests |
| silent MCP poll failure | baseline `McpInstallWizard.tsx` empty catch | 1 | 0 | -1 | adversarial | MUST FIX closed / F149 | disconnected/timeout retain state and expose retry |
| style drift in touched pages | baseline Skill modal hard-coded light palette | 1 cluster | 0 | -1 | mechanical+visual | MUST FIX closed / F149 | style ratchet PASS；20-surface visual review PASS |
| import cycles | production TS/TSX relative graph | 0 | 0 | 0 | mechanical | RATCHET / frontend architecture | boundary checker PASS |
| `index.css` growth | `octoagent/frontend/src/index.css:1` | 4476 physical lines | 4476 | 0 | mechanical | RATCHET / future style extraction | threshold 4480，complexity PASS |
| Settings God file | `octoagent/frontend/src/domains/settings/SettingsPage.tsx:1` | 997 physical | 992 | -5 | mechanical+adversarial | RATCHET / future vertical extraction | no new transport/store；maintenance extracted by seam |
| Agent page growth | `octoagent/frontend/src/pages/AgentCenter.tsx:1` | 843 | 936 | +93 | adversarial | RATCHET / future Agent slice | intentional F149 feature growth；threshold 4800；helpers/components extracted |
| chat helper limit | `octoagent/frontend/src/hooks/chatStreamHelpers.ts:1` | 477 | 480 | +3 | mechanical | RATCHET / future chat slice | threshold 500；complexity PASS |
| chat hook limit | `octoagent/frontend/src/hooks/useChatStream.ts:1` | 487 | 486 | -1 | mechanical | RATCHET / future chat slice | threshold 500；complexity PASS |
| F149 action seam | `octoagent/frontend/src/platform/actions/f149Actions.ts:1` | new | 487 | +487 | adversarial | RATCHET / F149 application owner | typed finite action seam；threshold 500；no registry/service split |
| MCP center | `octoagent/frontend/src/pages/McpProviderCenter.tsx:1` | touched | 679 | feature growth | adversarial | RATCHET / future MCP slice | threshold 1200；wizard separated；no second state source |
| workbench query owner | `octoagent/frontend/src/platform/queries/useWorkbenchData.ts:1` | touched | 309 | feature growth | adversarial | RATCHET / platform query owner | threshold 500；401 owner centralized |
| internal auth event literal | `src/api/client.ts:42`；`src/platform/queries/useWorkbenchData.ts:17` | 0 | 2 identical literals | +2 | adversarial | FUTURE / F150 front-door owner | private DOM signal，非 public API/store；不得扩散到第三处 |
| controlled catches | `src/platform/queries/useWorkbenchData.ts:177,284` | existing/touched | 2 | — | adversarial | RATCHET / platform query owner | 对应可恢复的资源加载/轮询，用户态由 owner state 表达；无 secret/错误静默丢失 |

## God / hidden state / mock-self 挑战

- 没有新增第二 transport、store、secret registry、per-page service 或兼容层。
- `f149Actions.ts` 是有限、typed application seam，不负责网络和页面状态；尚未构成第二 registry。
- 页面测试不仅断言 mock 调用，还断言 DOM、view-state、error mapping、secret absence 和 browser focus。
- 401 使用既有全局 shell owner；403 保留页面资源态。内部 CustomEvent 只连接 canonical client 与 platform query，没有形成公开 subscriber API。
- MCP/Settings 轮询 timer 可取消；断线保留既有状态并提供恢复，不存在不可达或 silent terminal branch。

## 最终机械门

- `node scripts/check-f149-boundaries.mjs`：PASS。
- `node scripts/check-f149-style-ratchet.mjs`：PASS。
- `npm run check:complexity`：PASS。
- production page/domain direct `fetch(`：0。
- F149 closure token/header helper：0。
- production relative import cycle：0。
- MUST FIX：0。
