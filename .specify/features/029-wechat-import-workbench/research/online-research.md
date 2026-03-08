---
required: true
mode: full
points_count: 3
tools:
  - mcp__openrouter-perplexity__web_search
  - mcp__openrouter-perplexity__research
queries:
  - "WeChat desktop message export parser attachments sqlite media path GitHub README PyWxDump WeChat history export"
  - "import workbench dry run mapping dedupe resume cursor source connector UX official docs Airbyte incremental sync state"
  - "为个人知识系统设计 WeChat Import + Multi-source Import Workbench：重点关注 WeChat 桌面聊天导出/解析形态、附件保真、dry-run/mapping/dedupe/cursor-resume UX，以及导入到 artifact/fragment/memory pipeline 的最佳实践。优先引用官方文档或开源项目 README。"
findings:
  - "WeChat 官方个人数据导出更偏向账户级数据，聊天历史并不是稳定的服务端导出主路径；对 029 来说，默认输入应是用户提供的本地导出物，而不是在线 API。"
  - "PyWxDump、Python-Wxid-Dump 一类开源项目普遍围绕本地 SQLite / HTML / CSV / 媒体目录解析，且都把附件路径解析与导出视为一等能力，这支持 029 采用 source-specific adapter + attachment materialization 设计。"
  - "Airbyte 的 connector 文档与 UX 实践强调 incremental cursor/state、typing/deduping、warnings 落可审计元数据，以及在真正写入前提供 preview/test 结果；这些模式可以直接迁移到 029 的 dry-run、mapping、dedupe、resume 工作台。"
impacts_on_design:
  - "WeChat adapter 应优先支持本地导出目录 / HTML / JSON / SQLite snapshot 等离线输入，不把在线登录、进程注入或远程抓取设为主路径。"
  - "附件必须 artifact-first，并保留 source path / media checksum / message provenance；否则很难承接 WeChat 社区导出物里的图片、语音、视频等多媒体。"
  - "Import Workbench 需要把 cursor/resume、dedupe、warnings/errors 提升为正式 control-plane resource，而不只是一次性 action result。"
skip_reason: ""
---

# Feature 029 在线调研记录

## 调研点 1：WeChat 导入的现实输入形态

### 参考来源

- WeChat Help Center：个人数据导出说明
- `PyWxDump` README / release 说明
- `Python-Wxid-Dump` README

### 关键发现

- 官方导出更偏向账户侧数据，聊天记录不是稳定的服务端导出主路径。
- 社区的实际可用路径基本都是本地导出、数据库解析、HTML/CSV/JSON 生成，以及媒体目录联动。
- 因此 029 的 WeChat adapter 更适合设计成“消费用户提供的导出物”，而不是要求系统直接接管 WeChat 运行时。

### 对设计的影响

- WeChat source adapter 的默认输入必须是离线导出包或本地解析产物。
- adapter contract 需要显式描述 media roots、message store、account/conversation metadata。
- 工作台需要把“输入源是否合法、是否缺附件、是否缺 conversation metadata”作为首屏检测结果输出。

## 调研点 2：Import Workbench 的成熟体验模式

### 参考来源

- Airbyte 文档：incremental sync / cursor state
- Airbyte 文档：typing and deduping
- Airbyte UX/connector handbook 相关材料

### 关键发现

- connector 型产品通常先做 detect / preview / validate，再做真正运行。
- cursor/state 是恢复与增量同步的一等对象，而不是失败时才临时补的实现细节。
- typing/deduping 不应该静默吞错，常见做法是把异常或不匹配行落到元数据/警告面，供用户修正。

### 对设计的影响

- 029 必须把 dry-run、mapping、dedupe、resume 做成工作台核心步骤。
- 导入报告必须展示 warnings/errors/detail，而不是只报导入条数。
- `import.preview` 与 `import.resume` 需要正式 action + resource 组合，而不只是 CLI flag。

## 调研点 3：多媒体与知识系统的导入保真

### 参考来源

- 开源 WeChat 导出项目的 HTML/媒体联动设计
- 实时研究摘要（见上方查询）

### 关键发现

- WeChat 导出里的高价值内容不仅是文本，还包括图片、语音、视频、文件与链接上下文。
- 社区工具普遍会保留媒体路径和导出目录结构，否则聊天记录很快失去可追溯性。
- 对知识系统来说，附件更适合作为 artifact + fragment ref，再根据可用能力选择进入 MemU 或转录链路。

### 对设计的影响

- 029 必须定义统一附件 materialization contract。
- artifact、fragment、MemU 必须串成一条有 provenance 的链，而不是把附件内容直接揉进消息文本。
- 当 MemU 不可用时，系统必须优雅降级为 artifact/fragment-only，并把降级原因写入导入报告。
