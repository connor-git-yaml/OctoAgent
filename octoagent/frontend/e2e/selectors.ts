/**
 * F140 L1 data-testid 选择器契约——单一事实源。
 *
 * 规则（spec D6）：
 * - Playwright specs 只经本清单引用锚点，禁止散落字面量；
 * - 每个锚点必须在 src/**.tsx 源码中字面出现（`data-testid="<value>"`），
 *   由 vitest 契约测试 `frontend/testing/l1SelectorsContract.test.ts` 机械校验
 *   ——组件重构删锚点时 vitest 先红，不等 Playwright 在 CI 才炸；
 * - 新增场景需要新锚点：先加组件属性，再登记此处，两侧同一 commit。
 */
export const L1_TESTIDS = {
  /** 聊天输入 textarea（空会话/常规两处表单同名，同时只渲染一个） */
  chatInput: "chat-input",
  /** 聊天发送按钮 */
  chatSend: "chat-send",
  /** assistant 侧消息气泡根节点（等待回复的稳定信号锚点） */
  chatMessageAssistant: "chat-message-assistant",
  /** 用户侧消息气泡根节点（MessageBubble 三元的另一臂；v0.1 spec 未消费，
   *  登记以保持「源码内 L1 锚点 ↔ 清单」双向完整——删除会留孤儿属性） */
  chatMessageUser: "chat-message-user",
  /** FrontDoorGate token 输入框 */
  frontdoorTokenInput: "frontdoor-token-input",
  /** FrontDoorGate 提交按钮（保存 Token 并重试） */
  frontdoorSubmit: "frontdoor-submit",
  /** FrontDoorGate「在此设备记住 token」勾选框 */
  frontdoorPersistCheckbox: "frontdoor-persist-checkbox",
  /** F145 审批中心：规则精简提议卡根节点（场景③ 可见性锚点） */
  approvalCompactCard: "approval-compact-card",
  /** F145 审批中心：规则精简卡「接受」按钮（场景③ 点击锚点） */
  approvalCompactAccept: "approval-compact-accept",
} as const;

export type L1TestId = (typeof L1_TESTIDS)[keyof typeof L1_TESTIDS];
