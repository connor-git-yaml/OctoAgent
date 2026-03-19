# Tasks: 全面改进 Behavior 默认模板内容 (Feature 065)

**Input**: `.specify/features/065-improve-behavior-templates/` 下的 spec.md, plan.md, data-model.md
**Prerequisites**: plan.md (required), spec.md (required)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可与同 Phase 内其他 [P] 任务并行（不同内容域，无依赖）
- **[Story]**: 所属 User Story（US1-US7）
- 所有模板变更集中在单一函数：`_default_content_for_file()` 行 1542-1622

## 变更范围

- **唯一源码文件**: `octoagent/packages/core/src/octoagent/core/behavior_workspace.py`
- **唯一变更函数**: `_default_content_for_file()` (行 1542-1622)
- **测试文件 1**: `octoagent/packages/core/tests/test_behavior_workspace.py`
- **测试文件 2**: `octoagent/apps/gateway/tests/test_butler_behavior.py`

---

## Phase 1: P1 模板 -- 核心角色、安全与首次体验

**Purpose**: 改进对 Agent 行为影响最大的 5 个模板文件（7 个模板文本），建立角色锚点、安全边界和首次体验入口

### US1: 新 Agent 首次启动时获得充分的角色与协作指令

- [x] T001 [P] [US1] 重写 AGENTS.md Butler 版模板
  - **文件**: `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` -- `_default_content_for_file()` 函数内 `file_id == "AGENTS.md"` 且 `is_worker_profile=False` 分支（行 1558-1566）
  - **FR**: FR-001, FR-003, FR-034, FR-035, FR-036
  - **验收标准**:
    1. 内容包含角色定位与三层架构感知（Butler/Worker/Subagent）
    2. 内容包含委派决策框架（自行处理 vs 委派 Worker 的判断准则）
    3. 内容包含安全红线（不可执行的动作类型）
    4. 内容包含内存与存储协议（事实/偏好/秘密的存放位置指引）
    5. 内容包含 A2A 状态机交互感知
    6. `len()` <= 3200 且 >= 960（30%+ 利用率）
    7. 使用中文散文 + 英文代码标识符双语规范
    8. 反映 OctoAgent 实际架构能力
  - **复杂度**: 中
  - **可并行**: 是（与 T002 并行）

- [x] T002 [P] [US1] 重写 AGENTS.md Worker 版模板
  - **文件**: `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` -- `_default_content_for_file()` 函数内 `file_id == "AGENTS.md"` 且 `is_worker_profile=True` 分支（行 1550-1557）
  - **FR**: FR-002, FR-003, FR-034, FR-035, FR-036
  - **验收标准**:
    1. 内容包含 Worker 角色定位与自主范围
    2. 内容包含与 Butler 的协作协议（接收委派、报告完成/失败）
    3. 内容包含任务执行纪律（围绕 delegate objective 执行）
    4. 内容包含 Subagent 创建的判断准则
    5. `len()` <= 3200 且 >= 960
    6. 使用双语规范，反映 OctoAgent 架构
  - **复杂度**: 中
  - **可并行**: 是（与 T001 并行）

### US3: Agent 工具使用遵循清晰的优先级和安全规范

- [x] T003 [P] [US3] 重写 TOOLS.md 模板
  - **文件**: `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` -- `_default_content_for_file()` 函数内 `file_id == "TOOLS.md"` 分支（行 1583-1595）
  - **FR**: FR-013, FR-014, FR-015, FR-016, FR-017, FR-034, FR-035, FR-036
  - **验收标准**:
    1. 内容包含工具选择优先级规范（至少 3 级：受治理工具 > terminal > 外部调用）
    2. 内容包含 secrets 安全边界规范（不写入 behavior files / secret-bindings.json 值字段 / LLM 上下文）
    3. 内容包含 delegate 信息整理规范（objective + 上下文 + 工具边界）
    4. 内容包含读写场景快速指引
    5. `len()` <= 3200 且 >= 960
  - **复杂度**: 中
  - **可并行**: 是

### US2: 引导仪式帮助用户完成初始化设置

