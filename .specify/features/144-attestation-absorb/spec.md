# F144 验收自动吸收 — Feature Spec

## 1. 目标

把可自动化的人工验收项下沉到正确测试层，只让真正需要物理动作的残余进入 attestation 清单。

## 2. 范围

### A. Front-door mode×header 矩阵

- `loopback / bearer / trusted_proxy` 覆盖无 header 与各类 proxy hint header。
- bearer 正确 credential 在代理 header 存在时仍可放行。
- loopback 收到代理 header 时拒绝，避免伪造来源绕过。
- trusted_proxy 校验 CIDR 与共享 header。
- SSE credential 正负路径与普通 HTTP 使用同一 Guard 语义。

### B. Service live 探针

- `octo attest service [--dry-run] [--json]` 检查托管服务崩溃自愈。
- 真执行使用 SIGKILL 终止当前 pid，轮询 service manager 与 `/ready`，要求恢复为新 pid。
- `--dry-run` 只展示动作，不发送 signal。
- 三态：`pass / not_enabled / fail`，exit code 分别为 0 / 0 / 1；release lane 必须解析 JSON `status`，不能只看 exit code。
- JSON stdout 保持纯 JSON；闪断声明与诊断输出走 stderr。

### C. 写入审批 L3 scripted

- 脚本脑驱动真实审批 REST 全链。
- approve 路径写盘，reject 路径不写盘。
- `permission_preset=full` 也不得绕过 IRREVERSIBLE 审批。

### D. 物理残余清单

- 只保留 `ATT-129-BOOT`：真机 reboot 后验证 launchd 开机自启。
- 新增项必须说明为什么 L4/L3/L1/live 探针均无法吸收。

## 3. 验收

- front-door 矩阵覆盖全部模式与 header 分支。
- service 探针的 pass/not_enabled/fail、恢复超时、pid 未变化、dry-run 与 secret 扫描均有 hermetic 单测。
- L3 approve/reject 双路径全绿。
- attestation YAML 可被 release gate 解析，且不存在已自动化的死项。
