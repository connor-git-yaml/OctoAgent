# F151 当前架构权威复验

- 日期：2026-08-02
- 当前提交：`2f6fcbc91408e14bc3f5291678bdfd018cc24473`
- `origin/master`：`db3214fff722c6f969baf99528a76fc03a1e21a1`
- GitHub Actions run：`30717516171`
- architecture job：`success`
- repository architecture gate：`PASS`
- F151 canonical index：
  `.specify/features/151-runtime-boundary-architecture-truth/evidence/evidence-index.v2.json`
- canonical index SHA-256：
  `bd717b9d7889376d468d60480f95664dcd53dc27d116f536b4934d6c171ece9e`
- records / chain head：
  `269 / 913082c297cf19db3c76f73891efc56faa2b8db6993d03d8d233385403cbf878`
- F151 final report SHA-256：
  `50521128b546ebc6d649fbfe86104756aff9684c53cbc6704d0b1f54b5cc500f`

## 判定

F151 Feature 的最终 index、chain 与 final report 均已提交；当前提交又在 GitHub
clean checkout 中通过唯一 repository architecture gate。因此，F151 作为当前架构
authority 为 `PASS`，不再作为 F154/F155/F156 的笼统上游阻断。

后续 Feature 仍必须在同一 checker 中为自己的 exact paths/symbols 取得独立
RED→GREEN→REFACTOR authority；本 attestation 不替代 F155 T003 或 F156 T003。

## 历史留档限制

F151 的 `.specify/features/151-runtime-boundary-architecture-truth/evidence/.gitignore`
从设计上将 `/local/` 定义为本地 raw evidence；这些 raw bytes 未提交且当前已不存在。
因此不能从干净检出逐字节重放历史 269 条 TDD run，也禁止重新生成、拼接或伪造旧
RED/GREEN/REFACTOR。

该限制记为 `HISTORICAL_RAW_ARCHIVE_NOT_SELF_CONTAINED`：它不推翻已经提交的
F151 final index/report，也不能被误写成“历史 raw 已恢复”。未来 evidence producer
必须把 release 所需原始证据做成 committed/外部不可变留档，避免重复此缺口。
