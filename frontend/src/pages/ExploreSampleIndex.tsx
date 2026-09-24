import { useEffect, useRef, useState } from "react";
import { Button, InputNumber, Space, Typography } from "antd";
import { ipc } from "../ipc/client";
import { useT } from "../i18n";
import { useWorkspace, type AnalysisSampleScope } from "../stores/workspace";

type SampleIdentity = { dataset_id: string; total: number; i: number | null; frame: number | null; row: number | null };

export default function ExploreSampleIndex({ scope, frame, atom }: { scope: AnalysisSampleScope; frame: number; atom?: number }) {
  const { t } = useT();
  const [identity, setIdentity] = useState<SampleIdentity | null>(null);
  const [input, setInput] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const params = { run_id: scope.runId, mode: scope.mode, ...(scope.viewId ? { view_id: scope.viewId } : {}) };

  useEffect(() => {
    const requests = generation;
    const current = ++generation.current;
    setIdentity(null);
    setInput(null);
    setError(null);
    setBusy(true);
    void ipc.request<SampleIdentity>("analysis.sample_identity", { ...params, frame, ...(atom == null ? {} : { row: atom }) })
      .then((value) => { if (generation.current === current && value.dataset_id === scope.datasetId) setIdentity(value); })
      .catch((reason) => { if (generation.current === current) setError(String(reason)); })
      .finally(() => { if (generation.current === current) setBusy(false); });
    return () => { ++requests.current; };
    // Scope fields are the request identity; params is a fresh object on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.datasetId, scope.runId, scope.mode, scope.viewId, frame, atom]);

  const target = input ?? identity?.i ?? null;
  const valid = target != null && Number.isInteger(target) && target >= 0 && identity != null && target < identity.total;
  const locate = async () => {
    if (!valid || busy) return;
    const current = ++generation.current;
    setBusy(true);
    setError(null);
    try {
      const value = await ipc.request<SampleIdentity>("analysis.sample_identity", { ...params, i: target });
      if (generation.current !== current || value.dataset_id !== scope.datasetId || value.frame == null) return;
      const workspace = useWorkspace.getState();
      if (workspace.activeDatasetId !== scope.datasetId) return;
      workspace.setSelectedSample({ datasetId: scope.datasetId, runId: scope.runId, mode: scope.mode, frame: value.frame, ...(value.row == null ? {} : { atom: value.row }) });
      workspace.setActiveFrame(value.frame);
      setIdentity(value);
      setInput(null);
    } catch (reason) {
      if (generation.current === current) setError(String(reason));
    } finally {
      if (generation.current === current) setBusy(false);
    }
  };

  return <Space wrap size={8}>
    <Typography.Text>{t("Analysis sample index i")}</Typography.Text>
    <InputNumber aria-label={t("Analysis sample index i")} size="small" min={0} max={identity ? Math.max(0, identity.total - 1) : undefined}
      value={target} onChange={setInput} onPressEnter={() => void locate()} disabled={busy || !identity?.total} style={{ width: 110 }} />
    <Button size="small" onClick={() => void locate()} disabled={!valid || busy}>{t("Locate sample")}</Button>
    <Typography.Text type="secondary">
      {t("Current i")}: {identity?.i ?? "—"} · {t(scope.mode === "atom" ? "Atom" : "Structure")} · {t(scope.viewId ? "Dataset view" : "Full dataset")}
      {identity && identity.total > 0 && ` · 0–${identity.total - 1}`}
      {` · ${scope.runId}${scope.viewId ? ` / ${scope.viewId}` : ""}`}
    </Typography.Text>
    {identity?.i == null && !busy && !error && <Typography.Text type="secondary">{t("Select a sample in this analysis scope to see i.")}</Typography.Text>}
    {error && <Typography.Text type="danger">{error}</Typography.Text>}
  </Space>;
}