- [x] T004 [P] [US2] 重写 BOOTSTRAP.md 模板
  - **文件**: `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` -- `_default_content_for_file()` 函数内 `file_id == "BOOTSTRAP.md"` 分支（行 1596-1605）
  - **FR**: FR-018, FR-019, FR-020, FR-021, FR-034, FR-035, FR-036
  - **验收标准**:
    1. 内容包含编号引导步骤序列（不少于 4 步），每步包含提问内容、预期回答类型、数据存储去向
    2. 内容在最后包含完成标记机制说明（behavior.write_file 替换为 `<!-- COMPLETED -->`）
    3. 内容在引导开始前包含简短的自我介绍话术模板
    4. 至少覆盖 5 个维度：称呼、Agent 名称、性格偏好、时区/地点、长期偏好
    5. `len()` <= 2200 且 >= 660
    6. 关键词 `"完成引导"` 和 `"<!-- COMPLETED -->"` 必须保留（已有测试依赖）
  - **复杂度**: 中
  - **可并行**: 是

### US4: Agent 具有稳定的人格和沟通风格

- [x] T005 [P] [US4] 重写 SOUL.md 模板
  - **文件**: `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` -- `_default_content_for_file()` 函数内 `file_id == "SOUL.md"` 分支（行 1606-1609）
  - **FR**: FR-022, FR-023, FR-024, FR-025, FR-034, FR-035
  - **验收标准**:
    1. 内容包含核心价值观列表（不少于 3 条），语义覆盖"结论优先""不装懂""边界明确"
    2. 内容包含沟通风格原则（稳定、可解释、协作）
    3. 内容包含认知边界声明（承认不确定或不知道的场景）
    4. `len()` <= 1600 且 >= 480
  - **复杂度**: 低
  - **可并行**: 是

- [x] T006 [P] [US4] 重写 IDENTITY.md Butler 版模板
  - **文件**: `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` -- `_default_content_for_file()` 函数内 `file_id == "IDENTITY.md"` 且 `is_worker_profile=False` 分支（行 1610-1616）
  - **FR**: FR-026, FR-027, FR-028, FR-034, FR-035, FR-036
  - **验收标准**:
    1. 内容包含结构化身份字段：名称（`{agent_name}` 动态插值）、角色定位（默认会话 Agent）、表达风格占位
    2. 内容包含自我修改权限说明（可提出 behavior proposal，默认不静默改写）
    3. `len()` <= 1600 且 >= 480
  - **复杂度**: 低
  - **可并行**: 是（与 T007 并行）

- [x] T007 [P] [US4] 重写 IDENTITY.md Worker 版模板
  - **文件**: `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` -- `_default_content_for_file()` 函数内 `file_id == "IDENTITY.md"` 且 `is_worker_profile=True` 分支（行 1610-1616）
  - **FR**: FR-026, FR-027, FR-028, FR-034, FR-035, FR-036
  - **验收标准**:
    1. 内容包含结构化身份字段：名称（`{agent_name}` 动态插值）、角色定位（specialist worker）、表达风格占位
    2. 角色定位字段反映 specialist worker 身份
    3. 内容包含自我修改权限说明
    4. `len()` <= 1600 且 >= 480
  - **复杂度**: 低
  - **可并行**: 是（与 T006 并行）

**Checkpoint**: P1 阶段完成。7 个模板文本（5 个 file_id）已重写。Agent 角色锚点、安全边界、首次体验、人格定义已就位。

---

## Phase 2: P2 模板 -- 渐进式画像与长任务自检

**Purpose**: 改进用户/项目画像、知识管理和长任务自检模板，提供结构化框架

### US5: 用户画像和项目上下文有渐进式结构化模板

- [x] T008 [P] [US5] 重写 USER.md 模板
  - **文件**: `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` -- `_default_content_for_file()` 函数内 `file_id == "USER.md"` 分支（行 1567-1572）
  - **FR**: FR-004, FR-005, FR-006, FR-034, FR-035
  - **验收标准**:
    1. 内容包含渐进式用户画像框架，至少覆盖：基本信息区、沟通偏好区、工作习惯区
    2. 每个区域提供占位提示文字
    3. 在显著位置标注存储边界：稳定事实应通过 Memory 服务存储
    4. `len()` <= 1800 且 >= 540
  - **复杂度**: 低
  - **可并行**: 是（与 T009 并行）

- [x] T009 [P] [US5] 重写 PROJECT.md 模板
  - **文件**: `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` -- `_default_content_for_file()` 函数内 `file_id == "PROJECT.md"` 分支（行 1573-1577）
  - **FR**: FR-007, FR-008, FR-009, FR-034, FR-035
  - **验收标准**:
    1. 内容包含项目元信息框架，至少覆盖：项目目标、关键术语表、核心目录结构、验收标准
    2. 保留 `{project_label}` 动态插值
    3. `len()` <= 2400 且 >= 720
  - **复杂度**: 低
  - **可并行**: 是（与 T008 并行）

