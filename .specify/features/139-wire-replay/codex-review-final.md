# F139 双评审终审闭环记录

## Codex final（`codex review --base origin/master`，gpt-5.4 high，全实现 diff）

结论：0 HIGH + 3 P2，全部真缺陷（Codex 本地各自复现），全接受修复 + 各配钉住测试：

| # | Sev | Finding | 处理 |
|---|-----|---------|------|
| P2-1 | P2 | `_IDENTITY_VIOLATION_PATTERNS` 在最终序列化 cassette 上匹配转义 JSON，`{"user":"alice"}` / `{"instructions":"..."}` 等**正常模型输出**会被误判「未洗刷身份字段」拒绝落盘 | **接受**：违规检查收窄到无歧义身份键 `IDENTITY_VIOLATION_FIELDS = (safety_identifier, prompt_cache_key)`；`user`/`instructions` 通用键仅保留录制侧 scrub（保守面不变）。钉住测试 `test_scan_does_not_flag_generic_keys_in_model_output` |
| P2-2 | P2 | `RecordingTransport` 未转发 `aclose()`（基类 no-op）→ 录制脚本连接池悬空 | **接受**：转发 `aclose` 到 inner。钉住测试 `test_recording_transport_forwards_aclose` |
| P2-3 | P2 | query 拒录路径把完整 URL（含 query，可能带签名/token）拼进异常文本回显 console | **接受**：报错只给 `scheme://host/path?<redacted>`。钉住测试 `test_query_refusal_does_not_echo_query_value` |

## Opus 自审（general-purpose agent, model=opus，六挑战全实测取证）

事实底座：范围红线验证（src/pyproject/frontend/.github/.githooks/gateway 零文件
在 diff）；wire_replay 47 passed 结构性无 socket；provider 全包 1051 passed；
hermetic（清空凭证 env + 假 HOME）仍全绿；U+2028 程序化字节级验证（body_text
load 后真实 U+2028 字符 0 次、转义形态 1 次）；8 盘 cassette grep+人眼逐盘扫描
零泄漏。

结论：**0 HIGH / 0 MED / 5 LOW**，达到可合入标准。LOW 处置：

| # | Finding | 处理 |
|---|---------|------|
| LOW-1 | 禁串逐字比对跑在 redact **之后**——shaped 凭证（sk-/JWT）已被掩成 6+4，dump 扫描拿不到全串，「逐字比对更硬」名不副实（退化为与 F129 日志同级掩码；非泄漏路径，三层纵深仍保全串永不落盘） | **接受修复**：record() 在 redact 之前对 raw body 逐字比对，命中硬 raise（高危回显信号宁可不录）；dump 扫描保留为最终后网。测试重构 + 新增 3 用例（raw 硬闸 / shaped 回显硬停 / 绕过 record 的后网） |
| LOW-2 | 消费护栏失败分支（stale→pytest.fail）无 pytester 自动化，负向靠 Gate C 人工 tamper | **归档**：spec D5 显式权衡（判定核心纯函数已单测 + 正向接线已测 + tamper 实证留档 `1 passed, 1 error`）；护栏是二级网（一级=精确值断言） |
| LOW-3 | 身份违规 backstop 仅覆盖 2 个无歧义键，instructions/user 无机械兜底 | **归档**：Codex P2-1 的刻意设计（通用词误伤正常输出）；录制侧 scrub 四字段保持 + handwritten golden 人眼 review 兜底 |
| LOW-4 | `debug_locate_forbidden` ±70 字符上下文可能把相邻的**另一个**禁串明文回显 console | **接受修复**：上下文窗口对全部已登记禁串统一掩码后才输出 |
| LOW-5 | 身份洗刷是 codex 形态（4 固定字段），未来 provider 新字段名回显不覆盖（informational） | **归档**：header allowlist + redact + raw 禁串 + 人眼四层仍在；重录流程含人眼 review 步骤（脚本 docstring） |

## 收敛状态

Codex 3 P2 全修（各配钉住测试）+ Opus 2 LOW 修 / 3 LOW 带理由归档。
**0 HIGH / 0 MED 残留。** spec 阶段另有 2H+3M+1L 已在 codex-review-spec.md 闭环。
