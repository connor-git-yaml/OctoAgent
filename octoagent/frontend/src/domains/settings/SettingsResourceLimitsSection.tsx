import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  AgentProfilesDocument,
  WorkerProfilesDocument,
} from "../../types";

// ─── 资源限制字段定义 ───

interface LimitFieldDef {
  key: string;
  label: string;
  description: string;
  unit: string;
  type: "int" | "float";
  placeholder: string;
}

const LIMIT_FIELDS: LimitFieldDef[] = [
  {
    key: "max_steps",
    label: "最大步数",
    description: "单次 Skill 最多执行的 LLM 调用轮次",
    unit: "步",
    type: "int",
    placeholder: "30",
  },
  {
    key: "max_budget_usd",
    label: "成本上限",
    description: "单次 Skill 最大 LLM 调用成本",
    unit: "USD",
    type: "float",
    placeholder: "0.30",
  },
  {
    key: "max_duration_seconds",
    label: "超时时间",
    description: "单次 Skill 最大运行时长",
    unit: "秒",
    type: "float",
    placeholder: "180",
  },
  {
    key: "max_tool_calls",
    label: "工具调用上限",
    description: "单次 Skill 最多工具调用次数",
    unit: "次",
    type: "int",
    placeholder: "20",
  },
  {
    key: "max_request_tokens",
    label: "请求 Token 上限",
    description: "累计发送给模型的 Token 数上限",
    unit: "Token",
    type: "int",
    placeholder: "不限",
  },
  {
    key: "max_response_tokens",
    label: "响应 Token 上限",
    description: "累计从模型收到的 Token 数上限",
    unit: "Token",
    type: "int",
    placeholder: "不限",
  },
];

// ─── 预设类型定义 ───

interface PresetInfo {
  label: string;
  description: string;
}

const PRESET_MAP: Record<string, PresetInfo> = {
  butler: { label: "Butler", description: "主 Agent 预设（50 步 / $0.50 / 5 分钟）" },
  worker: { label: "Worker 通用", description: "通用 Worker 预设（30 步 / $0.30 / 3 分钟）" },
  worker_coding: { label: "Coding Worker", description: "编码 Worker 预设（100 步 / $1.00 / 10 分钟）" },
  worker_research: { label: "Research Worker", description: "研究 Worker 预设（60 步 / $0.50 / 5 分钟）" },
  subagent: { label: "Subagent", description: "临时子代理预设（15 步 / $0.10 / 1 分钟）" },
};

const PRESET_VALUES: Record<string, Record<string, number>> = {
  butler: { max_steps: 50, max_budget_usd: 0.5, max_duration_seconds: 300, max_tool_calls: 30 },
  worker: { max_steps: 30, max_budget_usd: 0.3, max_duration_seconds: 180, max_tool_calls: 20 },
  worker_coding: { max_steps: 100, max_budget_usd: 1.0, max_duration_seconds: 600, max_tool_calls: 80 },
  worker_research: { max_steps: 60, max_budget_usd: 0.5, max_duration_seconds: 300, max_tool_calls: 40 },
  subagent: { max_steps: 15, max_budget_usd: 0.1, max_duration_seconds: 60, max_tool_calls: 10 },
};

// ─── 类型定义 ───

type TargetType = "agent_profile" | "worker_profile";

interface TargetOption {
  type: TargetType;
  id: string;
  label: string;
  sublabel: string;
}

type DraftLimits = Record<string, string>;

// ─── 组件 Props ───

interface SettingsResourceLimitsSectionProps {
  agentProfiles: AgentProfilesDocument | null;
  workerProfiles: WorkerProfilesDocument | null;
  onSubmit: (
    targetType: TargetType,
    profileId: string,
    limits: Record<string, unknown>,
  ) => Promise<void>;
  busy: boolean;
}

// ─── 辅助函数 ───

function limitsToFormState(limits: Record<string, unknown> | undefined): DraftLimits {
  const state: DraftLimits = {};
  for (const field of LIMIT_FIELDS) {
    const raw = limits?.[field.key];
    if (raw !== undefined && raw !== null && raw !== 0) {
      state[field.key] = String(raw);
    } else {
      state[field.key] = "";
    }
  }
  return state;
}

