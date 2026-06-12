# F108b W6 对账清单（审批缓存类下沉 + living-docs 三层定调）

> 双评审：Codex **PASS 0 finding**（sha256 双侧 `32525676…` 字节等价）+ Opus **APPROVE 0H/1L**。

## 改动

- `_ApprovalOverrideMemoryCache`（48 行无 TTL 内存 fallback）capability_pack.py → `tooling/permission.py`（其实现的 `ApprovalOverrideCacheProtocol` 正下方）。字节级保真：diff EXIT=0 + import 对象同一性 `is` True（Opus）+ sha256 一致（Codex）。
- cap_pack 注解诚实化：构造参数 + property 返回 `_ApprovalOverrideMemoryCache` → `ApprovalOverrideCacheProtocol`——生产注入的本就是 `policy.ApprovalOverrideCache`（TTL 版，harness:608 构造、broker:631 + cap_pack:785 同一实例），旧注解与生产不符，**修正非变更**（Opus O1/实测 #2 确认）。
- living-docs：harness-and-context.md **§2.8 三层职责边界与跨层契约**（职责表格 + 4 条跨层契约现状 + Manus/az-1 设计原则）+ module-design.md 9.2/9.8/9.13 同步。**Opus 漂移闸逐条实测：零漂移**（三层 import 方向 / TTL 语义差异 / F108a 全部行数 / 子模块列举 / 截断双层 hooks_legacy.py:276 / 双 safety-scan / 错误兜底 broker.py:204…478 / blocked 三态 :565 / 6 符号钉死语义）。

## 豁免/偏离

| # | 项 | 处置 |
|---|---|------|
| 1 | 计划"import + re-export 兜底"实施仅 import（Opus O2 LOW）| grep 实证该私有符号**无任何外部 import 面**（仅 cap_pack import + fallback 构造 + 定义 + docs），re-export 无必要省略——对比 `_ssrf_request_hook` 有 import 面才 re-export，判断标准一致 |
| 2 | 协议无 `@runtime_checkable` 且全仓零 isinstance 用法（Codex #3）| 注解协议化无运行时风险确认 |

## 验证

- 全量门：**4130 passed / 0 failed**（零 deselect，持平 F108a 合入后新基线）/ 5:53
- 焦点：246 passed + 1 xpassed（tooling 全量 + approval_override_e2e + capability_pack_tools，双席各自复跑）
- patch 面：全仓（含根 tests/）`_ApprovalOverrideMemoryCache` 零 patch 字符串（W5 红线检查）
- e2e_smoke：commit hook 自动

**0 HIGH 残留。**
