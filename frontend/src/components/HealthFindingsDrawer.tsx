// Data-health findings drawer: the actionable half of the health rail.
// Opens from a health-rail row click, lists the flagged frames behind each
// check (capped by the backend), previews rows in the Explore viewer, and
// saves selected frames as reusable views without ever touching the source
// files.
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Drawer, Empty, Table, Tabs, Tag, Tooltip, Typography } from "antd";
import {
  ArrowLeft16Regular,
  ArrowRight16Regular,
  Eye16Regular,
} from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import SaveViewModal from "./SaveViewModal";
import { waitForSuccessfulJob } from "../stores/jobs";
import { activeDataset, refetchDatasets, useWorkspace } from "../stores/workspace";
import { useT } from "../i18n";
import { CHECK_KEYS, healthCheckTitle } from "../util/healthChecks";
import { createAsyncRequestGuard } from "../util/asyncRequestGuard";
import type { FindingsRow, Stats } from "../types/protocol";

type StatisticsResponse = {
  recalculating: boolean;
  job_id: string | null;
  stats: Stats | null;
};

type FindingsResponse = {
  recalculating: boolean;
  job_id: string | null;
  total: number;
  returned: number;
  rows: FindingsRow[];
};

/** Localized labels for per-frame missing-property tags (dataset properties). */
const PROP_LABELS: Record<string, string> = { energy: "Energy", forces: "Forces", virial: "Virial" };

