# F139 实施计划

> 上游：spec.md（收窄版，D1-D7 设计决策 + §5 U+2028 决策表）。
> worktree：`.claude/worktrees/F139-wire-replay`（branch `feature/139-wire-replay`，
> 基于 origin/master d22378b8）。验证纪律：禁 `uv sync`；
> `uv run --project octoagent --no-sync python -m pytest` + PYTHONPATH 锁 worktree。

## Phase A 地基（零网络，纯代码）

1. `tests/wire_replay/_wire_recorder.py`：
   - cassette 数据模型（dataclass + JSON round-trip，format_version=1；request 仅
     结构摘要 body_summary，URL 拆 scheme/host/path 禁 query）
   - secret 过滤 serializer（D3 六道管线：drop-token-endpoint / 请求头 allowlist /
     响应头 allowlist / body redact（复用 core.log_redaction）/ 落盘前 fail-closed
     禁串+模式扫描 / 事务式原子落盘 temp+os.replace）
   - `RecordingTransport`（wrap 真 transport：缓冲响应、剥 content-encoding 等三头、
     登记 ResolvedAuth 禁串、非 2xx 拒绝）
   - `ReplayTransport`（顺序 pop + method/host/path 松匹配 + 耗尽报错 + played 计数）
2. `tests/wire_replay/conftest.py`：makereport hookwrapper + `wire_cassette` loader
   fixture + 完整消费 autouse 护栏（D5；判定核心纯函数）。
3. `tests/wire_replay/scenarios.py`：录制/回放共享场景输入（单一事实源——instructions/
   history/tools/tool_choice per transport per scenario）。
4. 单测：`test_wire_serializer_secrets.py`（FR-1/2/3）+ `test_wire_replay_guards.py`
   （FR-6/7，用手工内联 cassette 驱动，不依赖 Phase B 产物）。

**Gate A**：新增测试全绿 + provider 包全量无回归。commit。

## Phase B 录制（真调用，gate opt-in，人监督）

1. `record_cassettes.py`：读宿主 `~/.octoagent`（load_project_dotenv + ProviderRouter
   resolve_for_alias 拿 runtime，重建 ProviderClient(runtime, http_client=录制 client)）；
   要求 `OCTOAGENT_ALLOW_MODEL_REQUESTS=1`。
2. 真录（预算 D7）：
   - siliconflow/`bench`：simple + tool_call + U+2028 探针 + embed
   - openai-codex/`main`：simple + tool_call
3. anthropic golden 手写（照 provider_client 解析器 + Anthropic 公开文档事件序列；
   `meta.source=handwritten-golden`）。
4. 录完立即：secret 断言测试针对新 cassette 跑 + `grep -RE` 人工双查 + 人眼全文 review。

**Gate B**：cassettes 落盘 + AC-2 双查零命中。commit（cassette 单独 commit 便于 review）。

## Phase C 回放钉住

1. 三 transport 回放测试（FR-4/5）：从 cassette 读出精确期望写死断言（tool_calls 名+
   参数 / usage / model_name / content 特征）。
2. `test_cassette_secret_scan.py`（FR-8 永久扫描）。
3. 验证 hermetic：unset 全凭证 env 跑回放套件（AC-1）。

**Gate C**：回放套件独立全绿 + 消费护栏实际生效（人为注释掉一个 call 验证 FAIL 一次，
恢复）。commit。

## Phase D U+2028 决断（基于 Phase B 证据）

按 spec §5 决策表（收紧后唯一动生产条件=探针在 wire 上抓到未转义 U+2028 原始字节）。
命中→最小修：`provider_client.py` 加 `_iter_sse_lines()` helper + 3 处调用点替换 +
F142 钉住测试翻转 + cassette/边界用例钉修复后行为；未命中→归档：completion-report
写明证据链与残余风险，F142 测试维持现状断言，修复候选保持归档（前摄 hardening 另立
Feature 人裁）。

**Gate D**：全量回归 0 regression（修复分支时重点跑 provider + gateway SSE 相关面）。
commit。

## Phase E 收口

1. 终门：全量回归 vs baseline（d22378b8）0 regression + e2e_smoke + AC-1/AC-2 复跑。
2. 双评审：`codex review`（final，挑战面=secret 过滤真零泄漏/回放真离线/护栏真抓
   未消费/U+2028 结论有据）+ Opus 自审。0 HIGH 收敛。
3. living-docs：testing-strategy.md F139 行 📋→✅（含重录路径一行）+ milestones.md
   F139 行 ✅ + e2e-testing 或 codebase-architecture 如涉及。
4. completion-report.md（4 件交付 / cassette 清单+录制成本 / U+2028 结论 / 偏离归档）。

**不 push origin。** 归总报告给主 session 等拍板。

## 关键文件清单（预期改动面）

| 文件 | 动作 |
|------|------|
| `octoagent/packages/provider/tests/wire_replay/*`（新目录） | 新增（基建+测试+cassette+脚本） |
| `octoagent/packages/provider/src/octoagent/provider/provider_client.py` | 仅 U+2028 修复分支时改（helper + 3 调用点） |
| `octoagent/packages/provider/tests/test_provider_client_wire_boundaries.py` | 仅 U+2028 修复分支时翻转钉住断言 |
| `docs/blueprint/testing-strategy.md` / `docs/blueprint/milestones.md` | living-docs 收口 |
| `.specify/features/139-wire-replay/*` | spec/plan/completion-report |
| pyproject / frontend / .github / .githooks / gateway | **零改动** |
