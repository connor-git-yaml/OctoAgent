import { startTransition, useEffect, useMemo, useState } from "react";
import { fetchWorkerProfileRevisions } from "../../api/client";
import type {
  ControlPlaneSnapshot,
  WorkerProfileItem,
  WorkerProfileRevisionItem,
} from "../../types";
import {
  appendStudioListValue,
  buildRootAgentPayload,
  buildRootAgentStudioDraft,
  type CapabilityProviderEntry,
  type RootAgentReviewResult,
  type RootAgentStudioDraft,
} from "./agentCenterData";

type RootAgentEditorMode = "existing" | "create";

interface UseRootAgentStudioArgs {
  rootAgentProfiles: WorkerProfileItem[];
  rootAgentProfilesGeneratedAt: string;
  selector: ControlPlaneSnapshot["resources"]["project_selector"];
  capabilityProviderEntries: CapabilityProviderEntry[];
}

export function useRootAgentStudio({
  rootAgentProfiles,
  rootAgentProfilesGeneratedAt,
  selector,
  capabilityProviderEntries,
}: UseRootAgentStudioArgs) {
  const [selectedRootAgentId, setSelectedRootAgentId] = useState(
    rootAgentProfiles[0]?.profile_id ?? ""
  );
  const [rootAgentDraft, setRootAgentDraft] = useState<RootAgentStudioDraft>(() =>
    buildRootAgentStudioDraft(rootAgentProfiles[0] ?? null, selector, capabilityProviderEntries)
  );
  const [rootAgentReview, setRootAgentReview] = useState<RootAgentReviewResult | null>(null);
  const [rootAgentRevisions, setRootAgentRevisions] = useState<WorkerProfileRevisionItem[]>([]);
  const [rootAgentRevisionLoading, setRootAgentRevisionLoading] = useState(false);
  const [rootAgentRevisionError, setRootAgentRevisionError] = useState("");
  const [rootAgentSpawnObjective, setRootAgentSpawnObjective] = useState("");
  const [rootAgentEditorMode, setRootAgentEditorMode] =
    useState<RootAgentEditorMode>(rootAgentProfiles[0] ? "existing" : "create");

  const selectedRootAgentProfile = useMemo(
    () => rootAgentProfiles.find((profile) => profile.profile_id === selectedRootAgentId) ?? null,
    [rootAgentProfiles, selectedRootAgentId]
  );

  const rootAgentDraftDirty = useMemo(
    () =>
      JSON.stringify(buildRootAgentPayload(rootAgentDraft, capabilityProviderEntries)) !==
      JSON.stringify(
        buildRootAgentPayload(
          buildRootAgentStudioDraft(selectedRootAgentProfile, selector, capabilityProviderEntries),
          capabilityProviderEntries
        )
      ),
    [capabilityProviderEntries, rootAgentDraft, selectedRootAgentProfile, selector]
  );

  const rootAgentRevisionSyncKey = [
    selectedRootAgentId,
    selectedRootAgentProfile?.active_revision ?? 0,
    selectedRootAgentProfile?.draft_revision ?? 0,
  ].join(":");

  useEffect(() => {
    if (rootAgentEditorMode === "create") {
      return;
    }
    const nextSelectedId =
      rootAgentProfiles.find((profile) => profile.profile_id === selectedRootAgentId)?.profile_id ??
      rootAgentProfiles.find((profile) => profile.origin_kind !== "builtin")?.profile_id ??
      rootAgentProfiles[0]?.profile_id ??
      "";
    const nextSelectedProfile =
      rootAgentProfiles.find((profile) => profile.profile_id === nextSelectedId) ?? null;
    if (nextSelectedProfile === null) {
      setSelectedRootAgentId("");
      setRootAgentDraft(buildRootAgentStudioDraft(null, selector, capabilityProviderEntries));
      setRootAgentReview(null);
      setRootAgentEditorMode("create");
      return;
    }
    if (nextSelectedId !== selectedRootAgentId) {
      setSelectedRootAgentId(nextSelectedId);
    }
    if (nextSelectedId !== selectedRootAgentId || !rootAgentDraftDirty) {
      setRootAgentDraft(
        buildRootAgentStudioDraft(nextSelectedProfile, selector, capabilityProviderEntries)
      );
      setRootAgentReview(null);
    }
  }, [
    capabilityProviderEntries,
    rootAgentDraftDirty,
    rootAgentEditorMode,
    rootAgentProfiles,
    rootAgentProfilesGeneratedAt,
    selectedRootAgentId,
    selector,
  ]);

  useEffect(() => {
    if (!selectedRootAgentId) {
      setRootAgentRevisions([]);
      setRootAgentRevisionError("");
      setRootAgentRevisionLoading(false);
      return;
    }
    let cancelled = false;
    setRootAgentRevisionLoading(true);
    setRootAgentRevisionError("");
    void fetchWorkerProfileRevisions(selectedRootAgentId)
      .then((document) => {
        if (cancelled) {
          return;
        }
        setRootAgentRevisions(document.revisions ?? []);
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        setRootAgentRevisionError(error instanceof Error ? error.message : "revision 加载失败");
        setRootAgentRevisions([]);
      })
      .finally(() => {
        if (!cancelled) {
          setRootAgentRevisionLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [rootAgentRevisionSyncKey, selectedRootAgentId]);

  function selectRootAgentProfile(profile: WorkerProfileItem | null) {
    startTransition(() => {
      setRootAgentEditorMode(profile ? "existing" : "create");
      setSelectedRootAgentId(profile?.profile_id ?? "");
      setRootAgentDraft(buildRootAgentStudioDraft(profile, selector, capabilityProviderEntries));
      setRootAgentReview(null);
    });
  }

  function updateRootAgentDraft<Key extends keyof RootAgentStudioDraft>(
    key: Key,
    value: RootAgentStudioDraft[Key]
  ) {
    setRootAgentDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function updateRootAgentCapabilitySelection(itemId: string, selected: boolean) {
    setRootAgentDraft((current) => ({
      ...current,
      capabilitySelection: {
        ...current.capabilitySelection,
        [itemId]: selected,
      },
    }));
  }

  function updateRootAgentProject(projectId: string) {
    setRootAgentDraft((current) => ({
      ...current,
      projectId,
    }));
  }

  function appendRootAgentDraftValue(
    key:
      | "defaultToolGroupsText"
      | "selectedToolsText"
      | "runtimeKindsText"
      | "policyRefsText"
      | "instructionOverlaysText"
      | "tagsText",
    value: string
  ) {
    setRootAgentDraft((current) => ({
      ...current,
      [key]: appendStudioListValue(current[key], value),
    }));
  }

  function resetToFreshRootAgent() {
    setRootAgentEditorMode("create");
    setSelectedRootAgentId("");
    setRootAgentDraft(buildRootAgentStudioDraft(null, selector, capabilityProviderEntries));
    setRootAgentReview(null);
    setRootAgentSpawnObjective("");
  }

  return {
    selectedRootAgentId,
    setSelectedRootAgentId,
    selectedRootAgentProfile,
    rootAgentDraft,
    setRootAgentDraft,
    rootAgentDraftDirty,
    rootAgentReview,
    setRootAgentReview,
    rootAgentRevisions,
    rootAgentRevisionLoading,
    rootAgentRevisionError,
    rootAgentSpawnObjective,
    setRootAgentSpawnObjective,
    rootAgentEditorMode,
    setRootAgentEditorMode,
    selectRootAgentProfile,
    updateRootAgentDraft,
    updateRootAgentCapabilitySelection,
    updateRootAgentProject,
    appendRootAgentDraftValue,
    resetToFreshRootAgent,
  };
}
