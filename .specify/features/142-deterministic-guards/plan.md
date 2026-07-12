# F142 实施计划

> 每件独立 commit；小步；被杀重启以 `git log origin/master..HEAD` 续。
> 验证一律：worktree PYTHONPATH 锁（8 packages src + gateway src）+
> `uv run --project . --no-sync python -m pytest`；禁 `uv sync`。

## Phase 顺序（先简后难 + 依赖驱动）

| Phase | 内容 | commit 粒度 |
|-------|------|-------------|
| P0 | spec/plan 制品 commit → `codex review --base origin/master` 0 HIGH 后进 P1 | 1 |
| P1 | 件 5b：4 欠账处置（2 治根因移除 skipif + 2 永久豁免注释升级）——最小独立，先建信心 | 1 |
| P2 | 件 1：`tests/lib_semantics/` 三真库钉住 + `__init__` 归档（aiosqlite 略过说明） | 1 |
| P3 | 件 3：wire 边界用例族 + 行缓冲评估结论（docstring + spec 已归档） | 1 |
| P4 | 件 2：prompt 预算护栏（cap 实测收口：先跑测定脚本记录实测值 → 写死 cap） | 1 |
| P5 | 件 4：dirty-equals dev-dep（uv lock）+ 2-3 样例改造 + importorskip 防御 | 1 |
| P6 | 件 5a：xdist_group 标注 → 本地 `-n auto --dist=loadgroup`（CI scope）×3 → 按证据迭代补组 | 1-2 |
| P7 | CI backend job 并行翻转（仅 run 参数区块；×3 不稳则跳过本 commit 并归档） | 0-1 |
| P8 | 终门：全量 0 regression vs baseline + e2e_smoke 8/8 + （若翻转）loadgroup ×3 记录 | — |
| P9 | 双评审：codex final review（挑战面见下）+ Opus 对抗自审 → 0 HIGH 闭环 | fix 若干 |
| P10 | completion-report + living-docs（testing-strategy.md:36 F142 行 / milestones.md F142 行 ✅） | 1 |

## 双评审挑战面（预登记，评审时逐条回答）

1. 钉住测试是否**真用真库**（不是又一层 fake）——AC-1 必须真 TLS socket 真 httpx client；
   AC-2 必须真 CronTrigger.next_fire_time；AC-3 必须 importorskip 真 piper。
2. 预算 cap 是否拍脑袋——必须附实测值 + 余量计算 + 测定环境注释。
3. xdist 分组是否漏 race 文件——3 轮运行证据 + 失败迭代记录。
4. skip 移除是否真治根因——f131/f009 必须是「确定性完成信号等待」不是放大 sleep。
5. 生产代码零改承诺是否兑现——`git diff --stat` 生产路径应为 0（除非触发预许可例外并显式报）。

## 风险与对策

- **hook 共享 venv sync 漂移**：commit 触发 hook `uv run`（无 --no-sync）会把共享 venv
  editable 指到本 worktree 并装 dirty-equals——test-only 改动风险低；合入后主仓 `uv sync`
  收敛（F137/F138 先例）。dirty_equals 未装窗口由 importorskip 防御。
- **TLS 真 server 测试自身 flaky**：设计为确定性中断（服务端硬断）非赌竞态窗口；繁忙
  loop 仅作压力背景不作断言条件；若实测异常类型平台相关（ReadError vs ConnectError），
  按实测收口断言集合并注释。
- **-n auto 揭出新 race**：预算 1-2 轮迭代补组；不稳回退串行是显式允许的结论。
- **e2e_live 在本地全量下的并行行为**：CI scope 验证用 `--ignore=e2e_live` 与 CI 一致；
  本地全量（含 e2e_live）仍默认串行跑终门，不给 e2e_live 强加并行验证。
