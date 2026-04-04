"""LLMService -- Feature 002 版本

对齐 contracts/gateway-changes.md SS3。
支持 FallbackManager + AliasRegistry，返回 ModelCallResult。
保留 M0 EchoProvider/MockProvider/LLMResponse 供向后兼容（标记废弃）。
"""

import asyncio
import re
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from octoagent.core.models.agent_context import DEFAULT_PERMISSION_PRESET
from octoagent.provider import (
    AliasRegistry,
    EchoMessageAdapter,
    FallbackManager,
    ModelCallResult,
    TokenUsage,
)
from octoagent.skills import (
    SkillDiscovery,
    SkillExecutionContext,
    SkillManifest,
    SkillPermissionMode,
    SkillRunner,
    SkillRunStatus,
    extract_mounted_tool_names,
)
from octoagent.skills.limits import get_global_defaults, merge_usage_limits
from octoagent.skills.models import UsageLimits, _MAX_STEPS_HARD_CEILING
from octoagent.tooling.models import ToolSearchResult

from .tool_promotion import ToolPromotionService
from pydantic import BaseModel, Field

# Skill 注入格式常量（agent_context.py 依赖此格式解析和截断）
SKILL_SECTION_PREFIX = "--- Loaded Skill: "
SKILL_SECTION_SEPARATOR = "\n\n" + SKILL_SECTION_PREFIX

# ============================================================
# M0 遗留类型（废弃，保留供旧测试兼容）
# ============================================================


@dataclass
class LLMResponse:
    """LLM 调用响应 -- M0 遗留类型

    .. deprecated:: 0.2.0
        Feature 002 起使用 ``ModelCallResult`` 替代。
        保留此类仅供 M0 测试兼容，后续版本将删除。
        迁移指南: 将 ``LLMResponse`` 替换为 ``from octoagent.provider import ModelCallResult``。
    """

    content: str
    model_alias: str
    duration_ms: int
    token_usage: dict[str, int]

    def __post_init__(self):
        warnings.warn(
            "LLMResponse 已废弃，请使用 octoagent.provider.ModelCallResult 替代。"
            "此类将在 v0.3.0 移除。",
            DeprecationWarning,
            stacklevel=2,
        )


class LLMProvider(ABC):
    """LLM 提供者抽象接口 -- M0 遗留接口

    .. deprecated:: 0.2.0
        Feature 002 起使用 FallbackManager + EchoMessageAdapter 替代。
    """

    _SUPPRESS_LLM_PROVIDER_DEPRECATION_WARNING: bool = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if getattr(cls, "_SUPPRESS_LLM_PROVIDER_DEPRECATION_WARNING", False):
            return
        warnings.warn(
            f"{cls.__name__} 继承自已废弃的 LLMProvider，"
            "请迁移到 FallbackManager + EchoMessageAdapter 模式。",
            DeprecationWarning,
            stacklevel=2,
        )

    @abstractmethod
    async def call(self, prompt: str) -> LLMResponse:
        """调用 LLM"""
        ...


class EchoProvider(LLMProvider):
    """Echo 模式 -- 返回输入回声

    .. deprecated:: 0.2.0
        Feature 002 起使用 EchoMessageAdapter 替代。
        保留供 M0 旧测试兼容。
    """

    _SUPPRESS_LLM_PROVIDER_DEPRECATION_WARNING = True

    def __init__(self, model_alias: str = "echo") -> None:
        self._model_alias = model_alias

    async def call(self, prompt: str) -> LLMResponse:
        """返回输入的回声"""
        await asyncio.sleep(0.01)
        response_text = f"Echo: {prompt}"
        prompt_tokens = len(prompt.split())
        completion_tokens = len(response_text.split())
        return LLMResponse(
            content=response_text,
            model_alias=self._model_alias,
            duration_ms=10,
            token_usage={
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": prompt_tokens + completion_tokens,
            },
        )


class MockProvider(LLMProvider):
    """Mock 模式 -- 返回固定响应

    .. deprecated:: 0.2.0
        Feature 002 起使用 Mock ModelCallResult 替代。
    """

    _SUPPRESS_LLM_PROVIDER_DEPRECATION_WARNING = True

    def __init__(
        self,
        response: str = "This is a mock response.",
        model_alias: str = "mock",
    ) -> None:
        self._response = response
        self._model_alias = model_alias

    async def call(self, prompt: str) -> LLMResponse:
        """返回固定响应"""
        return LLMResponse(
            content=self._response,
            model_alias=self._model_alias,
            duration_ms=5,
            token_usage={"prompt": 1, "completion": 1, "total": 2},
        )