### US6: 知识管理有入口地图而非内容堆砌

- [x] T010 [P] [US6] 重写 KNOWLEDGE.md 模板
  - **文件**: `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` -- `_default_content_for_file()` 函数内 `file_id == "KNOWLEDGE.md"` 分支（行 1578-1582）
  - **FR**: FR-010, FR-011, FR-012, FR-034, FR-035
  - **验收标准**:
    1. 内容包含知识入口地图框架，至少覆盖：canonical 文档引用区、API/接口文档区、运维/部署知识区
    2. 内容明确指引"引用入口而非复制正文"原则
    3. 内容包含更新触发条件提示
    4. `len()` <= 2200 且 >= 660
  - **复杂度**: 低
  - **可并行**: 是

### US7: 长任务有结构化的自检和进度报告规范

- [x] T011 [P] [US7] 重写 HEARTBEAT.md 模板
  - **文件**: `octoagent/packages/core/src/octoagent/core/behavior_workspace.py` -- `_default_content_for_file()` 函数内 `file_id == "HEARTBEAT.md"` 分支（行 1617-1621）
  - **FR**: FR-029, FR-030, FR-031, FR-032, FR-034, FR-035
  - **验收标准**:
    1. 内容包含自检触发条件说明
    2. 内容包含自检清单（至少 4 项）：任务进度、是否偏离目标、工具使用是否合理、是否应收口
    3. 内容包含进度报告要素指引（完成了什么、遇到什么阻碍、下一步计划）
    4. 内容包含收口判断标准
    5. `len()` <= 1600 且 >= 480
  - **复杂度**: 低
  - **可并行**: 是

**Checkpoint**: 全部 11 个模板文本（9 个 file_id）已重写。

---

## Phase 3: 测试更新与全量验证

**Purpose**: 更新已有测试中因模板内容变化而失效的断言，新增预算合规和内容域覆盖测试，运行全量回归

### 已有测试适配

- [x] T012 更新 `test_butler_behavior.py` 中的硬编码模板断言
  - **文件**: `octoagent/apps/gateway/tests/test_butler_behavior.py`
  - **FR**: FR-036（函数签名兼容性隐含测试通过）
  - **需要更新的断言**:
    1. 行 215: `assert "specialist Worker" in pack.files[0].content` -- Worker 版 AGENTS.md 内容变化，确认新内容仍包含 "specialist Worker"（或等价表述）
    2. 行 216: `assert "Butler 负责默认会话总控" in pack.files[0].content` -- 确认新内容仍包含此关键语义（或更新断言关键词）
    3. 行 420: `assert "web / filesystem / terminal" in files["AGENTS.md"].content` -- Butler 版 AGENTS.md 内容变化，确认新内容是否保留此精确表述或需更新
    4. 行 421: `assert "sticky worker lane" in files["AGENTS.md"].content` -- 同上
    5. 行 422: `assert "specialist worker lane" in files["AGENTS.md"].content` -- 同上
    6. 行 423: `assert "不要把用户原话原封不动转发过去" in files["TOOLS.md"].content` -- TOOLS.md 内容变化，确认新内容是否保留或需更新
  - **验收标准**:
    1. 所有断言与新模板内容一致（关键词匹配而非精确字符串匹配）
    2. 断言仍然验证核心语义不变（角色差异化、委派原则、工具规范）
    3. `uv run pytest apps/gateway/tests/test_butler_behavior.py -v` 全部通过
  - **复杂度**: 中
  - **可并行**: 否（依赖 T001-T003 完成后才知道新内容）

- [x] T013 确认 `test_behavior_workspace.py` 中已有断言不受影响
  - **文件**: `octoagent/packages/core/tests/test_behavior_workspace.py`
  - **需要确认的断言**:
    1. 行 204: `assert "完成引导" in content` -- BOOTSTRAP.md 新模板必须保留此关键词（T004 验收标准第 6 条）
    2. 行 205: `assert "<!-- COMPLETED -->" in content` -- 同上
  - **验收标准**:
    1. 这两个断言无需修改（T004 已确保保留关键词）
    2. `uv run pytest packages/core/tests/test_behavior_workspace.py -v` 全部通过
  - **复杂度**: 低
  - **可并行**: 否（依赖 T004 完成）

