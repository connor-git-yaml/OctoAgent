# F149 TDD Evidence Audit

## 结论

**PASS WITH DISCLOSED EVIDENCE LIMITATIONS**。32 个 behavior task 均有 RED/GREEN/REFACTOR machine record，96 个阶段记录的 exit shape 全部为 `1/0/0`，所有实际 Python evidence command 均使用 post-F151 canonical profile；但历史证据并非 32/32 都满足严格 test-first。以下限制不可被 completion report 改写或隐藏。

## 机械复算

| item | result |
|---|---:|
| behavior tasks | 32 |
| RED/GREEN/REFACTOR YAML records | 96 |
| exact exit shape | 32× RED=1；32× GREEN=0；32× REFACTOR=0 |
| normal test-first tasks | 27 |
| late RED tasks | T051、T052、T053、T054 |
| partial RED task | T044 |
| raw phase outputs present | 94/96 |
| missing raw phase outputs | T041 GREEN、T044 RED |
| Python evidence commands | 15 |
| bad post-F151 Python profiles | 0 |
| planned atomic relocation | 0 |

T015 GREEN 按计划包含两个原始输出，两个文件都存在；不能因为 phase 数为一而漏计第二份输出。

## 命令纪律

15 条实际 Python evidence command 均满足：

- `PYTHONNOUSERSITE=1`
- `PYTHONPATH` 只含 `core/provider/protocol/tooling/skills/policy/memory` 七包与 Gateway
- retired SDK path 为 0
- `uv run --project . --no-sync python -m pytest`
- 无 retry、固定 sleep、宿主 credential、网络或真 LLM 依赖

前端 Vitest/Playwright 证据均使用对应 task 的 exact selector；formal evidence 没有通过 broad suite 代替窄 oracle。

## 限制与处置

### T044 partial RED

T044 首次 exact RED 中有 7 个目标合同稳定见红，但 4 个 wizard 行为被 fake-timer timeout 污染，且原始 RED log 没有保留。因此它证明目标能力当时缺失，但不能声称为完整、干净的 test-first RED。

后续 GREEN/REFACTOR 使用同一合同并全绿；最终页面、generated type、全前端、build、OpenAPI、boundary/style/complexity 与 coverage 都通过。该历史限制不通过重跑“制造过去”来修饰。

### T051–T054 late RED

四项 RED 均为真实、稳定、同 oracle 的行为失败，但对应页面 GREEN 已先于合同建立：

- T051：十页 390px Web 窄窗口语义/a11y。
- T052：Task→Detail 浏览器旅程与普通区内部 ID。
- T053：Skills 安装弹层初始焦点与焦点归还。
- T054：F150 global 401 与 F149 origin 403 owner 边界。

这些证据只能称为 late RED→GREEN→REFACTOR，不能倒签为 pre-implementation TDD。

### 原始输出缺口

- T041 GREEN：machine record 在，原始输出未保留。
- T044 RED：machine record 在，原始输出未保留。

两项均进入长期 evidence limitation；后续 Feature 必须继续保留三阶段原始输出，不能把 YAML 摘要当作原始日志。

## Relocation

实现没有出现纯原子搬迁 task，`planned atomic relocation=0`。因此不触发 before/after/absence/import/diff 五证；也没有把行为改变伪装成搬迁。

## Review 判定

本 Feature 的产品和架构门可以通过，因为所有最终合同均有可执行 GREEN/REFACTOR 与全量回归支撑，且历史限制被逐项披露。流程质量不记满分：27/32 为正常 test-first，T044 为 partial，T051–T054 为 late RED。该判定不授权未来 Feature 复用这些例外。
