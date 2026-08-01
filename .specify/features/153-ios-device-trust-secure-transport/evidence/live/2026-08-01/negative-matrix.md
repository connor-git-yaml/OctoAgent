# F153 Web/mobile edge 负向 live 矩阵

## 结论

- 执行日期：2026-08-01（Asia/Shanghai）；
- 个人部署 Web hostname：`octo.maojiwang.work`；
- 个人部署 iOS hostname：`ios.maojiwang.work`；
- Web Access 保护：PASS；
- mobile Host/path 隔离：PASS；
- Web Cookie/service token 拒绝：PASS；
- origin typed error：PASS；
- owner challenge→真机 enrollment→approve→signed ready→replay→revoke：本文件未执行，
  继续 MISSING；
- F153 T014/T015/T018：保持 unchecked。

本轮只发送不创建、不批准、不撤销设备记录的负向请求。请求没有携带真实 Cookie、
Access JWT、service token、device token、challenge、签名或私钥。Cloudflare 登录跳转的
一次性 query 没有写入本证据。

## 结果

| 场景 | HTTP | Content-Type | 稳定结果 | body SHA-256 |
|---|---:|---|---|---|
| Web 根页（无登录态） | 302 | `text/html; charset=UTF-8` | 跳转到 Access 登录 host | `12755429beb15d5eb57eafa45b8dba326343dd099bf0552038694c3856e8860e` |
| Web hostname `/api/mobile/v1/ready` | 302 | `text/html; charset=UTF-8` | 仍由 Web Access 拦截 | `12755429beb15d5eb57eafa45b8dba326343dd099bf0552038694c3856e8860e` |
| iOS hostname `/` | 404 | `application/json` | `MOBILE_ROUTE_NOT_FOUND` | `6a4f5ec6bbcede0289e3b804e3048fc34eee298c402f91ce720ea08f7d892b7e` |
| iOS hostname `/health` | 404 | `application/json` | `MOBILE_ROUTE_NOT_FOUND` | `6a4f5ec6bbcede0289e3b804e3048fc34eee298c402f91ce720ea08f7d892b7e` |
| iOS hostname `/docs` | 404 | `application/json` | `MOBILE_ROUTE_NOT_FOUND` | `6a4f5ec6bbcede0289e3b804e3048fc34eee298c402f91ce720ea08f7d892b7e` |
| iOS hostname `/openapi.json` | 404 | `application/json` | `MOBILE_ROUTE_NOT_FOUND` | `6a4f5ec6bbcede0289e3b804e3048fc34eee298c402f91ce720ea08f7d892b7e` |
| iOS hostname owner route | 404 | `application/json` | `MOBILE_ROUTE_NOT_FOUND` | `6a4f5ec6bbcede0289e3b804e3048fc34eee298c402f91ce720ea08f7d892b7e` |
| mobile ready 无 device proof | 401 | `application/json` | `DEVICE_PROOF_HEADERS_INVALID` | `a8409304afb68fb18aadc2ffe61e89b962df6a684989508d07c9f20c3833a9d6` |
| mobile ready 携带伪 Web Cookie | 401 | `application/json` | `MOBILE_WEB_CREDENTIAL_REJECTED` | `448fdd2e3c4de9208f7a7c6f29cc2c6eb35fd2c635b65a1a2d30f0ac6107d34c` |
| mobile ready 携带伪 service-token headers | 401 | `application/json` | `MOBILE_WEB_CREDENTIAL_REJECTED` | `448fdd2e3c4de9208f7a7c6f29cc2c6eb35fd2c635b65a1a2d30f0ac6107d34c` |
| enrollment 空 JSON | 422 | `application/json` | FastAPI/Pydantic typed validation | `2fd3929d7f163fd36063ee4011a3c03f3a726bd2de59138edd77988870c0d2fe` |
| unknown device token challenge | 404 | `application/json` | `DEVICE_NOT_ACTIVE` | `c763c516687c9d932724d985458330aef0990f39e7a364c5cd8c41e054fc6148` |

## 判定边界

该矩阵证明：

1. Web Access application 没有被 mobile Bypass 放宽；
2. iOS hostname 不暴露 SPA、health、docs、OpenAPI 或 owner route；
3. mobile route 不接受 Web Cookie、Access JWT/service-token 模式；
4. Bypass 后仍由 Gateway Host/path/device-proof 合同 fail closed。

它不能证明 Secure Enclave、ThisDeviceOnly Keychain、owner approval、opaque token、
signed ready、nonce replay、网络切换、前后台恢复或 revoke/rotation。必须由真 iPhone
正向链补齐后，T014/T015/T018 才能完成。
