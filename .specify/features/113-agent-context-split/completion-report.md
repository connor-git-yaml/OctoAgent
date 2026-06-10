# F113 完成报告（completion-report）

> Feature：agent_context.py 按职责簇拆 mixin（M6 地基 sprint 收官件）
> baseline：origin/master `167b9cf4`；分支 `claude/festive-bohr-a78a7e`
> 原则：行为零变更（方法签名 / self.X 引用 / 实例化 / 外部 import 全不变）

## 1. 计划 vs 实际（逐批对照）

| 批次（refactor-plan） | 计划 | 实际 | 偏离 |
|------|------|------|------|
| B1 helpers 地基 | 常量+dataclass+自由函数 ~470 行 + re-export | ✅ helpers 481 行；re-export 30 名字（ast 提取） | 手工清单漏 3 个 `_SESSION_TRANSCRIPT_LIMIT_*` 常量 → 当场改用 ast 程序化提取补全；re-export 块初版误置 import 区外（E402）→ 移入 import 区 |
| B2 EntityEnsureMixin | 14 方法 ~1049 行 | ✅ 1149 行文件（含头部） | 无 |
| B3 MemoryServiceMixin | 8 方法 ~192 行 | ✅ 239 行文件 | ruff I001 --fix 动了 5 个 getter 函数内 lazy import：4 个重排（Phase 4 AST 对账抓出，3f2202a6 还原）+ 第 5 个 get_reranker_service 仅换行（AST 等价，第二评审字节级 diff 抓出，已还原；对账标准随之升级为字节级） |
| B4 MemoryRecallMixin | 7 方法 ~476 行 | ✅ 547 行文件 | 无 |
| B5 SessionReplayMixin | 12 方法 ~644 行 | ✅ 705 行文件 | 无 |
| B6 PromptAssemblyMixin | 12 方法 ~773 行 | ✅ 862 行文件 | 唯一语义适配：`_render_snapshot` 内 `AgentContextService._render_list` 类名自引用改 `AgentContextPromptAssemblyMixin._render_list`（同一 staticmethod 函数对象；mixin 不可回向 import 主类）。首次跑出 1 F821 + 13 测试失败，当场修复 |

**无跳过批次**。主文件 4600 → **1079 行（-76.5%）**；7 文件合计 5278 行（+678 = 文件头 import/docstring 开销，字节级对账证内容零丢失）。

任务期望"主文件 ~600 行"未达成（实际 1079）：`build_task_context` 单方法 556 行 + `_resolve_context_bundle` 154 行是审计 A4 决议"跨 4 簇组合根不可抽出"；进一步压缩须做方法内部分解，属行为变更风险更高的另一类重构，显式不纳入 F113（见 impact-report §5）。

## 2. 解决的问题（用户视角）

- M6 地基 sprint 最后一件收官：4600 行 god-file（F093 抽错缝后被 F094/F096/F097/F098/F124 持续堆回）按 5 个职责簇拆为独立 mixin 文件，每个文件顶部有职责边界声明（"新增 X 类方法放这里；Y 不属于本簇"），防止后续 Feature 再堆回单文件
- 后续 Feature（F105 Gateway / F108 Capability 等）触碰 context 装配时，改动面从"4600 行单文件"缩小到对应簇文件，review 成本与 merge 冲突面显著下降
- 外部零感知：6 个生产文件 + 全部测试的 import 路径、调用方式完全不变

## 3. 关键技术决策记录

1. **helpers 文件先行**：常量/dataclass/自由函数移到拆分叶子文件 `agent_context_helpers.py`（不依赖本目录 service/mixin；对 core/memory/agent_decision 依赖与拆分前一致），是打破"mixin ↔ 主文件"循环 import 的结构前提（非单纯减行数）
2. **re-export 保契约**：主文件以 redundant-alias 形式（`X as X`）re-export 全部 30 个模块级名字，含 orchestrator.py:84 跨模块 import 的私有名 `_dynamic_transcript_limit`；外部 import 零改动
3. **沿用 F093 mixin 范式**：无状态 mixin + 类级 annotation（`_stores: Any`）+ docstring 依赖约定；测试类名直调的 4 个静态方法（含 task_service 生产代码直调 `_memory_hit_payload`）经 MRO 继承保持可见（实测 callable）
4. **`_shared_*` 3 类属性 + `set_*` classmethod 留主类**：e2e_live/conftest 等 4 处直接 get/setattr `AgentContextService.<attr>`
5. **切割用 ast 精确边界**（含装饰器 + 紧邻上方注释吸收），方法体逐字节原样搬运；6 批每批独立 commit（回滚单元 = 批）

## 4. 验证结果

- 字节级全量对账（标准两轮收紧 AST→字节级）：拆前 91 个有名定义（81 函数/方法 + 10 类/常量）→ 拆后零丢失，90/91 byte-identical（唯一豁免 `_render_snapshot` 已记录）——见 residual-report.md
- 循环 import：7 模块独立加载干净 ✅
- 全仓 38 个外部 import 名字逐一可解析 ✅
- focused 测试组（services 全目录 + 4 直调测试文件 + 跨包 store 测试）：377 passed × 5 轮
- e2e_smoke：**8 passed** ✅（PYTHONPATH 锁 worktree 手动跑）
- 全量回归 vs baseline：{{PENDING_FULL_REGRESSION}}
- ruff（F821/E402/F401 新引入维度）：全干净；主文件保留与 baseline 一致的 9 个预存 F401（不顺手清理）

