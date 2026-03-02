# Quickstart: Feature 005 — Pydantic Skill Runner

## 1. 安装与同步

```bash
cd octoagent
uv sync
```

## 2. 运行 Skills 包测试

```bash
uv run pytest packages/skills/tests -v
```

## 3. 运行与 004 契约相关联测试

```bash
uv run pytest packages/skills/tests/test_runner.py -v
```

## 4. 运行全量核心回归（推荐）

```bash
uv run pytest packages/skills/tests packages/tooling/tests packages/core/tests -v
```

## 5. Lint 检查

```bash
uv run ruff check packages/skills/src packages/skills/tests
```

## 6. 最小示例

```python
from pydantic import BaseModel

from octoagent.skills import (
    SkillExecutionContext,
    SkillManifest,
    SkillRegistry,
)

class EchoInput(BaseModel):
    text: str

# 省略 OutputModel 与 model client mock 的构造

ctx = SkillExecutionContext(task_id="t1", trace_id="tr1", caller="worker")
registry = SkillRegistry()
manifest = SkillManifest(
    skill_id="demo.echo",
    version="0.1.0",
    input_model=EchoInput,
    output_model=...,  # 实际使用时传具体 BaseModel 子类
    model_alias="main",
    tools_allowed=[],
)
registry.register(manifest, prompt_template="echo user input")
```
