// Descriptors page (M3): registry list + describe inspector + schema-driven
// form + execution + scope + submit with cache-hit dialog (ADR-11/16, §92).
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App as AntApp,
  Button,
  Checkbox,
  Empty,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Tooltip,
  Typography,
} from "antd";
import { ipc } from "../ipc/client";
import { activeDataset, useWorkspace } from "../stores/workspace";
import { trackJob, watchJob } from "../stores/jobs";
import { SchemaField, speciesToNumbers, type ParamValues } from "../components/SchemaForm";
import type { DescriptorInfo, DescriptorSchema } from "../types/protocol";

export default function Descriptors() {
  const { message } = AntApp.useApp();
  const st = useWorkspace();
  const d = activeDataset(st);
  const [list, setList] = useState<DescriptorInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [schema, setSchema] = useState<DescriptorSchema | null>(null);
  const [values, setValues] = useState<ParamValues>({});
  const [scope, setScope] = useState<"dataset" | "frame">("dataset");
  const [frameIndex, setFrameIndex] = useState(0);
  const [threads, setThreads] = useState<number | undefined>(undefined);
  const [dtype, setDtype] = useState<string>("float64");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    (async () => {
      const l = await ipc.request<DescriptorInfo[]>("descriptor.list");
      setList(l);
      setSelected((cur) => cur ?? l[0]?.name ?? null);
    })();
  }, []);

  const loadSchema = useCallback(async (name: string) => {
    const s = await ipc.request<DescriptorSchema>("descriptor.describe", { name });
    setSchema(s);
    setValues({});
  }, []);

  useEffect(() => {
    if (selected) void loadSchema(selected);
  }, [selected, loadSchema]);

  const elementOptions = d?.elements ?? [];

  // ADR-11 precheck: disable descriptors incompatible with the dataset's periodicity
  const incompatibleReasons = useMemo(() => {
    const out = new Map<string, string>();
    if (!d) return out;
    const p = d.periodicity;
    for (const info of list) {
      const input = (info as unknown as { input?: { periodicity?: string[]; mixed_periodicity?: boolean } }).input;
      if (!input) continue;
      const allowed = input.periodicity ?? ["isolated", "fully_periodic"];
      if (p.mixed && input.mixed_periodicity === false) {
        out.set(info.name, "Rejects mixed periodicity");
      } else if (p.fully_periodic && !p.isolated && !allowed.includes("fully_periodic")) {
        out.set(info.name, `Supports only: ${allowed.join(", ")}`);
      } else if (p.isolated && !p.fully_periodic && !allowed.includes("isolated")) {
        out.set(info.name, `Supports only: ${allowed.join(", ")}`);
      }
    }
    return out;
  }, [d, list]);

  if (!d) return <Empty description="Register a dataset first" style={{ marginTop: 120 }} />;

  const filtered = list;

  const submit = async () => {
    if (!schema || !selected) return;
    const params: ParamValues = { ...values };
    // species multi-select holds symbols -> engine wants atomic numbers
    if (schema.parameters.species && Array.isArray(params.species)) {
      params.species = speciesToNumbers(params.species as string[], elementOptions);
    }
    setSubmitting(true);
    try {
      const r = await ipc.request<{ job_id: string | null; cache: { existing_run_id: string } | null }>(
        "descriptor.submit",
        {
          dataset_id: d.id,
          descriptor_name: selected,
          parameters: params,
          scope,
          frame_index: scope === "frame" ? frameIndex : undefined,
          output_dtype: dtype,
        },
      );
      if (r.cache) {
        Modal.confirm({
          title: "Existing compatible result found",
          content: `A completed run with identical configuration exists (${r.cache.existing_run_id}). Recalculate anyway?`,
          okText: "Recalculate",
          cancelText: "Use existing",
          onOk: async () => {
            const r2 = await ipc.request<{ job_id: string | null }>("descriptor.submit", {
              dataset_id: d.id,
              descriptor_name: selected,
              parameters: params,
              scope,
              frame_index: scope === "frame" ? frameIndex : undefined,
              output_dtype: dtype,
              force: true,
            });
            if (r2.job_id) {
              trackJob(r2.job_id, "descriptor.compute");
              void watchJob(r2.job_id).then((done) => {
                if (done.status === "COMPLETED") message.success(`Run ${done.result?.run_id} completed`);
                else message.error(`Compute ${done.status}: ${done.error?.message ?? ""}`);
              });
            }
          },
          onCancel: () => message.info(`Using existing run ${r.cache!.existing_run_id}`),
        });
        return;
      }
      if (r.job_id) {
        trackJob(r.job_id, "descriptor.compute");
        message.info("Compute submitted — see Jobs");
        void watchJob(r.job_id).then((done) => {
          if (done.status === "COMPLETED") message.success(`Run ${done.result?.run_id} completed`);
          else message.error(`Compute ${done.status}: ${done.error?.message ?? ""}`);
        });
      }
    } catch (e) {
      const err = e as { code: string; message: string };
      message.error(`${err.code}: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ display: "flex", gap: 16, height: "100%", minHeight: 420 }}>
      {/* registry list */}
      <div
        style={{
          width: 260,
          background: "#FFFFFF",
          border: "1px solid #EAECF0",
          borderRadius: 6,
          overflowY: "auto",
          padding: 8,
        }}
      >
        <Input.Search placeholder="Filter descriptors" size="small" style={{ marginBottom: 8 }} />
        {filtered.map((info) => {
          const reason = incompatibleReasons.get(info.name);
          const disabled = Boolean(reason);
          const active = selected === info.name;
          return (
            <Tooltip key={info.name} title={disabled ? "Incompatible with this dataset's periodicity" : ""}>
              <div
                onClick={() => !disabled && setSelected(info.name)}
                style={{
                  padding: "6px 10px",
                  borderRadius: 6,
                  cursor: disabled ? "not-allowed" : "pointer",
                  opacity: disabled ? 0.45 : 1,
                  background: active ? "#EBF3FC" : "transparent",
                  marginBottom: 2,
                }}
              >
                <div style={{ fontSize: 13, fontWeight: active ? 600 : 400, color: active ? "#0F6CBD" : "#242424" }}>
                  {info.display_name}
                </div>
                <div style={{ fontSize: 11, color: "#616161" }}>
                  {info.level} · {info.backend} · {info.category}
                </div>
              </div>
            </Tooltip>
          );
        })}
      </div>

      {/* configuration form */}
      <div style={{ flex: 1, background: "#FFFFFF", border: "1px solid #EAECF0", borderRadius: 6, padding: 16, overflowY: "auto" }}>
        {schema ? (
          <>
            <Typography.Title level={5} style={{ marginTop: 0 }}>
              {schema.display_name}
            </Typography.Title>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: -4 }}>
              {schema.description}
            </Typography.Paragraph>
            <Typography.Text strong style={{ fontSize: 12, color: "#616161" }}>
              CONFIGURATION
            </Typography.Text>
            <div style={{ marginTop: 8 }}>
              {Object.entries(schema.parameters).map(([name, meta]) => (
                <SchemaField
                  key={name}
                  name={name}
                  schema={meta}
                  value={values[name]}
                  elementOptions={elementOptions}
                  onChange={(v) => setValues((prev) => ({ ...prev, [name]: v }))}
                />
              ))}
            </div>
            <Typography.Text strong style={{ fontSize: 12, color: "#616161" }}>
              EXECUTION
            </Typography.Text>
            <div style={{ marginTop: 8, marginBottom: 12 }}>
              <div style={{ marginBottom: 12 }}>
                <Typography.Text style={{ fontSize: 13, fontWeight: 500 }}>Device</Typography.Text>
                <div style={{ marginTop: 4 }}>
                  <Select value="cpu" style={{ width: 200 }} disabled options={[{ value: "cpu", label: "CPU" }]} />
                </div>
              </div>
              {schema.execution.num_threads && (
                <div style={{ marginBottom: 12 }}>
                  <Typography.Text style={{ fontSize: 13, fontWeight: 500 }}>Threads</Typography.Text>
                  <div style={{ marginTop: 4 }}>
                    <Tooltip title="v0.1 uses the engine default thread count">
                      <InputNumber min={1} max={64} value={threads} disabled onChange={(v) => setThreads(v ?? undefined)} style={{ width: 200 }} placeholder="engine default" />
                    </Tooltip>
                  </div>
                </div>
              )}
              <div style={{ marginBottom: 12 }}>
                <Typography.Text style={{ fontSize: 13, fontWeight: 500 }}>Output</Typography.Text>
                <div style={{ marginTop: 4 }}>
                  <Select
                    value={dtype}
                    style={{ width: 200 }}
                    options={(schema.output.dtypes ?? ["float64"]).map((t) => ({ value: t, label: t }))}
                    onChange={setDtype}
                  />
                </div>
              </div>
              <div style={{ marginBottom: 12 }}>
                <Typography.Text style={{ fontSize: 13, fontWeight: 500 }}>Scope</Typography.Text>
                <div style={{ marginTop: 4 }}>
                  <Select
                    value={scope}
                    style={{ width: 200 }}
                    onChange={(v) => setScope(v)}
                    options={[
                      { value: "dataset", label: "Entire dataset" },
                      { value: "frame", label: "Current frame" },
                    ]}
                  />
                </div>
              </div>
              {scope === "frame" && (
                <div style={{ marginBottom: 12 }}>
                  <Typography.Text style={{ fontSize: 13, fontWeight: 500 }}>Frame index</Typography.Text>
                  <div style={{ marginTop: 4 }}>
                    <InputNumber min={0} max={d.number_of_frames - 1} value={frameIndex} onChange={(v) => setFrameIndex(v ?? 0)} style={{ width: 200 }} />
                  </div>
                </div>
              )}
              <Space>
                <Button type="primary" onClick={() => void submit()} loading={submitting}>
                  Calculate
                </Button>
                <Checkbox checked disabled>
                  Cache reuse enabled
                </Checkbox>
              </Space>
            </div>
          </>
        ) : (
          <Empty description="Select a descriptor" />
        )}
      </div>

      {/* descriptor inspector */}
      <div style={{ width: 280, background: "#FFFFFF", borderLeft: "1px solid #E1E4E8", padding: 12, overflowY: "auto" }}>
        <Typography.Text strong style={{ fontSize: 12, color: "#616161", letterSpacing: 1 }}>
          DESCRIPTOR INFO
        </Typography.Text>
        {schema && (
          <div style={{ marginTop: 8 }}>
            {(
              [
                ["Level", schema.level],
                ["Backend", schema.backend],
                ["Category", schema.category],
                ["Devices", schema.execution.devices.join(", ")],
                ["Threads", schema.execution.num_threads ? "yes" : "no"],
                ["Cancelable", schema.execution.cooperative_cancel ? "yes" : "no"],
                ["Sparse", String(schema.output.sparse)],
                ["Model", schema.asset.policy === "none" ? "none" : `${schema.asset.policy} (${schema.asset.bundled_resources[0] ?? "external"})`],
                ["Input periodicity", schema.input.periodicity.join(", ")],
                ["Spin", schema.input.spin ? "yes" : "no"],
              ] as [string, string][]
            ).map(([k, v]) => (
              <div key={k} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", fontSize: 13 }}>
                <span style={{ color: "#616161" }}>{k}</span>
                <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 500 }}>{v}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
