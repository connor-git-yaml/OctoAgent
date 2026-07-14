"""F111 AC-12：behavior compact 合并质量真 LLM 用例（拍板④c，release live lane）。

**被测物 = LLM 判断力本身**（四层判定表 L2 行：决策质量是被测物）——脚本化用例
（AC-11）已证管道全链，本文件植入一份语义重复明显的 AGENTS.md 变体，真打 main
alias（GPT-5.5，宿主 ChatGPT Pro OAuth），断言**质量下限**（F127 G-lite 硬断言/
质量观察二分的硬断言半边）：

1. 管道通：非 fallback、契约 A' 解析成功、产出 1 条候选；
2. 质量下限：候选严格更小（H1 护栏本身保证——此处断言的是"真 LLM 面对明显
   冗余真的会提议"而非被护栏全拒）+ PROTECTED 区段字节级保留 + rationale 非空。

质量观察（不作断言，打印供人工复核）：合并后规则语义等价性——这是 H4 人审
兜底的维度，自动断言留 M7 统一强 model OctoBench 方案（spec §0.1.3 H5）。

Marker：``e2e_full + e2e_live + real_llm``（意图 + 事实双标，tests/AGENTS.md 纪律）；
宿主缺 auth-profiles.json → SKIP（baseline lane 记录不阻断；release live lane
按 unexpected FAIL——release 要求凭证在场）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# pre-merge 窗口防御（F138 同款）：hook 可能以非本 worktree 的 src 收集本文件。
pytest.importorskip("octoagent.gateway.services.behavior_compaction")

from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.e2e_full, pytest.mark.e2e_live, pytest.mark.real_llm]

_PROTECTED_SECTION = (
    "<!-- 🔒 PROTECTED -->\n- 核心红线：绝不删除用户数据，绝不绕过审批\n<!-- /🔒 PROTECTED -->"
)

#: 语义重复植入物：三组明显冗余（简洁性 ×4 / 中文提交 ×3 / 先问后做 ×3）+ 独立规则 ×2
_REDUNDANT_AGENTS_MD = (
    "# AGENTS\n\n"
    f"{_PROTECTED_SECTION}\n\n"
    "## 沟通规则\n"
    "- 回复保持简洁，不要冗长\n"
    "- 回答用户时尽量简短，避免长篇大论\n"
    "- 输出务必精炼，不要啰嗦重复\n"
    "- 内容尽可能简明扼要，别绕弯子\n"
    "## 提交规则\n"
    "- commit message 用中文\n"
    "- 提交说明必须使用中文书写\n"
    "- 所有 git 提交信息一律采用中文\n"
    "## 操作规则\n"
    "- 遇到不确定的事情要先问用户\n"
    "- 拿不准的操作先跟用户确认再执行\n"
    "- 有疑问时先询问，不要擅自行动\n"
    "## 独立规则（无冗余，合并后必须保留语义）\n"
    "- 测试运行必须用托管实例，站用户视角\n"
    "- 去掉功能时直接删除代码，不保留注释掉的死代码\n"
)


async def _build_real_app(root: Path, credential_store: Any):
    """真 harness（真凭证 + 真 ProviderRouterMessageAdapter 默认接线）+ compact 路由。"""
    from fastapi import FastAPI
    from octoagent.gateway.harness.octo_harness import OctoHarness
    from octoagent.gateway.routes import behavior_compact as compact_routes

    (root / "behavior" / "system").mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "mcp-servers").mkdir(parents=True, exist_ok=True)
    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )
    if (fixtures_root / "octoagent.yaml.template").exists():
        from apps.gateway.tests.e2e_live.helpers.factories import (
            copy_local_instance_template,
        )

        copy_local_instance_template(fixtures_root, root)

    harness = OctoHarness(
        project_root=root,
        credential_store=credential_store,
        mcp_servers_dir=root / "mcp-servers",
        data_dir=root / "data",
    )
    app = FastAPI()
    await harness.bootstrap(app)
    harness.commit_to_app(app)
    app.include_router(compact_routes.router)
    return harness, app


async def test_real_llm_compacts_redundant_rules(
    tmp_path: Path, real_codex_credential_store: Any
) -> None:
    root = tmp_path / "octoagent_e2e_root"
    harness, app = await _build_real_app(root, real_codex_credential_store)
    try:
        service = app.state.behavior_compaction_service
        assert service is not None, "harness 未装配 behavior_compaction_service"
        assert service.llm_client is not None, (
            "provider_router 在场时 compact llm_client 应为真 adapter"
        )

        agents_md = root / "behavior" / "system" / "AGENTS.md"
        agents_md.write_text(_REDUNDANT_AGENTS_MD, encoding="utf-8")

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            timeout=180.0,
        ) as client:
            resp = await client.post(
                "/api/behavior/compact/trigger", json={"file_id": "AGENTS.md"}
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        outcome = body["outcomes"][0]

        # 硬断言 1（管道通）：真 LLM 输出过了契约解析——不是 fallback
        assert outcome["status"] != "fallback", (
            f"真 LLM 输出未过契约 A' 解析（{outcome}）——检查分隔符 prompt 约束"
        )
        # 硬断言 2（质量下限）：面对三组明显冗余必须产出提议（而非被护栏全拒）
        assert outcome["status"] == "proposed", (
            f"真 LLM 未对明显冗余产出提议：reason={outcome.get('reason')}"
        )
        assert outcome["size_after"] < outcome["size_before"]

        sg = app.state.store_group
        candidate = await sg.behavior_compact_store.get_candidate(
            outcome["candidate_id"]
        )
        assert candidate is not None
        # 硬断言 3：PROTECTED 区段字节级保留（占位符方案全链形态）
        assert _PROTECTED_SECTION in candidate.compacted_content
        assert "<<<PROTECTED_" not in candidate.compacted_content
        # 硬断言 4：rationale 非空（人审展示可用）
        assert candidate.rationale.strip()

        # 质量观察（不作断言，人工复核语义保留——H4/H5 维度）
        print("\n===== F111 AC-12 真 LLM 质量观察 =====")
        print(f"size: {outcome['size_before']} → {outcome['size_after']}")
        print(f"rationale: {candidate.rationale}")
        print("----- 精简后全文 -----")
        print(candidate.compacted_content)
        print("===== 观察结束 =====")
    finally:
        await harness.shutdown(app)
