# F156 启动 / 功能 / 视觉场景矩阵

> 每行最终必须有真实 command、exit、artifact、screenshot 与 commit。当前仅为
> Design draft，`evidence=0`。

| ID | Surface | Scenario | Layer | Functional oracle | Visual oracle | Device required |
|---|---|---|---|---|---|---|
| C01 | App root | cold launch / no credentials | L1 | F153 registration | awaiting baseline | Simulator+Device |
| C02 | App root | connecting | L1 | single connection state | connecting baseline | Simulator |
| C03 | App root | connected shell | L1 | exact four tabs | shell baseline | Simulator+Device |
| C04 | App root | offline cached view | L1/L3 | writes disabled | offline banner | Simulator+Device |
| C05 | App root | revoked | L1/L3 | credentials cleared | revoked baseline | Simulator+Device |
| C06 | Chat | loading | L4/L1 | no false empty | loading baseline | Simulator |
| C07 | Chat | empty | L4/L1 | new conversation entry | empty baseline | Simulator |
| C08 | Chat | list loaded | L3/L1 | exact conversations | list baseline | Simulator |
| C09 | Chat | send text | L3/L1 | one request/id | pending bubble | Simulator+Device |
| C10 | Chat | streaming | L3/L1 | ordered dedup stream | streaming baseline | Simulator+Device |
| C11 | Chat | stream reconnect | L3/L1 | no duplicate suffix | reconnect banner | Simulator+Device |
| C12 | Chat | send failure/retry | L3/L1 | typed retry | failure baseline | Simulator |
| C13 | Tasks | loading/empty | L4/L1 | distinct states | two baselines | Simulator |
| C14 | Tasks | running list | L3/L1 | canonical status | running baseline | Simulator |
| C15 | Task detail | progress/events/artifact | L3/L1 | exact projection | detail baseline | Simulator |
| C16 | Task detail | allowed action | L3/L1 | state+capability | action feedback | Simulator+Device |
| C17 | Task detail | 409/expired action | L3/L1 | server wins | conflict baseline | Simulator |
| C18 | Inbox | no pending items | L4/L1 | true empty | empty baseline | Simulator |
| C19 | Approval | approve | L3/L1 | one decision | approved feedback | Simulator+Device |
| C20 | Approval | reject | L3/L1 | one decision | rejected feedback | Simulator+Device |
| C21 | Approval | expired/conflict | L3/L1 | no local overwrite | conflict baseline | Simulator |
| C22 | Memory | candidate review | L3/L1 | exact candidate | review baseline | Simulator |
| C23 | Memory | accept/reject | L3/L1 | explicit second decision | feedback baseline | Simulator+Device |
| C24 | Settings | connection/device | L3/L1 | single device truth | connection baseline | Simulator+Device |
| C25 | Settings | notification explanation | L1 | no cold prompt | pre-prompt baseline | Simulator+Device |
| C26 | Settings | notification denied | L1 | app still functional | denied baseline | Device |
| C27 | APNs | receive task notification | L3/L1 | opaque payload | system + destination | Device |
| C28 | APNs | receive approval notification | L3/L1 | fetch then navigate | approval destination | Device |
| C29 | APNs | expired/revoked payload | L3/L1 | no action | safe fallback | Device |
| C30 | Health | owner unavailable/available | L1 | F154 gate truth | settings card states | Simulator+Device |
| C31 | Calendar | removed/deferred/available | L1 | F155 decision truth | settings card states | Simulator+Device |
| C32 | Lifecycle | foreground/background | L3/L1 | cancel/refresh finite | no stale overlay | Device |
| C33 | Network | Wi-Fi↔cellular | L3/L1 | reconnect/proof | transient banner | Device |
| C34 | Token | refresh/expiry/revoke | L3/L1 | no ambient fallback | connection feedback | Device |
| C35 | A11y | AXXXL | L1 | all actions reachable | snapshot matrix | Simulator+Device |
| C36 | A11y | VoiceOver | L1 | labels/order/actions | accessibility tree | Simulator+Device |
| C37 | A11y | Reduce Motion | L1 | no essential motion | stable snapshot | Simulator |
| C38 | A11y | Differentiate Without Color | L1 | state has shape/text | snapshot matrix | Simulator |
| C39 | Visual | supported iPhone sizes | L1 | no clip/overflow | pixel matrix | Simulator |
| C40 | Privacy | snapshot/log/package scan | Gate | sensitive body=0 | N/A | Clean checkout |

## 完成规则

- 40/40 行必须被 owner test 映射；
- Simulator-only 行不能冒充 Device 行；
- visual baseline 与 function E2E 使用同一 deterministic fixture；
- failure/retry 不得覆盖；任何 missing scene 保持 Goal incomplete。
