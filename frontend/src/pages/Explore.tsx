// Explore page (M2): 3Dmol viewer dominant (~70%), Structure Inspector (30%),
// frame navigation, Atom Table. Design doc §18/§89, ADR-10 perf targets.
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Empty, InputNumber, Space, Table, Typography } from "antd";
import {
  ArrowLeft16Regular,
  ArrowRight16Regular,
  ArrowShuffle16Regular,
  ArrowFit16Regular,
} from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { activeDataset, useWorkspace } from "../stores/workspace";
import { elementColor } from "../util/elements";
import type { FramePayload } from "../types/protocol";

export default function Explore() {
  const st = useWorkspace();
  const d = activeDataset(st);
  const [frame, setFrame] = useState<FramePayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [jumpTo, setJumpTo] = useState<number | null>(null);
  const viewerDiv = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<
    {
      clear: () => void;
      addModel: (s: string, f: string) => unknown;
      addLine: (spec: object) => void;
      setStyle: (sel: object, style: object) => void;
      addStyle: (sel: object, style: object) => void;
      zoomTo: () => void;
      render: () => void;
    } | null
  >(null);
  const [viewerReady, setViewerReady] = useState(false);
  const [viewerError, setViewerError] = useState<string | null>(null);
  const loadStart = useRef<number>(0);

  const total = d?.number_of_frames ?? 0;

  const fetchFrame = useCallback(
    async (index: number) => {
      if (!d) return;
      const idx = Math.max(0, Math.min(index, total - 1));
      setLoading(true);
      loadStart.current = performance.now();
      try {
        const f = await ipc.request<FramePayload>("dataset.frame", { id: d.id, index: idx });
        setFrame(f);
        st.setActiveFrame(idx);
        setJumpTo(null);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [d, total],
  );

  // reset when dataset changes
  useEffect(() => {
    setFrame(null);
    if (d && total > 0) void fetchFrame(st.activeFrameIndex || 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d?.id]);

  // 3Dmol lifecycle
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mod = await import("3dmol");
        const $3Dmol = ((mod as { default?: unknown }).default ?? mod) as {
          createViewer: (el: HTMLElement, opts: object) => never;
        };
        if (cancelled || !viewerDiv.current) return;
        viewerRef.current = $3Dmol.createViewer(viewerDiv.current, {
          backgroundColor: "white",
        }) as never;
        setViewerReady(true);
      } catch (e) {
        console.error("3dmol init failed", e);
        setViewerError(String(e));
      }
    })();
    return () => {
      cancelled = true;
      setViewerReady(false);
      viewerRef.current?.clear();
      viewerRef.current = null;
      if (viewerDiv.current) viewerDiv.current.innerHTML = "";
    };
  }, []);

  useEffect(() => {
    const v = viewerRef.current;
    if (!viewerReady || !v || !frame) return;
    v.clear();
    v.addModel(frame.xyz, "xyz");
    for (const el of new Set(frame.atom_rows.map((r) => r.el))) {
      const color = elementColor(el);
      v.setStyle({ elem: el }, { sphere: { scale: 0.28, color }, stick: { radius: 0.12, color } });
    }
    // unit cell wireframe (12 edges) for periodic frames; cell is row-major a1,a2,a3
    if (frame.cell && frame.cell.length === 9) {
      const A = frame.cell;
      const p = (i: number, j: number, k: number) => ({
        x: i * A[0] + j * A[3] + k * A[6],
        y: i * A[1] + j * A[4] + k * A[7],
        z: i * A[2] + j * A[5] + k * A[8],
      });
      const edges: [number, number, number, number, number, number][] = [
        [0, 0, 0, 1, 0, 0], [0, 0, 0, 0, 1, 0], [0, 0, 0, 0, 0, 1],
        [1, 1, 1, 0, 1, 1], [1, 1, 1, 1, 0, 1], [1, 1, 1, 1, 1, 0],
        [1, 0, 0, 1, 1, 0], [1, 0, 0, 1, 0, 1],
        [0, 1, 0, 1, 1, 0], [0, 1, 0, 0, 1, 1],
        [0, 0, 1, 1, 0, 1], [0, 0, 1, 0, 1, 1],
      ];
      for (const [i1, j1, k1, i2, j2, k2] of edges) {
        v.addLine({
          start: p(i1, j1, k1),
          end: p(i2, j2, k2),
          color: "#0F6CBD",
          opacity: 0.9,
          linewidth: 2,
        });
      }
    }
    v.zoomTo();
    v.render();
    if (frame.ghost_count) console.info(`frame ${frame.index}: +${frame.ghost_count} periodic image atoms`);
    const ms = performance.now() - loadStart.current;
    console.info(`frame ${frame.index} fetched+rendered in ${ms.toFixed(0)}ms`);
  }, [viewerReady, frame]);

  if (!d) return <Empty description="Register a dataset first" style={{ marginTop: 120 }} />;

  const idx = frame?.index ?? st.activeFrameIndex;
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 8 }}>
      {/* frame navigation bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          background: "#FFFFFF",
          border: "1px solid #EAECF0",
          borderRadius: 6,
          padding: "6px 12px",
        }}
      >
        <Typography.Text strong>
          Frame {idx} / {total - 1}
        </Typography.Text>
        <Space size={4}>
          <Button size="small" icon={<ArrowLeft16Regular />} onClick={() => void fetchFrame(idx - 1)} disabled={loading || idx <= 0} />
          <Button size="small" icon={<ArrowRight16Regular />} onClick={() => void fetchFrame(idx + 1)} disabled={loading || idx >= total - 1} />
          <Button
            size="small"
            icon={<ArrowShuffle16Regular />}
            onClick={() => void fetchFrame(Math.floor(Math.random() * total))}
            disabled={loading}
          >
            Random
          </Button>
          <InputNumber
            size="small"
            min={0}
            max={total - 1}
            value={jumpTo ?? idx}
            onChange={(v) => setJumpTo(v)}
            onStep={(v) => void fetchFrame(v)}
            onPressEnter={() => {
              if (jumpTo !== null) void fetchFrame(jumpTo);
            }}
            style={{ width: 90 }}
          />
          <Button
            size="small"
            icon={<ArrowFit16Regular />}
            onClick={() => {
              viewerRef.current?.zoomTo();
              viewerRef.current?.render();
            }}
          />
        </Space>
        {loading && <Typography.Text type="secondary">loading…</Typography.Text>}
      </div>

      <div style={{ display: "flex", gap: 12, flex: 1, minHeight: 0 }}>
        {/* 3D viewer ~70% */}
        <div
          style={{
            flex: 7,
            background: "#FFFFFF",
            border: "1px solid #EAECF0",
            borderRadius: 6,
            position: "relative",
            minHeight: 420,
          }}
        >
          <div ref={viewerDiv} style={{ width: "100%", height: "100%", minHeight: 420 }} />
          {viewerError && (
            <div
              style={{
                position: "absolute",
                top: 8,
                left: 8,
                color: "#C42B1C",
                fontSize: 12,
                fontFamily: "monospace",
              }}
            >
              viewer error: {viewerError}
            </div>
          )}
        </div>
        {/* Structure Inspector ~30% */}
        <div
          style={{
            flex: 3,
            background: "#FFFFFF",
            borderLeft: "1px solid #E1E4E8",
            padding: 12,
            overflowY: "auto",
          }}
        >
          <Typography.Text strong style={{ fontSize: 12, color: "#616161", letterSpacing: 1 }}>
            STRUCTURE
          </Typography.Text>
          <InspectorRows
            rows={[
              ["Frame", String(idx)],
              ["Formula", frame?.formula ?? "—"],
              ["Atoms", String(frame?.natoms ?? "—")],
              ["E / atom", frame?.energy_per_atom != null ? `${frame.energy_per_atom.toFixed(4)} eV` : "—"],
              ["Max |F|", frame?.force_max != null ? `${frame.force_max.toFixed(4)} eV/Å` : "—"],
              ["Volume", frame?.volume != null ? `${frame.volume.toFixed(2)} Å³` : "—"],
              ["PBC", frame?.pbc ?? "—"],
            ]}
          />
        </div>
      </div>

      {/* Atom table */}
      <div style={{ background: "#FFFFFF", border: "1px solid #EAECF0", borderRadius: 6, maxHeight: 260, overflow: "auto" }}>
        <Table
          size="small"
          pagination={false}
          dataSource={frame?.atom_rows ?? []}
          rowKey="i"
          columns={[
            { title: "#", dataIndex: "i", key: "i", width: 60 },
            { title: "Element", dataIndex: "el", key: "el", width: 80 },
            { title: "x (Å)", dataIndex: "x", key: "x", align: "right" },
            { title: "y (Å)", dataIndex: "y", key: "y", align: "right" },
            { title: "z (Å)", dataIndex: "z", key: "z", align: "right" },
            { title: "Fx", dataIndex: "fx", key: "fx", align: "right", render: fmt },
            { title: "Fy", dataIndex: "fy", key: "fy", align: "right", render: fmt },
            { title: "Fz", dataIndex: "fz", key: "fz", align: "right", render: fmt },
            { title: "|F|", dataIndex: "f", key: "f", align: "right", render: fmt },
          ]}
        />
      </div>
    </div>
  );
}

function fmt(v: number | null): string {
  return v == null ? "—" : v.toFixed(4);
}

function InspectorRows({ rows }: { rows: [string, string][] }) {
  return (
    <div style={{ marginTop: 8 }}>
      {rows.map(([k, v]) => (
        <div
          key={k}
          style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", fontSize: 13 }}
        >
          <span style={{ color: "#616161" }}>{k}</span>
          <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 500 }}>{v}</span>
        </div>
      ))}
    </div>
  );
}
