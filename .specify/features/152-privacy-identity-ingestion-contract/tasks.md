# F152 Tasks

> Design/Tasks/Verify Gate 均已通过。F152 T001-T014 已完成；F153 production
> 已解锁，但不得绕过本 Feature 冻结的安全合同。

- [x] **T000 [PROCESS]** 复核 F151 stable、F158 Milestone audit 与 M12 Blueprint
- [x] **T001 [L4][RED→GREEN→REFACTOR]** F151 exact authority：只放行 inventory
  冻结的 core/protocol/policy/test/artifact paths，拒绝 iOS、Gateway device/HealthKit/
  EventKit route、第二 registry/store/session
- [x] **T002 [L4][RED→GREEN→REFACTOR]** Data stage、provenance、TTL 与 canonical
  hash Pydantic models
- [x] **T003 [L4][RED→GREEN→REFACTOR]** finite capability vocabulary、owner/device/
  audience/expiry/token-id exact validation
- [x] **T004 [L4][RED→GREEN→REFACTOR]** request proof payload：method/path/body-hash/
  timestamp/nonce canonicalization；不实现 F153 crypto
- [x] **T005 [L4][RED→GREEN→REFACTOR]** consent grant 绑定 packet hash/purpose/
  owner/device/expiry 且 single-use
- [x] **T006 [L4][RED→GREEN→REFACTOR]** review bundle → approved packet →
  analysis result → optional Memory candidate 单向状态机
- [x] **T007 [L4/L3][RED→GREEN→REFACTOR]** durable audit exact metadata 与
  secret/raw-body negative
- [x] **T008 [L4/L3][RED→GREEN→REFACTOR]** deletion cascade / receipt 可重入、
  partial failure 可恢复
- [x] **T009 [L4][RED→GREEN→REFACTOR]** expiry、revoke、cross-owner/device、
  replay、unknown stage/capability adversarial matrix
- [x] **T010 [L4][RED→GREEN→REFACTOR]** Memory candidate 必须二次确认，禁止分析
  result 自动直写
- [x] **T011 [CONTRACT]** 生成 exact JSON schema 与 F153/F154/F155 consumer
  compatibility fixtures
- [x] **T012 [ARCHITECTURE]** 单 registry/store/audit/memory owner、complexity 与
  import-direction ratchet
- [x] **T013 [DOC]** 同步 Blueprint security/data-boundary 说明和 Feature trace
- [x] **T014 [VERIFY]** focused/full regression、secret scan、verification report；
  成功后才解锁 F153

## 测试层级

- L4：所有 model、canonicalization、policy、state transition 与 adversarial matrix。
- L3：真实 store transaction、expiry/revoke/delete、Gateway schema projection。
- L1：F152 无 UI；F153 起验证原生 transport。
- L2：本 Feature 不使用真实 LLM。

## 每个行为任务的证据

每个 RED/GREEN/REFACTOR 必须记录 exact selector、exit、UTC、Git tree、稳定 oracle、
stdout/stderr hash。禁止以缺文件/import/collection 错误冒充 RED，禁止 rerun 掩盖失败。
