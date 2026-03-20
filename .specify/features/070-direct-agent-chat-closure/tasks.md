# Tasks

## Slice A - 后端建模

- [x] 审核 direct session 创建链路，明确 `agent_profile_id` 与 `requested_worker_profile_id` 的职责
- [x] 修正 direct session 的 runtime/session kind，避免继续伪装成 `BUTLER_MAIN`
- [x] 为旧 direct session 数据保留兼容恢复逻辑

## Slice B - 模型别名与首条消息

- [x] 打通 profile `model_alias` -> enqueue/process_task_with_llm
- [x] 让首条 direct message 稳定携带 `session_id / thread_id`
- [x] 修复 `USER_MESSAGE.control_metadata.session_id/thread_id` 持久化
- [x] 增加 route/gateway 回归测试

## Slice C - 用户会话投影与前端恢复

- [x] 过滤 `worker_internal` 和其他内部 runtime session，不再暴露到用户聊天列表
- [x] 前端 route session 恢复优先级收口
- [x] 验证 direct session 首次发送、刷新恢复、继续旧会话
- [x] 增加 frontend 回归测试

## Validation

- [x] `pytest apps/gateway/tests/test_chat_send_route.py -q`
- [x] `pytest apps/gateway/tests/test_control_plane_api.py::TestControlPlaneApi::test_session_new_can_prepare_explicit_agent_session_entry -q`
- [x] `pytest apps/gateway/tests/test_control_plane_api.py::TestControlPlaneApi::test_session_create_with_project_returns_projected_session_id_and_thread_seed apps/gateway/tests/test_control_plane_api.py::TestControlPlaneApi::test_snapshot_returns_control_plane_resources_and_registry -q`
- [x] `npm test -- --run src/pages/ChatWorkbench.test.tsx`
- [ ] 如需要，手工检查 live `/api/control/resources/sessions`
