# Tasks - Feature 052

## Phase 1 - 规格与模型

- [x] T001 [P0] 新建 052 `spec.md / plan.md / tasks.md`，回写与 Agent Zero / OpenClaw 的对标结论
- [x] T002 [P0] 在 `packages/core` capability / control-plane 模型中新增 `recommended_tools`、`mount_policy`、`permission_mode` 等正式字段

## Phase 2 - Trusted Baseline

- [x] T003 [P0] 在 `capability_pack.py` 收口 trusted local baseline，提升 `general / research / dev` 默认 `tool_profile`
- [x] T004 [P0] 在 orchestration / runtime 侧确保 trusted baseline 仍受 policy 上限约束

## Phase 3 - MCP Auto Mount

- [x] T005 [P0] 扩展 MCP provider 配置与 control-plane catalog，增加 `mount_policy`
- [x] T006 [P0] 在 capability pack runtime filtering 中实现 `auto_readonly` 挂载
- [x] T007 [P1] 补 MCP mount policy 的后端测试

## Phase 4 - Skill Inherit Mode

- [x] T008 [P0] 扩展 skill provider 配置与 control-plane CRUD，增加 `permission_mode`
- [x] T009 [P0] 改造 `LLMService / LiteLLMSkillClient / SkillRunner`，让 `inherit` 默认继承 ambient tool universe
- [x] T010 [P1] 保持 `restrict + tools_allowed` 收窄器兼容，补相关测试

## Phase 5 - Recommended Tools Contract

- [x] T011 [P0] 扩展 `DynamicToolSelection / EffectiveToolUniverse`，正式区分 `recommended_tools` 与 `mounted_tools`
- [x] T012 [P0] 升级 `selected_tools_json` 为 recommended mirror，并同步更新 prompt/metadata 读取逻辑
- [x] T013 [P1] 在 control plane / settings 资源中暴露 `recommended_tools / blocked_tools`

## Phase 6 - 文档与验证

- [x] T014 [P0] 回写 `docs/blueprint.md` 与必要 README / feature docs
- [x] T015 [P0] 跑后端定向回归、前端定向回归、`tsc -b`、`ruff check`、`git diff --check`
