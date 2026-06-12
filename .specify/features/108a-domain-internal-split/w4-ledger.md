# W4 对账清单（worker_service + session_service 拆分）

> AC-3 制品。双评审：Codex **pass 0H/0M/0L** + Opus **PASS 0H/0M**。

## 搬运对账

- worker_service **2101→1298**（E 簇 21 方法 → `worker_profile_ops.py` `WorkerProfileOpsMixin` 852 行）；方法级 39/39 **0 豁免通过**（双席双跑一致）。
- session_service **1847→1503**（D 簇 13 方法 → `session_projection_helpers.py` `SessionProjectionMixin` 382 行）；方法级 34/34 **0 豁免通过**。
- Codex 深度核：方法体 AST 完全一致 / decorator 列表无差异 / raise 字面值字节一致 / staticmethod descriptor 保留（worker 6 + session 4）/ mixin 内 0 super() / 0 事件迁移。

## 豁免与决策记录

| # | 项 | 处置 |
|---|---|------|
| 1 | session 3 个模块级常量（`_AUDIT_TASK_ID`/`_LEGACY_CONTEXT_POLLUTED_*`）单一定义迁 mixin 模块 + 主文件 import 回 | Opus O5 判最优（拆分两侧真实共用；备选"mixin import 宿主"成环被正确否决；`_coordinator` 同名是独立副本惯例）；Codex 确认全仓无外部引用/monkeypatch 断裂 |
| 2 | worker mixin 顶部 import `ControlPlaneActionError`（W3 mixin 无此先例） | E 簇方法体直接 raise 该异常，体零变更必然；`_base` 叶子无环（Opus O7） |
| 3 | 7 项 pre-existing 死 import 留置（session 5 + worker 2，含 W1 冻结区内 1 个局部变量） | 100% HEAD 即死（Opus O8 逐项对账），零新增；清理归 F108a 收尾独立 cleanup commit（不进字节对账） |
| 4 | 3 处 lazy import 红线 | `normalize_behavior_agent_slug`/`EventType` 随方法体进 mixin 函数体内原位；`delete_session_cascade` 留主文件（双席核过） |

## 实施偏离

1. **worker 单文件 `worker_profile_ops.py` vs 计划双文件**（helpers+revision mixin）：合一形态，statics 以 @staticmethod 留 mixin（W3 已裁定范式延续）。接受。
2. session mixin 承载（非纯自由函数 helpers）：部分"投影辅助"需 self._stores，mixin 更贴合。接受。

## 红线复核

- W1 改造的 `_handle_behavior_read_file`/`_handle_behavior_write_file` **字节级冻结确认**（Codex byte_equal_after_dedent=True；Opus 1639b/2242b MATCH）。
- session"刻意少动"守住：编排根 `_build_session_projection_items` + E 簇 5 解析辅助 + F 10 个 handlers 全留主文件。
- mixin 反向调用主类（worker mixin→`_get_capability_pack_document`）MRO 可达（Codex 核）。

## 验证

- wave 回归门：**4105 passed / 0 failed**（持平 W2/W3 基线）/ 4:22
- 焦点：112 passed / 1 skipped（既有 skip）×双席各自复跑
- e2e_smoke：commit hook 自动

**0 HIGH 残留。双席判定均为 PASS/pass。**
