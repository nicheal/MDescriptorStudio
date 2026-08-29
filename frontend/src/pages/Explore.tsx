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
      addModel: (s: string, f: string) => void;
      setStyle: (sel: object, style: object) => void;
      addStyle: (sel: object, style: object) => void;
      zoomTo: () => void;
      render: () => void;
    } | null
  >(null);
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
      const $3Dmol = (await import("3dmol")).default;
      if (cancelled || !viewerDiv.current) return;
      viewerRef.current = ($3Dmol as never as { createViewer: (el: HTMLElement, opts: object) => never }).createViewer(
        viewerDiv.current,
        { backgroundColor: "white" },
      ) as never;
    })();
    return () => {
      cancelled = true;
      viewerRef.current?.clear();
      viewerRef.current = null;
      if (viewerDiv.current) viewerDiv.current.innerHTML = "";
    };
  }, []);

  useEffect(() => {
    const v = viewerRef.current;
    if (!v || !frame) return;
    v.clear();
    v.addModel(frame.xyz, "xyz");
    v.setStyle({}, { sphere: { scale: 0.28 }, stick: { radius: 0.12 } });
    v.addStyle({}, { line: { opacity: 0.0 } });
    // cell box for periodic frames (from xyz we rebuild via backend cell? use positions bbox)
    v.zoomTo();
    v.render();
    const ms = performance.now() - loadStart.current;
    console.info(`frame ${frame.index} fetched+rendered in ${ms.toFixed(0)}ms`);
  }, [frame, viewerRef.current]);

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
            onPressEnter={() => jumpTo !== null && void fetchFrame(jumpTo)}
            style={{ width: 90 }}
          />
          <Button size="small" icon={<ArrowFit16Regular />} onClick={() => viewerRef.current?.zoomTo()} />
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
