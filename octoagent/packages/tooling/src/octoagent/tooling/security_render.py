"""F124 T020: render_tool_result_for_llm —— tool 结果进 LLM 文本的唯一 finding-aware 渲染 helper。

plan FR-3.1/3.5：**任何"（持久化）tool result → LLM 可见文本"的消费者 MUST 经此 helper**，
从 security_findings 派生 `[security-warning]` 前缀，**不改 raw output/error**（机器消费者如
tool_search 读 fb.output 原值，FR-2.4）。

已知消费者（plan §6 sink 清单）：
- live：`skills/provider_model_client.py:_append_feedback_to_history`（D1，T021）
- 再入口（从**持久化** finding 重渲染，D2/T025-T026）：agent_context replay 投影 /
  context_compaction message builder / session_memory_extractor / research handoff

no-bypass 契约测试（T027）守护：LLM-bound sink 不得绕过本 helper 直拼 raw tool 内容。
"""

from __future__ import annotations

from .models import ToolSecurityFinding


def render_tool_result_for_llm(
    text: str, findings: list[ToolSecurityFinding] | None
) -> str:
    """有 finding 则前置确定性 `[security-warning]` 标注；否则原样返回。

    - **确定性**（无时间戳/随机）：replay / prefix-cache 稳定（FR-3.2）。
    - **不回显恶意片段**：标注来自 finding.advisory 固定文案，非命中的原始内容（FR-3.2）。
    - **不改入参原文**：纯前置，raw output/error 不动（FR-2.4）。

    Args:
        text: 即将进入 LLM 的 tool 结果文本（output 或 error 渲染体）。
        findings: 该 tool 结果的威胁 finding（None/空 → 不标注）。

    Returns:
        标注后的文本（有 finding）或原文（无 finding）。
    """
    if not findings:
        return text
    # 去重 advisory 保序（findings 通常 1 条；多条取各自 advisory 去重）
    advisories = list(dict.fromkeys(f.advisory for f in findings if f.advisory))
    if not advisories:
        return text
    return "\n".join(advisories) + "\n---\n" + text


def findings_from_turn_metadata(
    metadata: dict | None,
) -> list[ToolSecurityFinding]:
    """从持久化的 turn metadata（`security_findings: list[dict]`）重建 finding（F124 D2/E1）。

    JSON-native 往返：E1 用 `model_dump(mode="json")` 写入，这里 `ToolSecurityFinding(**d)` 还原。
    容错：缺字段/坏数据跳过（degrade gracefully，不因脏数据丢整条渲染）。
    """
    if not metadata:
        return []
    raw = metadata.get("security_findings") or []
    out: list[ToolSecurityFinding] = []
    for d in raw:
        if isinstance(d, dict):
            try:
                out.append(ToolSecurityFinding(**d))
            except Exception:
                continue
    return out


def render_persisted_tool_turn_for_llm(
    summary: str, turn_metadata: dict | None
) -> str:
    """D2 再入口唯一渲染：从持久化 turn metadata 重建 finding 并对 summary 重加 `[security-warning]`。

    replay / compaction / memory-extraction 等"持久化 tool result → LLM 文本"消费者 MUST 经此
    （而非直接拼 turn.summary），保证重启/replay 后标注不丢（FR-3.1/3.4/3.5）。
    """
    return render_tool_result_for_llm(summary, findings_from_turn_metadata(turn_metadata))
