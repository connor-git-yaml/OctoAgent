# F139 Provider Wire 录制回放 — Completion Report

> 2026-07-13。worktree `F139-wire-replay`（branch `feature/139-wire-replay`），
> 基线 origin/master d22378b8。**未 push origin，等用户拍板。**

## 1. 交付对照（spec 范围 4 件，全闭环）

| 件 | 计划 | 实际 |
|----|------|------|
| ① secret 过滤 serializer 先行 | 五道管线 + 落盘前机械断言 | **升级为六道 + 三次评审加固**：token 端点 drop / 请求头 allowlist / 响应头 allowlist / body redact（复用 core.log_redaction）+ **身份字段定点洗刷**（实施中真录抓到 codex 回显 `safety_identifier=user-xxx` 账户标识、`prompt_cache_key` UUID、`instructions` 请求内容回流——模式扫描抓不到，人眼 review 抓到，新增 [scrubbed] 洗刷 + 扫描不变量）/ **raw 层已知凭证禁串硬比对**（Opus LOW-1：redact 之前逐字命中即拒录）+ dump 扫描最终后网 / 事务式原子落盘（temp+os.replace，失败零半成品） |
| ② 三 transport 真样本 cassette | 各 2-4 交互 | **8 盘**：siliconflow 4 真录（simple / tool_call / U+2028 探针 / embeddings 非流式路径）+ codex 2 真录（simple / tool_call）+ anthropic 2 **手写 golden**（宿主无凭证，`meta.source=handwritten-golden` 显式标注，含 `event:` 行/ping/SSE 注释行等真 wire 元素钉「非 data: 行跳过」路径）。回放测试 49 个全默认跑（hermetic：unset 全凭证 env + 假 HOME 双验证全绿；ReplayTransport 结构性无 socket） |
| ③ 完整消费护栏 | pydantic-ai fail_partially_used 范式 | 已落地：makereport hookwrapper + autouse teardown 检查（测试通过才检查，不遮蔽原始失败）；判定核心 `unplayed_indexes()` 纯函数单测；**tamper 实证**：给 cassette 多塞交互 → `1 passed, 1 error`（teardown FAIL）后还原 |
| ④ U+2028 修复评估 | 复核是否真生产 bug，极小修或归档 | **归档不动生产（证据完整）**：探针实测 DeepSeek 模型能 round-trip 输出 U+2028（parsed content=`'A B'`），但 wire 上 SiliconFlow 对 line-separator **特判转义**（delta 行实锤 `"content":" "`），而 CJK 却原样（`ensure_ascii=False` 实锤）→ 正是「实现对 line-separator 特判」的行业惯例（Codex spec review M3 预测命中）。按收紧判据（唯一动生产条件=探针抓到未转义原始字节）**未命中 → 不改生产**；F142 现状钉住测试维持，`openai_chat_u2028_probe.json` + `test_u2028_probe_replay_provider_escapes_line_separator` 把 provider 转义习性钉成永久回归（若重录后漂移=触发面出现，按 spec §5 重启评估） |

## 2. 关键设计偏离（已在 spec D1 论证 + 双评审过审）

**弃 vcrpy/pytest-recording，自研极简 recorder/replay（stdlib JSON cassette）**：
零新依赖 → 回放测试在主仓 venv / pre-commit hook / CI 处处默认可跑（无 uv sync
协调、无 importorskip 假绿窗口）；无全局 monkeypatch 与 F142 刚翻的 xdist 并行
零互扰。**pyproject 零改动**（任务红线里预期的 dev-dep union 冲突面归零）。
pydantic-ai 范式按设计移植（serializer 过滤/fail_partially_used/松 matcher/重录
文档化），不按依赖移植。代价（放弃 record-mode 矩阵/matcher registry）与自研
成本（压缩解码/头剥除/早停语义）在 spec D1 显式承认并入自证矩阵。

## 3. 真调用成本（D7 纪律）

| Provider | 计划预算 | 实际 | 说明 |
|----------|---------|------|------|
| SiliconFlow（API key） | ≤ 8 次 | **8 次** | run1 simple+tool_call(400) 2 次 + 手动探因 2 次 + run2 全 4 场景 4 次；成本 < $0.01 |
| openai-codex（订阅 OAuth） | ≤ 4 次 | **4 次** | 3 次 simple（前 2 次被 fail-closed 扫描正确拦下：禁串假阳性→当场修）+ 1 次 tool_call；另有 1 次连接失败未达 provider 不计 |
| anthropic | 0 | **0** | 手写 golden，未申请新 key |

