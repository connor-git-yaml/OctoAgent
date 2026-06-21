# F106 User Plugin Loader — Spec Quality Checklist

**生成时间**: 2026-06-21  
**检查基准**: spec.md v0.1（Draft，待 GATE_DESIGN 拍板）  
**检查员**: 质量检查表子代理

---

## 维度 A：FR 可测性 + 向 AC/SC 的可追溯性

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| A1 | FR-1.1（发现扫描）有 AC/SC 映射 | **PASS** | US1-AC1 + SC-001 明确覆盖 |
| A2 | FR-1.2（PluginManifest schema）有可测断言 | **PASS** | US2-AC1（非法 manifest）= 直接测 Pydantic 校验 |
| A3 | FR-1.3（manifest 校验 kebab/制品存在/allowlist）有 AC/SC | **PASS** | US1-AC3（缺制品）+ US2-AC1（非法 manifest）+ SC-003 覆盖 kebab/name_mismatch；edge case 段覆盖 name_mismatch |
| A4 | FR-1.4（plugins_dir DI）有 AC/test | **PASS** | SC-010 + `test_plugins_dir_di_isolation` |
| A5 | FR-2.1（skill 注册 PLUGIN source）有 AC | **PASS** | US1-AC1 + §9 `test_valid_plugin_skill_registered` |
| A6 | FR-2.2（名冲突安全，不覆盖内置）有 AC/SC | **PASS** | US2-AC3 + SC-003 + `test_name_collision_does_not_override_builtin` |
| A7 | FR-2.3（#6 降级隔离）有 AC/SC/test | **PASS** | US2-AC1 + SC-002 + e2e `test_bad_plugin_does_not_crash_gateway` |
| A8 | FR-2.4（fail-fast 注册器二分）有可测断言 | **GAP** | FR-2.4 "MAY 构造期抛错"——§9 AC↔test 无对应测试条目；构造期 fail-fast 路径未显式断言。实际可测，但绑定缺失 |
| A9 | FR-2.5（plugin 不新增工具 schema）有可测断言 | **GAP** | FR-2.5 是重要安全需求，但 §9 无测试绑定；SC 无对应条目（SC-007/FR-9.2 只提 `_PROFILE_ALLOWLIST`，不直接覆盖"不新增工具 schema"这个断言）。需加 test binding 或归入 SC-007 并明确 |
| A10 | FR-3.1/3.2/3.3（toggle + 持久）有 AC/SC | **PASS** | US3-AC1/2 + SC-005 + `test_toggle_disable_enable_persists` |
| A11 | FR-4.1（扫描经 ContentThreatScanService 单入口）有 test | **PASS** | §9 `test_scan_via_content_threat_service` |
| A12 | FR-4.2（BLOCK → 拒载）有 AC/SC | **PASS** | US2-AC2 + SC-004 |
| A13 | FR-4.3（不新增 pattern）有可测断言 | **GAP** | FR-4.3 是一个"禁止类"需求，§9 无对应测试绑定；无法用常规测试"断言没有新 pattern"（需负面验证）。建议在 plan 阶段加 static/grep 检查，或在 §9 注明验证方式 |
| A14 | FR-4.4（审计无原文）有 SC | **PASS** | SC-004"审计无原文" + FR-7.2 |
| A15 | FR-5.1（behavior 最低优先级）有 AC/test | **PASS** | US4-AC1 + `test_plugin_behavior_lowest_priority` |
| A16 | FR-5.2（behavior allowlist 约束）有 AC/test | **PASS** | US4-AC2 + SC-006 + `test_plugin_behavior_allowlist_enforced` |
| A17 | FR-5.3（behavior 不逃逸目录，不绕 `_PROFILE_ALLOWLIST`）有 test | **GAP** | §9 无针对 FR-5.3 路径守卫的测试绑定（与 US4-AC2 的 allowlist file-id 不同——这是物理路径穿越）。需补 test entry，如 `test_plugin_behavior_path_traversal_rejected` |
| A18 | FR-6.1–6.6（REST API 全部）有 test | **PASS** | US5 + SC-009 + `test_list_and_delete`（覆盖范围；FR-6.5 refresh 原子性未单独绑定——接受，可归入 FR-6.5 contract test 范围内）|
| A19 | FR-7.1–7.3（审计事件 + EventStore 降级）有 test | **PASS**（部分）| FR-7.1/7.2 有 AC 覆盖；FR-7.3 EventStore 降级无专门 test binding——§9 未列该条目 |
| A20 | FR-8.1（bootstrap 段序）有 test | **PASS** | `test_plugin_bootstrap_order` |
| A21 | FR-8.2（bootstrap try/except 降级）有 SC | **PASS** | SC-002 + US2-AC1（坏 plugin 不拖垮 bootstrap）|
| A22 | FR-8.3（watchdog shutdown）有 AC/SC | **PASS**（条件）| FR-8.3 标注"若 DP-6 选 watchdog"，条件合理；FR-10.1 对应 |
| A23 | FR-9.1–9.3（H1/H2/H3 + 0 regression）有 SC | **PASS** | SC-007/SC-008/SC-010 |
| A24 | FR-10.1/10.2（条件需求）明确说明 test 待拍板后补 | **PASS** | §9 末尾注明"hot-reload/git update AC↔test 待 GATE_DESIGN 拍板后补"，合理豁免 |

