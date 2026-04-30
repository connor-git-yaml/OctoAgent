# F087 e2e 脱敏 local-instance fixture

**目的**：给 e2e_live 套件提供"看起来像 ~/.octoagent 但完全脱敏"的实例模板。

## 使用方式

测试 fixture 把本目录 `*.template` 文件 copy 到 tmp_dir，去掉 `.template` 后缀
后作为 e2e 实例的初始 state。

## 目录结构

- `octoagent.yaml.template` — 脱敏 octoagent.yaml（不含真实凭证）
- `behavior/system/USER.md.template` — 脱敏 USER.md
- `behavior/system/MEMORY.md.template` — 脱敏 MEMORY.md
- `.gitignore` — 严格规则，禁止任何非 `.template` 文件入仓

## 安全约束

- **禁止**：在本目录保存任何真实 API_KEY / TOKEN / SECRET / OAuth profile
- 验证：`grep -rn "API_KEY\|TOKEN\|SECRET" tests/fixtures/local-instance/ | grep -v template | grep -v .gitignore | grep -v README` 返回空
- F087 P5 T-P5-5 跑前后强制 grep 验证（SC-8）
