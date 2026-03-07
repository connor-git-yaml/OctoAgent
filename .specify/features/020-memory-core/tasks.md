# Feature 020 Tasks

## Phase 1: Workspace & Package

- [x] T001 新增 `octoagent/packages/memory/pyproject.toml`
- [x] T002 更新 `octoagent/pyproject.toml` 注册 `octoagent-memory`
- [x] T003 新增 `octoagent/packages/memory/src/octoagent/memory/__init__.py`

## Phase 2: Models & Schema

- [x] T004 实现 `enums.py`
- [x] T005 实现 `models.py`
- [x] T006 实现 `store/sqlite_init.py`
- [x] T007 为 SoR current 唯一约束补单测

## Phase 3: Store & Service

- [x] T008 实现 `store/memory_store.py`
- [x] T009 实现 `protocols.py`
- [x] T010 实现 `service.py`
- [x] T011 实现 `propose_write()` / `validate_proposal()`
- [x] T012 实现 `commit_memory()`
- [x] T013 实现 `search_memory()` / `get_memory()`
- [x] T014 实现 `before_compaction_flush()`

## Phase 4: Tests & Verification

- [x] T015 新增 `tests/conftest.py`
- [x] T016 新增 `tests/test_models.py`
- [x] T017 新增 `tests/test_memory_store.py`
- [x] T018 新增 `tests/test_memory_service.py`
- [x] T019 运行 Memory package 测试
- [x] T020 生成 verification 报告并回填任务状态

## Phase 5: Backend 插件化

- [x] T021 实现 `backends/protocols.py` 定义 `MemoryBackend`
- [x] T022 实现 `SqliteMemoryBackend` 与 `MemUBackend` adapter
- [x] T023 在 `MemoryService` 中接入 backend orchestration 与 fallback
- [x] T024 新增 `tests/test_memory_backends.py` 覆盖 adapter 委托与降级路径
