import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import type { SkillProviderItem } from "../types";

interface SkillProviderDraft {
  provider_id: string;
  label: string;
  description: string;
  enabled: boolean;
  model_alias: string;
  worker_type: string;
  tool_profile: string;
  tools_allowed_text: string;
  prompt_template: string;
  install_hint: string;
}

const SKILL_PROVIDER_PRESETS: Array<{
  provider_id: string;
  label: string;
  description: string;
  model_alias: string;
  worker_type: string;
  tool_profile: string;
  tools_allowed: string[];
  prompt_template: string;
}> = [
  {
    provider_id: "repo-doc-brief",
    label: "仓库文档摘要",
    description: "先看项目与产物，再输出简短结论。",
    model_alias: "main",
    worker_type: "research",
    tool_profile: "minimal",
    tools_allowed: ["project.inspect", "artifact.list", "task.inspect"],
    prompt_template: "你负责快速阅读项目上下文与已有产物，只输出高密度摘要、已知事实和下一步建议。",
  },
  {
    provider_id: "dev-safe-patch",
    label: "安全修补建议",
    description: "限制工具面，优先阅读、定位和小步修补。",
    model_alias: "reasoning",
    worker_type: "dev",
    tool_profile: "standard",
    tools_allowed: ["project.inspect", "task.inspect", "artifact.list"],
    prompt_template: "你负责在受控边界内提出并执行小步修补，先说明风险和影响，再做最小必要改动。",
  },
];

function emptyDraft(): SkillProviderDraft {
  return {
    provider_id: "",
    label: "",
    description: "",
    enabled: true,
    model_alias: "main",
    worker_type: "general",
    tool_profile: "minimal",
    tools_allowed_text: "",
    prompt_template: "",
    install_hint: "",
  };
}

function joinLines(values: string[]): string {
  return values.join("\n");
}

function parseLines(value: string): string[] {
  return value
    .split(/[\n,]+/g)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item, index, all) => all.indexOf(item) === index);
}

function draftFromItem(item: SkillProviderItem): SkillProviderDraft {
  return {
    provider_id: item.provider_id,
    label: item.label,
    description: item.description,
    enabled: item.enabled,
    model_alias: item.model_alias,
    worker_type: item.worker_type,
    tool_profile: item.tool_profile,
    tools_allowed_text: joinLines(item.tools_allowed),
    prompt_template: item.prompt_template,
    install_hint: item.install_hint,
  };
}

