# F108a 残留扫描报告（Phase 4 制品）

> 扫描范围：pyproject testpaths 全部 9 路径 + docs/ + 根目录脚本（W2 教训后的全覆盖口径）。

## 旧符号 / 旧路径残留

| 旧符号 | 残留 | 判定 |
|--------|------|------|
| `behavior_workspace.py`（单文件路径引用） | 0 live 引用；无字符串模块路径、无 monkeypatch 属性引用（W1 C1 agent 全仓侦察） | ✅ 零残留 |
| `_build_registry` | 仅 `action_registry.py` docstring 出处注记 | ✅ 豁免（溯源注释惯例） |
| `LLMWorkerAdapter` | `orchestrator.py:344` docstring + `octoagent-architecture.md:283`"前名"注记 | ✅ 豁免（w2-ledger #4，Opus O3 惯例确认） |
| `resolve_write_path_by_file_id`/`check_behavior_file_budget` 旧直调序列 | worker_service/misc_tools 已收口为 prepare/commit；函数本身仍是 behavior_workspace 公开 API（其他消费方合法使用） | ✅ |
| setup/worker/session/capability_pack 被搬走方法名 | 全部经 mixin MRO 在原类上可达（这是设计——非残留） | ✅ |
| 死 import | 23 个已清（收尾 commit）；2 个故意锚点 noqa 标注 | ✅ |

## import surface 变化记账

- `octoagent.core.behavior_workspace`：52 个实际被 import 的名字全覆盖 + 3 个新增（write 模块）；顶层模型别名透传**有意废止**（0 真实消费方，w1-ledger 豁免 #3）。
- 其余 6 个被拆文件：类名/模块路径零变化（mixin 为内部细节）；`capability_pack._ssrf_request_hook` re-export 保持 e2e import 路径。

**结论：旧名称零残留（豁免逐条归档）。**