### 新增测试

- [x] T014 新增全量字符预算合规参数化测试
  - **文件**: `octoagent/packages/core/tests/test_behavior_workspace.py`
  - **FR**: FR-003, FR-006, FR-009, FR-012, FR-017, FR-021, FR-025, FR-028, FR-032, FR-033
  - **内容**:
    ```python
    @pytest.mark.parametrize("file_id,is_worker", [
        ("AGENTS.md", False), ("AGENTS.md", True),
        ("USER.md", False), ("PROJECT.md", False),
        ("KNOWLEDGE.md", False), ("TOOLS.md", False),
        ("BOOTSTRAP.md", False), ("SOUL.md", False),
        ("IDENTITY.md", False), ("IDENTITY.md", True),
        ("HEARTBEAT.md", False),
    ])
    def test_default_template_within_budget(file_id, is_worker):
        ...
    ```
    - 验证 `len(content) <= BEHAVIOR_FILE_BUDGETS[file_id]`
    - 验证 `len(content) >= budget * 0.3`（SHOULD 级下限）
  - **验收标准**:
    1. 11 个参数化用例全部通过
    2. 无预算溢出，无内容过少
  - **复杂度**: 低
  - **可并行**: 否（依赖 T001-T011 全部完成）

- [x] T015 新增内容域覆盖关键词测试
  - **文件**: `octoagent/packages/core/tests/test_behavior_workspace.py`
  - **FR**: FR-001 至 FR-032 中的内容域要求
  - **内容**: 为每个模板新增关键词子字符串匹配测试，确保内容域完整。至少覆盖：
    - AGENTS.md Butler: "委派" / "delegate", "Worker", "安全" / "红线", "Memory" / "记忆"
    - AGENTS.md Worker: "Worker", "Butler", "Subagent" / "子代理", "objective" / "目标"
    - TOOLS.md: "优先级", "secrets" / "SecretService", "delegate", "filesystem" / "读"
    - BOOTSTRAP.md: "完成引导", "<!-- COMPLETED -->", "称呼" / "名称"
    - SOUL.md: "价值观" / "原则", "边界" / "不确定"
    - IDENTITY.md Butler: "agent_name" 插值结果, "默认会话" / "Butler", "proposal"
    - IDENTITY.md Worker: "agent_name" 插值结果, "specialist" / "worker", "proposal"
    - USER.md: "Memory" / "记忆", "偏好" / "习惯"
    - PROJECT.md: "project_label" 插值结果, "术语" / "目录" / "验收"
    - KNOWLEDGE.md: "引用" / "入口", "canonical", "更新"
    - HEARTBEAT.md: "自检" / "检查", "进度" / "报告", "收口"
  - **验收标准**:
    1. 所有关键词断言通过
    2. 测试采用子字符串匹配而非精确结构验证
  - **复杂度**: 中
  - **可并行**: 否（依赖 T001-T011 全部完成）

### 全量回归验证

- [x] T016 运行全量测试回归并验证通过
  - **命令**:
    ```bash
    cd octoagent && uv run pytest packages/core/tests/test_behavior_workspace.py -v
    cd octoagent && uv run pytest apps/gateway/tests/test_butler_behavior.py -v
    ```
  - **FR**: SC-004
  - **验收标准**:
    1. `test_behavior_workspace.py` 全部通过（含新增的预算合规和内容域覆盖测试）
    2. `test_butler_behavior.py` 全部通过（含更新后的断言）
    3. 无 warning、无 skip、无 xfail
  - **复杂度**: 低
  - **可并行**: 否（依赖 T012-T015 全部完成）

**Checkpoint**: 全部测试通过，Feature 065 实现完成。

---

## FR 覆盖映射表

