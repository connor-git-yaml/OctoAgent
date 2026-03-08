# Feature 027 调研综合结论

## 结论

Feature 027 应以“**Memory 产品化投影层**”来实现，而不是重做 memory engine：

1. **020** 继续作为唯一治理内核：`WriteProposal -> validate -> commit`、SoR current 唯一约束、Vault default deny 均保持不变。
2. **026** 继续作为唯一控制面框架：Memory 通过新增 canonical resources / actions / events 接入现有 control plane 和 Web 控制台。
3. **027** 新增的是 operator-facing 的浏览、授权、审计和校验入口：
   - Memory 浏览器
   - `subject_key` 历史与 evidence refs
   - Vault 授权申请/记录/检索结果
   - WriteProposal 审计视图
   - export / inspect / restore verify 入口

## 推荐架构

- `packages/memory`：继续提供权威事实与治理服务，不直接承担 UI projection
- `packages/core`：新增 Memory/Vault control-plane documents 与相关 action/result/event models
- `packages/provider/dx`：新增 memory projection / vault authorization / export-verify 等 durable stores
- `apps/gateway`：扩展现有 control-plane producer、routes、action executor、event emission
- `frontend`：在现有 Control Plane 中新增 Memory 视图，只消费 canonical resources

## 为什么不是别的方案

- **不是前端直读 memory tables**：会把 020 的内部 schema 变成公共 API，也无法承载授权与审计。
- **不是新造一套 memory console API**：会绕开 026 control plane，导致 control-plane contract 再次分叉。
- **不是提前交付 028**：高级引擎集成、MemU recall、多模态 ingest 都还不属于 027。

## 对 spec 的直接约束

- 027 必须明确 project/workspace/filter 与底层 `scope_id` 的桥接方式。
- Vault 详细原文只允许在授权检索结果中暴露，默认资源视图必须 redacted。
- 所有 Vault 授权与检索动作必须落审计记录，并进入 control-plane event 链。
- WriteProposal 审计必须展示 proposal 来源、validate 结果、commit 状态和 evidence refs。
- export / inspect / restore 在 027 仅提供检查/验证入口，不旁路执行权威写入。

## 与 Feature 028 的接口边界

027 允许：

- 预留 `backend_id`、`retrieval_backend`、`index_health` 等 integration fields
- 预留高级 recall 的 summary/ref 槽位

027 不允许：

- 引入 MemU 深度引擎语义
- 用高级 recall 结果直接改写 SoR/Vault
- 新增多模态 ingest / consolidation pipeline
