---
name: session-logs
description: "Search and analyze session logs and conversation history using OctoAgent Event Store and SQLite. Use when: user asks about prior conversations, session history, task execution logs, or event timeline."
version: 1.0.0
author: OctoAgent
tags:
  - session
  - logs
  - history
  - events
tools_required:
  - task.inspect
  - runtime.inspect
  - sessions.list
  - session.status
---

# Session Logs

Search and analyze conversation history and task execution logs through OctoAgent's Event Store.

## When to Use

- User asks about prior conversations or session history
- Debugging task execution flow
- Reviewing what happened in a specific session
- Finding historical context from past interactions

## Data Sources

OctoAgent stores all session data in SQLite via the Event Store:

- **Tasks**: task_id, status, timestamps, requester info
- **Events**: append-only event log (tool calls, state transitions, LLM calls)
- **Sessions**: agent session metadata and conversation turns

## Common Queries

### List Recent Sessions

Use the `sessions.list` tool to see recent sessions:

```
sessions.list limit=10
```

### Check Session Status

```
session.status
```

### Inspect a Specific Task

```
task.inspect task_id=<task-id>
```

This returns:
- Task status and timestamps
- Recent events (tool calls, state changes)
- Execution session details

### View Runtime State

```
runtime.inspect
```

Returns current runtime information including active tasks, worker states, and system health.

## Event Types

The Event Store records these event types:

- `TASK_CREATED` / `TASK_COMPLETED` / `TASK_FAILED` - Task lifecycle
- `TOOL_CALL_STARTED` / `TOOL_CALL_COMPLETED` / `TOOL_CALL_FAILED` - Tool execution
- `STATE_CHANGED` - Task state transitions
- `LLM_CALL_STARTED` / `LLM_CALL_COMPLETED` - Model calls
- `APPROVAL_REQUESTED` / `APPROVAL_GRANTED` / `APPROVAL_DENIED` - Approval flow

## Tips

- Events are append-only and never deleted
- Use `task.inspect` for detailed per-task event timeline
- Session metadata includes loaded skills and tool selection state
- Cost and token usage data is embedded in LLM call events
