---
name: coding-agent
description: "Delegate coding tasks to OctoAgent Workers. Use when: (1) building/creating new features, (2) reviewing PRs, (3) refactoring codebases, (4) iterative coding that needs file exploration. NOT for: simple one-liner fixes (just edit), reading code (use filesystem tools directly)."
version: 1.0.0
author: OctoAgent
tags:
  - coding
  - development
  - worker
  - delegation
tools_required:
  - subagents.spawn
  - work.split
  - terminal.exec
---

# Coding Agent (OctoAgent Worker Delegation)

Delegate coding tasks to OctoAgent dev Workers via the delegation plane.

## When to Use

- Building/creating new features or apps
- Reviewing PRs or code changes
- Refactoring large codebases
- Iterative coding that needs file exploration and multi-step edits

## When NOT to Use

- Simple one-liner fixes -> edit directly with filesystem tools
- Just reading code -> use `filesystem.read_text` directly
- Non-code tasks -> use appropriate specialized skills

## Delegation Pattern

OctoAgent uses a Butler -> Worker delegation model. The Butler (main agent) dispatches
coding tasks to dev Workers who have full tool access.

### Quick Task (Single Worker)

Use `subagents.spawn` to create a dev worker:

```
subagents.spawn worker_type=dev objective="Add error handling to the API module"
```

The worker will:
1. Explore the codebase using filesystem tools
2. Make changes using terminal.exec
3. Report results back through the delegation plane

### Complex Task (Work Splitting)

For large tasks, split work into parallel sub-tasks:

```
work.split objective="Refactor auth module" subtasks=["Extract JWT logic", "Add refresh token support", "Update tests"]
```

Each subtask gets its own Worker that works independently.

## Monitoring Workers

```
subagents.list           # List all active workers
session.status           # Check current session status
```

## Best Practices

1. **Be specific in objectives** - Workers perform better with clear, bounded tasks
2. **Use work.split for parallelism** - Independent subtasks can run concurrently
3. **Monitor progress** - Check worker status and provide guidance if stuck
4. **Let Workers use their tools** - Don't micromanage; Workers have filesystem, terminal, and git access
5. **Review results** - Check Worker output before marking tasks complete

## Notes

- Workers inherit the project context from the Butler session
- Each Worker has its own execution context and tool access
- Workers report results through the Event Store
- Failed Workers can be retried with adjusted objectives