function formStateToPayload(draft: DraftLimits): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const field of LIMIT_FIELDS) {
    const raw = draft[field.key]?.trim();
    if (!raw) continue;
    const num = field.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
    if (!isNaN(num) && num > 0) {
      payload[field.key] = num;
    }
  }
  return payload;
}

function buildTargetOptions(
  agentProfiles: AgentProfilesDocument | null,
  workerProfiles: WorkerProfilesDocument | null,
): TargetOption[] {
  const options: TargetOption[] = [];
  if (agentProfiles) {
    for (const profile of agentProfiles.profiles) {
      options.push({
        type: "agent_profile",
        id: profile.profile_id,
        label: profile.name || profile.profile_id,
        sublabel: `Agent · ${profile.scope}`,
      });
    }
  }
  if (workerProfiles) {
    for (const profile of workerProfiles.profiles) {
      options.push({
        type: "worker_profile",
        id: profile.profile_id,
        label: profile.name || profile.profile_id,
        sublabel: `Worker · ${profile.static_config.base_archetype}`,
      });
    }
  }
  return options;
}

function getResourceLimitsForTarget(
  target: TargetOption | null,
  agentProfiles: AgentProfilesDocument | null,
  workerProfiles: WorkerProfilesDocument | null,
): Record<string, unknown> {
  if (!target) return {};
  if (target.type === "agent_profile" && agentProfiles) {
    const found = agentProfiles.profiles.find((p) => p.profile_id === target.id);
    return (found?.resource_limits as Record<string, unknown>) ?? {};
  }
  if (target.type === "worker_profile" && workerProfiles) {
    const found = workerProfiles.profiles.find((p) => p.profile_id === target.id);
    return (found?.static_config?.resource_limits as Record<string, unknown>) ?? {};
  }
  return {};
}

// ─── 主组件 ───

