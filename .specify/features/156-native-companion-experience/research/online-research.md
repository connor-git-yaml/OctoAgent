# F156 Apple 官方在线调研

- 日期：2026-07-29
- 来源：Apple 官方一手资料

## 证据点

1. [Designing for iOS](https://developer.apple.com/design/human-interface-guidelines/designing-for-ios/)
   iPhone 需要聚焦主要任务、减少同屏控制、适应 Dark Mode/Dynamic Type，并把常用
   操作放在舒适可达区域；这支持保留 Claude early 视觉语言但不复制桌面三栏。
2. [NavigationStack](https://developer.apple.com/documentation/swiftui/navigationstack)
   原生 stack 提供系统 back/swipe，并可用 typed path 管理 destination。
3. [Tab bars](https://developer.apple.com/design/human-interface-guidelines/tab-bars)
   tab bar 用于顶层导航而不是动作，并在切换 section 时保留各自 navigation state。
4. [EnvironmentValues](https://developer.apple.com/documentation/swiftui/environmentvalues)
   SwiftUI 原生暴露 Dynamic Type/locale/scenePhase/VoiceOver/Reduce Motion 等环境事实。
5. [accessibilityReduceMotion](https://developer.apple.com/documentation/swiftui/environmentvalues/accessibilityreducemotion)
   Reduce Motion 开启时应避免大幅或模拟三维的动画。
6. [Asking permission to use notifications](https://developer.apple.com/documentation/usernotifications/asking-permission-to-use-notifications)
   通知是打扰性能力，应在上下文中请求；provisional authorization 可安静试用，但仍
   需要真实产品决定。
7. [Registering your app with APNs](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns)
   device token 由 App 从系统取得后发送给 provider server；token 会变化，不能当设备
   身份或永久 secret。
8. [Setting up a remote notification server](https://developer.apple.com/documentation/usernotifications/setting-up-a-remote-notification-server)
   APNs 需要 App、provider server、Apple 服务三方；delivery 可能延迟、离线存储或
   合并，不能当可靠任务队列。
9. [Using background tasks to update your app](https://developer.apple.com/documentation/uikit/using-background-tasks-to-update-your-app)
   系统决定 background task 何时运行；必须处理取消/过期并报告完成，不能承诺固定
   轮询周期或常驻运行。
10. [SceneStorage](https://developer.apple.com/documentation/swiftui/scenestorage)
    只适合轻量 per-scene 状态，系统不保证保存时机，且明确不应保存敏感数据。
