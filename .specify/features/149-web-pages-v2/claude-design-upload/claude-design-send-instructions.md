# F149 Claude Design 发送说明

> 目标项目：Claude Design project `851e3fb2-2b5b-4251-a095-8a678b1b7fec`，文件 `OctoAgent Web.dc.html`。
> ZIP 仅是离线备份。Claude Design 不会自动解压 ZIP；禁止把只上传 ZIP 当成输入已交付。

## 唯一推荐输入

直接上传解压目录 `claude-design-upload/` 内的各文件/文件夹：

- `claude-design-prompt.md`：READY Prompt；最后再把完整正文粘贴到请求框。
- `pending-product-decisions.md`：8/8 已决定的 Tasks / Settings 产品事实。
- `claude-design-send-instructions.md`：本说明。
- `claude-design-upload-checklist.md`：逐文件可见性核对表。
- `references/current/actual-input-manifest.md`：Claude 可见的 canonical byte-for-byte 副本。
- `references/current/capture-metadata.json`：route、viewport、commit、fixture 与 `not_real_backend=true`。
- `references/current/*.png`：22 张 current UI Reference。

## 必须按顺序执行

1. 打开完整 UUID 对应的 `OctoAgent Web.dc.html`；不要用短前缀判断项目。
2. 在本地解压上传包，或直接使用已生成的 `claude-design-upload/` 目录。不要把 ZIP 本身上传给 Claude Design，因为它不会自动解压。
3. 先上传 `references/current/actual-input-manifest.md`、`capture-metadata.json` 与 22 张 PNG；有单次附件数量限制时可以分批，但必须留在同一项目/设计上下文。
4. 打开 Claude 项目文件列表，按 `claude-design-upload-checklist.md` 逐项确认：manifest 1、metadata 1、PNG 22，共 24 个 Reference 文件全部可见，文件名与 size 一致。
5. 再确认 Prompt、产品决定和发送说明可见；连接中断或应用重启后必须从第 4 步重新确认，不能假设文件仍被挂载。
6. 确认 Claude 当前选择器即使显示 Spotify Design System，目标设计也不得使用或继承其 token、颜色、组件、排版或 theme；唯一视觉 SoT 是 F148 shell 与现有 `--cp-*` tokens。
7. 只有上述核对全部通过后，才把 `claude-design-prompt.md` 的完整正文粘贴到请求框并发送。不要只粘贴本机路径，因为 Claude Design 无法读取本机文件系统。
8. 若 Claude 返回 `INPUT MISSING: <file>`，重新上传并从项目文件列表复核对应文件；不要让它猜测缺失页面，也不要继续部分生成。
9. 设计完成后回存 20/20 唯一页面 frame、`F149/Shared-States`、`F149/Advanced-Pattern`、`F149/References`、20 行逐页验收表与 theme/token provenance，并导出 `.dc.html` 或逐 frame 截图供 Design Gate 审查。

## 旧设计与 Reference 边界

- `OctoAgent Web.dc.html` 现有 2 pages 仅作旧现状/反例，不得作为 F149 target template；不得继承其中的内部术语或旧主题选择。
- Reference 只证明 deterministic fixture 下的 current UI/layout，不证明真实后端数据、权限、错误或 SSE 行为。
- 逐页普通用户术语 absence 与 Spotify absence 未提供证据时，Design Gate 不能通过。