**维度 A 小计**：20 PASS / 4 GAP（A8、A9、A13、A17）

---

## 维度 B：User Story AC ↔ §9 AC↔Test 绑定完整性

| US / AC | §9 有对应条目 | 状态 |
|---------|--------------|------|
| US1-AC1 | 有（`test_valid_plugin_skill_registered`）| **PASS** |
| US1-AC2 | 有（`test_plugin_skill_load_returns_body`）| **PASS** |
| US1-AC3 | 有（`test_missing_artifact_rejected`）| **PASS** |
| US2-AC1 | 有（`test_bad_manifest_isolated_good_loads`）| **PASS** |
| US2-AC2 | 有（`test_threat_flagged_plugin_rejected`）| **PASS** |
| US2-AC3 | 有（`test_name_collision_does_not_override_builtin`）| **PASS** |
| US3-AC1 | 有（`test_toggle_disable_enable_persists`，合并 AC1/AC2）| **PASS** |
| US3-AC2 | 同上 | **PASS** |
| US4-AC1 | 有（`test_plugin_behavior_lowest_priority`）| **PASS** |
| US4-AC2 | 有（`test_plugin_behavior_allowlist_enforced`）| **PASS** |
| US5-AC1 | 有（`test_list_and_delete`）| **PASS** |
| US5-AC2 | 有（`test_list_and_delete`，合并）| **PASS** |

**维度 B 小计**：12/12 PASS

---

## 维度 C：Success Criteria 可量化性

| SC | 可量化/可机械验证 | 状态 | 说明 |
|----|-----------------|------|------|
| SC-001（100% 合法 plugin 被发现）| 可验证 | **PASS** | "100%" 在 hermetic tmp 环境内可数 |
| SC-002（混装降级 e2e）| 可验证 | **PASS** | 结构化测试 `test_bad_plugin_does_not_crash_gateway` |
| SC-003（名冲突内置不被覆盖）| 可验证 | **PASS** | 读 SkillDiscovery 断言 |
| SC-004（威胁拒载 + 审计无原文）| 可验证 | **PASS** | 注入 payload + 断言 REJECTED + 检查 event payload 字段 |
| SC-005（toggle 持久跨重启）| 可验证 | **PASS** | 写 `.disabled` + 重跑 discovery + 断言 |
| SC-006（behavior allowlist + 最低优先级）| 可验证 | **PASS** | 需要 2 个 assertion：①allowlist 校验；②优先级 merge |
| SC-007（H1/H2/H3 不破）| **部分主观** | **GAP（低）** | "无 plugin 可提供绕过渠道"在 Model A 是构造性保证（无代码），可用静态断言替代运行时测试；但"`_PROFILE_ALLOWLIST` 行为不变"需与 baseline 比对——未明确比对方法 |
| SC-008（0 regression）| 可验证 | **PASS** | 全量 pytest + e2e_smoke |
| SC-009（REST 契约测试全绿）| 可验证 | **PASS** | |
| SC-010（hermetic DI）| 可验证 | **PASS** | |

**维度 C 小计**：9 PASS / 1 GAP（SC-007，低优先级——构造性保证已充分，验证方法可在 plan 补）

---

## 维度 D：Constitution 映射覆盖（§7）

