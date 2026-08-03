# F155 Threat Model

## 资产

- 设备私钥与短期 token；
- calendar event title/time；
- full-access 授权状态；
- review/approved packet/result；
- deletion receipt 与 non-sensitive audit。

## 主要威胁与控制

| Threat | Control |
|---|---|
| 用“只读权限”误导用户接受 full access | 系统 sheet 前 exact 文案 + Gate snapshot |
| Agent 或未来代码写日历 | 无写 capability/route/protocol；AST/package adversarial gate |
| raw calendar bulk export | finite fields/window + local projection + raw zero-retention |
| 标题默认泄漏 | packet 默认不含 title；每次 preview 独立 opt-in |
| notes/location/attendee 泄漏 | model allowlist + schema/raw-field negative |
| 跨设备/owner 重放 | F153 proof/replay/revoke + F152 consent binding |
| result 自动写 Memory | F152 OptionalMemoryCandidate + second confirmation |
| 删除不完整 | F152 durable provenance deletion + reentry |
| 后台/通知静默请求权限 | user-action-only coordinator + UI/runtime tests |
| Simulator 冒充真机 | Gate 明确要求 real iPhone system sheet 与真实事件 |

## 不可信输入

- EventKit title/availability/time；
- LLM output；
- remote notification/deep link；
- Memory/retrieval content；
- server error body。

日历标题只作为用户已批准的数据，不是 prompt instruction 或 tool command。
