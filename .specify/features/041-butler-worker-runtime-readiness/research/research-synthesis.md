# Research Synthesis: Feature 041 Butler / Worker Runtime Readiness + Ambient Context

## 结论

OctoAgent 现在缺的不是“有没有网络工具”，而是“这些能力有没有进入默认运行面”。因此 041 的最小正确做法是同时补三层：

1. **Ambient context**  
   系统默认提供当前本地时间、日期、timezone/locale。

2. **Delegation cognition**  
   Butler 明白“实时/最新/外部世界”问题应该优先变成受治理 worker 执行，而不是直接宣称没有实时能力。

3. **Worker readiness**  
   child worker 明白自己当前在哪个 project/workspace、有什么可用工具面、当前 runtime 是否 degraded。

## 为什么要单独成 Feature

- 033 解决的是 context continuity，不是 ambient current time / freshness query。
- 039 解决的是 supervisor / worker 治理，不是 Butler 对“外部事实问题”的默认反应方式。
- 040 解决的是 workbench acceptance，不是“真实世界查询”这条运行主链。

所以 041 不是重复已有 feature，而是把这些 feature 的接缝补成日常用户能直接感知的 ready state。
