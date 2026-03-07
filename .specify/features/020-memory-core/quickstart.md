# Feature 020 Quickstart

## 安装 workspace 依赖

```bash
cd octoagent
uv sync
```

## 运行 Memory package 测试

```bash
cd octoagent
uv run pytest packages/memory/tests -q
```

## 运行关键集成测试

```bash
cd octoagent
uv run pytest packages/memory/tests/test_memory_service.py -q
```

## 运行 backend 插件化测试

```bash
cd octoagent
uv run pytest packages/memory/tests/test_memory_backends.py -q
```
