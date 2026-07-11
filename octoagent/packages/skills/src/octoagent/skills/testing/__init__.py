"""OctoAgent Skills 测试共享件（F138，随包发布的 testing 命名空间）。

仿 pydantic-ai ``pydantic_ai/models/test.py`` 范式：**可 import 但生产从不 wire**。
供三方消费者（``apps/gateway`` e2e_live / OctoBench runner / L4 单测）跨包共享
脚本化 ``StructuredModelClientProtocol`` 实现，替代此前埋在
``packages/skills/tests/conftest.py`` 无法跨包 import 的困境。

Constitution #9 边界（构造性不可达，非约定）：
- 本模块只被测试 / benchmark 代码 import；
- 进入生产决策环的唯一通道是 ``OctoHarness(model_client=...)`` DI，默认 None；
- 生产入口 ``main.py`` 只传 ``project_root``，不存在任何 env / yaml 开关能启用
  脚本化路径 → 生产 SkillRunner 恒用真 ``ProviderModelClient``。

依赖面：仅 skills 包自身模型 + 标准库（零新增第三方依赖）。

Phase 2 deferred（spec §2.2）：``SchemaTestAdapter``（TestModel 等价，按工具
JSON schema 自动填合法参数扫 63 工具广度）尚未落地，届时加入本模块。
"""

from .scripted_model import ScriptedModelClient

__all__ = ["ScriptedModelClient"]
