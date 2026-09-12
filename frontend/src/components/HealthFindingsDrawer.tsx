// Data-health findings drawer: the actionable half of the health rail.
// Opens from a health-rail row click, lists the flagged frames behind each
// check (capped by the backend), previews rows in the Explore viewer, and
// soft-deletes (exclude/restore) or writes a cleaned copy without ever
// touching the source files.
import { useCallback, useEffect, useRef, useState } from "react";
import { App as AntApp, Button, Drawer, Empty, Table, Tabs, Tag, Tooltip, Typography } from "antd";
import { save as saveDialog } from "@tauri-apps/plugin-dialog";
import {
  ArrowLeft16Regular,
  ArrowRight16Regular,
  ArrowExportLtr16Regular,
  Eye16Regular,
} from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import SaveViewModal from "./SaveViewModal";
import { activeDataset, refetchDatasets, useWorkspace } from "../stores/workspace";
import { useT } from "../i18n";
import type { FindingsRow, Stats } from "../types/protocol";

type StatisticsResponse = {
  recalculating: boolean;
  job_id: string | null;
  stats: Stats | null;
};

const CHECK_KEYS = [
  "missing_values",
  "invalid_cell",
  "duplicate_structures",
  "extreme_force",
  "nonphysical_structures",
  "net_force",
] as const;

const EXCLUDED_TAB = "__excluded__";

/** Localized labels for per-frame missing-property tags (dataset properties). */
const PROP_LABELS: Record<string, string> = { energy: "Energy", forces: "Forces", virial: "Virial" };

/** Resolves when the backend job finishes (same contract as the rail's helper). */
function jobDone(jobId: string, onProgress?: (p: number) => void): Promise<void> {
  return new Promise((resolve) => {
    const offDone = ipc.on("job.finished", (data) => {
      const j = data as { job_id: string };
      if (j.job_id !== jobId) return;
      offDone();
      offProgress();
      resolve();
    });
    const offProgress = ipc.on("job.progress", (data) => {
      const j = data as { job_id: string; progress: number };
      if (j.job_id !== jobId) return;
      onProgress?.(j.progress);
    });
  });
}

