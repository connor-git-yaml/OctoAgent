# Research Synthesis - Feature 049

## 综合判断

你提出的方向是对的：

- 不应该继续做“天气有一套、排期有一套、推荐再来一套”的行为 patch
- 更合理的是把 Butler 默认行为收口为通用 clarification-first 体系

## 推荐方案

### 1. 用行为文件替代散落 prompt patch

对外是 7 个 markdown 文件。  
对内是分层装配的 behavior runtime。

### 2. 用通用 clarification decision 替代单案特判

核心问题不是“天气缺城市”，而是“用户请求缺关键上下文”。  
天气只是其中一个实例。

### 3. 用 proposal-governance 替代 silent self-edit

Agent 可以帮助用户进化人格和行为，但不应该绕过治理直接重写核心行为文件。

### 4. 用 behavior slice 替代全量继承

Worker 只拿到任务相关的行为切片，避免把 Butler 的私有习惯和用户私有偏好全部传出去。
