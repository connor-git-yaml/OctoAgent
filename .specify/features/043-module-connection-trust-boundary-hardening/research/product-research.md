# Product Research: Feature 043 Module Connection Trust-Boundary Hardening

## 用户面问题

043 解决的不是单点 bug，而是用户对“系统有没有真正接住消息并按治理边界执行”的基本信任。

当前链路的产品风险集中在四处：

1. 用户输入 metadata 和系统控制 metadata 混在一起  
   用户很难知道哪些字段只是输入提示，哪些字段真的会影响 worker / tool / profile 决策。

2. chat send 的 accepted 语义过宽  
   当 task 没创建或没入队时仍返回 `accepted`，会制造“看起来发送成功，但系统其实没干活”的幽灵体验。

3. delegation 边界缺少 typed truth  
   一旦 metadata 在连接点被降格成字符串，operator 很难解释为什么本轮选了这个 worker、这个 target、这个 profile。

4. control-plane snapshot 缺少 section 级降级  
   某个资源出错时整页挂掉，会让用户把“局部资源暂时异常”误认为“系统整体不可用”。

## 为什么这不是纯后端重构

如果只做局部代码修补，不把 trust boundary 和降级语义收成正式合同，会留下三个产品层面的长期问题：

- 用户仍无法理解“输入提示”和“控制指令”的边界
- Operator 仍无法从 snapshot / runtime truth 判断故障是 task 入口、delegation 还是资源投影
- 后续 044+ Feature 会继续在同一条连接链上叠加语义债务

因此 043 必须作为一条正式 feature 主链处理，而不是若干零散 fix。

## 产品原则

1. 输入提示默认是 non-authoritative  
   渠道侧 metadata 默认只能作为 input hints，不能直接改写运行控制。

2. 控制元数据必须显式进入 trusted envelope  
   只有系统显式写入或白名单映射的字段，才能进入 orchestrator / delegation / runtime control。

3. 成功语义必须和执行语义一致  
   chat send 只有在 task 已创建且已进入执行主链时，才可以返回 `accepted`。

4. 降级必须局部化、可解释  
   control-plane 的局部资源失败要变成 `partial / degraded`，而不是拖垮整张快照。

5. 连接层决策必须可审计  
   需要能回答：
   - 哪些字段被当作 input hints
   - 哪些字段进入 control envelope
   - 哪些字段被丢弃、清空或降级

## 用户价值

- Owner 不会再因为一次 `accepted` 响应而误判系统已经开始处理。
- Chat / Work / Control Plane 会共享同一条 trust-boundary 事实链。
- 后续 profile-first、worker governance、memory/context feature 的接缝会更稳，不再依赖字符串约定和 prompt 假设。
