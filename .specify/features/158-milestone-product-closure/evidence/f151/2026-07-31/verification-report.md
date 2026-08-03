# F151 当前提交与历史证据可复验性审计

## 范围

- 日期：2026-07-31
- 当前提交：`564f40d48f0aa0ab873c43f608f78cbd5c186875`
- 当前 `origin/master`：
  `db3214fff722c6f969baf99528a76fc03a1e21a1`
- F151 immutable base：
  `9d5e1e48691c5ae5a12b33f224d64ac03d5442fc`
- 本报告是 F158 对 F151 当前代码与历史证据可复验性的独立审计，不改写 F151
  canonical index、anchor、final report 或历史完成声明。

## 当前代码架构门

在 repo root、post-SDK `PYTHONPATH`、`PYTHONNOUSERSITE=1` 下执行：

```bash
uv run --project octoagent --no-sync python \
  repo-scripts/check-runtime-architecture.py all \
  --base-ref origin/master \
  --scope-mode repository
```

结果：`exit=0`，stdout/stderr 均为空。

该结果与当前真值提交 `dc8b1b417fa0cb79a90c1aa290a2dc44e11fcad4`
的 GitHub Actions run `30602259193` architecture job PASS 一致。它直接证明当前
F158 分支相对当前主线仍满足 import、retired、quality 与 complexity 门。

## Canonical index 静态完整性

- index：
  `.specify/features/151-runtime-boundary-architecture-truth/evidence/evidence-index.v2.json`
- SHA-256：
  `bd717b9d7889376d468d60480f95664dcd53dc27d116f536b4934d6c171ece9e`
- records：`269`
- unique record hashes：`269`
- 独立重算 record hash/previous chain 错误：`0`
- 独立重算 chain head：
  `913082c297cf19db3c76f73891efc56faa2b8db6993d03d8d233385403cbf878`
- final report SHA-256：
  `50521128b546ebc6d649fbfe86104756aff9684c53cbc6704d0b1f54b5cc500f`

因此 committed index JSON 自身的 schema/hash chain 未损坏。

## 干净检出的两项失败

### 使用报告中记录的可变 ref

```bash
uv run --project octoagent --no-sync python \
  repo-scripts/check-runtime-architecture.py tdd-evidence verify \
  --mode committed \
  --base-ref origin/master \
  --evidence-index \
  .specify/features/151-runtime-boundary-architecture-truth/evidence/evidence-index.v2.json \
  --through-task T124
```

结果：`exit=1`：

```text
EVIDENCE_BASE_REF_INVALID: origin/master
```

原因是 `origin/master` 已前移到 `db3214ff…`，当前 merge base 不再等于 index/anchor
冻结的 `9d5e1e48…`。这不是当前 architecture gate 失败，而是 final report 使用了
可变 ref 名称。

### 使用 immutable base

将 `--base-ref` 改为 index/anchor 冻结的
`9d5e1e48691c5ae5a12b33f224d64ac03d5442fc` 后，结果仍为 `exit=1`：

```text
EVIDENCE_ARTIFACT_MISSING EVIDENCE_PATH_OR_NAME_INVALID:
S001-import-direction/RED: []
```

仓库当前事实：

- `evidence/.gitignore` 明确忽略 `/local/`；
- 干净检出中 `evidence/local/` 不存在；
- tracked evidence 只有 `.gitignore`、anchor 与 index；
- 已检查当前 worktree、现存 Codex worktree、主工作树和 `/tmp`，未找到可恢复的
  F151 `evidence/local` 原始六件套。

因此 269-record index 中保存的 artifact SHA/size 无法在干净检出中逐件复验。

## 判定

- F151 当前产品架构合同：`PROVEN_CURRENT`。
- F151 canonical index JSON/hash chain：`INTACT_METADATA`。
- F151 历史 TDD raw evidence 的干净检出可复验性：
  `CONTRADICTED_MISSING_RAW_ARTIFACTS`。
- F151 final report 中 `origin/master` 的长期稳定性：
  `CONTRADICTED_MUTABLE_BASE_REF`。

禁止根据 index 中的 hash 反向伪造 raw artifacts，禁止重跑当前 GREEN 代码冒充历史
RED/GREEN/REFACTOR。旧 index/anchor/report 继续只读保留。F158 最终完成声明必须区分
“当前架构门已证明”与“历史 TDD raw archive 不自包含”，不得用前者掩盖后者。