录制期间管线拦截全部按设计工作：非 2xx 拒录（tool_call 400 时错误回显未落盘）、
fail-closed 拒绝产出（两次禁串命中零半成品文件）。

## 4. 实施中抓到并修复的问题（按时间序）

1. 场景工具名带点（`demo.weather`）→ chat transport 调用方契约是 fn 形态（tools
   透传、仅 tool_choice 转换）→ tools/tool_choice 名字不一致 → SiliconFlow 400。
   修：场景改无点名 `demo_weather`（三 transport 转换全自恒等）。
2. 禁串 blanket 登记 OAuth extra_headers 全部值 → 良性协议头（OpenAI-Beta=
   `responses=experimental` / originator）假阳性拒绝落盘。修：只登记不在请求头
   allowlist 的身份类头值 + 短值（<8 字符）不做子串禁串。
3. codex 响应回显账户身份（`safety_identifier` / `prompt_cache_key`）+
   `instructions` 回流——**人眼 review 抓到（模式扫描抓不到的类别）**。修：身份
   字段定点洗刷 + 扫描不变量；已录两盘离线重洗复扫（零新真调用）。

## 5. 双评审闭环（0 HIGH / 0 MED 残留）

- spec 阶段 Codex（`codex exec` 对抗 prompt）：2H+3M+1L 全闭环（详
  `codex-review-spec.md`）——request 不落完整 body / 事务式落盘 / D1 理由收窄 /
  回放定位诚实化 / U+2028 判据收紧 / URL 禁 query。
- final Codex（`codex review --base`）：0 HIGH + 3 P2 全修各配钉住测试。
- final Opus（独立 agent 六挑战实测取证）：0 HIGH / 0 MED / 5 LOW——2 修
  （raw 层禁串硬比对 / 诊断上下文全掩码）+ 3 带理由归档（详
  `codex-review-final.md`）。

## 6. 终门数据

- **全量回归**：master 基线 5066 passed / 15 skipped / 1 xfailed / 1 xpassed →
  worktree 5110（LOW 闭环前）→ 终态 5115 passed（+49 = 本 Feature 新增测试数），
  skipped/xfailed/xpassed 逐项相同，**0 regression 数学闭合**。
- **e2e_smoke**：8 passed（+1 skipped，基线一致）。
- **AC-1 hermetic**：unset 全凭证 env（Opus 复验另加假 HOME）回放套件全绿。
- **AC-2 secret 双查**：`grep -RE "sk-...|eyJ..."` + auth 头名 → 8 盘零
  命中；`test_cassette_secret_scan.py` 常绿化（含期望清单防目录漂移）。
- **AC-5 diff 面**：生产代码 0 行 / pyproject 0 行 / frontend/.github/.githooks/
  gateway 0 文件（红线全守住）。
- lint：ruff check + format 全绿。

## 7. 已知 limitations / 归档

- anthropic 2 盘为手写 golden（非 wire 真样本），可信度与既有 fake 单测同级 +
  真 wire 元素增量；拿到凭证后 `record_cassettes.py anthropic` 真录替换（脚本
  docstring 有步骤）。
- 消费护栏失败分支无 pytester 自动化（Opus LOW-2 归档）：纯函数单测 + tamper
  人工实证；护栏是二级网。
- 身份洗刷为 codex 已知形态（4 字段）；未来 provider 新回显字段依赖 allowlist/
  redact/raw 禁串/人眼四层 + 重录流程的人眼 review 步骤（Opus LOW-5 归档）。
- cassette 是快照定位：钉「解析栈能处理已见过的真实形状」，不承诺追 provider
  改版；重录成本=跑一次脚本 + 更新精确断言。
- 录制期 console 暴露面与日常生产进程同面（spec D3 归档；非 2xx 拒录 + 上下文
  全掩码后，可回显面只剩 provider 侧正常输出）。

## 8. living-docs

- `docs/blueprint/testing-strategy.md`：VCR 规划行 → Wire 真样本录制回放 ✅
  落地（含重录路径一行 + U+2028 归档结论）；Provider 抽象层路由行交叉引用。
- `docs/blueprint/milestones.md`：F139 行 ✅ 完成态。
- 重录文档：`record_cassettes.py` module docstring（命令 + 必做四步）。

## 9. 合入建议

**建议合入 origin/master**：0 regression 数学闭合 / 双评审 0 HIGH 0 MED 残留 /
红线（生产零改动、pyproject 零改动、不碰并行 agent 地盘）全守住 / cassette
经机械+人眼双查零 secret。与 F141（gate 编排）/ F143（frontend）文件面零交集，
rebase 预期零冲突。合入后主仓 `uv sync` 无需（零依赖变化）。
