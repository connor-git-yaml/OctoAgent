"""TaskService 用户可见答复清洗测试。"""

from octoagent.gateway.services.task_service import TaskService


def test_sanitize_user_visible_response_strips_inline_tool_transcript_and_json() -> None:
    raw = """先给结论：我会先尝试直接找到当前项目 README。 to=memory.search 非结构化json
{"query":"README 当前项目 README 开头 项目一句话","scopes":["workspace"],"limit":10}to=memory.search 更多json
{"matches":[],"exhaustive":true}to=memory.read id 列表json
{"ids":["control-plane-project-default"]}
最终结论：这个项目一句话是个人 AI OS。"""

    sanitized = TaskService._sanitize_user_visible_response(raw)

    assert "to=memory.search" not in sanitized
    assert '"query"' not in sanitized
    assert '"matches"' not in sanitized
    assert '"ids"' not in sanitized
    assert "先给结论：我会先尝试直接找到当前项目 README。" in sanitized
    assert "最终结论：这个项目一句话是个人 AI OS。" in sanitized


def test_sanitize_user_visible_response_keeps_regular_json_examples() -> None:
    raw = """配置如下：
```json
{"result":"ok","ids":[1,2,3]}
```
按这个格式返回即可。"""

    sanitized = TaskService._sanitize_user_visible_response(raw)

    assert sanitized == raw
