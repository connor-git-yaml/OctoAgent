"""OctoAgent SDK — 轻量级 Agent 框架。

3 行代码创建智能 Agent：

    from octoagent_sdk import Agent
    agent = Agent(model="gpt-4o")
    result = await agent.run("帮我查深圳天气")

带工具：

    from octoagent_sdk import Agent, tool

    @tool
    async def search(query: str) -> str:
        \"\"\"搜索网页\"\"\"
        return f"搜索结果: {query}"

    agent = Agent(model="gpt-4o", tools=[search])
    result = await agent.run("搜索 Python 教程")
"""

from ._agent import Agent, AgentResult, AgentChunk
from ._tool import tool

__all__ = ["Agent", "AgentResult", "AgentChunk", "tool"]
