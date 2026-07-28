# F154 Research Synthesis

## 结论

F154 可进入 Design/Tasks Gate，但 production 继续受 F153 `GATE_VERIFY=true` 硬门
约束。v0.1 采用最小、一次性、用户可见的 Health vertical slice：

```text
用户动作
  -> Apple 健康按类型读取授权
  -> 步数/睡眠一次性查询
  -> 本地最小聚合
  -> 用户预览
  -> 当次批准
  -> F153 device proof
  -> F152 review/approved packet
  -> 单次 Agent 分析
  -> 可删除结果
  -> 可选 Memory candidate 二次确认
```

## 决策

1. v0.1 exact types：`stepCount`、`sleepAnalysis`。
2. 默认 24 小时，允许 3 天/7 天，最大 7 天。
3. raw sample 不落盘、不上传；只上传聚合事实。
4. 权限请求只由明确用户动作触发。
5. 空查询统一表示“没有可读数据或权限受限”。
6. 不写 Apple 健康、不后台同步、不自动分析、不自动写 Memory。
7. F154 slice UI 采用 Claude Design 早期视觉语言和 SwiftUI 原生交互；F156 仍拥有
   最终 companion shell。
8. F153 mobile live 与真机 Gate 未通过前，只允许 artifacts/test contract，不允许
   F154 production 文件或 entitlement 进入工程。

## 证据

- Apple 官方在线证据见 `research/online-research.md`。
- 产品边界见 `research/product-research.md`。
- 技术/测试边界见 `research/tech-research.md`。
