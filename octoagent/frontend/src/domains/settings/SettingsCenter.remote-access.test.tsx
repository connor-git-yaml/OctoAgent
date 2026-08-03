import { type ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SettingsCenter from "./SettingsCenter";

const ORACLE = "F158_F150_SETTINGS_COMPOSITION_MISSING";

vi.mock("../../components/shell/WorkbenchLayout", () => ({
  useWorkbench: () => ({
    snapshot: {
      resources: {
        config: {
          current_value: {
            providers: [],
          },
        },
      },
    },
  }),
}));

vi.mock("./SettingsPage", () => ({
  default: ({ remoteAccess }: { remoteAccess?: ReactNode }) => (
    <section aria-label="设置主体">{remoteAccess}</section>
  ),
}));

vi.mock("./MaintenanceRecoverySection", () => ({
  default: () => <section aria-label="维护与恢复" />,
}));

vi.mock("./RemoteAccessSettings", () => ({
  RemoteAccessSettings: () => (
    <section
      aria-label="远程访问"
      data-visual-baseline="claude-design-original"
    />
  ),
}));

describe("SettingsCenter · F150 用户可达闭包", () => {
  it("在正式 Settings composition 中渲染唯一远程访问入口", () => {
    const { container } = render(<SettingsCenter />);

    expect(
      screen.getByRole("region", { name: "远程访问" }),
      ORACLE
    ).toBeInTheDocument();
    expect(
      container.querySelectorAll(
        '[data-visual-baseline="claude-design-original"]'
      ),
      ORACLE
    ).toHaveLength(1);
  });
});
