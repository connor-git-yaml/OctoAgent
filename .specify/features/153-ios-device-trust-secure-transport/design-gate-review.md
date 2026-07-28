# F153 Design Gate Review

## 判定

`GATE_DESIGN=true`

## 审查结论

设计已对 transport、edge、device key、registration、token、proof、replay、revoke、
rotation、App Attest、Host/path、durable state、iOS UI 与证据等级给出单一答案。

选择 origin device proof 而不是 Web Cookie/service token/mTLS，是为了同时满足：

- App 不携带静态 deployment secret；
- Gateway 不持有 Cloudflare account write credential；
- 每个自托管部署可替换 hostname，不重写 App；
- F152 capability/revoke/request proof 可以直接成为唯一 authority；
- 不增加 Worker、WARP、第二 tunnel 或第二 identity registry。

接受的代价是 mobile path 不受 Access identity/logging 保护；因此设计不把 Bypass
描述成 Zero Trust，并要求 exact path live negative、origin audit 与 request proof
全链通过。外部配置失败不会通过文档声明冒充可用。

## 硬门

- 产品：PASS
- 安全：PASS（live edge/device evidence pending Implement）
- 分层：PASS
- 测试：PASS
- 可维护性：PASS
- 外部权限：清晰分离
