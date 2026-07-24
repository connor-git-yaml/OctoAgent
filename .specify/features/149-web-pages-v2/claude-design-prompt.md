# READY TO SEND — Claude Design Prompt

## 最高优先级禁止项

- **Do not use Spotify Design System.** Claude Design 当前选择器即使显示 Spotify Design System，也不得导入、套用或继承其 token、颜色、组件、排版、theme 或交互模式。原因：F149 的唯一视觉事实源是已附 F148 shell 与现有 `--cp-*` tokens；使用 Spotify 会制造第二主题并破坏 F148 连续性。
- **Do not inherit the existing two-page design as target UI.** `OctoAgent Web.dc.html` 里现有 2 pages 只作为旧现状/反例；不得继承其中的 LiteLLM、JWT、JWKS、AUD 等内部术语或旧主题选择。原因：F149 面向普通用户，20 个目标 frame 必须逐页通过术语 absence 验收。
- **Do not start before all inputs are visible.** Claude 项目文件列表必须能看见 `references/current/actual-input-manifest.md`、`references/current/capture-metadata.json` 与 22 张 PNG；缺一项就输出 `INPUT MISSING: <file>` 并停止。原因：ZIP 不会由 Claude Design 自动解压，本机路径也不可访问。

> 发送前置已满足：canonical manifest 的 22/22 已由 main 视觉复核，`pending-product-decisions.md` 8/8 已决定。发送前仍必须按 `claude-design-upload-checklist.md` 在 Claude 项目文件列表中确认 24 个 Reference 文件实际可见，再粘贴本 Prompt。

请在 Claude Design project `851e3fb2-2b5b-4251-a095-8a678b1b7fec` 的 `OctoAgent Web.dc.html` 中补齐 F149 Web 其余页面 v2。只做设计，不发明后端能力，不推断本地源码事实。

## 已附参考输入（必须使用，不得自行补）

使用随 Prompt 附带的 `references/current/` 文件，唯一清单与 route/viewport/date/commit/data source 在 `references/current/actual-input-manifest.md`。它包含当前 10 个 surface 的 Desktop/390 与 F148 shell Desktop/390，共 22 张。

- 每张 deterministic fixture 截图都标记 `not_real_backend=true`；它用于还原当前 UI 结构，不代表 live 后端或真实用户数据。
- main 已视觉复核这 22 张图。它们只证明固定 fixture 下的 current UI/layout 与响应式问题，不证明真实后端数据、权限、错误或 SSE 行为。
- 不要美化、替换或重制 Reference：Tasks 当前维护内容占满首屏、Settings 390px 信息密度与断行等问题正是目标设计的有效输入。
- 若 22 张任一缺失、打不开或 metadata 不一致，输出 `INPUT MISSING: <file>` 并停止对应页设计；不要凭经验补现状。
- 在 Claude 文件内建立 `F149/References`，只摆放这些已附文件并保留 route、viewport、capture date、fixture 标识。

## 必须交付的唯一 frame 名称

| Surface | Desktop frame | 390px frame |
|---|---|---|
| Approvals | `F149/Approvals/Desktop` | `F149/Approvals/390` |
| Tasks | `F149/Tasks/Desktop` | `F149/Tasks/390` |
| Task-Detail | `F149/Task-Detail/Desktop` | `F149/Task-Detail/390` |
| Automation | `F149/Automation/Desktop` | `F149/Automation/390` |
| Settings | `F149/Settings/Desktop` | `F149/Settings/390` |
| Agents | `F149/Agents/Desktop` | `F149/Agents/390` |
| Memory | `F149/Memory/Desktop` | `F149/Memory/390` |
| Files | `F149/Files/Desktop` | `F149/Files/390` |
| Skills | `F149/Skills/Desktop` | `F149/Skills/390` |
| MCP | `F149/MCP/Desktop` | `F149/MCP/390` |

## 20 个页面 frame 的逐页硬验收

对上表每一个 frame 单独输出验收记录；不得用一条“全局已遵循”代替逐页结果：

