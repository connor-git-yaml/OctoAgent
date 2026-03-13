import type { ConfigFieldHint } from "../../types";
import {
  buildFieldGuide,
  optionLabelForHint,
  selectOptions,
  type FieldErrors,
  type FieldState,
} from "./shared";

interface SettingsHintFieldsProps {
  hints: ConfigFieldHint[];
  schema: Record<string, unknown>;
  fieldState: FieldState;
  fieldErrors: FieldErrors;
  usingEchoMode: boolean;
  onFieldValueChange: (fieldPath: string, value: string | boolean) => void;
}

export default function SettingsHintFields({
  hints,
  schema,
  fieldState,
  fieldErrors,
  usingEchoMode,
  onFieldValueChange,
}: SettingsHintFieldsProps) {
  return (
    <div className="wb-form-grid">
      {hints.map((hint) => {
        const currentValue = fieldState[hint.field_path] ?? "";
        const options = selectOptions(schema, hint);
        const error = fieldErrors[hint.field_path];
        const fieldGuide = buildFieldGuide(hint, usingEchoMode);
        return (
          <label
            key={hint.field_path}
            className={`wb-field ${hint.multiline ? "wb-field-span-2" : ""}`}
          >
            <span>{hint.label}</span>
            {hint.help_text ? <small>{hint.help_text}</small> : null}
            {fieldGuide ? (
              <details className="wb-field-guide wb-field-guide-disclosure">
                <summary>{fieldGuide.title}</summary>
                <p>{fieldGuide.description}</p>
                {fieldGuide.actions?.length ? (
                  <div className="wb-inline-actions wb-inline-actions-wrap">
                    {fieldGuide.actions.map((action) => (
                      <button
                        key={action.label}
                        type="button"
                        aria-label={action.label}
                        className="wb-button wb-button-tertiary wb-button-inline"
                        onClick={() => onFieldValueChange(hint.field_path, action.value)}
                      >
                        {action.label}
                      </button>
                    ))}
                  </div>
                ) : null}
                {fieldGuide.example ? (
                  <div className="wb-field-guide-sample">
                    <small>{fieldGuide.exampleLabel ?? "示例"}</small>
                    <pre>{fieldGuide.example}</pre>
                  </div>
                ) : null}
              </details>
            ) : null}
            {hint.widget === "toggle" ? (
              <input
                type="checkbox"
                checked={Boolean(currentValue)}
                onChange={(event) => onFieldValueChange(hint.field_path, event.target.checked)}
              />
            ) : hint.widget === "select" && options.length > 0 ? (
              <select
                value={String(currentValue)}
                onChange={(event) => onFieldValueChange(hint.field_path, event.target.value)}
              >
                {options.map((option) => (
                  <option key={option} value={option}>
                    {optionLabelForHint(hint, option)}
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
                onChange={(event) => onFieldValueChange(hint.field_path, event.target.value)}
              />
            ) : (
              <input
                type="text"
                value={String(currentValue)}
                placeholder={hint.placeholder}
                onChange={(event) => onFieldValueChange(hint.field_path, event.target.value)}
              />
            )}
            {hint.description ? <small>{hint.description}</small> : null}
            {error ? <small className="wb-field-error">{error}</small> : null}
          </label>
        );
      })}
    </div>
  );
}