| 规则 | 覆盖 | 状态 |
|------|------|------|
| #1 Durability First | `.disabled` 落盘 | **PASS** |
| #2 Everything is an Event | 3 个新 EventType | **PASS** |
| #3 Tools are Contracts | skill `tools_required` 只引用已存在工具 | **PASS** |
| #4 Side-effect Must be Two-Phase | **未出现在 §7** | **GAP** |
| #5 Least Privilege | 审计不存原文 + plugin 不放大权限 | **PASS** |
| #6 Degrade Gracefully | 单 plugin 降级 + bootstrap try/except | **PASS** |
| #7 User-in-Control | toggle/delete 用户可控 | **PASS** |
| #8 Observability is a Feature | **未出现在 §7** | **GAP** |
| #9 Agent Autonomy | plugin = 数据，LLM 自主选用 | **PASS** |
| #10 Policy-Driven Access | 不旁路 Policy；威胁扫描经单入口 | **PASS** |
| H1 管家 mediated | plugin 不提供绕过渠道 | **PASS** |
| H2 完整对等 | 不破坏 `_PROFILE_ALLOWLIST` | **PASS** |
| H3 委托 | 不引入新委托模式 | **PASS** |

**维度 D 小计**：11 PASS / 2 GAP（#4、#8 缺失）

**#4 分析**：DELETE plugin 目录是不可逆副作用，理应触发"Plan→Gate→Execute"。spec 对 DELETE 的处理（FR-6.4 直接删目录 + `_ensure_path_within`）未提及是否需要确认。对 v0.1 单用户个人 OS + 显式 REST DELETE 而言，可论证为用户已给出操作授权（#7 User-in-Control 兜底），但 §7 应明确解释"DELETE 为何豁免 #4"，否则为未处理缺口。

**#8 分析**：plugin 子系统状态（加载了哪些、哪个拒了为什么）完全可观测（EventStore + REST `/api/plugins`），但 §7 未显式映射。低优先级，但属于遗漏。

---

## 维度 E：Out-of-Scope 边界清晰度 + 推迟目标归属

| 排除项 | 归属标注 | 状态 |
|--------|---------|------|
| 代码可执行 plugin（Model B）| "Model B / v0.2+"，明确 | **PASS** |
| channel-as-plugin | "扩展点 / Model B"，§8 有 handoff 说明 | **PASS** |
| Companion | "M7"，明确 | **PASS** |
| 沙箱/签名/provenance | "后续加固"，明确 | **PASS** |
| per-project/per-agent scoping | "v0.2"，明确（DP-8）| **PASS** |
| 上传安装（REST 多文件）| "v0.2"，明确 | **PASS** |
| plugin 市场/多用户 | "退出（Blueprint §0）"，明确 | **PASS** |
| plugin 改 Policy/新增工具 schema | "不做（#9/#10）"，明确 | **PASS** |
| H1/H2/H3 协作改动 | "不做"，明确 | **PASS** |

**维度 E 小计**：9/9 PASS

---

## 维度 F：Decision Points 与 FR 内部一致性

| DP | 相关 FR | 一致性 | 状态 |
|----|---------|--------|------|
| DP-1（Model A 声明式）→ FR-2.5（不新增工具 schema）| 一致 | **PASS** |
| DP-2（名冲突拒载）→ FR-2.2（名冲突安全）| 一致，MUST 语义匹配 | **PASS** |
| DP-3（manifest schema）→ FR-1.2/1.3 | 一致，Pydantic 校验链完整 | **PASS** |
| DP-4（`.disabled` toggle）→ FR-3.1/3.2/3.3 | 一致 | **PASS** |
| DP-5（fail-fast 二分）→ FR-2.3/2.4 | 一致；"构造期 fail-fast vs 外部资源降级"区分清晰 | **PASS** |
| DP-6（hot-reload，待拍板）→ FR-10.1 + FR-8.3 | 一致，条件需求清晰 | **PASS** |
| DP-7（git update，待拍板）→ FR-10.2 | 一致 | **PASS** |
| DP-8（全局 user-level）→ §2.2 Out-of-Scope per-project | 一致 | **PASS** |
| DP-9（装载期威胁扫描）→ FR-4.1/4.2/4.3/4.4 | 一致；但 fail mode（fail-open vs fail-closed）DP-9 末尾"倾向 fail-open，plan 定"与 FR 未绑——FR-4 无对应 MUST/MAY 说明 | **GAP** |
| DP-10（H1 + 文件系统护栏）→ FR-9.1/FR-5.3 | 一致 | **PASS** |
| DP-11（behavior overlay 最低优先 + allowlist 受限）→ FR-5.1/5.2/5.3 | 一致 | **PASS** |

**维度 F 小计**：10 PASS / 1 GAP（DP-9 fail mode 未绑定到 FR-4）

---

## 维度 G：安全 / 信任模型覆盖（Model A 声明式）