1. `theme/token provenance` 必须写明 `F148 shell + existing --cp-* tokens only`，并明确 `Spotify imports/tokens/components/theme: none`。
2. 普通用户可见标题、说明、状态、按钮、表格、空态、失败态、权限态、toast、modal 与 drawer 对 `debug`、`JWT`、`AUD`、`JWKS`、`LiteLLM`、`operator`、`ops`、`raw schema`、`runtime ID` 做逐页 absence 检查；仅本 Prompt 的禁令文本和明确 Advanced 事实说明可提到这些检查词。
3. Desktop 与 390 必须分别核对页面目标、已有数据、允许/禁止动作、必备状态、Advanced 字段与敏感字段，不得把 Desktop 机械缩放成 390。
4. 输出一份覆盖 20 行的验收表，至少包含 `frame name`、`F148/--cp provenance`、`Spotify absence`、`ordinary-language absence`、`states covered` 与 `390 overflow/focus`；任一项未证实时标 `BLOCKED`，不得标 PASS。

另交付：

- `F149/Shared-States`：loading、empty、recoverable error、origin 403 permission、not found（适用页）、disconnected（实时页）；
- `F149/Advanced-Pattern`：普通摘要/高级字段分层、截断、复制、secret write-only 与权限规则；
- `F149/References`：只使用已附 22 张实际输入。

## 已冻结的 Tasks / Settings 归位

- Tasks 继续拥有一个管理入口，但用户文案只能叫“待处理事项”，不得出现 operator/ops 等内部词。有待处理项时显著提示并可进入；0 项时折叠或隐藏，不占普通首屏。它与 F145 的新记忆、记忆整合、行为压缩三类知识候选完全分离，不得合并。
- Recovery summary、backup create、chat export、update dry-run/apply、runtime restart/verify 全部从 Tasks 移出，并直接设计到现有 Settings → Advanced → 维护与恢复。
- “维护与恢复”中：backup 需要危险确认；chat export 显示导出范围；update dry-run 必须先于 apply；apply 强确认并展示最近一次有效 dry-run 摘要；restart 强确认并说明短暂不可用；verify 保留在同一区域。
- 不新增独立页面或第二套管理信息架构。设计应把“维护与恢复”作为 Settings 内部窄 section，不把七项动作铺满 Settings 普通首屏。

## 逐页产品事实