export default function HealthFindingsDrawer() {
  const { t } = useT();
  const st = useWorkspace();
  const d = activeDataset(st);
  const datasetId = d?.id;
  const findingsCheck = st.findingsCheck;
  const open = st.findingsDrawerOpen;
  const [stats, setStats] = useState<Stats | null>(null);
  const [activeTab, setActiveTab] = useState<string>(CHECK_KEYS[0]);
  const [rows, setRows] = useState<FindingsRow[]>([]);
  const [rowsTotal, setRowsTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [current, setCurrent] = useState<number | null>(null);
  const [saveViewOpen, setSaveViewOpen] = useState(false);
  const [saveViewMode, setSaveViewMode] = useState<"save" | "subtract">("save");
  const tabRef = useRef(activeTab);
  tabRef.current = activeTab;
  const pendingResetTabRef = useRef<string | undefined>(undefined);
  const loadGuardRef = useRef(createAsyncRequestGuard());

  const title = (key: string): string => healthCheckTitle(key, t);

  // reset when the drawer opens for another dataset / scan
  useEffect(() => {
    if (!open) return;
    pendingResetTabRef.current = findingsCheck ?? CHECK_KEYS[0];
    setStats(null);
    setRows([]);
    setSelected([]);
    setCurrent(null);
    setActiveTab(findingsCheck ?? CHECK_KEYS[0]);
  }, [open, datasetId, findingsCheck]);

  const fetchStats = useCallback(async (): Promise<Stats | null> => {
    if (!datasetId) return null;
    const dsId = datasetId;
    let r = await ipc.request<StatisticsResponse>("dataset.statistics", { id: dsId });
    if (!r.stats && r.job_id) {
      await waitForSuccessfulJob(r.job_id);
      await refetchDatasets();
      r = await ipc.request<StatisticsResponse>("dataset.statistics", { id: dsId });
    }
    // legacy cache between the health pass and the findings pass: one rescan
    // fills in health_findings
    if (r.stats?.health && !r.stats.health_findings) {
      const rescan = await ipc.request<{ job_id: string }>("dataset.rescan", { id: dsId });
      await waitForSuccessfulJob(rescan.job_id);
      await refetchDatasets();
      r = await ipc.request<StatisticsResponse>("dataset.statistics", { id: dsId });
    }
    if (useWorkspace.getState().activeDatasetId !== dsId) return null;
    return r.stats;
  }, [datasetId]);

  const fetchRows = useCallback(
    async (s: Stats | null, tab: string): Promise<{ rows: FindingsRow[]; total: number } | null> => {
      if (!datasetId || !s) return null;
      const dsId = datasetId;
      const params: Record<string, unknown> = { id: dsId, check: tab, limit: 1000 };
      let r = await ipc.request<FindingsResponse>("dataset.findings", params);
      if (r.recalculating && r.job_id) {
        await waitForSuccessfulJob(r.job_id);
        if (useWorkspace.getState().activeDatasetId !== dsId) return null;
        r = await ipc.request<FindingsResponse>("dataset.findings", params);
      }
      if (useWorkspace.getState().activeDatasetId !== dsId) return null;
      return { rows: r.rows ?? [], total: r.total ?? r.rows?.length ?? 0 };
    },
    [datasetId],
  );

  useEffect(() => {
    const loadGuard = loadGuardRef.current;
    const loadRequestId = loadGuard.next();
    let disposed = false;
    if (!open || !datasetId) {
      return () => {
        disposed = true;
        loadGuard.invalidate();
      };
    }
    const dsId = datasetId;
    const resetTab = pendingResetTabRef.current;
    pendingResetTabRef.current = undefined;
    if (resetTab != null && resetTab !== activeTab) {
      setActiveTab(resetTab);
      return () => {
        disposed = true;
        loadGuard.invalidate();
      };
    }
    const isCurrent = (tab?: string) =>
      !disposed
      && loadGuard.isCurrent(loadRequestId)
      && useWorkspace.getState().findingsDrawerOpen
      && useWorkspace.getState().activeDatasetId === dsId
      && (tab == null || tabRef.current === tab);
    (async () => {
      setLoading(true);
      setRows([]);
      setRowsTotal(0);
      try {
        const s = await fetchStats();
        if (!isCurrent()) return;
        if (s) {
          setStats(s);
          // the check behind the rail click may have lost its findings since
          // (or the drawer may have opened without one): snap to the first
          // tab that actually has rows instead of rendering a headless table
          const countFor = (k: (typeof CHECK_KEYS)[number]) => s.health_findings?.[k]?.length ?? 0;
          const known = new Set<string>(CHECK_KEYS.filter((k) => countFor(k) > 0));
          const requestedTab = tabRef.current;
          if (!known.has(requestedTab)) {
            const first = CHECK_KEYS.find((k) => countFor(k) > 0);
            if (!first) return;
            setActiveTab(first);
            return;
          }
          const result = await fetchRows(s, requestedTab);
          if (!result || !isCurrent(requestedTab)) return;
          setRows(result.rows);
          setRowsTotal(result.total);
          setSelected([]);
        }
      } catch (e) {
        console.error("dataset.findings failed", e);
      } finally {
        if (isCurrent()) setLoading(false);
      }
    })();
    return () => {
      disposed = true;
      loadGuard.invalidate();
    };
  }, [open, datasetId, st.statsTick, activeTab, fetchStats, fetchRows]);

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
  const showEnergy = activeTab === "energy_anomaly";
  const showMaxForce = activeTab === "extreme_force";
  const showMinDistance = activeTab === "nonphysical_structures";
  const tabItems = [
    ...CHECK_KEYS.filter((k) => (findings?.[k]?.length ?? 0) > 0).map((k) => ({
      key: k,
      label: `${title(k)} ${(findings?.[k]?.length ?? 0).toLocaleString()}`,
    })),
  ];

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
            rowClassName={(row) => (row.index === current ? "findings-row-current" : "")}
            rowSelection={{
              selectedRowKeys: selected,
              onChange: (keys) => setSelected(keys.map(Number)),
            }}
            onRow={(row) => ({ onClick: () => preview(row.index), style: { cursor: "pointer" } })}
            columns={[
              // Fixed px widths on the shared trio + one widthless LAST column
              // that absorbs the drawer's leftover space: the trio renders
              // pixel-identical on every card, whether that card's extra
              // column exists (the metric checks) or not (invalid cell /
              // net force / energy, and legacy caches without the duplicate
              // mapping — a blank filler, invisible on the borderless table).
              // The specified widths (+32px selection) stay well under the
              // 560px drawer, so it never scrolls sideways.
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
              ...(showEnergy
                ? [
                    {
                      title: t("E / atom"),
                      dataIndex: "energy_per_atom",
                      key: "energy_per_atom",
                      align: "right" as const,
                      render: (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(4)} eV/atom`),
                    },
                  ]
                : []),
              ...(!showDupOf && !showEnergy && !showMaxForce && !showMinDistance && !showMissing
                ? [{ title: "", key: "filler" }]
                : []),
            ]}
          />
          <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
            <Button
              size="small"
              disabled={selected.length === 0}
              onClick={() => {
                setSaveViewMode("save");
                setSaveViewOpen(true);
              }}
            >
              {t("Save selection as view")}
            </Button>
            <Button
              size="small"
              disabled={selected.length === 0}
              onClick={() => {
                setSaveViewMode("subtract");
                setSaveViewOpen(true);
              }}
            >
              {t("Subtract selection from base scope")}
            </Button>
          </div>
          {d && (
            <SaveViewModal
              open={saveViewOpen}
              onClose={() => setSaveViewOpen(false)}
              datasetId={d.id}
              frames={selected}
              totalFrames={d.number_of_frames}
              initialMode={saveViewMode}
              defaultName={`${title(activeTab)} ${selected.length}`}
              source={{ source: "health_findings", check: activeTab }}
            />
          )}
        </>
      )}
    </Drawer>
  );
}
