# F144 → F141 Release Lane Handoff

## Service 探针

命令：`octo attest service --json`

lane 必须解析 JSON `status`：

- `pass`：通过；
- `not_enabled`：release FAIL；
- `fail`：release FAIL。

不能只根据 exit code判断，因为 `not_enabled` 与 `pass` 都返回 0。探针会 SIGKILL 真实 service pid，恢复窗口内有秒级闪断；CI 不运行，逻辑由 hermetic 单测守护。

## Attestation 清单

release lane 使用 `check-attestation.py --require-signed` 检查非 optional、frequency=release 的项目。当前仅 `ATT-129-BOOT`，要求最近 90 天内签署。

## 顺序

`deterministic tests → live-real-llm → attest-service → attestation-signed`