| Surface | 用户目标 | 已有数据 | 允许动作 | 禁止动作 | 必备状态 | Advanced 字段 | 敏感字段 |
|---|---|---|---|---|---|---|---|
| Approvals | 判断并处理知识候选 | 新记忆、记忆整合、行为压缩三类候选 | edit/promote/discard/bulk discard；accept/reject | 不合并通用工具审批；不增候选类型 | loading、三类 empty、分来源 error/retry、origin 403、409 | reason、内部引用 ID | 正文截断；secret 不出现 |
| Tasks | 查看工作并进入详情 | 任务列表、待处理事项数量/摘要 | 打开任务；非零时进入“待处理事项”管理区 | 不新增 task cancel/resume；不显示 operator/ops；不与 F145 知识候选合并；不保留 Recovery/backup/export/update/restart/verify | loading、empty、error/retry、origin 403；待处理非零显著、0 项折叠/隐藏 | raw status/ID、已净化待处理摘要 | path/log 普通区不可见 |
| Task-Detail | 理解进展、事件与产物 | detail、事件、artifacts、SSE status | visual/Advanced timeline、打开产物 | 不新增 cancel/resume；未知 event 不驱动新业务 | loading、not found、error/retry、origin 403、connecting/disconnected/final | 经 scrub 的 diagnostic event、ID | raw payload/secret 不下发；ID 截断 |
| Automation | 查看计划任务并启停 | automation resource、历史摘要 | pause、resume | 不增 create/run/delete | loading、empty、error/retry、origin 403、409 | job/action ID、cron 原式 | command/env 普通区不可见 |
| Settings | 连接、审查并保存配置；执行维护与恢复 | config/project/memory/retrieval/setup summary；recovery/update summary | review/apply/OAuth-and-apply/quick-connect/resource limits；Advanced 中 backup/export/update dry-run→apply/restart/verify | 不暴露内部 auth/runtime 实现；不把维护动作放普通首屏；不新增独立管理页 | loading、empty、validation/apply error、origin 403；维护加载/失败；危险确认；dry-run-before-apply；restart短暂不可用 | 服务端净化摘要、非敏感诊断、维护与恢复 summary | secret read=name/configured/redacted；edit 输入初始空，keep/replace/remove；输出路径按敏感策略 |
| Agents | 管理智能体配置与行为版本 | agent/worker、skill/MCP summary、behavior files/versions、overrides | create/edit/archive；behavior read/write/**restore version**；review/apply；revoke override | 不增 agent 类型/审批语义 | loading、empty、error/retry、origin 403、conflict | model alias、runtime ID、behavior path/diff | path 净化/截断；secret 不出现 |
| Memory | 查询与维护记忆 | memory/retrieval、query result、index lifecycle | query/consolidate/edit/archive/restore/index start/cancel/cutover/rollback | 不增阶段；不设计不可达 MemoryActionsSection 的 diagnostics/backup/export/operator/channel 动作；不链 `/advanced` | loading、empty、error/retry、origin 403、index transition | retrieval/embedding summary、record ID/history | 正文截断；vault/secret 不展开 |
| Files | 浏览产物与 workspace history | tasks/logical files/diff/versions/git history | browse/compare/two-phase rollback approve/reject | 不增写文件/git 动作 | loading、empty、error/retry、origin 403、rollback conflict | hash/storage/version/blame/diff | 只给 logical/relative path；绝对 path 不下发 |
| Skills | 查找、安装、卸载技能 | list/detail/content/install/delete | install/uninstall/view detail | 不增执行能力；不复制后端校验规则 | loading、empty、error/retry、origin 403、validation conflict | SKILL.md/trigger/tools/raw content | 技术正文折叠且净化 |
| MCP | 管理 provider 与安装 | catalog/save/delete/install/status | save/delete/install/status | 不增协议/包管理器 | loading、empty、error/retry、origin 403、poll failure/timeout | server ID、tool names、净化 command/args/cwd | read 只显示 env name/configured/redacted；edit 初始空，keep/replace/remove，placeholder 不回写 |

## 全局约束

- 唯一视觉 SoT 是已附 F148 深色 shell、导航与现有 `--cp-*` token；沿用其圆角、层级和密度。不得使用 Spotify Design System 的任何 token、颜色、组件、排版或 theme，不得创建第二主题或浅色方案。
- 现有 2 pages 只作旧现状/反例，不是可继承模板。普通界面不得出现 debug、JWT、AUD、JWKS、LiteLLM、operator、ops、raw schema、runtime ID 等内部词；必要技术内容只在明确 Advanced/管理区，并仍须使用普通用户能理解的标签。
- F150 负责未登录、401、session 过期、登出与全局 Access Gate；F149 只设计 origin 403 资源权限态，不设计逐页登录页。
- secret value 永不在 read response、SSE、error、log、DOM 或 clipboard 出现。secret create/edit 是 ephemeral masked input；save 明确 keep/replace/remove；redacted placeholder 不得提交；提交/失败/关闭后输入清空。
- Settings 与 MCP 使用同一 write-only 安全语义，但设计不得引入全局 secret registry、第二 transport 或另一套状态来源。
- path/command 普通区不可见；Advanced 默认中间截断。只有服务端已净化、非 secret、权限允许的 relative path/command summary 可复制。
- 390 frame 标注信息排序、横向溢出、sticky/primary action、drawer/modal 关闭、焦点顺序与焦点归还。
- 需要当前契约没有的数据/动作时标 `CONTRACT GAP`，不要自行补字段或状态。

请输出可回存审查的 `.dc.html` 或逐 frame 导出截图，保留所有唯一 frame 名称，并附 20 行逐页验收表与 theme/token provenance。20/20 page frame、Shared-States、Advanced-Pattern、References 或 provenance/absence 证据缺一不可。
