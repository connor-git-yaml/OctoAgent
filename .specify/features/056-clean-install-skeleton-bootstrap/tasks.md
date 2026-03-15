# Tasks

## Slice A - 文件系统骨架

- [x] 在 behavior_workspace.py 中新增 `ensure_filesystem_skeleton()` 函数
- [x] mkdir behavior/system、behavior/agents/butler、projects/default/behavior、projects/default/behavior/agents/butler
- [x] 写入 `projects/default/project.secret-bindings.json`（空 JSON）
- [x] 写入 instructions/README.md scaffold
- [x] 在 main.py lifespan 中调用 ensure_filesystem_skeleton

## Slice B - Startup Agent Profile + Bootstrap Session

- [x] 新增 startup_bootstrap.py 模块，包含 ensure_startup_records()
- [x] 创建默认 owner profile、agent profile、bootstrap session
- [x] 在 main.py lifespan 中调用 ensure_startup_records
- [x] 添加 store_group.conn.commit() 确保数据持久化
- [x] 验证 DB 中记录正确创建（agent_profiles=1, bootstrap_sessions=1, owner_profiles=1）

## Slice C - workspace_root_path 透传

- [x] 追踪 orchestrator → butler_behavior → resolve_behavior_workspace 的调用链
- [x] 确认 orchestrator L1175 已正确从 DB workspace.root_path 透传（无需修复）

## Slice D - 前端 Butler 去硬编码

- [x] presentation.ts: formatAgentRoleLabel 改用 opts.isMainAgent 参数
- [x] ChatWorkbench.tsx: 移除 butler 字符串匹配，用户可见文案改为"主助手"/"开始新会话"
- [x] WorkbenchLayout.tsx: "和 Butler 对话" → "和主助手对话"
- [x] HomePage.tsx: "Butler / Worker 协作" → "多 Agent 协作"，WORKER_LABELS.general 改为"主助手"
- [x] SettingsOverview.tsx: 两处 Butler 引用改为"主助手"
- [x] ChatWorkbench.test.tsx: 更新 "新开 Butler 会话" 断言为 "开始新会话"

## 验证

- [x] rm -rf ~/.octoagent + install-octo-home.sh 后 Gateway 启动自动创建全部骨架
- [x] 前端测试无新增失败（4 个失败均为预存基线问题）
