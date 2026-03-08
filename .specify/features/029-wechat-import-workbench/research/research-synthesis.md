# Feature 029 调研综合结论

## 结论

Feature 029 应实现为“**Import Core 之上的 source adapter + workbench projection 层**”，而不是重写导入内核：

1. **021** 继续作为唯一 generic import engine，负责 batch/cursor/dedupe/window/proposal/artifact/event。
2. **025** 提供 project/workspace 主路径，负责承接 source conversation 到 project/workspace/scope 的绑定。
3. **026** 继续作为唯一 control-plane 框架，029 通过新增 canonical resources / actions / events 接入，不新造后台。
4. **027** 提供 Memory/Proposal/Vault 的可解释面，029 的导入报告与结果应能直接引用这些视图。
5. **028** 只提供 MemU integration point；029 可把附件与 fragment 送到该入口，但不能定义新的 Memory 治理语义。

## 推荐架构

### 1. Source Adapter Layer

负责：

- WeChat 导出物检测与解析
- source-specific metadata、conversation info、attachment refs 提取
- mapping hints 与 cursor hints 生成
- 输出标准 `ImportedChatMessage` 流

### 2. Import Workbench Projection Layer

负责：

- dry-run
- mapping preview / 保存
- dedupe detail
- warnings / errors
- recent runs / resume entries
- import reports 的 control-plane 投影

### 3. 021 Import Core

继续负责：

- 统一 batch/cursor/dedupe/report durability
- artifact 写入
- fragment 生成
- proposal / validate / commit
- lifecycle audit event

## 为什么不是别的方案

- **不是继续只加 CLI 参数**：这会让 029 仍然停留在工程入口，不能满足 M3 产品化目标。
- **不是直接把 WeChat 解析塞进 021**：会把 generic import core 与 source-specific 细节耦死，破坏多源扩展性。
- **不是重写一套 import service**：会重复 021 的 durability 与治理逻辑，风险高且不必要。

## 对 spec 的直接约束

- 029 的 WeChat adapter 必须基于用户提供的离线导出物建模，不要求在线拉取聊天历史。
- 工作台必须把 dry-run、mapping、dedupe、cursor/resume、warnings/errors 做成正式资源。
- 附件必须 artifact-first，并带 source provenance；MemU 仅作为可降级 integration point。
- 导入结果必须能解释对 Memory proposal/commit 的影响，而不是只给出“成功/失败”。
- 029 必须明确与 031 的边界：只交付能力，不交付最终 M3 全局验收矩阵。

## 推荐的用户故事骨架

1. 作为 owner，我可以选择 WeChat 或其他 source adapter，先完成 detect/preview/mapping，再决定执行导入。
2. 作为 operator，我可以在导入工作台看到 dedupe、warnings/errors 和 resume 入口，而不是靠日志排障。
3. 作为知识系统维护者，我可以让附件稳定进入 artifact/fragment/MemU 管线，并保留 provenance。
4. 作为 owner/operator，我可以在 Control Plane 中查看导入报告、Memory proposal 结果和恢复建议。

## 与 Feature 031 的接口边界

029 可以交付：

- 单元测试
- API / integration 测试
- 必要的工作台 e2e

029 不应交付：

- M3 全局 fresh machine 到长期运行的验收矩阵
- 最终跨 Feature 端到端 acceptance bundle
- 为 031 设计的整体验收样本库与评分门槛
