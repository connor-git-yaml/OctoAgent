/**
 * PendingChangesBar — Feature 079 Phase 1。
 *
 * 解决"OAuth 授权成功但没真正入 config"的 UX 断层：React state 里 providers /
 * aliases / secrets 已经改了，但还没走 setup.apply；此时在 Settings 页顶部弹一个
 * 粘性 bar，显式提示"你有 N 个未保存变更"，并给一个一键保存按钮，避免用户以为
 * "授权成功 = 系统已启用"。
 *
 * 不做事：
 * - 不做校验（校验在 buildSetupDraft / 后端 review）
 * - 不做诊断（诊断在 SettingsOverview 的 review 面板）
 * - 仅纯 UI 提示 + 一键转交 onSave
 */

interface PendingChangesBarProps {
  /** 当前是否有未保存变更 */
  hasChanges: boolean;
  /** 具体变更类别列表（如 ["providers", "model_aliases"]） */
  categories: string[];
  /** 是否正在保存 */
  busy: boolean;
  /** 点"立即保存"触发 */
  onSave: () => void;
}

const CATEGORY_LABELS: Record<string, string> = {
  providers: "Provider 列表",
  model_aliases: "模型别名",
  runtime: "Runtime / Proxy",
  secrets: "未保存的凭证",
};

function renderCategories(categories: string[]): string {
  if (categories.length === 0) {
    return "一些配置";
  }
  return categories
    .map((cat) => CATEGORY_LABELS[cat] ?? cat)
    .join("、");
}

export default function PendingChangesBar({
  hasChanges,
  categories,
  busy,
  onSave,
}: PendingChangesBarProps) {
  if (!hasChanges) {
    return null;
  }
  return (
    <div
      className="wb-pending-changes-bar"
      role="status"
      aria-live="polite"
      data-testid="settings-pending-changes-bar"
    >
      <div className="wb-pending-changes-bar-main">
        <strong>你有未保存的变更</strong>
        <span className="wb-muted">
          刚改动的 {renderCategories(categories)} 还没落盘。完成保存后主 Agent 才会用上新配置。
        </span>
      </div>
      <div className="wb-pending-changes-bar-actions">
        <button
          type="button"
          className="wb-button wb-button-primary"
          onClick={onSave}
          disabled={busy}
        >
          {busy ? "正在保存…" : "立即保存"}
        </button>
      </div>
    </div>
  );
}
