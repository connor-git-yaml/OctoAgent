"""Control-plane action 注册表构建（F108a W2-C1 自 _coordinator._build_registry 抽出）。

纯声明层：内嵌 definition 闭包工厂 + 全量 ActionDefinition 声明与 capabilities，
零 self 捕获、零运行时状态；函数体与原方法逐字节对账（唯一差异 = def 行 + 缩进级）。
"""

from __future__ import annotations

from typing import Any

from octoagent.core.models import (
    ActionDefinition,
    ActionRegistryDocument,
    ControlPlaneCapability,
    ControlPlaneSupportStatus,
    ControlPlaneSurface,
)


def build_action_registry() -> ActionRegistryDocument:
    def definition(
        action_id: str,
        label: str,
        *,
        category: str,
        description: str = "",
        telegram_aliases: list[str] | None = None,
        params_schema: dict[str, Any] | None = None,
        risk_hint: str = "low",
        approval_hint: str = "none",
        telegram_supported: bool = False,
    ) -> ActionDefinition:
        support_status_by_surface = {
            "web": ControlPlaneSupportStatus.SUPPORTED,
            "telegram": (
                ControlPlaneSupportStatus.SUPPORTED
                if telegram_supported
                else ControlPlaneSupportStatus.DEGRADED
            ),
        }
        aliases: dict[str, list[str]] = {"web": [action_id]}
        if telegram_aliases:
            aliases["telegram"] = telegram_aliases
        return ActionDefinition(
            action_id=action_id,
            label=label,
            description=description,
            category=category,
            supported_surfaces=[ControlPlaneSurface.WEB, ControlPlaneSurface.SYSTEM]
            + ([ControlPlaneSurface.TELEGRAM] if telegram_aliases else []),
            surface_aliases=aliases,
            support_status_by_surface=support_status_by_surface,
            params_schema=params_schema or {"type": "object"},
            result_schema={"type": "object"},
            risk_hint=risk_hint,
            approval_hint=approval_hint,
            idempotency_hint="request_id",
            resource_targets=[],
        )

    return ActionRegistryDocument(
        actions=[
            definition("wizard.refresh", "刷新 Wizard", category="wizard"),
            definition(
                "wizard.restart", "重新开始 Wizard", category="wizard", risk_hint="medium"
            ),
            definition(
                "project.select",
                "切换项目",
                category="projects",
                telegram_aliases=["/project select"],
                params_schema={"type": "object", "required": ["project_id"]},
                telegram_supported=True,
            ),
            definition(
                "setup.review",
                "检查配置",
                category="setup",
                description="统一检查模型、渠道、主 Agent 和技能配置是否可以保存。",
                params_schema={"type": "object"},
                risk_hint="medium",
            ),
            definition(
                "setup.apply",
                "保存配置",
                category="setup",
                description="把当前主 Agent、模型和渠道设置一起保存。",
                params_schema={"type": "object"},
                risk_hint="medium",
            ),
            definition(
                "setup.oauth_and_apply",
                "授权并保存",
                category="setup",
                description=(
                    "Feature 079 Phase 2：OAuth 授权 + setup.apply 原子操作，"
                    "消除 auth-profiles 与 octoagent.yaml 之间的时序断层。"
                ),
                params_schema={"type": "object"},
                risk_hint="medium",
            ),
            definition(
                "setup.quick_connect",
                "连接并启用真实模型",
                category="setup",
                description=(
                    "保存 Provider 配置、启动 LiteLLM Proxy，"
                    "并在托管实例上自动切到真实模型。"
                ),
                params_schema={"type": "object"},
                risk_hint="medium",
            ),
            definition(
                "skills.selection.save",
                "保存技能默认范围",
                category="setup",
                description="保存当前 project 的 skills / MCP 默认启用范围。",
                params_schema={"type": "object"},
                risk_hint="medium",
            ),
            definition(
                "mcp_provider.save",
                "保存 MCP Provider",
                category="capability",
                description="安装或编辑一个 MCP provider。",
                params_schema={"type": "object", "required": ["provider"]},
                risk_hint="medium",
            ),
            definition(
                "mcp_provider.delete",
                "删除 MCP Provider",
                category="capability",
                description="删除一个 MCP provider。",
                params_schema={"type": "object", "required": ["provider_id"]},
                risk_hint="medium",
            ),
            definition(
                "provider.oauth.openai_codex",
                "连接 OpenAI Auth",
                category="setup",
                description=(
                    "通过浏览器 OAuth 连接 ChatGPT Pro / OpenAI Codex，"
                    "并写入本地凭证。"
                ),
                params_schema={"type": "object"},
                risk_hint="medium",
            ),
            definition("memory.query", "刷新 Memory 总览", category="memory"),
            definition(
                "memory.sor.edit",
                "编辑记忆内容",
                category="memory",
                risk_hint="medium",
                params_schema={
                    "type": "object",
                    "required": ["scope_id", "subject_key", "content", "expected_version"],
                },
            ),
            definition(
                "memory.sor.archive",
                "归档记忆",
                category="memory",
                risk_hint="medium",
                params_schema={
                    "type": "object",
                    "required": ["scope_id", "memory_id", "expected_version"],
                },
            ),
            definition(
                "memory.sor.restore",
                "恢复已归档记忆",
                category="memory",
                params_schema={
                    "type": "object",
                    "required": ["scope_id", "memory_id"],
                },
            ),
            definition("memory.browse", "浏览记忆目录", category="memory"),
            definition(
                "memory.subject.inspect",
                "查看 Subject 历史",
                category="memory",
                params_schema={"type": "object", "required": ["subject_key"]},
            ),
            definition(
                "memory.proposal.inspect",
                "查看 Proposal 审计",
                category="memory",
            ),
            definition(
                "memory.flush",
                "执行 Memory Flush",
                category="memory",
                risk_hint="medium",
            ),
            definition(
                "memory.reindex",
                "执行 Memory Reindex",
                category="memory",
                risk_hint="medium",
            ),
            definition(
                "memory.sync.resume",
                "恢复 Memory Sync",
                category="memory",
                risk_hint="medium",
            ),
            definition(
                "vault.access.request",
                "申请 Vault 授权",
                category="memory",
                approval_hint="operator",
                params_schema={"type": "object", "required": ["project_id"]},
            ),
            definition(
                "vault.access.resolve",
                "处理 Vault 授权",
                category="memory",
                risk_hint="high",
                approval_hint="operator",
                params_schema={"type": "object", "required": ["request_id", "decision"]},
            ),
            definition(
                "vault.retrieve",
                "检索 Vault 引用",
                category="memory",
                risk_hint="high",
                approval_hint="operator",
            ),
            definition(
                "memory.export.inspect",
                "检查 Memory 导出范围",
                category="memory",
            ),
            definition(
                "memory.restore.verify",
                "校验 Memory 恢复快照",
                category="memory",
                risk_hint="high",
                approval_hint="operator",
                params_schema={"type": "object", "required": ["snapshot_ref"]},
            ),
            definition(
                "retrieval.index.start",
                "开始 embedding 迁移",
                category="memory",
                risk_hint="medium",
            ),
            definition(
                "retrieval.index.cancel",
                "取消 embedding 迁移",
                category="memory",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["generation_id"]},
            ),
            definition(
                "retrieval.index.cutover",
                "切换到新 embedding 索引",
                category="memory",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["generation_id"]},
            ),
            definition(
                "retrieval.index.rollback",
                "回滚 embedding 索引",
                category="memory",
                risk_hint="medium",
                approval_hint="operator",
                params_schema={"type": "object", "required": ["generation_id"]},
            ),
            definition("capability.refresh", "刷新能力包", category="capability"),
            definition("work.refresh", "刷新委派视图", category="delegation"),
            definition("session.focus", "聚焦会话", category="sessions"),
            definition("session.unfocus", "取消聚焦会话", category="sessions"),
            definition("session.new", "开始新对话", category="sessions"),
            definition("session.create_with_project", "创建对话（含 Project）", category="sessions"),
            definition("session.reset", "重置会话 continuity", category="sessions"),
            definition("session.set_alias", "修改会话名称", category="sessions"),
            definition("agent.list_available_models", "查询可用模型别名", category="agent_management"),
            definition("agent.list_worker_archetypes", "查询 Worker archetype", category="agent_management"),
            definition("agent.list_tool_profiles", "查询工具权限等级", category="agent_management"),
            definition("agent.create_worker_with_project", "创建 Worker + Project", category="agent_management"),
            definition("session.export", "导出会话", category="sessions"),
            definition(
                "session.interrupt",
                "中断任务",
                category="sessions",
                telegram_aliases=["/cancel"],
                risk_hint="medium",
                telegram_supported=True,
            ),
            definition("session.resume", "恢复任务", category="sessions"),
            definition(
                "operator.approval.resolve",
                "处理审批",
                category="operator",
                telegram_aliases=["/approve"],
                approval_hint="policy",
                telegram_supported=True,
            ),
            definition("operator.alert.ack", "确认告警", category="operator"),
            definition(
                "operator.task.retry",
                "重试任务",
                category="operator",
                telegram_aliases=["/retry"],
                telegram_supported=True,
            ),
            definition(
                "operator.task.cancel",
                "取消任务",
                category="operator",
            ),
            definition("channel.pairing.approve", "批准 Pairing", category="channels"),
            definition("channel.pairing.reject", "拒绝 Pairing", category="channels"),
            definition(
                "agent_profile.save",
                "保存主 Agent",
                category="setup",
                risk_hint="medium",
                params_schema={"type": "object"},
            ),
            definition(
                "policy_profile.select",
                "切换安全等级",
                category="setup",
                params_schema={"type": "object", "required": ["profile_id"]},
            ),
            definition(
                "worker_profile.create",
                "新建 Root Agent",
                category="root_agents",
                risk_hint="medium",
                params_schema={"type": "object"},
            ),
            definition(
                "worker_profile.update",
                "更新 Root Agent 草稿",
                category="root_agents",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["profile_id"]},
            ),
            definition(
                "worker_profile.clone",
                "复制 Root Agent",
                category="root_agents",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["source_profile_id"]},
            ),
            definition(
                "worker_profile.archive",
                "归档 Root Agent",
                category="root_agents",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["profile_id"]},
            ),
            definition(
                "worker_profile.review",
                "检查 Root Agent",
                category="root_agents",
                risk_hint="medium",
                params_schema={"type": "object"},
            ),
            definition(
                "worker_profile.apply",
                "保存 Root Agent 草稿",
                category="root_agents",
                risk_hint="medium",
                params_schema={"type": "object"},
            ),
            definition(
                "worker_profile.publish",
                "发布 Root Agent Revision",
                category="root_agents",
                risk_hint="high",
                approval_hint="operator",
                params_schema={"type": "object", "required": ["profile_id"]},
            ),
            definition(
                "worker_profile.bind_default",
                "设为聊天默认 Root Agent",
                category="root_agents",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["profile_id"]},
            ),
            definition(
                "worker.spawn_from_profile",
                "按 Root Agent 启动任务",
                category="root_agents",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["profile_id", "objective"]},
            ),
            definition(
                "worker.extract_profile_from_runtime",
                "从运行态提炼 Root Agent",
                category="root_agents",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["work_id"]},
            ),
            definition("config.apply", "保存配置", category="config", risk_hint="medium"),
            definition(
                "backup.create",
                "创建备份",
                category="ops",
                telegram_aliases=["/backup"],
                risk_hint="medium",
                telegram_supported=True,
            ),
            definition("restore.plan", "生成恢复计划", category="ops", risk_hint="medium"),
            definition(
                "import.source.detect",
                "识别导入源",
                category="imports",
                risk_hint="low",
                params_schema={"type": "object", "required": ["source_type", "input_path"]},
            ),
            definition(
                "import.mapping.save",
                "保存导入 Mapping",
                category="imports",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["source_id"]},
            ),
            definition(
                "import.preview",
                "生成导入预览",
                category="imports",
                risk_hint="low",
                params_schema={"type": "object", "required": ["source_id"]},
            ),
            definition(
                "import.run",
                "执行聊天导入",
                category="imports",
                risk_hint="medium",
                params_schema={"type": "object"},
            ),
            definition(
                "import.resume",
                "恢复导入",
                category="imports",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["resume_id"]},
            ),
            definition(
                "import.report.inspect",
                "查看导入报告",
                category="imports",
                risk_hint="low",
                params_schema={"type": "object", "required": ["run_id"]},
            ),
            definition(
                "update.dry_run",
                "升级 Dry Run",
                category="ops",
                telegram_aliases=["/update dry-run"],
                risk_hint="medium",
                telegram_supported=True,
            ),
            definition(
                "update.apply",
                "执行升级",
                category="ops",
                telegram_aliases=["/update apply"],
                risk_hint="high",
                approval_hint="operator",
                telegram_supported=True,
            ),
            definition("runtime.restart", "重启 Runtime", category="ops", risk_hint="high"),
            definition("runtime.verify", "校验 Runtime", category="ops"),
            definition(
                "automation.create", "创建自动化任务", category="automation", risk_hint="medium"
            ),
            definition(
                "automation.run",
                "立即运行自动化任务",
                category="automation",
                telegram_aliases=["/automation run"],
            ),
            definition("automation.pause", "暂停自动化任务", category="automation"),
            definition("automation.resume", "恢复自动化任务", category="automation"),
            definition(
                "automation.delete", "删除自动化任务", category="automation", risk_hint="medium"
            ),
            definition(
                "work.cancel",
                "取消 Work",
                category="delegation",
                telegram_aliases=["/work cancel"],
                risk_hint="medium",
                telegram_supported=True,
            ),
            definition(
                "work.retry",
                "重试 Work",
                category="delegation",
                telegram_aliases=["/work retry"],
                telegram_supported=True,
            ),
            definition(
                "worker.review",
                "评审 Worker 方案",
                category="delegation",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["work_id"]},
            ),
            definition(
                "worker.apply",
                "应用 Worker 方案",
                category="delegation",
                risk_hint="high",
                approval_hint="operator",
                params_schema={"type": "object", "required": ["work_id", "plan"]},
            ),
            definition(
                "work.split",
                "拆分 Work",
                category="delegation",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["work_id", "objectives"]},
            ),
            definition(
                "work.merge",
                "合并 Work",
                category="delegation",
                risk_hint="medium",
                params_schema={"type": "object", "required": ["work_id"]},
            ),
            definition(
                "work.delete",
                "删除 Work",
                category="delegation",
                telegram_aliases=["/work delete"],
                risk_hint="medium",
                telegram_supported=True,
            ),
            definition(
                "work.escalate",
                "升级 Work",
                category="delegation",
                telegram_aliases=["/work escalate"],
                risk_hint="medium",
                telegram_supported=True,
            ),
            definition(
                "pipeline.resume",
                "恢复 Pipeline",
                category="pipeline",
                telegram_aliases=["/pipeline resume"],
                telegram_supported=True,
            ),
            definition(
                "pipeline.retry_node",
                "重试节点",
                category="pipeline",
                telegram_aliases=["/pipeline retry"],
                telegram_supported=True,
            ),
            definition(
                "diagnostics.refresh",
                "刷新诊断",
                category="diagnostics",
                telegram_aliases=["/status"],
                telegram_supported=True,
            ),
        ],
        capabilities=[
            ControlPlaneCapability(
                capability_id="control.actions",
                label="统一动作注册表",
                action_id="",
            )
        ],
    )
