# F127 G-lite 真 LLM 验证结果（DeepSeek-V3.2）

**日期**: 2026-07-03
**脚本**: [run_glite.py](./run_glite.py)（独立验证脚本，不进 production 包）
**Model**: `deepseek-ai/DeepSeek-V3.2`（bench alias / provider=siliconflow / transport=openai_chat / API key，非订阅 OAuth）
**结论**: **G-lite PASS（n=6，6/6）**——管道真通 + 提议质量下限达标。

---

## 1. 目的与范围（用户拍板 G→G-lite）

F127 全部单测/e2e 用注入 fake LLM——确定性编排（窗口/propose/validate/候选/事件/审批/CONFLICT）已证，但"真 LLM 走一遍 prompt→响应→解析→合法提议"此前从未发生。G-lite 验证：

1. **管道真通**：真 LLM 响应能被 `parse_llm_json_array` + F127 组校验消化，产出合法 PENDING 候选 + PROPOSED 事件，C4 红线守住（源仍 CURRENT）。
2. **提议质量下限**：植入的明显冗余组（3 条黑咖啡偏好小变体）能被找到；提议只引用植入冗余组（不拉不相关事实、无幻觉 id）。

**范围外**：强 model 质量评估（AC-8 完整版——recall 改善量化 + 记忆域 OctoBench task）归 M7 统一强 model OctoBench 方案。

## 2. 方法

- 每轮独立临时 SQLite（core.db + memory.db），**不碰 ~/.octoagent 生产数据**；只借实例 provider 配置 + `SILICONFLOW_API_KEY`。
- 植入 5 条 PROFILE 事实：冗余组 3 条（"用户喜欢黑咖啡" / "Connor 早上习惯喝一杯黑咖啡" / "偏好：咖啡不加糖不加奶"）+ 不相关 2 条（工作目录 / 养猫）。
- `ConsolidationDiscoveryService` 注入真 `ProviderRouterMessageAdapter`（包一层 alias 重定向 cheap→bench + 响应录制）→ `discover_and_propose(window_days=7, max_facts=50)`。
- 硬断言（9 项/轮）：facts_reviewed=5 / 非 fallback / ≥1 提议 / 全 PENDING / merged_content 非空 / **存在纯冗余组候选（source_sor_ids ⊆ 植入冗余组）** / 无幻觉 id / **C4 全部源仍 CURRENT** / PROPOSED 事件数=候选数。
- temperature：openai_chat transport payload 不含 temperature 字段（provider 默认），与 OctoBench 跑法一致；跑 2 批 × 3 轮 = **n=6** 看稳定性。

## 3. 结果（n=6，6/6 PASS）

| 轮 | 提议数 | 合并组构成 | confidence | 污染 | 幻觉 id | LLM 延迟 |
|----|--------|-----------|-----------|------|---------|---------|
| B1-1 | 1 | 咖啡 3 条全组 | 0.95 | 无 | 无 | — |
| B1-2 | 1 | 咖啡 3 条全组 | 0.95 | 无 | 无 | — |
| B1-3 | 1 | 咖啡 3 条全组 | 0.90 | 无 | 无 | 21.2s |
| B2-1 | 2 | 咖啡 2+2 双候选（共享 1 源） | 0.80 / 0.90 | 无 | 无 | 15.6s |
| B2-2 | 1 | 咖啡 2 条（显式排除第 3 条） | 0.70 | 无 | 无 | 49.8s |
| B2-3 | 1 | 咖啡 3 条全组 | 0.95 | 无 | 无 | 20.2s |

聚合：≥1 合法提议 6/6；**不相关事实污染 0/6**；幻觉 id 0/6；fallback 0/6；C4 源零触碰 6/6；事件链对齐 6/6。

## 4. 原始提议样例（摘录）

**B1-1（典型全组合并，带 code fence）**：

```json
{"groups": [{
  "source_ids": ["01KWKMYZ1X96RVVPST4KE1HAVG", "01KWKMYZ1X96RVVPST4KE1HAVD", "01KWKMYZ1WRRYXJMVRKT0H91SS"],
  "merged_content": "用户偏好黑咖啡，不加糖不加奶，早上习惯喝一杯。",
  "subject_key": "pref.coffee",
  "rationale": "这三条事实都指向用户对咖啡的偏好，内容互补且语义一致，可合并为一条更完整的咖啡偏好事实。",
  "confidence": 0.95
}]}
```

**B2-2（保守拆分——LLM 显式排除第 3 条，rationale 摘录）**：

> "ID 01KWKN1NY6W14BRFREX6DA7CRH（咖啡不加糖不加奶）虽然相关，但它描述的是'添加物'的偏好，而不是'风格'或'习惯'，合并可能导致混淆，因此不纳入。"

与 prompt 的"宁缺毋滥，合并是破坏性操作要谨慎"指令一致——保守行为是设计预期。

## 5. 质量观察（不作硬断言）

1. **分组方差**：4/6 全组合并（3→1）；1/6 拆成两对（共享源）；1/6 只合 2 条并给出排除理由。全部合法，语义均正确；变体差异属 LLM 非确定性 + prompt 保守指令的正常范围。
2. **共享源双候选自然复现（B2-1）**：两候选共享同一条源事实——accept 其一后另一候选的源会 SUPERSEDED，正是 FR-C7 新鲜度验证 → CONFLICT 终态设计预判的场景。真 LLM 第一次跑就命中该模式，佐证 codex 复审 P2（共享源候选冲突）修复的必要性。
3. **单源伪组被服务层过滤（B2-2）**：LLM 对 2 条不相关事实各产 1 个单源"组"（confidence 0.0，rationale="没有可合并的"）——`MIN_GROUP_SOURCE_COUNT=2` 校验静默丢弃，未成候选、未污染审批队列。真 LLM 的输出怪癖被组校验兜住。
4. **无 code fence 裸 JSON（B2-3）**：该轮响应无 markdown fence，`parse_llm_json_array` 兜底路径正常解析。
5. **延迟**：单次 LLM 调用 15.6–49.8s（SiliconFlow DeepSeek-V3.2，输出含较长 rationale）。深夜 cron 后台场景完全可接受；无一次触发 provider 瞬态重试。

## 6. 结论与归档

- **AC-8 G-lite 部分 PASS**：管道真通（prompt→LLM→解析→合法提议→PENDING 候选→PROPOSED 事件→C4 守界）+ 质量下限（植入明显冗余 6/6 被找到，零污染零幻觉）。
- **AC-8 剩余部分 → M7**：巩固对 recall 质量的改善量化（accept 后 recall 返回单条权威事实）+ 记忆巩固域 OctoBench task + 强 model 评估，归 M7 统一强 model OctoBench 方案（用户拍板）。
- 复跑方式见脚本 docstring；每轮 < 1 分钟、成本可忽略（DeepSeek-V3.2 API）。
