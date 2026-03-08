# Contract: Runtime Secret Reload

## 1. Goal

让当前 project 的 secret bindings 在不泄露明文的前提下真正进入 runtime 生效路径。

## 2. `octo secrets reload`

**Input**

- 当前 active project

**Output**

- redacted `RuntimeSecretMaterialization` summary
- runtime action result

## 3. Managed Runtime Path

1. 重新解析当前 project bindings
2. 生成 short-lived materialization summary
3. 调用 024 `UpdateService.restart()`
4. 调用 024 `UpdateService.verify()`
5. 汇总为单个 `reload` 结果

## 4. Unmanaged Runtime Path

- 允许生成 materialization summary
- 不伪装成已热重载
- 返回 `degraded/action_required` 与下一步建议

## 5. Security Rules

- summary 只能包含 env names、targets、counts、requires_restart 标志
- 不保存 materialized secret values
- reload 失败时也不得回显 secret 明文