export default function SettingsResourceLimitsSection({
  agentProfiles,
  workerProfiles,
  onSubmit,
  busy,
}: SettingsResourceLimitsSectionProps) {
  const targets = useMemo(
    () => buildTargetOptions(agentProfiles, workerProfiles),
    [agentProfiles, workerProfiles],
  );
  const [selectedTargetKey, setSelectedTargetKey] = useState("");
  const [draft, setDraft] = useState<DraftLimits>({});
  const [saveMessage, setSaveMessage] = useState("");

  const selectedTarget = useMemo(
    () => targets.find((t) => `${t.type}:${t.id}` === selectedTargetKey) ?? null,
    [targets, selectedTargetKey],
  );

  // 选中第一个可用 target
  useEffect(() => {
    if (!selectedTargetKey && targets.length > 0) {
      const firstKey = `${targets[0].type}:${targets[0].id}`;
      setSelectedTargetKey(firstKey);
    }
  }, [targets, selectedTargetKey]);

  // 选择变更时加载当前值
  useEffect(() => {
    const current = getResourceLimitsForTarget(selectedTarget, agentProfiles, workerProfiles);
    setDraft(limitsToFormState(current));
    setSaveMessage("");
  }, [selectedTargetKey, agentProfiles, workerProfiles, selectedTarget]);

  const handleFieldChange = useCallback((key: string, value: string) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setSaveMessage("");
  }, []);

  const handleApplyPreset = useCallback((presetKey: string) => {
    const values = PRESET_VALUES[presetKey];
    if (!values) return;
    const next: DraftLimits = {};
    for (const field of LIMIT_FIELDS) {
      const presetVal = values[field.key];
      next[field.key] = presetVal !== undefined ? String(presetVal) : "";
    }
    setDraft(next);
    setSaveMessage("");
  }, []);

  const handleClear = useCallback(() => {
    const empty: DraftLimits = {};
    for (const field of LIMIT_FIELDS) {
      empty[field.key] = "";
    }
    setDraft(empty);
    setSaveMessage("");
  }, []);

  const handleSave = useCallback(async () => {
    if (!selectedTarget) return;
    const payload = formStateToPayload(draft);
    try {
      await onSubmit(selectedTarget.type, selectedTarget.id, payload);
      setSaveMessage("已保存");
      setTimeout(() => setSaveMessage(""), 3000);
    } catch {
      setSaveMessage("保存失败，请重试。");
    }
  }, [selectedTarget, draft, onSubmit]);

  const hasChanges = useMemo(() => {
    if (!selectedTarget) return false;
    const current = getResourceLimitsForTarget(selectedTarget, agentProfiles, workerProfiles);
    const currentForm = limitsToFormState(current);
    return LIMIT_FIELDS.some((f) => (draft[f.key] ?? "") !== (currentForm[f.key] ?? ""));
  }, [selectedTarget, draft, agentProfiles, workerProfiles]);

  if (targets.length === 0) {
    return null;
  }

  return (
    <section id="settings-group-resource-limits" className="wb-panel">
      <div className="wb-panel-head">
        <div>
          <p className="wb-card-label">Resource Limits</p>
          <h3>资源限制配置</h3>
        </div>
      </div>

      {/* 目标选择器 */}
      <div className="wb-form-grid" style={{ marginBottom: "1rem" }}>
        <div className="wb-field">
          <label className="wb-field-label">配置对象</label>
          <select
            className="wb-select"
            value={selectedTargetKey}
            onChange={(e) => setSelectedTargetKey(e.target.value)}
            disabled={busy}
          >
            {targets.map((target) => (
              <option key={`${target.type}:${target.id}`} value={`${target.type}:${target.id}`}>
                {target.label} ({target.sublabel})
              </option>
            ))}
          </select>
          <span className="wb-field-hint">
            选择要配置资源限制的 Agent 或 Worker。留空的字段将使用预设默认值。
          </span>
        </div>
      </div>

      {/* 预设快捷按钮 */}
      <div style={{ marginBottom: "1rem" }}>
        <p className="wb-card-label" style={{ marginBottom: "0.5rem" }}>
          快速预设
        </p>
        <div className="wb-inline-actions wb-inline-actions-wrap">
          {Object.entries(PRESET_MAP).map(([key, info]) => (
            <button
              key={key}
              type="button"
              className="wb-button wb-button-tertiary"
              onClick={() => handleApplyPreset(key)}
              disabled={busy}
              title={info.description}
            >
              {info.label}
            </button>
          ))}
          <button
            type="button"
            className="wb-button wb-button-tertiary"
            onClick={handleClear}
            disabled={busy}
            title="清空所有覆盖值，恢复系统默认"
          >
            清空覆盖
          </button>
        </div>
      </div>

      {/* 限制字段表单 */}
      <div className="wb-form-grid">
        {LIMIT_FIELDS.map((field) => (
          <div key={field.key} className="wb-field">
            <label className="wb-field-label">
              {field.label}
              <span className="wb-field-unit"> ({field.unit})</span>
            </label>
            <input
              type="text"
              inputMode="decimal"
              className="wb-input"
              placeholder={field.placeholder}
              value={draft[field.key] ?? ""}
              onChange={(e) => handleFieldChange(field.key, e.target.value)}
              disabled={busy}
            />
            <span className="wb-field-hint">{field.description}</span>
          </div>
        ))}
      </div>

      {/* 保存操作 */}
      <div className="wb-inline-actions" style={{ marginTop: "1rem" }}>
        <button
          type="button"
          className="wb-button wb-button-primary"
          onClick={() => void handleSave()}
          disabled={busy || !hasChanges}
        >
          保存资源限制
        </button>
        {saveMessage ? (
          <span
            className={`wb-status-pill ${saveMessage === "已保存" ? "is-ready" : "is-warning"}`}
            style={{ marginLeft: "0.75rem" }}
          >
            {saveMessage}
          </span>
        ) : hasChanges ? (
          <span className="wb-status-pill is-warning" style={{ marginLeft: "0.75rem" }}>
            有未保存的变更
          </span>
        ) : null}
      </div>
    </section>
  );
}
