# Acceptance Matrix - Feature 054

## 1. 配置与默认层

| 场景 | 前置条件 | 操作 | 预期 |
| --- | --- | --- | --- |
| 只配 `main` alias | 新实例，无 retrieval aliases | 进入 Chat 并产生对话 | Memory 可生成基础记忆；Settings 表示当前在默认内建 retrieval 层 |
| 未配 `memory_rerank` | 已配 Providers 和 `main` | 保存 Memory 配置 | 保存成功；界面说明回退 heuristic rerank |
| 未配 `memory_embedding` | 已配 `main` | 触发 recall | 使用内建 embedding；若内建层不可用，则显式降级 lexical |

## 2. embedding 迁移

| 场景 | 前置条件 | 操作 | 预期 |
| --- | --- | --- | --- |
| 新建 target generation | active generation 已服务 | 切换 embedding alias | 创建 build job；旧 generation 仍 active |
| 迁移中继续查询 | target generation 在 `embedding/catching_up` | 发起 Chat / Memory 搜索 | 查询继续基于旧 generation，结果不断流 |
| cancel | target generation 未 cutover | 点击 `cancel` | 新 generation 标记 cancelled；旧 generation 保持 active |
| cutover | target generation `ready_to_cutover` | 执行切换 | active generation 原子切到新版本 |
| rollback | 已 cutover，仍在保留窗口 | 执行 rollback | 旧 generation 恢复 active；新 generation 标记 rolled_back |

## 3. 治理边界

| 场景 | 前置条件 | 操作 | 预期 |
| --- | --- | --- | --- |
| fact candidates 生成 | 引擎可用 | 触发 facts 加工 | 产生 candidates + evidence；不直接写 current facts |
| vault candidates 生成 | 引擎可用 | 触发 Vault 加工 | 产生候选；无审批/授权时不进入受保护层 |
| 引擎失败 | MemU 风格引擎不可用 | 继续 ingest / recall | 主链保留 fallback；治理链不损坏 |

## 4. UI 表达

| 场景 | 前置条件 | 操作 | 预期 |
| --- | --- | --- | --- |
| Settings 主路径 | 普通用户打开 `Settings > Memory` | 查看配置 | 不出现 `bridge_url / bridge_command / bridge_transport` |
| progress card | migration 进行中 | 打开 Settings / Memory / Work | 可见阶段、进度、ETA、最近错误 |
| active profile 删除 | active embedding profile 正在服务 | 尝试删除 alias/provider | 系统拒绝或先要求切换 |

## 5. 共享 retrieval platform

| 场景 | 前置条件 | 操作 | 预期 |
| --- | --- | --- | --- |
| 知识库复用平台默认 embedding | 平台已有 active profile | 新建知识库 corpus | 直接复用现有 profile/generation contract |
| 平台默认 embedding 切换 | Memory 与知识库同时存在 | 切换平台默认 embedding | 两边各自创建 build job，各自 cutover，不共享错误状态 |
