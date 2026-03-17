/**
 * SegmentedToggle -- 可视化 / Raw Data 模式切换控件
 */

export type ViewMode = "visual" | "raw";

interface SegmentedToggleProps {
  value: ViewMode;
  onChange: (mode: ViewMode) => void;
}

const OPTIONS: { value: ViewMode; label: string }[] = [
  { value: "visual", label: "可视化" },
  { value: "raw", label: "Raw Data" },
];

export default function SegmentedToggle({ value, onChange }: SegmentedToggleProps) {
  return (
    <div className="tv-segmented">
      {OPTIONS.map((opt) => (
        <button
          key={opt.value}
          className={`tv-segmented-option${value === opt.value ? " tv-segmented-option--active" : ""}`}
          onClick={() => onChange(opt.value)}
          type="button"
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
