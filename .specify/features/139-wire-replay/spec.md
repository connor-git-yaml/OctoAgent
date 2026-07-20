# F139 Provider Wire 录制回放（收窄版）

> M9 波④ / P2 / S-M。**Fable 复审收窄**：仅 provider transport 层 wire 真样本回归，
> **不承担「agent-loop 用例 L2→L3 降层」叙事**——该论证已被推翻（agent-loop 请求体含
> 全量 system prompt / BehaviorPack / recall，高挥发 → cassette body 匹配必常断、松匹配
> 必假绿；pydantic-ai 自家教义承认 cassette 对 body 不敏感、wire shape 须单测钉住）。
> 决策环降层由 F138 承担（已落地）。

## 1. 背景与价值锚

- **已有（别重造）**：provider_client 三 transport 的 wire body shape 已由 23 个既有用例
  钉住（`test_provider_client_chat.py` / `test_provider_client_responses.py` /
  `test_provider_client_anthropic.py` / `test_provider_client_tool_choice.py` /
  `test_provider_client_v1_url.py`），F142 又补了字节级粘包/半包/malformed JSON 边界族
  （`test_provider_client_wire_boundaries.py`）。这些全是**我们手写的 fake 响应**。
- **真缺**：解析栈从未被**真实 provider 的 wire 样本**穿透过——SSE 分帧习惯（`\n` vs
  `\r\n`）、事件序列真实形状（codex Responses 的 `response.output_item.added/done` 实际
  字段、SiliconFlow usage chunk 的落点、`ensure_ascii` 策略）、真实 finish 语义，全部
  基于我们对文档的理解手搓。F103d 的 double `/v1` 404 真 bug 正是「手搓假设 ≠ 真 wire」
  的实证（靠 instance workaround 掩盖过）。
- **本 Feature 的 cassette 价值** = **真实 provider 响应文本快照**回放穿透解析栈
  （事件序列 / 字段形状 / usage 落点 / finish 语义），做成默认跑（无 key 无网络）的
  确定性回归。诚实边界：回放是 buffered 快照，**不复刻 chunk 边界**——字节级粘包/半包
  重组维度归 F142 边界族（受控字节切片比任何真实分块更对抗）；wire **请求** shape
  正确性仍由既有 23 用例钉，不重复。

## 2. 范围（4 件）

1. **secret 过滤 serializer 先行**（Constitution #5 硬前置）：cassette 唯一落盘口内建
   过滤管线，落盘前机械断言零 secret；专项测试用假 token 走全管线验证。
2. **三 transport 各录少量真实 cassette**：
   - `openai_chat`：SiliconFlow（`SILICONFLOW_API_KEY`，宿主 `~/.octoagent/.env`），
     alias `bench`（deepseek-ai/DeepSeek-V3.2）——simple completion + tool_call +
     U+2028 探针 + `embed()`（非流式路径顺手覆盖，Qwen/Qwen3-Embedding-0.6B）。
   - `openai_responses`：openai-codex OAuth（宿主 auth-profiles.json），alias `main`
     （gpt-5.5）——simple completion + tool_call。订阅额度，**不循环重录**。
   - `anthropic_messages`：**手写 golden 样本**并显式归档（宿主无可用 anthropic provider
     条目；auth-profiles.json 中 `anthropic-claude-default` 的 43 字符 access_token 判定
     stale，且任务纪律「别为录制去要新 key」）。golden 按 Anthropic Messages API 公开
     文档 SSE 事件序列手写，cassette `meta.source = "handwritten-golden"` 与真录区分。
3. **cassette 完整消费护栏**：回放测试通过后 cassette 有未播放交互 = FAIL（抓「代码
   少发请求但测试仍绿」的静默 drift；pydantic-ai `fail_partially_used_vcr_cassettes`
   范式，含「不遮蔽原始失败」语义）。
4. **U+2028 修复评估**（F142 输入，**本 Feature 唯一可能的生产改动**）：用真 provider
   证据复核「未转义 U+2028 → LineDecoder 切行 → delta 静默丢」是否真生产 bug；确认且
   修复极小则最小修 + 真样本钉，否则归档结论不动生产。判据见 §5。

## 3. 不做什么（范围铁律）