# ============================================================
# Feature 002 LLMService
# ============================================================


class _GenericSkillInput(BaseModel):
    """普通聊天接 skill runner 的最小输入模型。"""

    objective: str = Field(min_length=1)


class _GenericSkillOutput(BaseModel):
    """SkillRunner 普通聊天输出模型。"""

    content: str = ""
    complete: bool = False
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    skip_remaining_tools: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMService:
    """LLM 服务 -- Feature 002 版本

    变更:
    - 构造器接受 FallbackManager + AliasRegistry（替代直接持有 providers dict）
    - call() 支持 messages 格式和 prompt 字符串（向后兼容）
    - 返回 ModelCallResult（替代 LLMResponse）

    向后兼容: 无参构造时自动创建 Echo 模式的 FallbackManager + AliasRegistry。
    """

    supports_butler_decision_phase = True
    supports_recall_planning_phase = True
    supports_single_loop_executor = True

    def __init__(
        self,
        fallback_manager: FallbackManager | None = None,
        alias_registry: AliasRegistry | None = None,
        default_provider: LLMProvider | None = None,
        *,
        skill_runner: SkillRunner | None = None,
        skill_discovery: SkillDiscovery | None = None,
        tool_promotion_service: ToolPromotionService | None = None,
        pipeline_registry: Any | None = None,
    ) -> None:
        """初始化 LLM 服务

        Args:
            fallback_manager: 包含 primary + fallback 的降级管理器
            alias_registry: 运行时 alias 注册表（配置 alias 优先，legacy alias 兼容）
            default_provider: M0 兼容参数（废弃，仅向后兼容）
            skill_runner: SkillRunner 实例
            skill_discovery: Feature 057 SkillDiscovery 实例，用于注入已加载 Skill 到 system prompt
            tool_promotion_service: Feature 061 工具提升服务，追踪 Deferred → Active 状态
            pipeline_registry: Feature 065 PipelineRegistry 实例，用于注入 Pipeline 列表到 system prompt
        """
        if fallback_manager is not None:
            # Feature 002 模式
            self._fallback_manager = fallback_manager
            self._alias_registry = alias_registry or AliasRegistry()
        else:
            # M0 向后兼容模式：自动创建 Echo FallbackManager
            echo_adapter = EchoMessageAdapter()
            self._fallback_manager = FallbackManager(
                primary=echo_adapter,
                fallback=None,
            )
            self._alias_registry = AliasRegistry()

        # M0 兼容：保留旧的 providers dict（仅供向后兼容）
        self._providers: dict[str, LLMProvider] = {}
        self._providers["echo"] = EchoProvider()
        self._providers["mock"] = MockProvider()
        self._skill_runner = skill_runner
        self._skill_discovery = skill_discovery
        # Feature 061 T-022: 工具提升服务
        self._tool_promotion = tool_promotion_service
        # Feature 065: Pipeline 注册表
        self._pipeline_registry = pipeline_registry

    def register(self, alias: str, provider: LLMProvider) -> None:
        """注册 LLM provider -- M0 兼容"""
        self._providers[alias] = provider

    async def call(
        self,
        prompt_or_messages: str | list[dict[str, str]],
        model_alias: str | None = None,
        *,
        task_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
        **kwargs: Any,
    ) -> ModelCallResult:
        """调用 LLM

        Args:
            prompt_or_messages:
                - str: 纯文本 prompt（M0 兼容，自动转为 messages 格式）
                - list[dict]: messages 格式（Feature 002 推荐）
            model_alias:
                - 显式配置 alias（如 "main" / "compaction" / "research-main"）优先原样消费
                - legacy 语义 alias（如 "planner"）仅在未显式配置时走兼容映射
                - None -> 使用 "main" 默认

        Returns:
            ModelCallResult
        """
        # 转换 prompt 为 messages 格式
        if isinstance(prompt_or_messages, str):
            messages = [{"role": "user", "content": prompt_or_messages}]
        else:
            messages = prompt_or_messages

        # 解析 alias
        resolved_alias = model_alias or "main"
        resolved_alias = self._alias_registry.resolve(resolved_alias)

        skill_result = await self._try_call_with_tools(
            prompt_or_messages=prompt_or_messages,
            model_alias=resolved_alias,
            task_id=task_id,
            trace_id=trace_id,
            metadata=metadata or {},
            worker_capability=worker_capability or "llm_generation",
            tool_profile=tool_profile or "standard",
        )
        if skill_result is not None:
            return skill_result

        # 通过 FallbackManager 调用（kwargs 透传 extra_body 等参数）
        return await self._fallback_manager.call_with_fallback(
            messages=messages,
            model_alias=resolved_alias,
            **kwargs,
        )

    async def _try_call_with_tools(
        self,
        *,
        prompt_or_messages: str | list[dict[str, str]],
        model_alias: str,
        task_id: str | None,
        trace_id: str | None,
        metadata: dict[str, Any],
        worker_capability: str,
        tool_profile: str,
        is_degraded_retry: bool = False,
    ) -> ModelCallResult | None:
        if self._skill_runner is None or not task_id or not trace_id:
            return None

        selected_tools = self._parse_selected_tools(metadata)
        if not selected_tools:
            return None

        # Feature 061 T-035/T-036: 同步已加载 Skill 的 tools_required 提升/回退
        loaded_skill_names = metadata.get("loaded_skill_names", [])
        if (
            isinstance(loaded_skill_names, list)
            and loaded_skill_names
            and self._tool_promotion is not None
            and self._skill_discovery is not None
        ):
            await self.sync_skill_tool_promotions(
                loaded_skill_names,
                task_id=task_id or "",
                trace_id=trace_id or "",
            )

        # Feature 061 T-022: 合并已提升的工具到 selected_tools
        if self._tool_promotion is not None:
            promoted_names = self._tool_promotion.active_tool_names
            if promoted_names:
                # 去重合并：promoted 工具追加到 selected_tools 末尾
                existing = set(selected_tools)
                for name in promoted_names:
                    if name not in existing:
                        selected_tools.append(name)
                        existing.add(name)

        conversation_messages = self._coerce_messages(prompt_or_messages)
        prompt = self._coerce_prompt(prompt_or_messages)
        if not prompt:
            return None

        worker_type = self._normalize_worker_type(metadata.get("selected_worker_type", ""))
        single_loop_executor = self._metadata_flag(metadata, "single_loop_executor")
        base_description = self._build_skill_description(
            worker_type,
            selected_tools,
            single_loop_executor=single_loop_executor,
            prompt=prompt,
        )
        # Feature 072: 注入 deferred 工具列表到 system prompt
        tool_selection_data = metadata.get("tool_selection", {})
        deferred_entries_raw = tool_selection_data.get("deferred_tool_entries", [])
        if deferred_entries_raw:
            from octoagent.tooling.models import DeferredToolEntry, format_deferred_tools_list
            deferred_entries = [
                DeferredToolEntry(**e) if isinstance(e, dict) else e
                for e in deferred_entries_raw
            ]
            deferred_text = format_deferred_tools_list(deferred_entries)
            if deferred_text:
                base_description = f"{base_description}\n\n{deferred_text}"

        manifest = SkillManifest(
            skill_id=f"chat.{worker_type}.inline",
            input_model=_GenericSkillInput,
            output_model=_GenericSkillOutput,
            model_alias=model_alias,
            description=base_description,
            permission_mode=SkillPermissionMode.INHERIT,
            tools_allowed=selected_tools,
        )
        # --- Feature 062: 资源限制合并 (T2.10) ---
        base_limits = get_global_defaults()
        profile_rl = metadata.get("resource_limits", {})
        # SKILL.md resource_limits 暂时为空（runtime 未注入时 fallback）
        skill_rl: dict[str, Any] = {}
        if self._skill_discovery:
            loaded_names = metadata.get("loaded_skill_names", [])
            if isinstance(loaded_names, list):
                for name in loaded_names:
                    entry = self._skill_discovery.get(str(name))
                    if entry and getattr(entry, "resource_limits", None):
                        skill_rl = entry.resource_limits
                        break  # 使用第一个有效的 Skill resource_limits
        usage_limits = merge_usage_limits(base_limits, profile_rl, skill_rl)

        execution_context = SkillExecutionContext(
            task_id=task_id,
            trace_id=trace_id,
            caller=(
                f"butler:{worker_type}" if single_loop_executor else f"worker:{worker_type}"
            ),
            agent_runtime_id=str(metadata.get("agent_runtime_id", "")).strip(),
            agent_session_id=str(metadata.get("agent_session_id", "")).strip(),
            work_id=str(metadata.get("work_id", "")).strip(),
            # Feature 061: 从 metadata 获取 Agent 权限 Preset
            permission_preset=str(metadata.get("permission_preset", DEFAULT_PERMISSION_PRESET)).strip() or DEFAULT_PERMISSION_PRESET,
            conversation_messages=conversation_messages,
            metadata=metadata,
            usage_limits=usage_limits,
        )

        try:
            result = await self._skill_runner.run(
                manifest=manifest,
                execution_context=execution_context,
                skill_input={"objective": prompt},
                prompt=prompt,
            )
        except Exception:
            return None

        if result.status != SkillRunStatus.SUCCEEDED or result.output is None:
            # --- Feature 062: STOPPED 状态处理 (T4.4) ---
            # STOPPED 表示被 StopHook 或用户取消优雅终止，优先返回最后有效输出
            if result.status == SkillRunStatus.STOPPED:
                if result.output is not None and result.output.content:
                    content = result.output.content
                else:
                    content = "请求已被停止。"
                return ModelCallResult(
                    content=content,
                    model_alias=model_alias,
                    model_name="system",
                    provider="system",
                    duration_ms=result.duration_ms,
                    token_usage=TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                    cost_usd=result.total_cost_usd,
                    cost_unavailable=False,
                    is_fallback=False,
                    fallback_reason="stopped",
                )

            # --- Feature 062: Error UX 友好中文提示模板 (T1.16) ---
            # 按 ErrorCategory 分类生成用户可读的错误提示
            if result.status == SkillRunStatus.FAILED:
                error_msg = result.error_message or "执行过程中遇到问题"
                category = result.error_category.value if result.error_category else "unknown"
                usage = result.usage or {}

                if category == "step_limit_exceeded":
                    content = (
                        f"处理步骤较多（{result.steps} 步），已达上限。"
                        "请尝试拆分为更小的问题。"
                    )
                elif category == "token_limit_exceeded":
                    tokens = usage.get("request_tokens", 0) + usage.get("response_tokens", 0)
                    content = (
                        f"本次对话消耗 token 较多（{tokens}），已达上限。"
                        "建议开启新对话或缩减请求范围。"
                    )
                elif category == "tool_call_limit_exceeded":
                    calls = usage.get("tool_calls", 0)
                    content = (
                        f"工具调用次数较多（{calls} 次），已达上限。"
                        "请尝试更具体的指令。"
                    )
                elif category == "budget_exceeded":
                    budget = usage.get("cost_usd", 0.0)
                    content = (
                        f"本次请求成本已达预算上限（${budget:.2f}）。"
                        "如需继续，请在设置中调整预算限制。"
                    )
                elif category == "timeout_exceeded":
                    seconds = usage.get("duration_seconds", 0)
                    content = (
                        f"请求处理时间过长（{seconds}s），已超时。"
                        "请稍后重试或简化请求。"
                    )
                else:
                    content = f"抱歉，处理请求时遇到了问题：{error_msg}。请稍后重试。"

                # --- Feature 062 Phase 5: 智能降级重试 (T5.2) ---
                # FAILED 后可降级模型重试一次（is_degraded_retry=True 防止递归）
                if (
                    not is_degraded_retry
                    and manifest.retry_policy.fallback_model_alias
                    and category in (
                        "step_limit_exceeded",
                        "timeout_exceeded",
                        "token_limit_exceeded",
                    )
                ):
                    # max_steps=None → 降级时也保持 None（不限）
                    degraded_steps = (
                        min(int(usage_limits.max_steps * 1.5), _MAX_STEPS_HARD_CEILING)
                        if usage_limits.max_steps is not None
                        else None
                    )
                    # 仅覆盖 max_steps，其余字段（含 max_budget_usd）保持不变
                    degraded_limits = usage_limits.model_copy(
                        update={"max_steps": degraded_steps}
                    )
                    degraded_ctx = SkillExecutionContext(
                        task_id=task_id,
                        trace_id=trace_id,
                        caller=execution_context.caller,
                        agent_runtime_id=execution_context.agent_runtime_id,
                        agent_session_id=execution_context.agent_session_id,
                        work_id=execution_context.work_id,
                        # Feature 061: 继承原始权限 Preset，避免降级重试时回退为 normal
                        permission_preset=execution_context.permission_preset,
                        conversation_messages=conversation_messages,
                        metadata=metadata,
                        usage_limits=degraded_limits,
                    )
                    degraded_manifest = SkillManifest(
                        skill_id=manifest.skill_id,
                        input_model=manifest.input_model,
                        output_model=manifest.output_model,
                        model_alias=manifest.retry_policy.fallback_model_alias,
                        description=manifest.load_description() or "",
                        permission_mode=manifest.permission_mode,
                        tools_allowed=list(manifest.tools_allowed),
                    )
                    try:
                        retry_result = await self._skill_runner.run(
                            manifest=degraded_manifest,
                            execution_context=degraded_ctx,
                            skill_input={"objective": prompt},
                            prompt=prompt,
                        )
                        if retry_result.status == SkillRunStatus.SUCCEEDED and retry_result.output:
                            r_meta = retry_result.output.metadata
                            r_usage = (
                                r_meta.get("token_usage", {})
                                if isinstance(r_meta, dict)
                                else {}
                            )
                            return ModelCallResult(
                                content=retry_result.output.content,
                                model_alias=manifest.retry_policy.fallback_model_alias,
                                model_name=str(r_meta.get("model_name", ""))
                                if isinstance(r_meta, dict)
                                else "",
                                provider=str(r_meta.get("provider", ""))
                                if isinstance(r_meta, dict)
                                else "",
                                duration_ms=retry_result.duration_ms,
                                token_usage=TokenUsage(
                                    prompt_tokens=int(r_usage.get("prompt_tokens", 0) or 0),
                                    completion_tokens=int(
                                        r_usage.get("completion_tokens", 0) or 0
                                    ),
                                    total_tokens=int(r_usage.get("total_tokens", 0) or 0),
                                ),
                                cost_usd=retry_result.total_cost_usd,
                                cost_unavailable=bool(
                                    r_meta.get("cost_unavailable", True)
                                )
                                if isinstance(r_meta, dict)
                                else True,
                                is_fallback=True,
                                fallback_reason="degraded_retry",
                            )
                    except Exception:
                        pass  # 降级重试失败，返回原始错误

                return ModelCallResult(
                    content=content,
                    model_alias=model_alias,
                    model_name="system",
                    provider="system",
                    duration_ms=result.duration_ms,
                    token_usage=TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                    cost_usd=result.total_cost_usd,
                    cost_unavailable=False,
                    is_fallback=True,
                    fallback_reason=f"skill_failed:{category}",
                )
            return None

        meta = result.output.metadata
        usage = meta.get("token_usage", {}) if isinstance(meta, dict) else {}
        return ModelCallResult(
            content=result.output.content,
            model_alias=model_alias,
            model_name=str(meta.get("model_name", "")) if isinstance(meta, dict) else "",
            provider=str(meta.get("provider", "")) if isinstance(meta, dict) else "",
            duration_ms=result.duration_ms,
            token_usage=TokenUsage(
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                total_tokens=int(usage.get("total_tokens", 0) or 0),
            ),
            cost_usd=float(meta.get("cost_usd", 0.0) or 0.0) if isinstance(meta, dict) else 0.0,
            cost_unavailable=bool(meta.get("cost_unavailable", True))
            if isinstance(meta, dict)
            else True,
            is_fallback=False,
            fallback_reason="",
        )

    @staticmethod
    def _parse_selected_tools(metadata: dict[str, Any]) -> list[str]:
        return extract_mounted_tool_names(metadata)

    async def process_tool_search_results(
        self,
        search_result_json: str,
        *,
        task_id: str = "",
        trace_id: str = "",
    ) -> list[str]:
        """Feature 061 T-022: 处理 tool_search 返回的结果，提升工具到 Active

        在 LLM 调用 tool_search 后，上层应调用此方法将搜索到的工具
        注册为 promoted，使其在下一个 run_step 中以完整 schema 注入。

        Args:
            search_result_json: tool_search 返回的 JSON 字符串
            task_id: 关联任务 ID
            trace_id: 追踪标识

        Returns:
            新增提升的工具名称列表
        """
        if self._tool_promotion is None:
            return []

        try:
            import json
            data = json.loads(search_result_json)
            result = ToolSearchResult.model_validate(data)
        except Exception:
            return []

        tool_names = [hit.tool_name for hit in result.results]
        if not tool_names:
            return []

        return await self._tool_promotion.promote_from_search(
            tool_names,
            query=result.query,
            task_id=task_id,
            trace_id=trace_id,
        )

    @property
    def tool_promotion_service(self) -> ToolPromotionService | None:
        """Feature 061: 返回工具提升服务实例"""
        return self._tool_promotion

    async def sync_skill_tool_promotions(
        self,
        loaded_skill_names: list[str],
        *,
        task_id: str = "",
        trace_id: str = "",
    ) -> tuple[list[str], list[str]]:
        """Feature 061 T-035/T-036: 同步已加载 Skill 的 tools_required 到工具提升状态

        对比当前 promotion state 中所有 skill:* 来源与最新的 loaded_skill_names，
        自动提升新加载 Skill 的工具、回退已卸载 Skill 的工具。

        Args:
            loaded_skill_names: 当前 session 已加载的 Skill 名称列表
            task_id: 关联任务 ID
            trace_id: 追踪标识

        Returns:
            (newly_promoted, demoted) 元组：新提升的工具名列表、回退的工具名列表
        """
        if self._tool_promotion is None or self._skill_discovery is None:
            return [], []

        loaded_set = set(loaded_skill_names)
        newly_promoted: list[str] = []
        demoted: list[str] = []

        # T-035: 提升新加载 Skill 的 tools_required
        for skill_name in loaded_skill_names:
            entry = self._skill_discovery.get(skill_name)
            if entry is None or not entry.tools_required:
                continue
            # 检查该 Skill 是否已有对应 source，避免重复提升
            source = f"skill:{skill_name}"
            tools_to_promote = []
            for tool_name in entry.tools_required:
                sources = self._tool_promotion.state.promoted_tools.get(tool_name, [])
                if source not in sources:
                    tools_to_promote.append(tool_name)
            if tools_to_promote:
                promoted = await self._tool_promotion.promote_from_skill(
                    tools_to_promote,
                    skill_name=skill_name,
                    task_id=task_id,
                    trace_id=trace_id,
                )
                newly_promoted.extend(promoted)

        # T-036: 回退已卸载 Skill 的 tools_required
        # 收集当前 promotion state 中所有 skill:* 来源
        tracked_skill_sources: set[str] = set()
        for _tool_name, sources in self._tool_promotion.state.promoted_tools.items():
            for src in sources:
                if src.startswith("skill:"):
                    tracked_skill_sources.add(src)

        # 找出已不在 loaded_skill_names 中的 Skill
        for source in tracked_skill_sources:
            skill_name = source.removeprefix("skill:")
            if skill_name in loaded_set:
                continue
            # 该 Skill 已卸载，回退其 tools_required
            entry = self._skill_discovery.get(skill_name)
            if entry is None:
                # Skill 定义不存在，通过遍历 promotion state 找到关联工具
                tools_with_source = [
                    tn for tn, srcs in list(self._tool_promotion.state.promoted_tools.items())
                    if source in srcs
                ]
            else:
                tools_with_source = list(entry.tools_required)
            if tools_with_source:
                demoted_tools = await self._tool_promotion.demote_from_skill(
                    tools_with_source,
                    skill_name=skill_name,
                    task_id=task_id,
                    trace_id=trace_id,
                )
                demoted.extend(demoted_tools)

        return newly_promoted, demoted

    @staticmethod
    def _coerce_messages(
        prompt_or_messages: str | list[dict[str, str]],
    ) -> list[dict[str, str]]:
        if isinstance(prompt_or_messages, str):
            content = prompt_or_messages.strip()
            return [{"role": "user", "content": content}] if content else []

        normalized: list[dict[str, str]] = []
        for item in prompt_or_messages:
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            role = str(item.get("role", "user")).strip().lower() or "user"
            if role not in {"system", "user", "assistant"}:
                role = "user"
            normalized.append({"role": role, "content": content})
        return normalized

    @classmethod
    def _coerce_prompt(cls, prompt_or_messages: str | list[dict[str, str]]) -> str:
        if isinstance(prompt_or_messages, str):
            return prompt_or_messages.strip()
        messages = cls._coerce_messages(prompt_or_messages)
        for item in reversed(messages):
            if item["role"] == "user":
                return item["content"]
        parts = [item["content"] for item in messages]
        return "\n\n".join(parts).strip()

    @staticmethod
    def _normalize_worker_type(value: Any) -> str:
        raw = str(value).strip().lower()
        if raw in {"ops", "research", "dev", "general"}:
            return raw
        return "general"

    def _build_loaded_skills_context(self, metadata: dict[str, Any]) -> str:
        """Feature 057: 从 session metadata 读取已加载 Skill 名称，构建注入文本。

        读取 metadata["loaded_skill_names"]，从 SkillDiscovery 缓存获取每个 Skill 的 content，
        按加载顺序拼接为 system prompt 注入文本。

        Args:
            metadata: AgentSession.metadata 或调用时传入的 metadata

        Returns:
            拼接后的 Skill 上下文文本，为空则返回空字符串
        """
        if self._skill_discovery is None:
            return ""

        loaded_names = metadata.get("loaded_skill_names", [])
        if not isinstance(loaded_names, list) or not loaded_names:
            return ""

        sections: list[str] = []
        for name in loaded_names:
            entry = self._skill_discovery.get(str(name))
            if entry is None or not entry.content:
                continue
            sections.append(
                f"{SKILL_SECTION_PREFIX}{entry.name} ---\n{entry.content}\n--- End Skill: {entry.name} ---"
            )

        if not sections:
            return ""

        return "## Active Skills\n\n" + "\n\n".join(sections)

    def _build_skill_catalog_context(self, metadata: dict[str, Any]) -> str:
        """构建 Skill 目录摘要（name + description），自动注入 system prompt。

        对齐 Claude Code 两阶段加载模式：Phase 1 摘要先行，LLM 看到后自主决定
        是否通过 skills action=load 加载完整内容。已加载的 Skill 不重复列出。
        """
        if self._skill_discovery is None:
            return ""

        items = self._skill_discovery.list_items()
        if not items:
            return ""

        loaded_names = set(metadata.get("loaded_skill_names", []))

        lines = ["## Available Skills\n"]
        for item in items:
            marker = " [loaded]" if item.name in loaded_names else ""
            lines.append(f"- **{item.name}**{marker}: {item.description}")

        lines.append("")
        lines.append(
            "Use `skills action=load name=<name>` to load a skill's full instructions "
            "into this session."
        )
        return "\n".join(lines)

    def _build_pipeline_catalog_context(self) -> str:
        """Feature 065 T-032: 构建 Pipeline 目录摘要，注入 Worker/Subagent system prompt。

        从 PipelineRegistry 获取 Pipeline 列表，格式化为 system prompt 段落。
        Pipeline 列表为空时返回空字符串（FR-065-07 AC-04）。
        包含 Pipeline vs Subagent 语义区分指引（FR-065-07 AC-02）。

        Returns:
            可直接拼入 system prompt 的文本，或空字符串。
        """
        if self._pipeline_registry is None:
            return ""

        try:
            items = self._pipeline_registry.list_items()
        except Exception:
            return ""

        if not items:
            return ""

        lines: list[str] = ["## Available Pipelines\n"]
        lines.append(
            "Deterministic workflow pipelines you can start, monitor and manage "
            "via `graph_pipeline` tool.\n"
        )

        for item in items:
            pid = getattr(item, "pipeline_id", "")
            desc = getattr(item, "description", "")
            hint = getattr(item, "trigger_hint", "")
            entry = f"- **{pid}**: {desc}"
            if hint:
                entry += f" (trigger: {hint})"
            lines.append(entry)

        # 语义区分指引（FR-065-07 AC-02）
        lines.append("")
        lines.append("### Pipelines vs Subagents\n")
        lines.append("Use **Pipeline** (graph_pipeline tool) when:")
        lines.append("- The task follows a known, repeatable sequence of steps")
        lines.append("- Steps need checkpoint/recovery guarantees (e.g., deploy, data migration)")
        lines.append("- Steps include approval gates or human review points")
        lines.append("- Deterministic execution is preferred over LLM reasoning\n")
        lines.append("Use **Subagent** (subagents tool) when:")
        lines.append("- The task requires exploration, reasoning, or multi-turn interaction")
        lines.append("- The approach is not predetermined and needs LLM judgment")
        lines.append("- The task involves creative work (writing, analysis, research)")
        lines.append("- Flexibility is more important than determinism")

        return "\n".join(lines)

    @staticmethod
    def _build_skill_description(
        worker_type: str,
        selected_tools: list[str],
        *,
        single_loop_executor: bool = False,
        prompt: str = "",
    ) -> str:
        tool_list = ", ".join(selected_tools)
        objective_guidance = LLMService._build_objective_guidance(
            prompt=prompt,
            selected_tools=selected_tools,
            single_loop_executor=single_loop_executor,
        )
        if single_loop_executor:
            worker_lens = (
                f" 当前回合按 {worker_type} worker 视角挂载工具。"
                if worker_type != "general"
                else ""
            )
            return (
                "你是 OctoAgent 的主 Butler。"
                f"{worker_lens} 当前回合直接挂载以下受治理工具：{tool_list}。"
                " 你需要在同一轮主执行链里自己决定是否调用工具、如何收集证据、以及何时直接回答。"
                " 不要先输出一段“计划说明”再等待下轮；"
                " 需要工具时直接调用，证据足够后直接给出最终答复。"
                " 如果上下文里已经提供 recent summary / session replay / memory hints，先利用这些事实，再决定是否继续查。"
                " 最终答复只能是自然语言，不要暴露 to=tool、原始 JSON、工具参数回显或中间调试文本。"
                f"{objective_guidance}"
            )
        return (
            f"你是 OctoAgent 的 {worker_type} worker。"
            f" 当前会话已经接入以下工具：{tool_list}。"
            " 当用户问题需要 project/session/runtime 等事实时，优先调用工具核实。"
            " 不要声称自己没有工具；只在确实没有必要时直接回答。"
            " 如果工具已经返回足够证据，直接输出结论；"
            "不要把“我先查一下”“我再看看”“接下来我会”这类计划句当成最终答复。"
            " 完成后用自然语言给出最终答复，不要输出 to=tool、原始 JSON、工具调用 transcript 或乱码。"
            f"{objective_guidance}"
        )

    @staticmethod
    def _build_objective_guidance(
        *,
        prompt: str,
        selected_tools: list[str],
        single_loop_executor: bool,
    ) -> str:
        normalized_prompt = prompt.strip().lower()
        selected = {item.strip().lower() for item in selected_tools if item.strip()}
        looks_like_local_doc_read = bool(
            normalized_prompt
            and (
                "readme" in normalized_prompt
                or "README" in prompt
                or "当前项目" in prompt
                or "仓库" in prompt
                or "文件" in prompt
            )
            and ("读取" in prompt or "read" in normalized_prompt or "开头" in prompt)
        )
        has_filesystem = {
            "filesystem.list_dir",
            "filesystem.read_text",
        }.issubset(selected) or (
            "filesystem.list_dir" in selected and "filesystem.read_text" in selected
        )
        if looks_like_local_doc_read and has_filesystem:
            terminal_hint = (
                " 只有在 filesystem.list_dir / filesystem.read_text 无法定位路径时，才允许退到 terminal.exec。"
                if "terminal.exec" in selected
                else ""
            )
            ownership_hint = (
                " 这是主助手直接解决的问题，不要把用户原话改写成另一个 worker 的委派请求。"
                if single_loop_executor
                else ""
            )
            return (
                " 当前任务是边界明确的本地文档读取/总结。"
                " 优先用 filesystem.list_dir 确认候选路径，再用 filesystem.read_text 读取 README 或指定文件。"
                " 不要用 memory.search / memory.recall / control-plane 元数据代替真实文件内容。"
                " 一旦成功读到目标 README 的开头或足够段落，就立刻停止继续探索并直接给出一句话总结。"
                f"{terminal_hint}{ownership_hint}"
            )
        return ""

    @staticmethod
    def _metadata_flag(metadata: dict[str, Any], key: str) -> bool:
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
