"""F113：AgentContextService 的 Memory-service getter 职责簇 mixin。

职责边界：记忆相关后台服务的 lazy 构造与缓存（consolidation / derived extraction /
ToM extraction / profile generator / session memory extractor / reranker）+
session 记忆提取 fire-and-forget spawn。新增"get_*_service 惰性单例"放这里；
recall 查询逻辑见 agent_context_memory_recall，实体 ensure 见
agent_context_entity_ensure，防止职责再次堆回单文件。

依赖约定（由继承类 AgentContextService 提供）：
- ``self._stores``：StoreGroup（conn 等）
- ``self._llm_service`` / ``self._project_root`` / ``self._provider_router`` /
  ``self._memory_runtime``：__init__ 注入
- ``self._shared_background_tasks``：类属性（main.py 启动注入，shutdown drain 用）
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from octoagent.core.models import (
    Project,
)
from octoagent.memory import (
    MemoryService,
)

# 路径不变（含 orchestrator 引用的 _dynamic_transcript_limit 等私有名）。redundant-alias
# 形式（X as X）向 ruff/类型检查器声明显式 re-export。

log = structlog.get_logger()


class AgentContextMemoryServiceMixin:
    """MemoryService 职责簇：见模块 docstring。

    本 mixin 不持有状态，所有依赖（self._stores 等）由继承类 AgentContextService 提供。
    方法签名、返回值与副作用与拆分前完全等价（F113 行为零变更）。
    """

    _stores: "Any"

    async def get_memory_service(
        self,
        *,
        project: Project | None,
    ) -> MemoryService:
        return await self._memory_runtime.memory_service_for_scope(
            project=project,
        )


    def get_consolidation_service(self):
        """获取 ConsolidationService 实例（Feature 065）。

        延迟创建，首次调用时实例化。若 LLM 服务不可用则返回 None。
        Phase 2: 自动注入 DerivedExtractionService。
        """
        if not hasattr(self, "_consolidation_service"):
            try:
                from octoagent.memory import SqliteMemoryStore
                from octoagent.gateway.services.inference.consolidation_service import ConsolidationService

                memory_store = SqliteMemoryStore(self._stores.conn)
                llm_service = self._llm_service

                # Phase 2: 创建 DerivedExtractionService 并注入
                derived_service = self.get_derived_extraction_service()
                # Phase 3: 创建 ToMExtractionService 并注入
                tom_service = self.get_tom_extraction_service()

                self._consolidation_service = ConsolidationService(
                    memory_store=memory_store,
                    llm_service=llm_service,
                    project_root=self._project_root,
                    derived_extraction_service=derived_service,
                    tom_extraction_service=tom_service,
                )
            except Exception:
                self._consolidation_service = None
        return self._consolidation_service


    def get_derived_extraction_service(self):
        """获取 DerivedExtractionService 实例（Feature 065 Phase 2, US-4）。

        延迟创建，首次调用时实例化。
        """
        if not hasattr(self, "_derived_extraction_service"):
            try:
                from octoagent.memory import SqliteMemoryStore
                from octoagent.gateway.services.inference.derived_extraction_service import DerivedExtractionService

                memory_store = SqliteMemoryStore(self._stores.conn)
                llm_service = self._llm_service
                self._derived_extraction_service = DerivedExtractionService(
                    memory_store=memory_store,
                    llm_service=llm_service,
                    project_root=self._project_root,
                )
            except Exception:
                self._derived_extraction_service = None
        return self._derived_extraction_service


    def get_tom_extraction_service(self):
        """获取 ToMExtractionService 实例（Feature 065 Phase 3, US-7）。

        延迟创建，首次调用时实例化。
        """
        if not hasattr(self, "_tom_extraction_service"):
            try:
                from octoagent.memory import SqliteMemoryStore
                from octoagent.gateway.services.inference.tom_extraction_service import ToMExtractionService

                memory_store = SqliteMemoryStore(self._stores.conn)
                llm_service = self._llm_service
                self._tom_extraction_service = ToMExtractionService(
                    memory_store=memory_store,
                    llm_service=llm_service,
                    project_root=self._project_root,
                )
            except Exception:
                self._tom_extraction_service = None
        return self._tom_extraction_service


    def get_profile_generator_service(self):
        """获取 ProfileGeneratorService 实例（Feature 065 Phase 3, US-9）。

        延迟创建，首次调用时实例化。
        """
        if not hasattr(self, "_profile_generator_service"):
            try:
                from octoagent.memory import SqliteMemoryStore
                from octoagent.gateway.services.inference.profile_generator_service import ProfileGeneratorService

                memory_store = SqliteMemoryStore(self._stores.conn)
                llm_service = self._llm_service
                self._profile_generator_service = ProfileGeneratorService(
                    memory_store=memory_store,
                    llm_service=llm_service,
                    project_root=self._project_root,
                )
            except Exception:
                self._profile_generator_service = None
        return self._profile_generator_service


    def get_session_memory_extractor(self):
        """获取 SessionMemoryExtractor 实例（Feature 067）。

        延迟创建，首次调用时实例化。若依赖不可用则返回 None。
        LLM service 每次从实例或类变量动态获取，避免构造时序问题。
        """
        if not hasattr(self, "_session_memory_extractor"):
            try:
                from .session_memory_extractor import SessionMemoryExtractor

                self._session_memory_extractor = SessionMemoryExtractor(
                    agent_context_store=self._stores.agent_context_store,
                    memory_service_factory=self.get_memory_service,
                    llm_service=self._llm_service or self._shared_llm_service,
                    project_root=self._project_root,
                    llm_service_resolver=lambda: self._llm_service or self._shared_llm_service,
                )
            except Exception:
                log.warning("session_memory_extractor_init_failed", exc_info=True)
                self._session_memory_extractor = None
        return self._session_memory_extractor


    def _spawn_session_memory_extraction(
        self,
        *,
        agent_session: Any,
        project: Any | None,
    ) -> asyncio.Task[Any] | None:
        """fire-and-forget 触发 Session 记忆提取，并注册进 harness shutdown drain 集合。

        注册进 ``_shared_background_tasks`` 后，harness shutdown 会在关闭 DB 连接前
        统一 await/cancel（octo_harness.shutdown，drain timeout 10s ≫ 提取 ~2s），
        避免在途提取落库命中已关闭连接，也避免最后一轮提取永久丢失（根因修复）。
        SessionMemoryExtractor 内部对残留的 closed-conn 竞态仍有 defense-in-depth
        降级（drain 超时被 cancel 的极端情形）。返回创建的 task（便于测试断言注册）。
        """
        extractor = self.get_session_memory_extractor()
        if extractor is None:
            log.warning(
                "session_memory_extractor_unavailable",
                llm_service_set=self._llm_service is not None,
                shared_llm_service_set=self._shared_llm_service is not None,
            )
            return None

        task = asyncio.create_task(
            extractor.extract_and_commit(
                agent_session=agent_session,
                project=project,
            )
        )

        # 注册进 shutdown drain 集合（若已注入）。集合是 set，add/discard 是 O(1)；
        # discard 回调在 task 结束后自动移除，避免集合无界增长。
        background_tasks = self._shared_background_tasks
        if isinstance(background_tasks, set):
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)

        def _on_extraction_done(t: asyncio.Task[Any]) -> None:
            # shutdown drain 超时会 cancel task；cancelled 时 t.exception() 会
            # raise CancelledError，必须先判 cancelled 再取 exception。
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                log.error(
                    "session_memory_extraction_task_failed",
                    error=str(exc),
                )

        task.add_done_callback(_on_extraction_done)
        return task


    def get_reranker_service(self):
        """获取 ModelRerankerService 实例（Feature 065 Phase 2, US-6）。

        延迟创建，首次调用时实例化。后台 warmup 模型。
        """
        if not hasattr(self, "_reranker_service"):
            try:
                from octoagent.gateway.services.inference.model_reranker_service import (
                    ModelRerankerService,
                )

                self._reranker_service = ModelRerankerService(auto_load=True)
            except Exception:
                self._reranker_service = None
        return self._reranker_service
