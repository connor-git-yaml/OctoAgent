# F156 产品调研

## 用户目标

iPhone 是随手查看和决定的 Companion，不是缩小版管理后台。最重要的动作是：

- 继续对话；
- 看任务是否在运行、是否需要处理；
- 对审批和 Memory candidate 作人工决定；
- 了解连接/通知/隐私状态；
- 在已获批准的 Health/Calendar slice 中主动发起一次分析。

## 四区信息架构

### 对话

- 最近会话；
- 当前会话消息；
- composer；
- sending/streaming/error/offline；
- 技术 event 仅 Advanced。

### 任务

- running/waiting/completed/failed；
- progress、用户语言事件摘要、工件；
- 只显示当前状态允许且后端已证明的控制动作。

### 收件箱

- approvals；
- Memory candidates；
- 明确来源、影响、过期/conflict；
- 不自动决定。

### 设置

- device/connection/revoke；
- notifications；
- privacy/deletion；
- Health；
- optional Calendar；
- Advanced diagnostics。

## 视觉

沿用用户指定的 Claude Design 最早期方案：

- 黑/蓝黑层次；
- 绿色主强调；
- 细边框、低亮度卡片；
- 大标题与克制正文；
- 有留白但不做空洞模板；
- 视觉状态不能只靠颜色。

后来出现的通用亮色卡片、稀疏占满一屏、圆角堆叠、无层级表单或为了代码复用而
搬来的 Web 三栏不进入 active design。
