"""``octoagent.provider.testing``——provider 测试基础设施子包（随包发布）。

现含 pytest11 entry-point 插件 ``pytest_model_request_gate``（F137 硬闸 deny
布线主通道）。本 ``__init__`` 刻意保持零 import：插件模块在 pytest 启动极早期
经 entry point 加载，子包 ``__init__`` 不得引入额外依赖/副作用。
"""
