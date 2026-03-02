# 接口契约: Feature 005 — Skills API

**Feature Branch**: `codex/feat-005-pydantic-skill-runner`
**状态**: LOCKED（M1 阶段）

---

## 1. StructuredModelClientProtocol

```python
class StructuredModelClientProtocol(Protocol):
    async def generate(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        prompt: str,
        feedback: list[ToolFeedbackMessage],
        attempt: int,
        step: int,
    ) -> SkillOutputEnvelope: ...
```

行为约定:
- 返回值必须满足 `SkillOutputEnvelope`。
- 失败抛异常，不返回半结构化对象。

---

## 2. SkillRunnerProtocol

```python
class SkillRunnerProtocol(Protocol):
    async def run(
        self,
        *,
        manifest: SkillManifest,
        execution_context: SkillExecutionContext,
        skill_input: BaseModel,
        prompt: str,
    ) -> SkillRunResult: ...
```

行为约定:
1. 必须执行 InputModel 校验。
2. 必须在每步执行 OutputModel 校验。
3. `tool_calls` 必须经 `ToolBrokerProtocol.execute()` 执行。
4. 必须遵守 `retry_policy`、`loop_guard`、`context_budget`。

---

## 3. SkillRegistryProtocol

```python
class SkillRegistryProtocol(Protocol):
    def register(self, manifest: SkillManifest, prompt_template: str) -> None: ...
    def get(self, skill_id: str) -> RegisteredSkill: ...
    def list_skills(self) -> list[SkillManifest]: ...
    def unregister(self, skill_id: str) -> bool: ...
```

行为约定:
- `skill_id` 全局唯一，重复注册抛出异常。
- `get()` 查询不存在 skill 必须抛出显式异常。

---

## 4. Runner 与 ToolBroker 交互契约

Runner -> ToolBroker 调用签名固定为:

```python
await tool_broker.execute(
    tool_name=tool_call.tool_name,
    args=tool_call.arguments,
    context=ExecutionContext(...),
)
```

消费字段（锁定）:
- `output`
- `is_error`
- `error`
- `duration`
- `artifact_ref`

Runner 不依赖 ToolBroker 内部 hook/event 细节。

---

## 5. 生命周期 Hook 契约

```python
class SkillRunnerHook(Protocol):
    async def skill_start(self, manifest: SkillManifest, context: SkillExecutionContext) -> None: ...
    async def skill_end(self, manifest: SkillManifest, context: SkillExecutionContext, result: SkillRunResult) -> None: ...
    async def before_llm_call(self, manifest: SkillManifest, attempt: int, step: int) -> None: ...
    async def after_llm_call(self, manifest: SkillManifest, output: SkillOutputEnvelope) -> None: ...
    async def before_tool_execute(self, tool_name: str, arguments: dict[str, Any]) -> None: ...
    async def after_tool_execute(self, feedback: ToolFeedbackMessage) -> None: ...
```

默认 no-op，不可阻断主流程；抛错时按 log-and-continue 降级。

---

## 6. 失败语义契约

- `SkillInputError`: 输入不合法，立即失败。
- `SkillValidationError`: 输出校验失败，可重试。
- `SkillRepeatError`: 可重试失败（模型可修复）。
- `SkillToolExecutionError`: 工具执行失败（可恢复或不可恢复）。
- `SkillLoopDetectedError`: 循环检测触发，失败终止。