export default function SkillProviderCenter() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const catalog = snapshot!.resources.skill_provider_catalog;
  const capabilityPack = snapshot!.resources.capability_pack.pack;
  const items = catalog.items;
  const toolOptions = capabilityPack.tools.map((tool) => ({
    tool_name: tool.tool_name,
    label: tool.label,
  }));
  const modelAliasOptions = Object.keys(
    (snapshot!.resources.config.current_value.model_aliases as Record<string, unknown>) ?? {}
  );
  const [selectedProviderId, setSelectedProviderId] = useState(items[0]?.provider_id ?? "");
  const [editorMode, setEditorMode] = useState<"existing" | "create">(
    items[0] ? "existing" : "create"
  );
  const [draft, setDraft] = useState<SkillProviderDraft>(
    items[0] ? draftFromItem(items[0]) : emptyDraft()
  );

  useEffect(() => {
    const selected =
      items.find((item) => item.provider_id === selectedProviderId) ?? items[0] ?? null;
    if (editorMode === "existing" && selected) {
      setSelectedProviderId(selected.provider_id);
      setDraft(draftFromItem(selected));
      return;
    }
    if (!selected) {
      setSelectedProviderId("");
      setDraft(emptyDraft());
      setEditorMode("create");
    }
  }, [catalog.generated_at]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedItem =
    items.find((item) => item.provider_id === selectedProviderId) ?? null;
  const installedCustomCount = Number(catalog.summary.custom_count ?? 0);
  const builtinCount = Number(catalog.summary.builtin_count ?? 0);
  const busy =
    busyActionId === "skill_provider.save" || busyActionId === "skill_provider.delete";

  const recommendedTools = useMemo(
    () =>
      toolOptions
        .filter((tool) =>
          ["project.inspect", "task.inspect", "artifact.list", "work.inspect"].includes(
            tool.tool_name
          )
        )
        .slice(0, 8),
    [toolOptions]
  );

  function updateDraft<Key extends keyof SkillProviderDraft>(
    key: Key,
    value: SkillProviderDraft[Key]
  ) {
    setDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function appendTool(toolName: string) {
    setDraft((current) => ({
      ...current,
      tools_allowed_text: joinLines(
        Array.from(new Set([...parseLines(current.tools_allowed_text), toolName]))
      ),
    }));
  }

  function selectItem(item: SkillProviderItem) {
    setEditorMode("existing");
    setSelectedProviderId(item.provider_id);
    setDraft(draftFromItem(item));
  }

  function handleCreate() {
    setEditorMode("create");
    setSelectedProviderId("");
    setDraft(emptyDraft());
  }

  function applyPreset(providerId: string) {
    const preset = SKILL_PROVIDER_PRESETS.find((item) => item.provider_id === providerId);
    if (!preset) {
      return;
    }
    setEditorMode("create");
    setSelectedProviderId("");
    setDraft({
      provider_id: preset.provider_id,
      label: preset.label,
      description: preset.description,
      enabled: true,
      model_alias: preset.model_alias,
      worker_type: preset.worker_type,
      tool_profile: preset.tool_profile,
      tools_allowed_text: joinLines(preset.tools_allowed),
      prompt_template: preset.prompt_template,
      install_hint: "",
    });
  }

  async function handleSave() {
    const result = await submitAction("skill_provider.save", {
      provider: {
        provider_id: draft.provider_id,
        label: draft.label,
        description: draft.description,
        enabled: draft.enabled,
        model_alias: draft.model_alias,
        worker_type: draft.worker_type,
        tool_profile: draft.tool_profile,
        tools_allowed: parseLines(draft.tools_allowed_text),
        prompt_template: draft.prompt_template,
        install_hint: draft.install_hint,
      },
    });
    if (result) {
      setEditorMode("existing");
      setSelectedProviderId(draft.provider_id);
    }
  }

  async function handleDelete() {
    if (!selectedItem || !selectedItem.removable) {
      return;
    }
    const result = await submitAction("skill_provider.delete", {
      provider_id: selectedItem.provider_id,
    });
    if (result) {
      handleCreate();
    }
  }

  return (
    <div className="wb-page">
      <section className="wb-hero wb-hero-compact">
        <div className="wb-hero-copy">
          <p className="wb-kicker">Skill Providers</p>
          <h1>把 Skills 当作可安装的能力 Provider 来管理</h1>
          <p>
            这里负责安装、编辑和删除 Skill Provider。安装完成后，先去
            <Link to="/agents?view=providers"> Agents &gt; Providers</Link> 决定当前
            Project 的默认范围，再回 Butler 或 Worker 模板做更细的绑定。
          </p>
          <div className="wb-chip-row">
            <span className="wb-chip">已安装 {items.length}</span>
            <span className="wb-chip">自定义 {installedCustomCount}</span>
            <span className="wb-chip">内置 {builtinCount}</span>
          </div>
        </div>
        <div className="wb-hero-actions">
          <Link className="wb-button wb-button-secondary" to="/agents?view=providers">
            返回 Agents &gt; Providers
          </Link>
          <Link className="wb-button wb-button-tertiary" to="/agents?view=templates">
            去 Worker 模板绑定
          </Link>
        </div>
      </section>

      <section className="wb-card-grid wb-card-grid-3">
        {SKILL_PROVIDER_PRESETS.map((preset) => (
          <article key={preset.provider_id} className="wb-card">
            <p className="wb-card-label">快速安装</p>
            <strong>{preset.label}</strong>
            <span>{preset.description}</span>
            <button
              type="button"
              className="wb-button wb-button-tertiary"
              onClick={() => applyPreset(preset.provider_id)}
            >
              用这个模板新建
            </button>
          </article>
        ))}
      </section>

      <div className="wb-split">
        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">已安装</p>
              <h3>当前 Skill Provider 目录</h3>
            </div>
            <button type="button" className="wb-button wb-button-primary" onClick={handleCreate}>
              新建 Provider
            </button>
          </div>
          <div className="wb-note-stack">
            {items.map((item) => (
              <button
                key={item.provider_id}
                type="button"
                className={`wb-list-row ${selectedProviderId === item.provider_id ? "is-active" : ""}`}
                onClick={() => selectItem(item)}
              >
                <div>
                  <strong>{item.label}</strong>
                  <p>{item.description || item.provider_id}</p>
                  <div className="wb-chip-row">
                    <span className="wb-chip">{item.source_kind}</span>
                    <span className={`wb-status-pill is-${item.availability}`}>
                      {item.availability}
                    </span>
                    <span className="wb-chip">{item.worker_type}</span>
                    <span className="wb-chip">{item.model_alias}</span>
                  </div>
                </div>
                <div className="wb-list-meta">
                  <strong>{item.provider_id}</strong>
                  <span>{item.enabled ? "已启用" : "已停用"}</span>
                </div>
              </button>
            ))}
            {items.length === 0 ? (
              <div className="wb-empty-state">
                <strong>当前还没有自定义 Skill Provider</strong>
                <span>可以直接从右侧新建，或先用上面的模板快速生成一份。</span>
              </div>
            ) : null}
          </div>
        </section>

        <section className="wb-panel">
          <div className="wb-panel-head">
            <div>
              <p className="wb-card-label">编辑器</p>
              <h3>{editorMode === "create" ? "安装新的 Skill Provider" : draft.label || draft.provider_id}</h3>
            </div>
            {selectedItem?.removable ? (
              <button
                type="button"
                className="wb-button wb-button-tertiary"
                onClick={() => void handleDelete()}
                disabled={busy}
              >
                删除
              </button>
            ) : null}
          </div>

          {selectedItem && !selectedItem.editable ? (
            <div className="wb-inline-banner is-muted">
              <strong>这是内置 Provider</strong>
              <span>可以查看它的边界与默认配置，但不能直接修改。要定制一份，请从模板新建。</span>
            </div>
          ) : null}

          <div className="wb-agent-form-grid">
            <label className="wb-field">
              <span>Provider ID</span>
              <input
                type="text"
                value={draft.provider_id}
                disabled={editorMode === "existing"}
                onChange={(event) => updateDraft("provider_id", event.target.value)}
                placeholder="例如 repo-doc-brief"
              />
            </label>
            <label className="wb-field">
              <span>显示名称</span>
              <input
                type="text"
                value={draft.label}
                disabled={selectedItem ? !selectedItem.editable : false}
                onChange={(event) => updateDraft("label", event.target.value)}
                placeholder="例如 仓库文档摘要"
              />
            </label>
            <label className="wb-field">
              <span>模型别名</span>
              <select
                value={draft.model_alias}
                disabled={selectedItem ? !selectedItem.editable : false}
                onChange={(event) => updateDraft("model_alias", event.target.value)}
              >
                {Array.from(new Set(["main", "cheap", "reasoning", ...modelAliasOptions])).map(
                  (option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  )
                )}
              </select>
            </label>
            <label className="wb-field">
              <span>默认 Worker</span>
              <select
                value={draft.worker_type}
                disabled={selectedItem ? !selectedItem.editable : false}
                onChange={(event) => updateDraft("worker_type", event.target.value)}
              >
                <option value="general">general</option>
                <option value="ops">ops</option>
                <option value="research">research</option>
                <option value="dev">dev</option>
              </select>
            </label>
            <label className="wb-field">
              <span>工具边界</span>
              <select
                value={draft.tool_profile}
                disabled={selectedItem ? !selectedItem.editable : false}
                onChange={(event) => updateDraft("tool_profile", event.target.value)}
              >
                <option value="minimal">minimal</option>
                <option value="standard">standard</option>
                <option value="privileged">privileged</option>
              </select>
            </label>
            <label className="wb-field">
              <span>启用状态</span>
              <select
                value={draft.enabled ? "enabled" : "disabled"}
                disabled={selectedItem ? !selectedItem.editable : false}
                onChange={(event) => updateDraft("enabled", event.target.value === "enabled")}
              >
                <option value="enabled">启用</option>
                <option value="disabled">停用</option>
              </select>
            </label>
            <label className="wb-field wb-field-span-2">
              <span>描述</span>
              <input
                type="text"
                value={draft.description}
                disabled={selectedItem ? !selectedItem.editable : false}
                onChange={(event) => updateDraft("description", event.target.value)}
                placeholder="一句话说明什么时候应该调用它"
              />
            </label>
            <label className="wb-field wb-field-span-2">
              <span>Prompt 模板</span>
              <textarea
                rows={5}
                className="wb-textarea-prose"
                value={draft.prompt_template}
                disabled={selectedItem ? !selectedItem.editable : false}
                onChange={(event) => updateDraft("prompt_template", event.target.value)}
                placeholder="写清楚这个 Provider 的默认职责、边界和输出方式。"
              />
            </label>
            <label className="wb-field wb-field-span-2">
              <span>允许的工具</span>
              <textarea
                value={draft.tools_allowed_text}
                disabled={selectedItem ? !selectedItem.editable : false}
                onChange={(event) => updateDraft("tools_allowed_text", event.target.value)}
                placeholder="每行一个 tool_name"
              />
            </label>
            <label className="wb-field wb-field-span-2">
              <span>安装提示</span>
              <input
                type="text"
                value={draft.install_hint}
                disabled={selectedItem ? !selectedItem.editable : false}
                onChange={(event) => updateDraft("install_hint", event.target.value)}
                placeholder="可选，用于提示依赖或前置条件"
              />
            </label>
          </div>

          <div className="wb-root-agent-token-card">
            <div className="wb-root-agent-column-head">
              <strong>推荐工具</strong>
              <span>点一下就能追加到允许列表</span>
            </div>
            <div className="wb-chip-row">
              {recommendedTools.map((tool) => (
                <button
                  key={tool.tool_name}
                  type="button"
                  className="wb-chip-button"
                  onClick={() => appendTool(tool.tool_name)}
                  disabled={selectedItem ? !selectedItem.editable : false}
                >
                  {tool.label}
                </button>
              ))}
            </div>
          </div>

          {selectedItem?.warnings.length ? (
            <div className="wb-note-stack">
              {selectedItem.warnings.map((warning) => (
                <div key={warning} className="wb-note">
                  <strong>提醒</strong>
                  <span>{warning}</span>
                </div>
              ))}
            </div>
          ) : null}

          <div className="wb-inline-actions wb-inline-actions-wrap">
            <button
              type="button"
              className="wb-button wb-button-primary"
              onClick={() => void handleSave()}
              disabled={busy || (selectedItem ? !selectedItem.editable : false)}
            >
              保存 Provider
            </button>
            <button type="button" className="wb-button wb-button-secondary" onClick={handleCreate}>
              清空表单
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
