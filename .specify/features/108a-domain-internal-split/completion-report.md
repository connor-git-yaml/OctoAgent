# F108a Domain-Internal Split — Completion Report

> 基线：origin/master `d6148903`。分支：`feature/108a-domain-internal-split`（**未 push，等用户拍板**）。
> 上游：program 计划 `.specify/features/108-capability-layer-refactor/`（双评审闭环 + 用户 2026-06-12 三项拍板）。

## 总成果（实际 vs 计划）

| Wave | 计划 | 实际 | 偏离 |
|------|------|------|------|
| W1 behavior 域收口 | 拆 package + D12 单函数收口 + C2 回迁 | ✅ 1741→8 模块（80 符号 0 DIFF）+ **两段式 prepare/commit**（优于计划，proposal 门约束实证）+ worker C2 薄化 + 14 golden | read 路径不抽 helper（无重复债，有意偏离） |
| W2 coordinator + D11 | registry 抽出 + telegram 参数化函数 + 改名 | ✅ 1889→1213（554 行 registry 抽出 md5 双一致）+ **telegram mixin**（优于参数化，保测试锚点）+ WorkerRuntimeAdapter | 簇 M 留守（计划授权）；D11 扫描首轮漏 tests/ 被回归门拦截 |
| W3 setup_service | 3 mixin + helpers 文件 | ✅ 2576→1520（43 方法 0 DIFF）| statics 随簇走不建 helpers（保调用点零变化，裁定范式） |
| W4 worker + session | helpers + mixin / 投影 helpers | ✅ 2101→1298 + 1847→1503（39+34 方法 0 DIFF）| worker 单 mixin 文件（合一）；session 常量单一定义迁 mixin |
| W5 capability_pack | 5 mixin 含 _launch_child_task | ✅ 2174→1523（83 方法 0 DIFF，业务逻辑出治理层）| **`_launch_child_task` 留主文件**（patch 命名空间耦合实证，7 failed 实测）+ httpx 锚点 |
| 收尾 | — | ✅ 23 个死 import 清理（AST 断言）+ 2 锚点 noqa | ledgers 承诺的显式 cleanup commit |

**行数账**：6 个巨型文件 12,328 行 → 主文件合计 8,577 行 + 18 个职责单一新模块（mixin/registry/package 模块，全部 <900 行）。最大文件从 2576 降到 1523。

## 验证总账

- **回归门（每 wave）**：W1 4102 / W2 4105（v2，v1 抓住 D11 漏改）/ W3 4105 / W4 4105 / W5 4104+1 flaky（×4 隔离复跑全过）/ 最终门见 git log——全部 vs baseline 账本（4091 + 14 golden 新增）**0 真回归**。
- **e2e_smoke**：每个 commit pre-commit hook 自动 8/8（共 13 个 commit 全过）。
- **AC 对照**（spec.md AC-1~6）：全部达标；AC-2 唯一例外 = D11 rename 固有的 f023 4 处引用更新（断言零改动，w2-ledger 记账）。
- **字节级对账**：两个自动化工具（顶层符号 + 方法级）+ 人工 md5/归一化 diff 三层；全部豁免逐条记录于 w1-w5 ledger。
- **6 个 e2e_live 真实 LLM 测试**：环境性挂起（provider 侧，baseline 即失败，与代码无关——已派独立诊断 chip）。

## 双评审总账（每 wave Codex + 第二席）

| Wave | Codex | 第二席（Opus） | HIGH 残留 |
|------|-------|---------------|----------|
| 计划 | 4H+4M 需修订后执行 | 1H+4M | 0（5 HIGH 全闭环） |
| W1 | 0H+1M+2L | APPROVE 0H/0M | 0 |
| W2 | 1H（f023，回归门先行拦截）+2L | PASS 0H/0M | 0 |
| W3 | safe to commit 0 finding | 通过 0H/0M | 0 |
| W4 | pass 0H/0M/0L | PASS 0H/0M | 0 |
| W5 | PASS 0H/0M/1L（patch 审计 0 漏网） | **席位中断（订阅限额）→ 主 session 按 F103c 先例接管六维**（limitation 记账） | 0 |

## 方法论沉淀（新增红线类别，供 F108b/后续重构）

1. **模块级 patch 命名空间耦合**（W5 实证）：`patch("<宿主模块>.<符号>")` 形态的测试钉死被 patch 符号必须留在宿主模块命名空间——函数名字解析走定义模块 `__globals__`，即使 `__get__`/getsource/MRO 三红线全满足，迁移仍会让 patch 失效。检查方法：grep 全部测试 patch 字符串含宿主模块名。
2. **残留扫描必须覆盖 pyproject testpaths 全部 9 路径**（W2 教训：漏根 `tests/` 被回归门拦截）。
3. **AST Name-Load 断言替代 grep**（W3 教训：字符串字面量骗 grep）。
4. **回归门在双评审之前先行拦截了唯一一次真错误**（W2 f023）——每 wave 全量回归非形式主义。

## 已知 limitations

1. W5 第二评审席为主 session 接管（非独立 Opus 模型）——订阅月度限额中断；六维确定性验证全过但缺独立模型视角。F108b 启动时若限额未恢复需用户拍板替代方案。
2. capability_pack 主文件 1523 行（预期 1100-1300）——`_launch_child_task` 134 行 + 锚点因 patch 耦合留守；继续压需改 phase_d 测试 patch 路径（测试修改，超 F108a 红线，可作 F108b/后续顺手项）。
3. living-docs（harness-and-context.md / module-design.md 三层职责定调）按计划归 F108b W6；本 Feature 仅顺手修了 2 处直接漂移（02-gateway 模块文档 + octoagent-architecture.md）。
4. 6 个 e2e_live 真实 LLM 测试环境性挂起（独立 chip 已派）。