- **不录 agent-loop / harness 层用例**（收窄理由见页首；决策环 L3 归 F138）。
- **不做 body 严格匹配**：matcher 松（method + host + path + 顺序），请求 body 不参与
  匹配——body shape 回归由既有 23 用例 + F142 边界族钉，cassette 只负责「真响应样本
  穿透解析栈」。
- **不替代既有 wire body shape 单测**——两者互补，零删改。
- **不引入 vcrpy / pytest-recording 依赖**（设计决策 D1，见 §4）。**pyproject 零改动**
  （与 F141 的 union 冲突面归零）。
- **不复刻 chunk 边界**：回放响应为 buffered 单块（httpx.Response(content=...)），粘包/
  半包重组面 F142 已按字节切片专门钉住，cassette 不重复该维度。
- **不碰** frontend（F143）/ .githooks / .github / tests-AGENTS（F141）/ gateway。
- **不录 OAuth token 交换**：结构性不可达（OAuth refresh 走 PkceOAuthAdapter 自己的
  HTTP 机器，不经注入 http_client）+ 防御性 drop（录制器丢弃 token 端点交互）双保险。

## 4. 设计决策

### D1 自研极简 recorder/replay（stdlib JSON cassette），不引入 vcrpy

照 pydantic-ai 范式移植**设计**（serializer 过滤 / fail_partially_used / 重录文档化 /
松 matcher），不移植**依赖**。核心理由收窄为两条（Codex spec review M1：不暗示
transport 语义风险低）：

1. **零新依赖 → 回放测试立即处处可跑**：主仓 venv / pre-commit hook / CI 无需 uv sync
   协调，无 `pytest.importorskip` 静默 SKIP 假绿窗口（三 agent 并行波次中 hook uv-sync
   改写共享 venv 指向是已知坑，memory `project_precommit_hook_execution_model`）；
   **pyproject 零改动**，与 F141 的 union 冲突面归零。
2. **无全局 monkeypatch 的并行隔离**：`ProviderClient.__init__(runtime, http_client)`
   （provider_client.py:304-310）是唯一 HTTP 出口，httpx `AsyncBaseTransport` 自定义
   transport 即录制/回放挂点——vcrpy patch httpcore 全局栈，与 F142 刚翻转的 CI
   `-n auto --dist=loadgroup` 并行存在互扰风险面；pydantic-ai conftest 自己都要
   monkeypatch `vcr.stubs.aiohttp_stubs` 绕 vcrpy#927。
3. 附带收益：secret 过滤单一代码路径可机械测试（自研 serializer 是唯一落盘口）。
4. **显式承认的自研成本**（transport 语义坑不低估，列入自证矩阵，绑定
   `test_wire_serializer_secrets.py` / `test_wire_replay_guards.py`）：
   - 压缩响应：录制侧存**解码后**文本 + 剥 `content-encoding/content-length/
     transfer-encoding` 三头（否则回放二次解压失败）——自证用例=inner transport 回
     gzip 编码 body，录→回放全链路绿；
   - 头大小写归一（lower）；
   - 非 2xx 交互拒绝落盘（见 D3）；
   - URL query 断言为空（见 D3）；
   - 解析器早停（chat 遇 `[DONE]` break）后 buffered body 剩余未读——回放交互一经
     取出即计 played，消费护栏语义按「交互」计不按「字节」计（显式定义）。
5. 代价：放弃 vcrpy 的 record-mode 矩阵 / matcher registry——我们只有一条录制路径
   （显式脚本）+ 一种 matcher 策略，不需要那套机器。实现预算 ~300 行（recorder +
   replayer + serializer 合计）。

### D2 cassette 格式（JSON / stdlib）

```json
{
  "format_version": 1,
  "meta": {
    "provider_id": "siliconflow", "transport": "openai_chat",
    "model": "deepseek-ai/DeepSeek-V3.2",
    "source": "live-recording | handwritten-golden",
    "recorded_at": "<UTC ISO8601>", "scenario": "<slug>", "note": "<可选>"
  },
  "interactions": [
    {
      "request":  {"method": "POST", "scheme": "https", "host": "<host>",
                   "path": "/v1/chat/completions",
                   "headers": {"<allowlist 后>": "..."},
                   "body_summary": {"model": "...", "stream": true,
                                     "message_roles": ["system", "user"],
                                     "tool_names": ["..."],
                                     "body_sha256": "<hex>"}},
      "response": {"status_code": 200, "headers": {"content-type": "..."},
                   "body_text": "<解码后完整 body，SSE framing 逐字符保真>"}
    }
  ]
}
```

