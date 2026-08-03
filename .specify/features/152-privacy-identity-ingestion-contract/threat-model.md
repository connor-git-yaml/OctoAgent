# F152 Threat Model

## 资产

- 设备私钥与短期 capability token；
- HealthKit/EventKit raw sample；
- normalized fact、review bundle、approved analysis packet；
- analysis result 与 Memory candidate；
- owner/device/capability/revoke/delete audit。

## Trust boundaries

1. Apple framework / Secure Enclave / Keychain；
2. 原生 App 进程与本地受保护暂存；
3. Cloudflare tunnel/edge（网络地基，不是设备身份）；
4. Gateway transport；
5. policy/application；
6. Provider/LLM；
7. Memory retrieval/store；
8. operator logs/backups。

## 威胁与控制

| 威胁 | 影响 | 必须控制 |
|---|---|---|
| App 包提取静态 secret | 任意客户端冒充 | App 包 secret=0；设备生成私钥 |
| token 被复制 | 跨设备访问 | key thumbprint + 请求 proof-of-possession |
| request replay | 重复敏感操作 | nonce/timestamp/body hash，单次 nonce |
| 被撤销设备继续用旧 token | 未授权访问 | 每次验证 device status；短 TTL；撤销优先于 exp |
| raw health/calendar 被批量上传 | 隐私扩大 | raw 默认 local-only；approved packet allowlist |
| consent 被复用 | 未来分析越权 | packet hash/purpose/expiry/single-use |
| 分析结果自动进 Memory | 长期泄漏 | explicit optional candidate + existing review |
| Memory 文本注入指令 | Agent 越权 | Memory 永远是 untrusted evidence |
| 日志/trace 泄漏 | 运维面暴露 | 只记录 hash/count/type/reason；secret scan |
| delete 只删主记录 | 派生数据残留 | provenance graph cascade + durable receipt |
| EventKit “read-only”误导 | 用户授权过宽不知情 | UI 明示 OS full access；写 capability/path=0 |
| Simulator 假装真机安全 | 安全结论错误 | Secure Enclave/App Attest/Apple 权限只接真机证据 |

## Fail-closed

unknown stage/capability、缺 provenance、过期 consent、错误 hash、跨 owner/device、
reused nonce、revoked device、部分删除、日志包含敏感字段均必须稳定失败；禁止 fallback
到 Web Cookie、service token、anonymous、ambient credential 或 best-effort success。
