import { useCallback, useEffect, useState } from "react";
import { fetchOperatorInbox, submitOperatorAction } from "../api/client";
import type {
  OperatorActionKind,
  OperatorActionResult,
  OperatorInboxItem,
  OperatorInboxResponse,
} from "../types";

export function useOperatorInbox() {
  const [inbox, setInbox] = useState<OperatorInboxResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyItemId, setBusyItemId] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<OperatorActionResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchOperatorInbox();
      setInbox(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load operator inbox");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function submitAction(item: OperatorInboxItem, kind: OperatorActionKind) {
    try {
      setBusyItemId(item.item_id);
      setError(null);
      const result = await submitOperatorAction({
        item_id: item.item_id,
        kind,
        source: "web",
        actor_id: "user:web",
        actor_label: "owner",
      });
      setLastResult(result);
      await load();
      return result;
    } catch (err) {
      setLastResult(null);
      setError(err instanceof Error ? err.message : "Failed to submit operator action");
      return null;
    } finally {
      setBusyItemId(null);
    }
  }

  return {
    inbox,
    loading,
    error,
    busyItemId,
    lastResult,
    reload: load,
    submitAction,
  };
}
