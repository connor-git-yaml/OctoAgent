# F144 → F141（三模式 lane 门禁）Handoff：探针如何进 release lane

> F144 交付了两个本机 live 探针 + 一份 attestation 残余清单。F141 做 release lane
> 编排时，以下是可直接消费的契约与建议编排，以及必须注意的语义。

## 1. 探针命令契约（已实现，`packages/provider/src/octoagent/provider/dx/attest_commands.py`）

| 命令 | 语义 | 副作用 |
|------|------|--------|
| `octo attest remote --json` | F130 AC-1 链路半边：mode==bearer（enabled 信号）→ tailscale READY → token（实例 .env）→ `GET /ready` → SPA → bearer 纵深（无 token 401 / 带 token 200）→ SSE（真握手借最近任务，无任务退化 404 判别） | **零**（只读 + GET-only） |
| `octo attest service --json` | F129 AC-1 崩溃自愈：status 健康 → SIGKILL 真 pid → poll `status()` 恢复 → 新 pid ≠ 旧 pid | **服务秒级闪断**（模拟崩溃；`--dry-run` 只检不杀） |

### 三态 × exit code（lane 判定的关键）

```
status ∈ {pass, not_enabled, fail}
exit code：pass=0 / not_enabled=0 / fail=1
```

- **`fail`（exit 1）= 阻断信号**：已启用但链路断（如 bearer 下 tailscale 断链、
  token 缺失、/ready 不通、自愈超时）。Codex spec 评审 P2-1 特意把「bearer +
  tailscale 断链」钉在 fail 而非 not_enabled——lane **不得**把它当可忽略。
- **`not_enabled`（exit 0）≠ pass**：exit code 区分不出——**lane 必须解析
  `--json` 的 `status` 字段**，按策略处理：
  - `attest remote` not_enabled：远程触达是 optional 能力 → 记录即可；
  - `attest service` not_enabled：部署机上服务未安装应视为 **WARN/FAIL 级**
    （常驻是部署形态的前提，F129）——lane 策略自定，探针不越权替 lane 判。

### `--json` 输出 shape（`AttestReport.to_json_dict()`）

```json
{
  "probe": "remote | service",
  "status": "pass | not_enabled | fail",
  "exit_code": 0,
  "checks": [{"name": "...", "ok": true, "detail": "...", "hint": "..."}],
  "next_steps": ["..."]
}
```

- `checks[].ok`：`true/false/null`（null = 信息项/未执行，不参与判定）。
- **token 零泄漏保证**：任何字段不含 token 值（单测有 sentinel 泄漏扫描）——
  lane 可放心把 JSON 全文归档进 release 记录。
- `attest service --json` 真跑时，「秒级闪断」声明走 **stderr**（stdout 保持纯
  JSON 可解析）。

## 2. 建议的 release lane 编排

```
release gate（在部署机上执行；不进 per-PR CI——探针有真副作用/依赖真实实例）:
  1. octo attest service --json   # 先验常驻+自愈（remote 探针依赖服务活着）
  2. octo attest remote --json    # 再验远程链路
  3. attestation 清单签署          # 见 §3
  任一 fail → release 阻断；JSON 归档进 release 记录
```

- **顺序**：service 在前——它会闪断服务，恢复后再跑 remote 探针（探针内部
  /ready 已验恢复，但顺序反了会让 remote 探针撞上闪断窗口偶发 fail）。
- **频率**：release-only。探针跑在用户真实托管实例上（`~/.octoagent`），
  per-commit 跑 service 探针=每次 commit 都闪断用户服务，不可接受。
- **CI 红线**：两探针都**不进 GitHub Actions**（CI 无 tailscale/无托管实例/无
  launchd 用户会话；探针逻辑的回归由 hermetic 单测
  `packages/provider/tests/dx/test_attest_commands.py` 在 CI 守）。

## 3. attestation 清单消费（`docs/codebase-architecture/attestation-checklist.md`）

- 机器可读源 = 该文档第一个 ```yaml fenced block，`attestations[]` 每项含
  `id / source_ac / why_physical / action / frequency / last_attested / optional`。
- release gate 语义：对每个 `optional: false` 且 `frequency: release` 的项，
  要求人工执行 `action` 后回填 `last_attested`（YYYY-MM-DD）；过期/为 null →
  release 清单未签署 → 按 lane 策略阻断或显式豁免记录。
- `optional: true` 项（ATT-130-PHONE）：记录不阻断。
- **首版仅 2 项**（ATT-129-BOOT / ATT-130-PHONE）。增项走文档内「增项纪律」。

## 4. 语义坑（实施 F141 时别踩）

1. **service 探针用 SIGKILL 是刻意的**（F144 spec §D-3）：launchd
   `KeepAlive{SuccessfulExit=false}` 对 SIGTERM→优雅 exit 0 **不拉起**（这是
   `octo stop` 的语义）。lane 若自己写「重启验证」别用 SIGTERM 模拟崩溃。
2. **`attest remote` 的 SSE 检查有两档**：有历史任务 → 真流式握手；空实例 →
   退化 404 认证判别（detail 含「streaming 未实测」）。lane 归档时保留 detail
   便于事后区分覆盖档位。
3. **探针不代跑 `octo remote enable` / `octo service install`**：not_enabled 的
   next_steps 给指引，启用动作永远归用户/主 session（Constitution #7）。
4. **恢复预算 90s**：慢机器上自愈探针可能贴边；若 lane 环境更慢，
   `run_service_probe(recovery_budget_s=…)` 有参数缝（CLI 暂未暴露 flag，
   F141 需要时加 `--budget` 是 S 级改动）。

## 5. F144 已吸收面（lane 不必重复造）

| 原手工验收 | 现归属 |
|-----------|--------|
| F130 AC-1 语义半边（bearer×XFF 等） | L4 矩阵 `test_frontdoor_auth.py::TestFrontDoorModeHeaderMatrix`（CI 常跑） |
| F130 AC-1 链路半边 | `octo attest remote`（release lane） |
| F129 AC-1 崩溃自愈 | `octo attest service`（release lane） |
| F129 AC-1 开机自启 | 清单 ATT-129-BOOT（签署项） |
| F135 gap-1 审批全链 | `test_e2e_scripted_write_approval.py`（e2e_scripted，CI 可跑） |