- **request 不落完整 body**（Codex spec review H1）：录制管线记录的是 ProviderClient
  实际发出的 body，天然含 instructions / history / tool schema——即使本次场景全合成，
  结构上不该给「重录时换了输入就把宿主内容落盘」留缝。只存**结构摘要**
  `body_summary`（model / stream / message_roles / tool_names / body_sha256），
  由 recorder 从 parsed body **结构化构造**（不走文本正则，无 JSON-safe 灰故障面，
  Codex L1）；sha256 供「重录后请求是否漂移」的人工参考。
- **URL 拆存 scheme/host/path，永久不持久化 query string**（Codex L1）：当前三
  transport URL 构造无 query；recorder 断言 query 为空，非空即 raise（人裁）。
- `response.body_text` 存**内容解码后**（gzip/br 解开）的完整文本；`content-encoding` /
  `content-length` / `transfer-encoding` 头剥除——否则回放时 httpx 会二次解压失败
  （pydantic-ai serializer 同款处理）。
- **仅 2xx 响应可落盘**（Codex H1）：非 2xx（provider 错误可能回显请求内容/身份信息）
  recorder 直接 raise 拒绝——本 Feature 只钉 happy-path 真样本。
- `body_text` 单字符串保 SSE framing 逐字符保真（`\r\n` vs `\n`、未转义 U+2028 原样
  保留——这正是真样本的价值）。落盘 `json.dumps(ensure_ascii=True)`：U+2028 存成
  `\u2028` 转义、load 后还原为原字符，保真不受影响且文件 ASCII 安全、diff 可读。

### D3 secret 过滤（比 pydantic-ai 更严：请求头 allowlist + 已知凭证禁串扫描）

落盘管线六道，顺序固定：

1. **drop token 端点交互**：URL path 含 `/token` / `/oauth` 或 host 以 `auth.` 开头 →
   整条交互丢弃（防御深度；正常构造下 token 交换根本不经注入 client）。
2. **请求头 allowlist**（denylist 会漏新 auth 头，allowlist 不会）：仅保留
   `{content-type, accept, accept-encoding, connection, host, content-length,
   openai-beta, anthropic-version, originator}`；`authorization` / `x-api-key` /
   `cookie` / `chatgpt-account-id`（OAuth 动态身份头）等一律不落盘。
3. **响应头 allowlist**：仅 `content-type`（回放唯一需要）。
4. **body 文本洗刷**：request/response 文本统一过 `octoagent.core.log_redaction.
   redact_sensitive_text`（规则源复用：sk- / Bearer / JWT / ENV 赋值 / JSON
   字段 / Telegram / 连接串）。
5. **落盘前机械断言（fail-closed）**：序列化全文扫描——
   a) 模式类：`sk-[A-Za-z0-9_-]{8,}` / JWT 三段式 `eyJ*.*.*` +
      无歧义身份键洗刷不变量（`safety_identifier`/`prompt_cache_key` 若以 string
      值出现必须已是 `[scrubbed]`）；
   b) **已知凭证逐字匹配（raw 层硬 stop）**：录制器登记当次现役凭证
      （`ResolvedAuth.bearer_token` + 身份类 header 值 + 相关 env 值）为禁串；
      **record() 在 redact 之前对 raw 响应 body 逐字比对**，命中即硬 raise
      （Opus final LOW-1：redact 会把 shaped 凭证掩成 6+4 形态，dump 时扫描拿
      不到全串——「已知凭证出现在响应体」是高危回显信号，宁可不录）；dump 时
      对序列化全文再比对一次作为最终后网（防绕过 record 直接拼交互）。
   这是 vcrpy 做不到的一层：录制进程内拿得到真凭证明文，逐字匹配比模式匹配更硬。
   注：良性协议头（OpenAI-Beta/originator 等，已在请求头 allowlist）的值不登记
   禁串——其值本就允许落盘，blanket 登记会假阳性（真录实测）；短值（<8 字符）
   不做子串禁串（误伤面大，allowlist 兜底）。
6. **事务式原子落盘**（Codex spec review H2）：序列化 + 第 5 道扫描全部通过后，先写
   同目录临时文件再 `os.replace` 原子提交到目标路径——任何一道失败都**不产生**目标
   文件，半成品 cassette 结构性不可能存在。

