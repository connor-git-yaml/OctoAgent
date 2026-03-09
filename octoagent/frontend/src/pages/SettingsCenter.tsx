import { useEffect, useState } from "react";
import { useWorkbench } from "../components/shell/WorkbenchLayout";
import {
  categoryForHint,
  deepClone,
  findSchemaNode,
  getValueAtPath,
  parseFieldStateValue,
  setValueAtPath,
  widgetValueToFieldState,
} from "../workbench/utils";
import type { ConfigFieldHint } from "../types";

type FieldState = Record<string, string | boolean>;
type FieldErrors = Record<string, string>;

function buildFieldState(
  hints: Record<string, ConfigFieldHint>,
  currentValue: Record<string, unknown>
): FieldState {
  return Object.fromEntries(
    Object.values(hints).map((hint) => [
      hint.field_path,
      widgetValueToFieldState(hint, getValueAtPath(currentValue, hint.field_path)),
    ])
  );
}

function buildConfigPayload(
  baseConfig: Record<string, unknown>,
  hints: Record<string, ConfigFieldHint>,
  fieldState: FieldState
): { config: Record<string, unknown>; errors: FieldErrors } {
  const nextConfig = deepClone(baseConfig);
  const errors: FieldErrors = {};

  Object.values(hints).forEach((hint) => {
    const parsed = parseFieldStateValue(hint, fieldState[hint.field_path] ?? "");
    if (parsed.error) {
      errors[hint.field_path] = parsed.error;
      return;
    }
    setValueAtPath(nextConfig, hint.field_path, parsed.value);
  });

  return { config: nextConfig, errors };
}

function groupLabel(groupId: string): { title: string; description: string } {
  switch (groupId) {
    case "main-agent":
      return {
        title: "Main Agent",
        description: "模型、provider、默认运行方式和主入口相关配置。",
      };
    case "channels":
      return {
        title: "Channels",
        description: "先把 Telegram 等渠道接入方式和可见范围说明白。",
      };
    default:
      return {
        title: "Advanced Fields",
        description: "这些字段已经暴露在 contract 中，但更偏高级设置。",
      };
  }
}

function selectOptions(
  schema: Record<string, unknown>,
  hint: ConfigFieldHint
): string[] {
  const schemaNode = findSchemaNode(schema, hint.field_path);
  const rawEnum = schemaNode?.enum;
  if (!Array.isArray(rawEnum)) {
    return [];
  }
  return rawEnum.map((item) => String(item));
}

export default function SettingsCenter() {
  const { snapshot, submitAction, busyActionId } = useWorkbench();
  const config = snapshot!.resources.config;
  const selector = snapshot!.resources.project_selector;
  const memory = snapshot!.resources.memory;
  const delegation = snapshot!.resources.delegation;
  const [fieldState, setFieldState] = useState<FieldState>(() =>
    buildFieldState(config.ui_hints, config.current_value)
  );
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});

  useEffect(() => {
    setFieldState(buildFieldState(config.ui_hints, config.current_value));
    setFieldErrors({});
  }, [config.generated_at]);

  const groupedHints = Object.values(config.ui_hints)
    .sort((left, right) => left.order - right.order)
    .reduce<Record<string, ConfigFieldHint[]>>((groups, hint) => {
      const key = categoryForHint(hint);
      groups[key] = [...(groups[key] ?? []), hint];
      return groups;
    }, {});

  async function handleSave() {
    const result = buildConfigPayload(config.current_value, config.ui_hints, fieldState);
    setFieldErrors(result.errors);
    if (Object.keys(result.errors).length > 0) {
      return;
    }
    await submitAction("config.apply", { config: result.config });
  }

  return (
    <div className="wb-page">
      <section className="wb-hero">
        <div>
          <p className="wb-kicker">Settings</p>
          <h1>图形化改配置，不再先想命令</h1>
          <p>
            这里仍然直接消费 `ConfigSchemaDocument + ui_hints + config.apply`，
            只是把它重新组织成更接近用户语言的分组。
          </p>
        </div>
        <div className="wb-hero-actions">
          <button
            type="button"
            className="wb-button wb-button-primary"
            onClick={() => void handleSave()}
            disabled={busyActionId === "config.apply"}
          >
            保存设置
          </button>
        </div>
      </section>

      <div className="wb-card-grid wb-card-grid-3">
        <article className="wb-card">
          <p className="wb-card-label">当前 Project</p>
          <strong>{selector.current_project_id}</strong>
          <span>workspace {selector.current_workspace_id}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">Memory 状态</p>
          <strong>{memory.status}</strong>
          <span>current {memory.summary.sor_current_count}</span>
        </article>
        <article className="wb-card">
          <p className="wb-card-label">Work 运行态</p>
          <strong>{delegation.works.length}</strong>
          <span>delegated works visible in current project</span>
        </article>
      </div>

      {Object.entries(groupedHints).map(([groupId, hints]) => {
        const group = groupLabel(groupId);
        return (
          <section key={groupId} className="wb-panel">
            <div className="wb-panel-head">
              <div>
                <p className="wb-card-label">{group.title}</p>
                <h3>{group.description}</h3>
              </div>
            </div>
            <div className="wb-form-grid">
              {hints.map((hint) => {
                const currentValue = fieldState[hint.field_path] ?? "";
                const options = selectOptions(config.schema, hint);
                const error = fieldErrors[hint.field_path];
                return (
                  <label key={hint.field_path} className="wb-field">
                    <span>{hint.label}</span>
                    {hint.help_text ? <small>{hint.help_text}</small> : null}
                    {hint.widget === "toggle" ? (
                      <input
                        type="checkbox"
                        checked={Boolean(currentValue)}
                        onChange={(event) =>
                          setFieldState((state) => ({
                            ...state,
                            [hint.field_path]: event.target.checked,
                          }))
                        }
                      />
                    ) : hint.widget === "select" && options.length > 0 ? (
                      <select
                        value={String(currentValue)}
                        onChange={(event) =>
                          setFieldState((state) => ({
                            ...state,
                            [hint.field_path]: event.target.value,
                          }))
                        }
                      >
                        {options.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                    ) : hint.widget === "string-list" ||
                      hint.widget === "provider-list" ||
                      hint.widget === "alias-map" ? (
                      <textarea
                        rows={hint.widget === "string-list" ? 4 : 8}
                        value={String(currentValue)}
                        placeholder={hint.placeholder}
                        onChange={(event) =>
                          setFieldState((state) => ({
                            ...state,
                            [hint.field_path]: event.target.value,
                          }))
                        }
                      />
                    ) : (
                      <input
                        type="text"
                        value={String(currentValue)}
                        placeholder={hint.placeholder}
                        onChange={(event) =>
                          setFieldState((state) => ({
                            ...state,
                            [hint.field_path]: event.target.value,
                          }))
                        }
                      />
                    )}
                    {hint.description ? <small>{hint.description}</small> : null}
                    {error ? <small className="wb-field-error">{error}</small> : null}
                  </label>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
