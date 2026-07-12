# F139 spec 阶段 Codex 评审闭环记录

## 轮次 1：`codex review --base origin/master`（默认指令，gpt-5.4 high）

结论：docs-only diff，0 finding。（默认 review 指令面向代码缺陷，对 spec 文档挖掘浅
——遂补轮次 2 定向对抗。）

注：CLI 配置模型 `gpt-5.6-sol` 需更新版 Codex CLI，本轮起统一 `-c model=gpt-5.4
-c model_reasoning_effort=high` 覆盖。

## 轮次 2：`codex exec`（对抗评审 prompt，读 spec/plan + 对照 provider_client /
F142 边界测试 / log_redaction 实码）

产出 2 HIGH + 3 MEDIUM + 1 LOW，全部实质，处理如下：

| # | Sev | Finding 摘要 | 处理 |
|---|-----|--------------|------|
| H1 | high | `request.body_json` 落盘偏乐观：body 天然含 instructions/history/tool schema，重录换输入即宿主内容落盘；4xx 回显同样被保留 | **接受**：D2 改「request 仅存结构摘要 body_summary（结构化构造）」+「非 2xx 拒绝落盘」；新增 FR-12/FR-13 |
| H2 | high | 录制事务性未闭环：半成品 cassette 可能落盘；provider_client 错误日志可能先把回显打到 console | **接受主体**：D3 加第 6 道「temp + 扫描通过 + os.replace 原子提交」（FR-12）；console 暴露面**归档接受**——与日常生产进程同面、人监督一次性、不被持久化，叠加非 2xx 拒绝后错误回显不可能进 cassette（归档理由写入 D3） |
| M1 | medium | D1 低估 transport 语义坑（压缩/流重建/早停/头处理） | **接受**：D1 理由收窄为「零依赖 + 无全局 monkeypatch 并行隔离」两条；自研成本显式列自证矩阵（gzip 全链路 / 头归一 / 非 2xx / query 断言 / 早停消费语义） |
| M2 | medium | buffered 回放的「解析栈全路径穿透」表述过强，chunk 边界维度被消掉 | **接受措辞**：§1/D4 改「真实响应文本快照回放」，chunk 维度显式归 F142；**拒绝**「录 chunk 边界 + AsyncByteStream 回放」备选——SSE 常带 content-encoding，解码后文本 chunk 边界非忠实 wire 产物，复刻是伪保真（理由入 D4） |
| M3 | medium | §5 决策表第二行判据过松：无 wire 实锤即改三条生产热路径，与 F142「非极小改动」原判冲突 | **接受**：收紧为唯一动生产条件=探针抓到未转义 U+2028 原始字节；其余分支一律归档（决策表已改，FR-11 同步） |
| L1 | low | URL 完整持久化留 query 泄漏缝；文本正则洗 JSON 有灰故障面 | **接受**：URL 拆存 scheme/host/path + query 非空 raise；body_summary 结构化构造不走文本正则（D2/FR-13） |

## 收敛状态

0 HIGH / 0 MEDIUM 残留（全部接受修订或带理由归档）；1 处显式拒绝（M2 的
AsyncByteStream 备选方案）带理由记录于 D4。