**录制期 console 暴露面归档**（Codex H2 剩余半条）：录制脚本跑真调用时，
provider_client 既有 error 日志（`body=error_body[:500]`）可能把 4xx 回显打到操作者
终端——该暴露面与日常跑 `octo` 生产进程完全同面（stdout 落盘侧已有 F129
log_redaction），且录制是人监督一次性动作、console 输出不被脚本持久化；叠加第 6 道
「非 2xx 拒绝落盘」，错误回显不可能进 cassette。归档为接受现状，脚本 docstring 提醒
操作者勿把录制终端输出粘贴外发。

**三重验证**：①专项单测——假 token（各形状）经全管线录到 tmp_path，读文件 grep 零命中
+ fail-closed 分支触发验证（含「失败不留半成品文件」断言）；②committed cassette 扫描
测试——遍历仓内 cassettes/*.json 全文断言零 secret 形状（永久回归，CI 每次跑）；
③合入前人眼 + 命令行 grep 双查（流程门）。

### D4 回放机制

- `ReplayTransport(httpx.AsyncBaseTransport)`：从 cassette **顺序** pop 交互；逐请求断言
  method + host + path 一致（松 matcher），mismatch → 带 expected/actual 的 AssertionError；
  交互耗尽后再有请求 → 显式报错。
- 回放响应：`httpx.Response(status_code, headers=stored, content=body_text.encode())`——
  `aiter_lines()` 在 buffered content 上照常走 LineDecoder + 三 transport 解析循环。
  诚实定位（Codex spec review M2）：这是「真实响应文本快照回放」，验证**事件形状/字段/
  finish 语义**维度；chunk 边界/增量重组维度不在此（F142 受控字节切片已钉，比真实
  分块更对抗）。不采用「录 chunk 边界 + AsyncByteStream 回放」：SSE 响应常带
  content-encoding 压缩，解码后文本的 chunk 边界不是忠实 wire 产物，复刻它是伪保真。
- 回放测试自建 `ProviderRuntime`（api_base 与录制一致以对齐 URL 构造）+ 假 resolver
  （`bearer_token="replay-token"`），**hermetic：不读宿主 ~/.octoagent、不要求任何 env**。
- F137 gate：回放测试按 F142 先例以
  `pytestmark = pytest.mark.usefixtures("allow_model_requests_for_dispatch_tests")`
  按文件放行（覆盖对象=dispatch 机器本身，零真网络）。
- 断言分两层：结构不变量（content 非空 / metadata.transport/provider 正确）+ **精确钉**
  （cassette 冻结后 tool_calls 名字+参数 dict、token_usage 精确值、model_name 可精确
  断言——录完从 cassette 读出写死）。

### D5 完整消费护栏

`tests/wire_replay/conftest.py`（子目录级，不污染 provider 包其它测试）：

- `pytest_runtest_makereport` hookwrapper 挂 `rep_setup` / `rep_call`（pydantic-ai 范式）；
- `wire_cassette` fixture（loader factory）登记本测试加载的 cassette；
- autouse fixture teardown：测试**通过**（`rep_call.passed`）且任一已登记 cassette
  存在未播放交互 → `pytest.fail`（列出未播放 index）；测试本身失败/跳过则不叠加，
  不遮蔽原始失败。
- 判定核心抽纯函数（`unplayed_indexes(cassette)`）单测直接覆盖（护栏自证不依赖
  pytester）。

### D6 目录布局

```
octoagent/packages/provider/tests/wire_replay/
├── __init__.py
├── conftest.py               # 消费护栏 + cassette loader fixture
├── _wire_recorder.py         # cassette 模型 / serializer(secret 过滤) / Recording+ReplayTransport
├── record_cassettes.py       # 录制脚本（显式跑；含重录文档 docstring）
├── scenarios.py              # 录制/回放共享的场景输入（单一事实源）
├── test_wire_serializer_secrets.py   # D3 机械断言（假 token 全管线）
├── test_wire_replay_openai_chat.py
├── test_wire_replay_openai_responses.py
├── test_wire_replay_anthropic.py
├── test_wire_replay_guards.py        # 消费护栏 + matcher 自证
├── test_cassette_secret_scan.py      # committed cassette 永久扫描
└── cassettes/
    ├── openai_chat_*.json
    ├── openai_responses_*.json
    └── anthropic_messages_*.json     # meta.source=handwritten-golden
```

`_wire_recorder.py` 放 tests 树内（test-only 基建，不进发布面）：录制/回放是测试资产，
不是产品能力（对照 F138 keystone 把 QueueModelClient 上提 skills.testing 是因为要跨包
消费；本件消费方只有本目录 + 录制脚本）。

### D7 录制纪律与成本

- **gate opt-in**：录制脚本要求 `OCTOAGENT_ALLOW_MODEL_REQUESTS=1` 显式设置（F137 通道
  ③），未设置直接退出并打印说明；脚本本身不 import 任何 pytest 布线。
- **调用预算**：SiliconFlow ≤ 8 次（simple / tool_call / U+2028 探针 / embed + 调试
  余量）——V3.2 输入 $0.14/M 输出 $0.28/M，总成本 < $0.01；codex OAuth ≤ 4 次（simple /
  tool_call + 余量），订阅额度一次性，**禁循环重录**；anthropic 0 次。
- **重录路径文档化**（仿 pydantic-ai `make update-vcr-tests`）：`record_cassettes.py`
  docstring + testing-strategy.md 落一行——重录 = 显式跑脚本 + 更新回放测试精确断言 +
  重跑 secret 扫描 + 人眼 review diff。
- 录完 cassette **立即**跑 secret 断言测试 + 人工 grep，然后才 commit。

## 5. U+2028 评估判据（范围件 4）

已确认的事实链（F142 + 本次侦察）：httpx 0.28.1 `LineDecoder` 按 `str.splitlines` 全集
切行（`_decoders.py` `NEWLINE_CHARS` 显式含 `  \x85`）；未转义 U+2028 是合法
JSON（`json.dumps(ensure_ascii=False)` 原样输出）；SSE 规范行分隔仅 CR/LF/CRLF。
F142 已把「data 行内未转义 U+2028 → 该 delta 静默丢、流继续」钉成 documented behavior，
修复候选（弃 aiter_lines 自管 SSE framing）归档给本 Feature 评估。

**证据实验**（录制阶段顺手，≤ 2 次真调用）：
1. 观察真 cassette：SiliconFlow SSE 是否 `ensure_ascii=False`（CJK 是否原样字节）；
2. U+2028 探针：请 DeepSeek 原样复读含 U+2028 的字符串，观察 wire 上该字符是否以
   **未转义原始字符**出现在 data 行内。

**决策表**：

| 证据 | 结论 | 动作 |
|------|------|------|
| wire 上出现未转义 U+2028（探针命中） | 真生产 bug 实锤（真 provider + 真模型输出可触发 delta 静默丢失） | 实施最小修：`_iter_sse_lines()` 模块级 helper（SSE 规范切行，仅 `\r\n`/`\n`/`\r`，含跨 chunk trailing-CR 处理），三 transport 的 `resp.aiter_lines()` 换成它（~25 行 + 3 处调用点）；F142 钉住测试翻转断言（其 docstring 预告的「已修复」分支）；真样本 cassette 钉修复后行为 |
| CJK 原样（ensure_ascii=False 实锤）但探针字符未 round-trip | 条件性风险：serializer 面实锤，但 **U+2028 本身是否会被 provider 单独转义未证实**（不少实现对 line-separator 特判转义），且模型 emit 概率未量化 | **归档不动生产**（Codex spec review M3：无 wire 实锤不改三条生产热路径——与 F142 原判「自管 framing 非极小改动」保持一致）；结论写明证据链与残余风险，修复候选保持归档状态（如需前摄 hardening 另立 Feature 人裁） |
| CJK 也被转义（SiliconFlow ensure_ascii=True） | 当前配置的 provider 集合无触发面 | 归档结论不动生产；F142 钉住测试保持现状断言 |

**收紧后的唯一动生产条件**（Codex M3）：wire 上抓到**未转义 U+2028 原始字节**出现在
SSE data 行内（探针命中，cassette 即证据）。其余一律归档。

修复若实施，属「生产改动」显式报告；行为面 = 仅 SSE 行切分语义从 splitlines 全集收窄到
SSE 规范集，其余逐字节等价（F142 边界族 16 用例 + 既有 23 wire 用例 + 本 cassette 三重
回归网）。

## 6. FR / AC（含 test 绑定）

| ID | 要求 | Test 绑定 |
|----|------|-----------|
| FR-1 | cassette 落盘唯一入口内建 secret 过滤五道管线（D3），fail-closed | `test_wire_serializer_secrets.py` |
| FR-2 | 含假 token（bearer/api-key/JWT/账户头）的交互经全管线落盘后文件全文零命中 | `test_wire_serializer_secrets.py::test_planted_secrets_never_reach_disk` |
| FR-3 | 已知凭证禁串命中时拒绝落盘（raise，不产出文件） | `test_wire_serializer_secrets.py::test_fail_closed_on_residual_secret` |
| FR-4 | 三 transport 回放测试默认跑：无凭证 env、无网络、无宿主 ~/.octoagent 依赖，全绿 | `test_wire_replay_openai_chat.py` / `..._openai_responses.py` / `..._anthropic.py` |
| FR-5 | 回放穿透真实解析栈：content / tool_calls（名+参数）/ token_usage / metadata 精确断言 | 同上三文件 |
| FR-6 | matcher 松：method/host/path + 顺序；mismatch 显式报错 | `test_wire_replay_guards.py` |
| FR-7 | 完整消费护栏：测试通过但 cassette 有未播放交互 → FAIL；测试失败时不遮蔽 | `test_wire_replay_guards.py` + `conftest.py`（判定核心纯函数单测） |
| FR-8 | committed cassettes 永久 secret 扫描（模式类全集） | `test_cassette_secret_scan.py` |
| FR-9 | 录制脚本 gate opt-in（env 通道③），未开闸退出；重录路径文档化 | `record_cassettes.py` docstring + 人工验证（脚本非测试） |
| FR-10 | anthropic golden 显式标注 `handwritten-golden` 且回放测试注明非真 wire | `test_wire_replay_anthropic.py` docstring + cassette meta |
| FR-11 | U+2028 评估按 §5 决策表闭环（唯一动生产条件=探针抓到未转义原始字节），结论归档；若修，F142 钉住测试同步翻转 + cassette 钉修复后行为 | `test_provider_client_wire_boundaries.py`（翻转或维持）+ completion-report |
| FR-12 | 事务式原子落盘：扫描失败不产生目标文件（无半成品）；非 2xx 响应拒绝落盘 | `test_wire_serializer_secrets.py`（fail-closed 无残留断言 + 非 2xx 拒绝用例） |
| FR-13 | request 仅存结构摘要（无完整 body）；URL 无 query 断言（非空 raise） | `test_wire_serializer_secrets.py` + `test_cassette_secret_scan.py`（committed cassette 无 `body_json`/query 字段） |

**AC（验收门）**：
- AC-1 回放套件在 `env -i`（或等价 unset 全凭证 env）下全绿 —— FR-4 绑定文件。
- AC-2 cassette 文件 `grep -RE "sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]+\."` 零命中
  + FR-8 测试常绿。
- AC-3 全量回归 0 regression vs master d22378b8 baseline + e2e_smoke 8/8。
- AC-4 双评审（Codex final + Opus 自审）0 HIGH 残留。
- AC-5 pyproject / 生产代码 diff 面 = 0（若 U+2028 修实施，生产 diff 仅
  provider_client.py 的 SSE 行切分 helper，显式报告）。

## 7. 风险

| 风险 | 缓解 |
|------|------|
| 真录响应含个人身份信息（codex 后端可能回显账户相关字段） | 录制场景 prompt 全部中性合成内容；request 不落完整 body（仅结构摘要）；非 2xx 拒绝落盘；response body 过 redact + 禁串扫描；落盘前人眼 review 全文 |
| cassette 随 provider 演进腐化（真实 API 改版后样本过时） | 本来就是快照定位：钉「解析栈能处理**已见过的**真实形状」，不承诺追新；重录路径文档化，腐化成本=跑一次脚本 |
| anthropic golden 非真 wire，可能与真实 API 有出入 | 显式标注 + 归档；它钉的是解析器对文档形状的正确性（与既有 fake 测试同级），真样本待未来有凭证时重录替换 |
| 自研 recorder 自身有 bug（如解码/headers 处理错） | recorder 只在录制脚本用（人监督下跑）；replay 路径被回放测试本身验证（能解析出合理结构=端到端自证）；护栏/serializer 各有独立单测 |
| U+2028 修改动 SSE 热路径引入回归 | 决策表约束「极小修」预算；F142 16 边界用例 + 既有 23 wire 用例 + 新 cassette 回放三重网先行在位 |
