# F156 Data Model

## Navigation

### `CompanionTab`

- `chat`
- `tasks`
- `inbox`
- `settings`

### `AppDestination`

- `conversation(id)`
- `task(id)`
- `approval(id)`
- `memoryCandidate(id)`
- `health`
- `calendar`（仅 F155 方案 A + Verify）
- `connection`
- `privacy`
- `advancedDiagnostics`

只持久化 tab/destination identity；正文不进入 SceneStorage。

## Product states

### `ConversationScreenState`

- loading
- empty
- loaded
- sending
- streaming
- recoverableError
- offline
- revoked

### `TaskScreenState`

- loading
- empty
- loaded
- recoverableError
- offline
- revoked

具体 task status 只使用 Gateway canonical finite enum。

### `InboxItem`

- typed identity
- kind: approval | memoryCandidate
- ordinary-language summary
- source
- state
- available actions
- expiresAt

### `NotificationRoute`

- type
- opaqueObjectID
- collapseIdentity

不得含正文、token、owner、Health/Calendar 数据。

## Persistent boundaries

- Keychain：F153 credentials only。
- SceneStorage：tab/destination ids only。
- UserDefaults/AppStorage：非敏感 UI preference only。
- Gateway：服务器 canonical content/state。
- SwiftUI snapshot：测试必须 scan/遮蔽敏感正文。