| 安全面 | 覆盖 | 状态 |
|--------|------|------|
| 恶意指令/间接注入（prompt injection）| FR-4 + ContentThreatScanService MEMORY scope | **PASS** |
| 文件系统路径穿越（`../`）| FR-1.3 kebab 校验 + FR-5.3 `_ensure_path_within` | **PASS** |
| plugin 劫持内置 skill 名（privilege escalation via naming）| FR-2.2 + SC-003 | **PASS** |
| 工具权限放大（plugin 新增工具 schema）| FR-2.5 + DP-1 + §2.2 Out-of-Scope | **PASS** |
| 审计敏感信息泄漏（原文进 payload）| FR-4.4 + FR-7.2 | **PASS** |
| H1 绕过（channel-as-plugin 绕主 Agent）| DP-10 + FR-9.1（构造性：v0.1 Model A 无此面）| **PASS** |
| plugin 篡改 agent 人格（IDENTITY/SOUL/HEARTBEAT）| DP-11 + FR-5.2 allowlist 禁 | **PASS** |
| EventStore 不可用泄漏状态 | FR-7.3 降级（仅 warning，不阻断）| **PASS** |
| ThreatScanner 异常时 fail mode | DP-9 末尾提出"倾向 fail-open，plan 定"，但 FR 未固化为 MUST/MAY | **GAP** |
| plugin 间名冲突（先注册胜）| DP-2 + FR-2.2（提到"先注册者胜"）| **PASS** |
| DELETE 越界（删非 plugin 目录）| FR-6.4 `_ensure_path_within` | **PASS** |

**维度 G 小计**：10 PASS / 1 GAP（ThreatScanner 异常 fail mode 未在 FR 固化）

---

## 维度 H：孤立 FR / 未覆盖 AC 扫描

**孤立 FR（有 FR 但无 AC/SC/test 绑定）**：
- FR-2.4（fail-fast 注册器构造期抛错）：§9 无条目 → GAP（同 A8）
- FR-2.5（不新增工具 schema）：§9 无条目，SC-007 间接覆盖但不精确 → GAP（同 A9）
- FR-4.3（不新增 pattern）：§9 无条目，纯禁止类 → GAP（同 A13）
- FR-5.3（behavior 不逃逸目录）：§9 无条目 → GAP（同 A17）
- FR-7.3（EventStore 降级）：§9 无条目 → GAP（同 A19）
- FR-8.2（bootstrap try/except 降级）：US2-AC1 间接覆盖（坏 plugin 不拖垮 bootstrap），可接受

**未覆盖 AC（AC 有但 §9 无 test binding）**：
- Edge Cases（§4 末段）未映射到 §9：`name_mismatch` / `non-kebab name` / `..` 路径分隔符 / 并发 refresh 等 8 个 edge case 均无专门 §9 条目。部分可归入已有 test，但路径穿越（`..`）和并发 refresh 应显式绑定。

**维度 H 小计**：5 孤立 FR（GAP），edge cases 未绑定（低优先级 GAP）

---

## 综合统计

| 维度 | 总项 | PASS | GAP |
|------|------|------|-----|
| A：FR 可测性 + 可追溯性 | 24 | 20 | 4 |
| B：US AC ↔ §9 绑定 | 12 | 12 | 0 |
| C：SC 可量化性 | 10 | 9 | 1 |
| D：Constitution 覆盖 | 13 | 11 | 2 |
| E：Out-of-Scope 边界 | 9 | 9 | 0 |
| F：DP ↔ FR 一致性 | 11 | 10 | 1 |
| G：安全/信任模型 | 11 | 10 | 1 |
| H：孤立 FR / 未覆盖 AC | — | — | 5（孤立 FR）+ edge case 未绑定 |
| **合计** | **90** | **81** | **9** |

---

## GAPS TO FIX BEFORE PLAN

### 高优先级（影响实现正确性或安全性）

**GAP-1（DP-9 / FR-4 fail mode 未固化）**
- 位置：DP-9 末句"倾向 fail-open，plan 定"；FR-4 无对应 MUST/MAY
- 问题：ThreatScanner 异常时 plugin 是装载还是拒载，spec 未决——plan 时可能实现歧义
- 修复：在 FR-4 增加 FR-4.5：`ThreatScanner.scan` 抛异常时，SHOULD 装载该 plugin + 写 `PLUGIN_REJECTED(reason=scan_error, level=warning)`（fail-open），或 MUST 拒载（fail-closed）——选一并固化

