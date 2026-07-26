# F149 Blueprint 同步记录

> 状态：`APPLIED`（2026-07-26）。以下权威表述已同步到
> `docs/blueprint/milestones.md` 与
> `docs/codebase-architecture/modules/06-frontend-workbench.md`。

## 建议同步目标

- `docs/blueprint/milestones.md` 的 M11/F149 依赖与验收段；
- `docs/codebase-architecture/modules/06-frontend-workbench.md` 的认证边界、网络 DTO 与页面状态段。

## 待应用的权威表述

### Auth ownership

- F150 唯一拥有 Web Access Gate：未登录、HTTP 401、session 过期与登出统一在 global transport/shell 处理。
- F149 页面不得重建 Access/401 状态机；只处理已登录用户遇到的 origin HTTP 403 或资源级 permission，并保持 shell 可用。
- 404 与 409 分别进入 domain not-found/conflict mapper，不得归并成 auth error。
- F150/F149 交界由一条 L1 旅程钉住：401 转入全局 Access；403 留在目标页面的资源权限态。各页面 403 分支由 L4 component tests 穷举。

### Contract ownership

- REST wire DTO 以稳定 OpenAPI 生成的 types-only artifact 为唯一来源；generated client 只有能注入现有统一认证 transport 时才允许。
- `GET /api/control/snapshot` 以命名 raw envelope 接收并通过 runtime decoder 产出 F149 consumed projection；不要求 F149 闭合整个 control-plane 聚合。
- task SSE frame schema/decoder 属 Gateway/web adapter；普通 JSON OpenAPI 只描述 `text/event-stream` endpoint。仅闭合业务实际消费 payload，未知/历史事件经 scrub 后只进入 Advanced diagnostic。
- control-plane action 的 dispatch、runtime validation、registry discovery 与生成 artifact 来自同一有限 action contract；不允许只补前端类型。
- F149 新增/触达 contract slice 禁止 `any`；`unknown`/递归 `JsonValue` 只允许停在明确命名的 raw/metadata/schema-as-data transport boundary，经 decoder/type guard 后才能进入 domain/UI；不授权清扫非 F149 历史代码。

### Sensitivity ownership

- secret value 在服务端边界移除，永不下发、渲染或复制；UI 只能接收变量名、是否已配置与服务端生成的脱敏摘要。
- secret mutation 是 write-only：create/edit 使用 ephemeral masked input，save 明确 keep/replace/remove，redacted placeholder 不得回写，提交后清空；response/SSE/error/log/DOM/clipboard/evidence 均不得回显。
- Settings 与 MCP 的窄 HTTP/application secret representation 由 F149 负责并复用现有 config/secret store、application boundary 与唯一 transport；F151 只保留底层 runtime/package/secret reference ownership，不另立 security Fix 或全局 secret registry。
- path/command 默认属于 operator-sensitive：普通区域不可见，Advanced 默认截断；只有已净化、非 secret、权限允许的 workspace-relative path 或 command summary 才允许复制。

### Scope ownership

- 审批继续表示 F145 的新记忆、记忆整合、行为压缩三类候选，不与通用 tool approval 合并。
- 自动化只交付 pause/resume；任务不新增 cancel/resume。
- Tasks 只保留用户语言“待处理事项”的管理入口：非零显著可进入、0 项折叠/隐藏，且不与 F145 候选合并。Recovery summary、backup/export/update dry-run/apply/restart/verify 归位 Settings → Advanced → 维护与恢复；以窄 UI section 组合现有状态，不新增页面、management service/registry 或第二状态源。
- F149 默认复用 `api/client` 唯一 transport、`platform/queries/actions` application orchestration、pure projection/state 与 page/WorkbenchContext composition；不为每页/每 endpoint 新建 service/port/registry，也不建立第二 transport 或状态容器。

## 同步验证

生产实施的 Review Task 必须比较本文件与上述目标文档，并在 `trace.md` 记录实际落点。仅更新 completion report 不算 Blueprint 同步完成。
