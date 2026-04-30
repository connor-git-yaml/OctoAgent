# Feature 087 — Clarifications

## 自动解决

- **[未量化：e2e_smoke "目标耗时 90-120s" 与"≤ 180s" 双指标关系模糊]**
  → 解决：180s 为强制 timeout（pre-commit hook 强杀阈值，SC-2 验收条件），90-120s 为"目标"（设计预算，不作为验收断言）。两者不冲突，实现时以 180s 作为 pytest timeout 参数，90-120s 作为注释说明设计意图。
  （来源：FR-22 / SC-2 / NFR-1 联合推断；Constitution §Durability First 不要求超强精度 SLA）

- **[未量化：e2e_full 单场景 timeout 未明确定义]**
  → 解决：全套 FR-12 定义单 LLM call timeout = 120s，FR-23 定义 smoke 单场景 = 30s，full 单场景 timeout 默认采用 120s（单 LLM call 上限），允许多轮 call 场景扩展到 max_steps × 120s 上限内。plan 阶段可基于此细化。
  （来源：FR-12 / FR-13 max_steps=10 联合推断）

- **[隐含假设：FR-8 "只读复制 auth-profiles.json" 后 CredentialStore 如何与其他 services 共享]**
  → 解决：OctoHarness DI 钩子 FR-2 已定义 `credential_store: CredentialStore | None`，e2e fixture 将 `real_codex_credential_store` 构造出的对象注入 OctoHarness，整个 session 内所有 service 通过 `app.state` 共享同一实例。无需额外设计。
  （来源：FR-2 / FR-8 / FR-3 联合；现有 OctoAgent Harness DI 架构约定）

- **[缺失边界：附录 A.1 提到"替换 vs 叠加"旧 5 域测试，spec 只写了"建议替换"但未锁定]**
  → 解决：自动选择"替换"（删除旧 `test_acceptance_scenarios.py`）。理由：spec §2.1 新 smoke 5 域（#1 #2 #3 #11 #12）覆盖且超越旧 5 域，叠加会制造双源真相，违反 CLAUDE.md"去掉功能时直接删除"原则。plan 阶段执行删除。
  （来源：附录 A.1 建议 + CLAUDE.md"去掉功能时直接删除所有相关代码"）

- **[缺失边界：附录 A.4 Codex OAuth quota 耗尽时 e2e 行为未锁定（FAIL vs SKIP）]**
  → 解决：自动选择 SKIP 并输出"quota exceeded"提示。理由：quota 耗尽属于环境问题而非代码问题，FAIL 会误报阻塞 commit；与 Perplexity 故障的处理策略（FR-24 retry 1 次后 SKIP）保持一致。
  （来源：FR-24 类比 + spec §3 Edge Cases "quota exceeded 提示而非 FAIL" 文字 + §11 风险 #1）

- **[隐含假设：FR-35 "从 mcp-servers.json 读后 redact 落日志"的 redact 范围未定义]**
  → 解决：redact 范围 = 所有满足 `[A-Za-z_]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Za-z_]*` 正则的 env 值，替换为 `[REDACTED]`。与 ThreatScanner 现有 pattern 风格一致。
  （来源：Constitution §5 Least Privilege by Default + FR-33/FR-34/FR-35 联合 + NFR-6）

- **[隐含假设：pre-commit hook 中 `repo:check` 命令来源未在 spec 中说明]**
  → 解决：`repo:check` 指现有 Makefile / 代码质量检查（lint / type check），非新引入命令；FR-32 仅定义分层顺序，具体实现复用现有 `repo:check` target（若不存在则 plan 阶段补充或跳过）。plan 阶段确认实际命令。
  （来源：FR-32 上下文推断；不影响架构决策）

## CRITICAL（需用户决策）

无 CRITICAL 问题。以下是评估过程中考虑过但最终自动解决的潜在 CRITICAL 项：

- **附录 A.2（测试 helper 归属）**：`_build_real_user_profile_handler` 等 helper 放 OctoHarness `test_factory()` 还是独立 `test_helpers.py`——两个方案都合理，但决策不影响本 spec 的 FR 定义，只影响 plan 阶段文件布局，**留 plan 阶段决策**，不作为 CRITICAL 澄清。

## 无歧义

spec.md 整体清晰，13 能力域划分明确，FR 覆盖完整，5 个 Open Question 均已在 §4.4 明确决议。核心指标（180s smoke / 10min full / max_steps=10 / LLM timeout=120s）有数字锚定。Constitution 兼容性、secrets 处理、污染隔离约束均有具体 FR 对应。
