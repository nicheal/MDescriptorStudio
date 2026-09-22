import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { ipc } from "../ipc/client";
import { useWorkspace } from "../stores/workspace";
import type { PcaMode } from "../stores/workspace";
import type { AnalysisPreview, FramePayload } from "../types/protocol";
import { analysisCache } from "./analysisCache";
import type { Point } from "./analysisShared";

type AnalysisSelectionArgs = {
  analysisId: string | null;
  pointDataset: { id: string } | null;
  pointRunId: string | null;
  preview: AnalysisPreview | null;
  points: Point[];
  selectedIndices: number[];
  setSelectedIndices: Dispatch<SetStateAction<number[]>>;
  tab: string;
  mode: PcaMode;
  localCutoff: number;
};

/** Own the point/frame selection lifecycle shared by the plot and inspector. */
export function useAnalysisSelection({
  analysisId,
  pointDataset,
  pointRunId,
  preview,
  points,
  selectedIndices,
  setSelectedIndices,
  tab,
  mode,
  localCutoff,
}: AnalysisSelectionArgs) {
  const [inspectedPoint, setInspectedPoint] = useState<Point | null>(null);
  const [selectedFrame, setSelectedFrame] = useState<FramePayload | null>(null);
  const [selectedFrameBusy, setSelectedFrameBusy] = useState(false);

  const selectedPoint = inspectedPoint ?? points.find((point) => point.i === selectedIndices[0]) ?? null;

  const selectedFrames = useMemo(() => {
    if (tab === "projection") return [];
    const frameOf = new Map<number, number>();
    for (const point of points) frameOf.set(point.i, point.frame);
    for (const row of preview?.selected ?? []) {
      const index = Number(row.i ?? row.sample_index);
      const frame = Number(row.frame);
      if (Number.isInteger(index) && index >= 0 && Number.isInteger(frame)) frameOf.set(index, frame);
    }
    const unmapped = selectedIndices.filter((index) => !frameOf.has(index));
    if (unmapped.length) console.warn("saving a view without a frame for", unmapped.length, "selected sample(s)");
    return [...new Set(selectedIndices.map((index) => frameOf.get(index)).filter((frame): frame is number => frame !== undefined))].sort((a, b) => a - b);
  }, [points, preview, selectedIndices, tab]);

  const inspectPoint = useCallback((point: Point) => {
    setInspectedPoint(point);
    if (pointDataset && pointRunId) {
      useWorkspace.getState().setSelectedSample({ datasetId: pointDataset.id, runId: pointRunId, mode: point.row == null ? mode : "atom", frame: point.frame, atom: point.row });
      useWorkspace.getState().setActiveFrame(point.frame);
    }
  }, [mode, pointDataset, pointRunId]);

  const openPointInExplore = useCallback(() => {
    if (!pointDataset || !selectedPoint) return;
    const workspace = useWorkspace.getState();
    workspace.setActiveDataset(pointDataset.id);
    const next = useWorkspace.getState();
    next.setActiveFrame(selectedPoint.frame);
    next.setSelectedSample({ datasetId: pointDataset.id, ...(pointRunId ? { runId: pointRunId } : {}), mode: selectedPoint.row == null ? mode : "atom", frame: selectedPoint.frame, atom: selectedPoint.row });
    next.setPage("explore");
  }, [mode, pointDataset, pointRunId, selectedPoint]);

  const updateCachedSelection = useCallback((indices: number[]) => {
    if (analysisId) analysisCache.setSelectedIndices(analysisId, indices);
  }, [analysisId]);

  const clearSelection = useCallback(() => {
    setInspectedPoint(null);
    setSelectedIndices([]);
    updateCachedSelection([]);
    useWorkspace.getState().setSelectedSample(null);
  }, [setSelectedIndices, updateCachedSelection]);

  const clearInspectedPoint = useCallback(() => setInspectedPoint(null), []);

  const clearFrame = useCallback(() => {
    setSelectedFrame(null);
    setSelectedFrameBusy(false);
  }, []);

  const handlePoint = useCallback((point: Point) => {
    setSelectedIndices([point.i]);
    updateCachedSelection([point.i]);
    inspectPoint(point);
  }, [inspectPoint, setSelectedIndices, updateCachedSelection]);

  const handlePreviewAtomSelect = useCallback((atom: number) => {
    if (!pointDataset || !selectedFrame) return;
    if (selectedPoint?.row === atom && selectedPoint.frame === selectedFrame.index) {
      clearSelection();
      return;
    }
    const existing = points.find((point) => point.frame === selectedFrame.index && point.row === atom);
    if (existing) {
      handlePoint(existing);
      return;
    }
    handlePoint({ i: selectedPoint?.i ?? selectedIndices[0] ?? points.length, frame: selectedFrame.index, row: atom, x: 0, y: 0 });
  }, [clearSelection, handlePoint, pointDataset, points, selectedFrame, selectedPoint, selectedIndices]);

  useEffect(() => {
    if (!pointDataset || !selectedPoint) {
      setSelectedFrame(null);
      setSelectedFrameBusy(false);
      return;
    }
    let disposed = false;
    setSelectedFrameBusy(true);
    const localShellActive = preview?.kind === "local_diversity" && selectedPoint.row != null;
    const displayCutoff = 2.4;
    const requestedCutoff = Math.min(10, Math.max(displayCutoff, localShellActive ? localCutoff : displayCutoff));
    void ipc.request<FramePayload>("dataset.frame", { id: pointDataset.id, index: selectedPoint.frame, bond_cutoff: requestedCutoff })
      .then((frame) => { if (!disposed) setSelectedFrame({ ...frame, bond_cutoff: displayCutoff }); })
      .catch(() => { if (!disposed) setSelectedFrame(null); })
      .finally(() => { if (!disposed) setSelectedFrameBusy(false); });
    return () => { disposed = true; };
  }, [localCutoff, pointDataset, preview?.kind, selectedPoint]);

  return {
    selectedPoint,
    selectedFrames,
    selectedFrame,
    selectedFrameBusy,
    inspectPoint,
    clearInspectedPoint,
    clearFrame,
    openPointInExplore,
    updateCachedSelection,
    handlePoint,
    handlePreviewAtomSelect,
  };
}