## 5. Codex adversarial review + 第二模型 spec-对齐 review（多评审 panel）

**双 review 总结论：0 HIGH 残留，可合入。**

### Codex adversarial review（GPT-5.4 挑战者立场）：0 HIGH + 1 MED + 4 LOW

| # | finding | 处理 |
|---|---------|------|
| M1 | logger name 真实可观测变化：各 mixin `structlog.get_logger()` 取本模块名，日志 `logger` 字段不再是 `...services.agent_context` | **拒绝绑回旧名**。理由：①F093 baseline 先例——turn_writer mixin 用 `get_logger(__name__)`，logger name 当年即跟随新模块；②绑回旧名属命名失真 + 兼容层叠加（违协作准则）；③仓库内零按 logger name 过滤的代码/断言（Codex 实测）。外部 Logfire dashboard/alert 若按旧 logger name 过滤需一次性调整——已列归总报告提示用户 |
| L1 | dataclass `__module__` 变为 `agent_context_helpers` | 接受记录为 introspection drift（仓库无 pickle/copyreg/`__module__` 消费，Codex 实测） |
| L2 | re-export 块无 `__all__` | 接受记录。验收口径=显式 import 路径兼容；无 star import 用户；新增 `__all__` 反会引入新的 star-import 语义面 |
| L3 | helpers"零依赖叶子"措辞不准（实依赖 core.models/memory/同目录 agent_decision） | **已修**：helpers docstring + 3 个制品文档措辞改为"拆分叶子（不依赖本目录 service/mixin；对外部包依赖与拆分前一致）" |
| L4 | `_render_snapshot` 豁免的 monkeypatch 边界：外部若 patch `AgentContextService._render_list` 不会被跟随 | 拒绝改 classmethod（那是真实签名变更，比现状更违零变更）；接受记录边界（仓库零 monkeypatch 此方法，Codex 实测） |

Codex 已验证无问题维度：MRO 零同名碰撞 / 4 处外部类名直调全解析 / `_dynamic_transcript_limit` re-export 链完整 / 循环 import 无 / `_shared_*` 查找路径不变 / 方法边界抽查无错误吸收。

### 第二评审（Claude Opus spec-对齐专项，SDD 多评审 panel）：PASS 可合入，0 high + 0 med + 3 low

独立验证：AST 全量对账 + 5 大方法字节级 diff（全部 byte-identical）+ 81 签名 AST 比对（0 mismatch）+ live MRO 解析 + 377 focused 实跑。3 low 全闭环（commit d5708ab4）：①抓出 Batch3 ruff 漏网的第 5 个 getter `get_reranker_service` import 换行（AST 等价但违字节级标准）→ 还原 baseline 原文，对账标准随之从 AST 级升级为字节级（90/91 byte-identical + 唯一豁免）；②residual-report 行数表 stale 数字刷新；③refactor-plan 补记实际 MRO 声明顺序。

### 两评审分歧 / 需人裁清单

**无残留需人裁项。** Opus 提出的"get_reranker_service 换行是否违字节级承诺"已通过还原 baseline 原文消解（两评审推荐方向一致）；两评审对 `_render_snapshot` 唯一豁免的等价性判断一致。M1 的拒绝决策若用户不认可，翻转成本低（5 个 mixin 各 1 行显式传 logger name）。

## 6. Living-docs 漂移闸

- `docs/codebase-architecture/harness-and-context.md` / `docs/blueprint/module-design.md`：grep 实测**无 AgentContextService 结构性现状描述**（仅 F093/F084 历史实施记录，历史记录不回改）→ 无结构 drift
- `docs/blueprint/milestones.md:526` F113 行：本次完成后更新完成标记（随最终 docs commit）
- 已知 limitations：`docs/blueprint/milestones.md:550` 的"F113 就绪确认"段引用 audit 预估数字（Entity ~1075/Memory ~718），与实测最终值（1049/476+192）有差——属审计估算 vs 实测的正常偏差，已在 impact-report §2 对账说明，不回改审计历史记录

## 7. 已知限制与后续

- **基础设施 bug（非 F113 引入，归总报告需用户知晓）**：worktree 内 pre-commit hook 触发 `uv sync`，把共享主仓 `.venv` 的 editable 安装重指到本 worktree（`_editable_impl_*.pth` 现指向 festive-bohr-a78a7e）——与集成 review 记录的"magical-bardeen .pth 残留"同一形成机制。合入后需在主仓 `octoagent/` 重跑 `uv sync` 恢复。本次 6 个 commit 均 `SKIP_E2E=1`（hook 在 worktree 下解释器被无关 bench worktree 污染，aiosqlite ModuleNotFoundError），e2e_smoke 已以 PYTHONPATH 锁 worktree 方式手动跑通 8/8
- test_f101 ask_back 测试存在 F083 同类偶发 flaky（慢跑时时序漂移），单跑/重跑均过，与本改动无关
- 主文件 1079 行中编排根 `build_task_context`(556) 的内部分解（提取子步骤方法）是潜在后续项，按审计决议显式不纳入 F113