| FR | 描述 | Task |
|----|------|------|
| FR-001 | AGENTS.md Butler 版内容域 | T001 |
| FR-002 | AGENTS.md Worker 版内容域 | T002 |
| FR-003 | AGENTS.md 字符预算 <= 3200 | T001, T002, T014 |
| FR-004 | USER.md 渐进式画像框架 | T008 |
| FR-005 | USER.md 存储边界提示 | T008 |
| FR-006 | USER.md 字符预算 <= 1800 | T008, T014 |
| FR-007 | PROJECT.md 项目元信息框架 | T009 |
| FR-008 | PROJECT.md 保留 project_label 插值 | T009 |
| FR-009 | PROJECT.md 字符预算 <= 2400 | T009, T014 |
| FR-010 | KNOWLEDGE.md 知识入口地图框架 | T010 |
| FR-011 | KNOWLEDGE.md 引用入口原则 | T010 |
| FR-012 | KNOWLEDGE.md 字符预算 <= 2200 | T010, T014 |
| FR-013 | TOOLS.md 工具选择优先级 | T003 |
| FR-014 | TOOLS.md secrets 安全边界 | T003 |
| FR-015 | TOOLS.md delegate 信息整理规范 | T003 |
| FR-016 | TOOLS.md 读写场景指引 | T003 |
| FR-017 | TOOLS.md 字符预算 <= 3200 | T003, T014 |
| FR-018 | BOOTSTRAP.md 编号引导步骤序列 | T004 |
| FR-019 | BOOTSTRAP.md 完成标记机制 | T004 |
| FR-020 | BOOTSTRAP.md 自我介绍话术 | T004 |
| FR-021 | BOOTSTRAP.md 字符预算 <= 2200 | T004, T014 |
| FR-022 | SOUL.md 核心价值观列表 | T005 |
| FR-023 | SOUL.md 沟通风格原则 | T005 |
| FR-024 | SOUL.md 认知边界声明 | T005 |
| FR-025 | SOUL.md 字符预算 <= 1600 | T005, T014 |
| FR-026 | IDENTITY.md 结构化身份字段 | T006, T007 |
| FR-027 | IDENTITY.md 自我修改权限说明 | T006, T007 |
| FR-028 | IDENTITY.md 字符预算 <= 1600 | T006, T007, T014 |
| FR-029 | HEARTBEAT.md 自检触发条件 | T011 |
| FR-030 | HEARTBEAT.md 自检清单 >= 4 项 | T011 |
| FR-031 | HEARTBEAT.md 进度报告要素 | T011 |
| FR-032 | HEARTBEAT.md 字符预算 <= 1600 | T011, T014 |
| FR-033 | 全量预算利用率 40%+ / 95%- | T014 |
| FR-034 | 中文散文 + 英文标识符双语规范 | T001-T011 |
| FR-035 | 反映 OctoAgent 实际架构能力 | T001-T011 |
| FR-036 | 函数签名兼容性不变 | T001-T011, T012 |

**覆盖率**: 36/36 FR = 100%

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (T001-T007): 无前置依赖，可立即开始
Phase 2 (T008-T011): 无前置依赖，可与 Phase 1 并行
Phase 3 (T012-T016): 依赖 Phase 1 + Phase 2 全部完成
```

### User Story 间依赖

- **US1-US7 之间无依赖**：所有模板改进互相独立，均修改同一函数的不同分支
- **注意**：虽然逻辑上独立，但物理上集中在同一函数，并行编辑需注意合并冲突。建议串行执行或由同一开发者批量完成

### Story 内部并行机会

- US1 内部: T001 (Butler) 和 T002 (Worker) 可并行
- US4 内部: T005 (SOUL), T006 (IDENTITY Butler), T007 (IDENTITY Worker) 可并行
- US5 内部: T008 (USER) 和 T009 (PROJECT) 可并行
- Phase 1 和 Phase 2 之间: 所有模板任务 (T001-T011) 理论上可并行

### 推荐实现策略

**Sequential by Priority（推荐）**:

由于所有变更集中在单一函数体内，推荐由同一开发者按以下顺序串行完成：

1. **P1 模板** (T001-T007): 逐个重写，每个完成后立即 `len()` 验证预算
2. **P2 模板** (T008-T011): 逐个重写，同样逐个验证预算
3. **测试适配** (T012-T013): 更新断言与新内容匹配
4. **新增测试** (T014-T015): 添加预算合规和内容域覆盖测试
5. **全量回归** (T016): 运行全部测试确认通过

预计单人 1-2 小时可完成全部 16 个任务。

---

## Notes

- 所有 [P] 标记仅表示逻辑上可并行，由于变更集中在单一函数，实际建议串行执行
- 每个模板编写后立即运行 `len()` 验证，不要等到最后才检查预算
- T004 (BOOTSTRAP.md) 特别注意保留 `"完成引导"` 和 `"<!-- COMPLETED -->"` 关键词
- T001/T002 (AGENTS.md) 的新内容应尽量保留旧模板中被测试断言依赖的语义关键词
- 如果 T012 中发现旧断言语义已被新模板完全覆盖（只是措辞变化），更新断言为新措辞即可