export default function HealthFindingsDrawer() {
  const { message } = AntApp.useApp();
  const { t } = useT();
  const st = useWorkspace();
  const d = activeDataset(st);
  const open = st.findingsDrawerOpen;
  const [stats, setStats] = useState<Stats | null>(null);
  const [activeTab, setActiveTab] = useState<string>(EXCLUDED_TAB);
  const [rows, setRows] = useState<FindingsRow[]>([]);
  const [rowsTotal, setRowsTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [current, setCurrent] = useState<number | null>(null);
  const [saveViewOpen, setSaveViewOpen] = useState(false);
  const tabRef = useRef(activeTab);
  tabRef.current = activeTab;

  const title = (key: string): string =>
    ({
      missing_values: t("Missing values"),
      invalid_cell: t("Invalid cell"),
      duplicate_structures: t("Duplicate structures"),
      extreme_force: t("Extreme force"),
      nonphysical_structures: t("Non-physical structures"),
      net_force: t("Net force"),
    })[key] ?? key;

  // reset when the drawer opens for another dataset / scan
  useEffect(() => {
    if (!open) return;
    setStats(null);
    setRows([]);
    setSelected([]);
    setCurrent(null);
    setActiveTab(st.findingsCheck ?? CHECK_KEYS[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, d?.id]);

  const fetchStats = useCallback(async (): Promise<Stats | null> => {
    if (!d) return null;
    const dsId = d.id;
    let r = await ipc.request<StatisticsResponse>("dataset.statistics", { id: dsId });
    if (!r.stats && r.job_id) {
      await jobDone(r.job_id);
      await refetchDatasets();
      r = await ipc.request<StatisticsResponse>("dataset.statistics", { id: dsId });
    }
    // legacy cache between the health pass and the findings pass: one rescan
    // fills in health_findings
    if (r.stats?.health && !r.stats.health_findings) {
      const rescan = await ipc.request<{ job_id: string }>("dataset.rescan", { id: dsId });
      await jobDone(rescan.job_id);
      await refetchDatasets();
      r = await ipc.request<StatisticsResponse>("dataset.statistics", { id: dsId });
    }
    if (useWorkspace.getState().activeDatasetId !== dsId) return null;
    setStats(r.stats);
    return r.stats;
  }, [d]);

  const fetchRows = useCallback(
    async (s: Stats | null, tab: string) => {
      if (!d || !s) return;
      const dsId = d.id;
      let indices: number[] | null = null;
      if (tab === EXCLUDED_TAB) {
        const exc = await ipc.request<{ indices: number[] }>("dataset.excluded", { id: dsId });
        indices = exc.indices;
        if (useWorkspace.getState().activeDatasetId !== dsId) return;
      } else {
        indices = null; // resolved server-side from health_findings[check]
      }
      const params: Record<string, unknown> = { id: dsId, limit: 1000 };
      if (tab !== EXCLUDED_TAB) params.check = tab;
      if (indices !== null) params.indices = indices;
      let r = await ipc.request<{ recalculating: boolean; job_id: string | null; total: number; returned: number; rows: FindingsRow[] }>(
        "dataset.findings",
        params,
      );
      if (r.recalculating && r.job_id) {
        await jobDone(r.job_id);
        if (useWorkspace.getState().activeDatasetId !== dsId) return;
        r = await ipc.request("dataset.findings", params);
      }
      setRows(r.rows ?? []);
      setRowsTotal(r.total ?? r.rows?.length ?? 0);
      setSelected([]);
    },
    [d],
  );

  useEffect(() => {
    if (!open) return;
    let disposed = false;
    (async () => {
      setLoading(true);
      try {
        const s = await fetchStats();
        if (!disposed && s) {
          // the check behind the rail click may have lost its findings since
          // (or the drawer may have opened without one): snap to the first
          // tab that actually has rows instead of rendering a headless table
          const countFor = (k: (typeof CHECK_KEYS)[number]) => s.health_findings?.[k]?.length ?? 0;
          const known = new Set([
            ...CHECK_KEYS.filter((k) => countFor(k) > 0),
            ...((s.excluded_frames?.count ?? 0) > 0 ? [EXCLUDED_TAB] : []),
          ]);
          if (!known.has(tabRef.current)) {
            const first = CHECK_KEYS.find((k) => countFor(k) > 0);
            if (first) setActiveTab(first);
          }
          await fetchRows(s, tabRef.current);
        }
      } catch (e) {
        console.error("dataset.findings failed", e);
      } finally {
        if (!disposed) setLoading(false);
      }
    })();
    return () => {
      disposed = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, d?.id, st.statsTick, activeTab]);

  const refreshAll = useCallback(async () => {
    await refetchDatasets();
    useWorkspace.getState().bumpStatsTick();
  }, []);

  const runMutate = useCallback(
    async (method: "dataset.exclude" | "dataset.restore", indices: number[]) => {
      if (!d || indices.length === 0) return;
      setBusy(true);
      try {
        const r = await ipc.request<{ job_id: string }>(method, { id: d.id, indices });
        await jobDone(r.job_id);
        await refreshAll();
        message.success(
          method === "dataset.exclude"
            ? t("Excluded {n} frames", { n: indices.length })
            : t("Restored {n} frames", { n: indices.length }),
        );
      } catch (e) {
        const err = e as { code: string; message: string };
        message.error(`${err.code}: ${err.message}`);
      } finally {
        setBusy(false);
      }
    },
    [d, refreshAll, message, t],
  );

  const exportCleaned = useCallback(async () => {
    if (!d) return;
    const isDeepmd = d.format === "deepmd";
    let dest: string | null = null;
    try {
      if (isDeepmd) {
        dest = await saveDialog({
          title: t("Choose where to write the cleaned DeepMD dataset"),
          defaultPath: `${d.name}.cleaned`,
        });
      } else {
        dest = await saveDialog({
          title: t("Choose where to write the cleaned extxyz file"),
          defaultPath: `${d.name}.cleaned.xyz`,
          filters: [{ name: "extxyz", extensions: ["xyz", "extxyz"] }],
        });
      }
    } catch {
      message.error(t("Could not open the file dialog"));
      return;
    }
    if (!dest) return;
    setBusy(true);
    try {
      const r = await ipc.request<{ job_id: string; dest_path: string }>("dataset.export_cleaned", {
        id: d.id,
        dest_path: dest,
      });
      await jobDone(r.job_id, () => undefined);
      // register the cleaned copy so it shows up next to the source dataset
      const reg = await ipc.request<{ job_id: string }>("dataset.register", {
        path: r.dest_path,
        name: `${d.name} (cleaned)`,
      });
      await jobDone(reg.job_id);
      await refreshAll();
      message.success(t("Cleaned dataset exported and registered"));
    } catch (e) {
      const err = e as { code: string; message: string };
      message.error(`${err.code}: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }, [d, refreshAll, message, t]);

  const preview = useCallback(
    (index: number) => {
      setCurrent(index);
      useWorkspace.getState().setActiveFrame(index);
      useWorkspace.getState().setPage("explore");
    },
    [],
  );

  const previewSibling = useCallback(
    (delta: number) => {
      if (rows.length === 0) return;
      const pos = current == null ? -1 : rows.findIndex((r) => r.index === current);
      const next = rows[Math.max(0, Math.min((pos < 0 ? 0 : pos + delta), rows.length - 1))];
      if (next) preview(next.index);
    },
    [rows, current, preview],
  );

  if (!d) return null;

  const findings = stats?.health_findings;
  const excludedCount = stats?.excluded_frames?.count ?? 0;
  // duplicate → first-occurrence mapping behind the "Duplicate of" column
  // (absent on caches from before the mapping existed)
  const dupOf = new Map<number, number>();
  const dupList = findings?.duplicate_structures ?? [];
  (findings?.duplicate_structures_of ?? []).forEach((orig, i) => {
    if (dupList[i] != null) dupOf.set(dupList[i], orig);
  });
  // every check keeps its own table lean: the shared Frame/Formula/Atoms
  // columns plus the single column behind that check's finding
  const showDupOf = activeTab === "duplicate_structures" && dupOf.size > 0;
  const showMissing = activeTab === "missing_values";
  const showMaxForce = activeTab === "extreme_force";
  const showMinDistance = activeTab === "nonphysical_structures";
  const tabItems = [
    ...CHECK_KEYS.filter((k) => (findings?.[k]?.length ?? 0) > 0).map((k) => ({
      key: k,
      label: `${title(k)} ${(findings?.[k]?.length ?? 0).toLocaleString()}`,
    })),
    ...(excludedCount > 0
      ? [{ key: EXCLUDED_TAB, label: `${t("Excluded")} ${excludedCount.toLocaleString()}` }]
      : []),
  ];
  const selectionType = activeTab === EXCLUDED_TAB ? "restore" : "exclude";

  return (
    <Drawer
      title={
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <Typography.Text strong style={{ fontSize: 14 }}>
            {t("Flagged frames")}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
            {d.name}
          </Typography.Text>
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 4, flex: "0 0 auto" }}>
            <Tooltip title={t("Previous flagged frame")}>
              <Button size="small" icon={<ArrowLeft16Regular />} disabled={rows.length === 0} onClick={() => previewSibling(-1)} />
            </Tooltip>
            <Tooltip title={t("Next flagged frame")}>
              <Button size="small" icon={<ArrowRight16Regular />} disabled={rows.length === 0} onClick={() => previewSibling(1)} />
            </Tooltip>
          </span>
        </div>
      }
      placement="right"
      width={560}
      open={open}
      onClose={st.closeFindings}
      destroyOnClose
    >
      {tabItems.length > 1 && (
        <Tabs
          size="small"
          activeKey={activeTab}
          onChange={setActiveTab}
          items={tabItems}
          style={{ marginBottom: 4 }}
        />
      )}
      {stats && findings && tabItems.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t("No flagged frames — this dataset passed every check.")}
          style={{ marginTop: 48 }}
        />
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <Button
              size="small"
              icon={<Eye16Regular />}
              disabled={current == null}
              onClick={() => current != null && preview(current)}
            >
              {t("Preview in Explore")}
            </Button>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {rowsTotal > rows.length
                ? t("Showing first {n} of {total} frames", { n: rows.length, total: rowsTotal.toLocaleString() })
                : t("{n} frames", { n: rows.length })}
            </Typography.Text>
          </div>
          <Table
            size="small"
            tableLayout="fixed"
            loading={loading}
            pagination={false}
            scroll={{ y: 360 }}
            dataSource={rows}
            rowKey="index"
            rowClassName={(row) => (row.index === current ? "findings-row-current" : row.excluded ? "findings-row-excluded" : "")}
            rowSelection={{
              selectedRowKeys: selected,
              onChange: (keys) => setSelected(keys.map(Number)),
              getCheckboxProps: () => ({ disabled: busy }),
            }}
            onRow={(row) => ({ onClick: () => preview(row.index), style: { cursor: "pointer" } })}
            columns={[
              // Fixed px widths on the shared trio + one widthless LAST column
              // that absorbs the drawer's leftover space: the trio renders
              // pixel-identical on every card, whether that card's extra
              // column exists (the four metric checks) or not (invalid cell /
              // net force / excluded, and legacy caches without the duplicate
              // mapping — a blank filler, invisible on the borderless table).
              // The specified widths (+32px selection) stay well under the
              // 560px drawer, so it never scrolls sideways; excluded rows are
              // marked by the strikethrough row style, not a column
              { title: t("Frame"), dataIndex: "index", key: "index", width: 64 },
              { title: t("Formula"), dataIndex: "formula", key: "formula", width: 140, ellipsis: true },
              { title: t("Atoms"), dataIndex: "natoms", key: "natoms", width: 64, align: "right" },
              ...(showDupOf
                ? [
                    {
                      title: t("Duplicate of"),
                      key: "duplicate_of",
                      render: (_: unknown, row: FindingsRow) => {
                        const orig = dupOf.get(row.index);
                        if (orig == null) return null;
                        return (
                          <Tooltip title={t("Identical geometry (positions, cell, composition); only the labels (e.g. forces) may differ.")}>
                            <Button
                              type="link"
                              size="small"
                              style={{ padding: 0, height: "auto", fontSize: 12, lineHeight: "18px" }}
                              onClick={(e) => {
                                // the row click previews the copy; the link
                                // jumps straight to the frame it repeats
                                e.stopPropagation();
                                useWorkspace.getState().setActiveFrame(orig);
                                useWorkspace.getState().setPage("explore");
                              }}
                            >
                              {orig}
                            </Button>
                          </Tooltip>
                        );
                      },
                    },
                  ]
                : []),
              ...(showMaxForce
                ? [
                    {
                      title: "max |F|",
                      dataIndex: "force_max",
                      key: "force_max",
                      align: "right" as const,
                      render: (v: number | null) => (v == null ? "—" : v.toFixed(3)),
                    },
                  ]
                : []),
              ...(showMinDistance
                ? [
                    {
                      title: t("Min distance"),
                      dataIndex: "min_distance",
                      key: "min_distance",
                      align: "right" as const,
                      render: (v: number | null) => (v == null ? "—" : `${v.toFixed(3)} Å`),
                    },
                  ]
                : []),
              ...(showMissing
                ? [
                    {
                      title: t("Missing"),
                      dataIndex: "missing_props",
                      key: "missing_props",
                      render: (_: unknown, row: FindingsRow) => (
                        <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 2 }}>
                          {(row.missing_props ?? []).map((p) => (
                            <Tag key={p} color="warning" style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: "16px" }}>
                              {t(PROP_LABELS[p] ?? p)}
                            </Tag>
                          ))}
                        </span>
                      ),
                    },
                  ]
                : []),
              ...(!showDupOf && !showMaxForce && !showMinDistance && !showMissing
                ? [{ title: "", key: "filler" }]
                : []),
            ]}
          />
          <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
            {selectionType === "exclude" ? (
              <Button
                size="small"
                danger
                disabled={selected.length === 0 || busy}
                onClick={() => void runMutate("dataset.exclude", selected)}
              >
                {t("Exclude selected ({n})", { n: selected.length })}
              </Button>
            ) : (
              <Button
                size="small"
                disabled={selected.length === 0 || busy}
                onClick={() => void runMutate("dataset.restore", selected)}
              >
                {t("Restore selected ({n})", { n: selected.length })}
              </Button>
            )}
            <Tooltip title={t("Writes a new dataset that skips excluded frames and registers it; the source files are never modified.")}>
              <Button
                size="small"
                icon={<ArrowExportLtr16Regular />}
                disabled={busy}
                onClick={() => void exportCleaned()}
              >
                {t("Export cleaned copy")}
              </Button>
            </Tooltip>
            <Button
              size="small"
              disabled={selected.length === 0 || busy}
              onClick={() => setSaveViewOpen(true)}
            >
              {t("Save selection as view")}
            </Button>
          </div>
          <Typography.Paragraph type="secondary" style={{ fontSize: 11, marginTop: 10, marginBottom: 0 }}>
            {t("Excluding only affects statistics and exports — descriptor runs still use the full source dataset; export a cleaned copy to train on the kept frames.")}
          </Typography.Paragraph>
          {d && (
            <SaveViewModal
              open={saveViewOpen}
              onClose={() => setSaveViewOpen(false)}
              datasetId={d.id}
              frames={selected}
              totalFrames={d.number_of_frames}
              defaultName={`${title(activeTab)} ${selected.length}`}
              source={{ source: "health_findings", check: activeTab }}
            />
          )}
        </>
      )}
    </Drawer>
  );
}