**GAP-2（FR-2.5 无 §9 test binding，安全性需求）**
- 位置：FR-2.5；§9 未列
- 问题："plugin MUST NOT 新增工具 schema / 改 Policy" 是 #9/#10 的核心安全需求，但 §9 无测试绑定——实现时可能遗漏断言
- 修复：§9 增加一行：`FR-2.5（plugin 不新增工具 schema）→ 验证方式：static grep 断言 plugin manifest 无 tools_required 外字段 + plan 阶段确认 PluginRegistry 无工具注册路径`

**GAP-3（FR-5.3 路径守卫无 §9 test binding）**
- 位置：FR-5.3；§9 未列
- 问题：behavior overlay 路径穿越（逃逸 plugin 目录）是文件系统安全约束，但 §9 只有 allowlist file-id 测试（US4-AC2），无物理路径穿越测试
- 修复：§9 增加：`FR-5.3（behavior 路径守卫）→ test_plugin_behavior_path_traversal_rejected`

### 中优先级（可测性缺口，实现时易漏覆盖）

**GAP-4（FR-2.4 fail-fast 注册器构造期无 §9 test binding）**
- 位置：FR-2.4；§9 未列
- 修复：§9 增加：`FR-2.4（注册器构造期 fail-fast）→ test_plugin_registry_internal_invariant_raises`（或注明"纳入构造函数单测，触发路径：编程错误注入"）

**GAP-5（§7 Constitution 缺 #4 Side-effect Two-Phase 覆盖）**
- 位置：§7 映射表
- 问题：DELETE plugin 目录是不可逆副作用；§7 未解释为何豁免 #4（可论证"用户显式 REST DELETE = Phase 1"，但需写明）
- 修复：§7 补：`#4 Side-effect Must be Two-Phase → DELETE plugin = 显式 REST DELETE（用户已确认意图 = Phase 1）；目录不可逆删除接受 Plan→Execute 合并（无敏感数据，可从文件系统恢复）`

**GAP-6（§7 Constitution 缺 #8 Observability 覆盖）**
- 位置：§7 映射表
- 修复：§7 补：`#8 Observability is a Feature → plugin 状态全经 EventStore（LOADED/REJECTED/TOGGLED）+ REST /api/plugins 可查询；每次拒载含 reason`

**GAP-7（FR-7.3 EventStore 降级无 §9 test binding）**
- 位置：FR-7.3；§9 未列
- 修复：§9 增加：`FR-7.3（EventStore 不可用降级）→ test_plugin_eventstore_unavailable_does_not_block_load`（mock EventStore 抛异常，断言 plugin 仍装载）

### 低优先级（plan 阶段可补）

**GAP-8（FR-4.3 不新增 pattern 无可执行测试绑定）**
- 位置：FR-4.3；§9 未列
- 说明：纯"禁止类"需求，传统测试难以直接表达；但无绑定会导致 plan/verify 遗漏
- 修复：§9 注明：`FR-4.3（不新增 pattern）→ 验证方式：code review 断言 plugin_registry.py 不 import/修改 ThreatScanner pattern 集；CI grep 检查`

**GAP-9（SC-007 `_PROFILE_ALLOWLIST` 行为不变缺明确比对方法）**
- 位置：SC-007
- 修复：SC-007 补：验证方式 = 全量回归 baseline（SC-008 覆盖），`_PROFILE_ALLOWLIST` 常量 grep 比对 vs f3d8a267——归入 SC-008 即可，或 SC-007 加注"静态断言：`_PROFILE_ALLOWLIST` 不被 F106 改动（grep diff）"

---

## 整体评估

**GATE_DESIGN 前提说明**：本 spec 在 3 个核心决策点（DP-1/DP-6/DP-7）明确标注"待用户拍板"，这是正确行为；§12 GATE_DESIGN 清单结构清晰。检查结论基于 DP-1 已选 Model A 的路径。

**可进入 PLAN 的条件**：
- 必须修复：GAP-1（DP-9 fail mode）、GAP-2（FR-2.5 test binding）、GAP-3（FR-5.3 test binding）
- 建议修复：GAP-4 至 GAP-7（中优先级，plan 启动前补完）
- 可在 plan 阶段补：GAP-8、GAP-9

**整体质量**：spec 核心路径（FR-1 至 FR-9）覆盖完整，Decision Points 与 FR 高度一致，Security/Trust Model 覆盖全面，User Story AC ↔ §9 绑定 100%（12/12）。主要缺口集中在禁止类需求的测试绑定、Constitution §7 两条规则遗漏，以及 ThreatScanner 异常 fail mode 未在 FR 层固化。**在 GATE_DESIGN 用户拍板 + 修复 GAP-1 至 GAP-3 后，可进入 plan 阶段。**
