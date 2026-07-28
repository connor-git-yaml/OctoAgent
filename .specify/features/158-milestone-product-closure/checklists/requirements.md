# F158 Requirements Checklist

## Gate

- [x] 权威 master 与干净工作树已确认
- [x] 初始 requirement-evidence-gap-owner 审计已建立
- [x] Claude Design 早期 frame 已真实渲染并形成可测基线
- [x] 当前 Web 已真实启动并逐场景采集
- [x] Web/iOS 测试矩阵完成
- [x] `GATE_DESIGN=true`
- [x] `GATE_TASKS=true`

## Web

- [x] F150 remote-access Settings 入口用户可达
- [x] Web 功能场景全部通过
- [x] Web 异常状态全部通过
- [x] desktop 视觉 diff 全部通过
- [x] 390px Web 窄窗口视觉/a11y 通过
- [x] 当前实现与早期 Claude Design 的偏离全部有合法理由

## iOS

- [x] 原生 SwiftUI 工程存在且 iPhoneOS App/XCTest target 可编译
- [ ] 原生 SwiftUI App 已在 Simulator 或真机真实启动
- [x] F152 隐私/身份 Research/Design/Tasks Gate 与 architecture authority 通过
- [ ] F153 真机 device trust/transport 通过
- [ ] F154 HealthKit 通过
- [ ] F155 EventKit 决策门通过或明确移出范围
- [ ] F156 Native Companion 场景通过
- [ ] 模拟器视觉回归通过
- [ ] 真机专属场景通过

## 设计与交付

- [x] Claude Design 后期不佳方案已删除或改回
- [x] 最终设计导出、frame/superseded manifest 已回存
- [x] F150 verification report 完成
- [ ] Blueprint/Milestone/Feature 状态无漂移
- [ ] 干净检出、CI、个人部署和 completion audit 全部通过
