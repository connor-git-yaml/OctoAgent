# F098 Pre-Implementation Codex Adversarial Review

**日期**: 2026-05-10
**审视命令**: `codex review --base origin/master`
**审视范围**: F098 设计阶段产出（commit fe551c8 vs origin/master 4441a5a）—— spec.md / plan.md / tasks.md / clarification.md / quality-checklist.md / phase-0-recon.md
**审视模型**: GPT-5.4 high (Codex 默认 with --base)

---

## Findings 闭环表

| ID | 严重度 | 文件:行 | 描述 | 处理决策 |
|----|--------|---------|------|---------|
| **P1** | high | tasks.md:117-121 | Worker→Worker source 端 A2A 改造缺失：仅删除 `enforce_child_target_kind_policy` + 修 target profile 不充分。`_prepare_a2a_dispatch` 仍硬编码 `source_role = MAIN` / `source_session = MAIN_BOOTSTRAP` / `source_agent_uri = "main.agent"`。worker→worker 场景下 audit chain 会被错误记录为 main→worker，AC-C3 / AC-I3 失败 | **接受**：spec.md 块 B 拆分为 B-1（source 派生）+ B-2（target 解析）；plan.md Phase B §3.2 新增 `_resolve_a2a_source_role` 函数；spec.md AC-B 拆分为 AC-B1-S1/S2/S3/S4 + AC-B2-T1/T2/T3 |
| **P2** | medium | plan.md:310-313 | 计划用 `self._capability_pack` 但 OrchestratorService 没有这个成员（实际通过 `_delegation_plane.capability_pack` 访问）。`except Exception` 会吞 error 并 fallback 到 source profile → receiver 仍复用 source profile，违反 AC-B2 | **接受**：plan.md Phase B §3.2 改用 `self._delegation_plane.capability_pack` 路径；fallback fail-loud（warning log + 测试覆盖路径 1/2 显式失败的场景）；spec.md AC-B2-T2 加 fail-loud 要求 |
| **P2** | medium | plan.md:480-492 | TaskService 用 class-level `_terminal_state_callbacks`，TaskRunner.__init__ 每实例注册一次 + 没有去重/注销 → 测试和运行时重建 TaskRunner 后旧 callback 残留 → 旧 StoreGroup 被持有引用 → 多实例场景重复 cleanup + 错误 DB 访问 + 内存泄漏 | **接受**：plan.md Phase H §6.2 改为**实例级**（`self._terminal_state_callbacks` instance attr）+ **幂等注册**（按 callback identity）+ **shutdown 注销**（`unregister_terminal_state_callback` API + TaskRunner.shutdown / __aexit__）；spec.md 块 H AC-H6 / H7 新增 |

---

## 闭环修订清单

### spec.md 修订

1. **§3 块 B 重组**：
   - 原"块 B：A2A Receiver target profile 独立加载"→ 拆为"B-1: Source 端从 runtime_context/envelope 派生"+"B-2: Target 端从 requested_worker_profile_id / worker_capability 解析"
   - B-1 + B-2 强制一起 commit（不可分离）
2. **§3 块 C**：增加"强制依赖块 B-1"声明
3. **§3 块 H**：实例级 callback + 幂等 + shutdown 注销 显式声明
4. **§5 验收标准**：
   - AC-B1/B2/B3 拆为 AC-B1-S1~S4（source）+ AC-B2-T1~T3（target）
   - AC-H6 新增（callback 幂等）+ AC-H7 新增（callback 生命周期）

### plan.md 修订

1. **§3.1 改动文件清单**：Phase B 行数估计 +60/-10 → +120/-25（双向修复）；测试文件改名 test_phase_b_a2a_target_profile.py → test_phase_b_a2a_source_target.py，行数 +300 → +500
2. **§3.2 关键代码点**：
   - 新增 B-1 子节：`_resolve_a2a_source_role` 函数完整代码 + `_prepare_a2a_dispatch` source_role/session_kind/uri 派生改造
   - B-2 子节：`_resolve_target_agent_profile` 改用 `self._delegation_plane.capability_pack`；fail-loud（不吞 except）
3. **§3.3 测试设计**：扩展为 B-1 + B-2 双向测试场景（共 9 行）
4. **§6.2.1 TaskService callback 机制**：
   - 完整重写：实例级 callback list（`self._terminal_state_callbacks`）
   - `register_terminal_state_callback` 幂等检测（按 callback identity）
   - 新增 `unregister_terminal_state_callback` API
   - `_invoke_terminal_state_callbacks` 在 lock 内 snapshot 列表（避免并发 register/unregister 引发 RuntimeError）
5. **§6.2.2 TaskRunner 注册**：
   - 注册时机推迟到 `start()` / `__aenter__`（避免 __init__ 中 asyncio.create_task）
   - shutdown 显式 unregister
   - `__aenter__` / `__aexit__` 配套
6. **§6.2.3 测试 fixture 设计**（**新增**）：
   - pytest fixture 必须 start/shutdown 配对，避免 callback 泄漏
   - try/finally 保护 shutdown 在测试失败时仍执行
7. **§6.3 测试设计**：新增 AC-H6 / AC-H7 / 多 TaskService 实例不串扰 3 个测试场景
8. **§6.4 Codex review 节点**：Phase H pre-review 增加"生命周期管理"检查

### tasks.md（推迟更新到下一次实施前）

tasks.md 是 plan 的派生，本次不重写，下次实施前依据 plan.md v0.2 重新拆解。

---

## 总结

- **High**: 1（**已修订**）
- **Medium**: 2（**已修订**）
- **Low**: 0

**Codex Pre-Implementation Review 全部 high + medium 闭环。spec.md / plan.md 已升级为 v0.2（Pre-Impl Codex 闭环）。tasks.md 待下次实施前重新拆解。**

---

## 决策建议

设计阶段产出经 Codex review 修订后已对齐 OctoAgent 当前代码现状（特别是 _delegation_plane.capability_pack 访问路径 + TaskService 实例化模式），可启动 Phase 0 → E → F → B → C → I → H → G → J → D 实施序列。

**Phase B 是最大变化点**：从单纯"target profile 独立"扩为"source/target 双向独立"，工作量 +1.5x（+60/-10 → +120/-25 + 测试 +300 → +500）。

**Phase H 是最关键变化点**：callback 生命周期管理（实例级 + 幂等 + shutdown）是 task state machine 改造的真正瓶颈，**必走 Codex pre + post review**（plan.md §6.4 已强制）。

---

**Pre-Impl Codex Review 闭环。下一步：在 worktree 内 commit 修订（spec/plan 升级 v0.2）+ 归总报告呈给用户。**
