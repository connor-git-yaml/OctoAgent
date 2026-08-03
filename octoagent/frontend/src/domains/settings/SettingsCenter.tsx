import { useWorkbench } from "../../components/shell/WorkbenchLayout";
import MaintenanceRecoverySection from "./MaintenanceRecoverySection";
import { RemoteAccessSettings } from "./RemoteAccessSettings";
import SettingsPage from "./SettingsPage";

function hasConnectedProvider(value: unknown): boolean {
  return (
    Array.isArray(value) &&
    value.some(
      (item) =>
        typeof item === "object" &&
        item !== null &&
        (item as Record<string, unknown>).enabled !== false,
    )
  );
}

export default function SettingsCenter() {
  const { snapshot } = useWorkbench();
  const currentValue = snapshot?.resources.config?.current_value;
  const providers =
    typeof currentValue === "object" && currentValue !== null
      ? (currentValue as Record<string, unknown>).providers
      : null;

  return (
    <div className="f149-settings-composition">
      <SettingsPage remoteAccess={<RemoteAccessSettings />} />
      <MaintenanceRecoverySection connected={hasConnectedProvider(providers)} />
    </div>
  );
}
