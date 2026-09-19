// Descriptors page (M3): registry list + describe inspector + schema-driven
// form + execution + scope + submit with cache-hit dialog (ADR-11/16, §92).
import { useEffect, useMemo, useState } from "react";
import {
  App as AntApp,
  Button,
  Checkbox,
  Empty,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Space,
  Tooltip,
  Typography,
} from "antd";
import { Info16Regular } from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { useActiveDataset } from "../stores/workspace";
import { trackJob, watchJob } from "../stores/jobs";
import { collectDefaults, SchemaField, speciesToNumbers, type ParamValues } from "../components/SchemaForm";
import { useT } from "../i18n";
import type { DescriptorInfo, DescriptorSchema } from "../types/protocol";

// Single-letter badge coding for the sidebar (soft tinted block + letter per
// enum value; the row tooltip expands each letter into its full meaning).
const BADGE_STYLES: Record<string, { bg: string; fg: string }> = {
  // level
  structure: { bg: "#E5F0FA", fg: "#0B5A9C" },
  atom: { bg: "#E6F3E6", fg: "#0C630C" },
  pair: { bg: "#FBEADD", fg: "#A84308" },
  // backend
  cpp: { bg: "#EFE8F7", fg: "#5F3A8C" },
  numpy: { bg: "#DFF2F2", fg: "#026E72" },
  // category
  local: { bg: "#E8ECFC", fg: "#354EC4" },
  matrix: { bg: "#DFF5EE", fg: "#07855F" },
  many_body: { bg: "#F6EFDC", fg: "#7A5300" },
  rotational: { bg: "#FAE8E8", fg: "#8C1F24" },
  model_backed: { bg: "#FDEAE0", fg: "#C24A08" },
};
const BADGE_LETTERS: Record<string, string> = {
  structure: "S",
  atom: "A",
  pair: "P",
  cpp: "C",
  numpy: "N",
  local: "L",
  matrix: "M",
  many_body: "B",
  rotational: "R",
  model_backed: "D",
};

function MetaBadge({ value }: { value: string }) {
  const c = BADGE_STYLES[value] ?? { bg: "#F2F2F2", fg: "#616161" };
  return (
    <span
      style={{
        width: 16,
        height: 16,
        borderRadius: 4,
        background: c.bg,
        color: c.fg,
        fontSize: 10,
        fontWeight: 600,
        lineHeight: "16px",
        textAlign: "center",
        flexShrink: 0,
      }}
    >
      {BADGE_LETTERS[value] ?? value.charAt(0).toUpperCase()}
    </span>
  );
}

function SectionHeading({ number, title, description }: { number: number; title: string; description?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 18 }}>
      <span
        aria-hidden="true"
        style={{
          width: 26,
          height: 26,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          border: "2px solid #2B83F6",
          borderRadius: "50%",
          color: "#1677FF",
          fontSize: 14,
          fontWeight: 600,
          lineHeight: 1,
          flexShrink: 0,
        }}
      >
        {number}
      </span>
      <Typography.Text strong style={{ fontSize: 16, color: "#242424" }}>
        {title}
      </Typography.Text>
      {description && (
        <Tooltip title={description}>
          <Info16Regular aria-label={description} style={{ color: "#98A2B3", fontSize: 15, cursor: "help" }} />
        </Tooltip>
      )}
    </div>
  );
}

// Display labels for engine-declared execution devices; unknown values fall
// back to the raw identifier uppercased in the option list.
const DEVICE_LABELS: Record<string, string> = { cpu: "CPU", cuda: "CUDA" };
const DEFAULT_OUTPUT_DTYPE = "float32";

export default function Descriptors() {
  const { message } = AntApp.useApp();
  const d = useActiveDataset();
  const { t } = useT();
  const [list, setList] = useState<DescriptorInfo[]>([]);
  const [schemas, setSchemas] = useState<Record<string, DescriptorSchema>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [values, setValues] = useState<ParamValues>({});
  const [scope, setScope] = useState<"dataset" | "frame">("dataset");
  const [frameIndex, setFrameIndex] = useState(0);
  const [threads, setThreads] = useState<number | undefined>(undefined);
  const [dtype, setDtype] = useState<string>(DEFAULT_OUTPUT_DTYPE);
  const [device, setDevice] = useState<string>("cpu");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    (async () => {
      const l = await ipc.request<DescriptorInfo[]>("descriptor.list");
      setList(l);
      setSelected((cur) => cur ?? l[0]?.name ?? null);
      // Preload every schema once so the sidebar tooltips can show the full
      // metadata (description, execution, asset, input) without per-click waits.
      const settled = await Promise.allSettled(
        l.map((info) => ipc.request<DescriptorSchema>("descriptor.describe", { name: info.name })),
      );
      const map: Record<string, DescriptorSchema> = {};
      settled.forEach((r, i) => {
        if (r.status === "fulfilled") map[l[i].name] = r.value;
      });
      setSchemas(map);
    })();
  }, []);

  const schema = selected ? (schemas[selected] ?? null) : null;

  const datasetElements = d?.elements;
  const elementOptions = useMemo(() => datasetElements ?? [], [datasetElements]);

  useEffect(() => {
    setValues(schema ? collectDefaults(schema.parameters, elementOptions) : {});
  }, [selected, schema, d?.id, elementOptions]);

  // ADR-11 precheck: disable descriptors incompatible with the dataset's periodicity
  const incompatibleReasons = useMemo(() => {
    const out = new Map<string, string>();
    if (!d) return out;
    const p = d.periodicity;
    for (const info of list) {
      const input = info.input;
      if (!input) continue;
      const allowed = input.periodicity ?? ["isolated", "fully_periodic"];
      if (p.mixed && input.mixed_periodicity === false) {
        out.set(info.name, t("Rejects mixed periodicity"));
      } else if (p.fully_periodic && !p.isolated && !allowed.includes("fully_periodic")) {
        out.set(info.name, t("Supports only: {list}", { list: allowed.join(", ") }));
      } else if (p.isolated && !p.fully_periodic && !allowed.includes("isolated")) {
        out.set(info.name, t("Supports only: {list}", { list: allowed.join(", ") }));
      }
    }
    return out;
  }, [d, list, t]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return list;
    return list.filter((info) =>
      [info.name, info.display_name, info.description, info.category]
        .filter(Boolean)
        .some((text) => text.toLowerCase().includes(needle)),
    );
  }, [list, query]);

  if (!d) return <Empty description={t("Register a dataset first")} style={{ marginTop: 120 }} />;

  const availableDtypes = schema?.output.dtypes?.length ? schema.output.dtypes : [DEFAULT_OUTPUT_DTYPE];
  const effectiveDtype = availableDtypes.includes(dtype) ? dtype : availableDtypes[0];
  // Device choices are always the schema-declared list (Rule 3: nothing hardcoded);
  // the pick persists across descriptors and is clamped per descriptor.
  const availableDevices = schema?.execution.devices?.length ? schema.execution.devices : ["cpu"];
  const effectiveDevice = availableDevices.includes(device) ? device : availableDevices[0];

  const submit = async () => {
    if (!schema || !selected) return;
    const params: ParamValues = { ...values };
    // species multi-select holds symbols -> engine wants atomic numbers
    if (schema.parameters.species && Array.isArray(params.species)) {
      params.species = speciesToNumbers(params.species as string[]);
    }
    setSubmitting(true);
    try {
      const submitRequest = {
        dataset_id: d.id,
        descriptor_name: selected,
        parameters: params,
        scope,
        frame_index: scope === "frame" ? frameIndex : undefined,
        output_dtype: effectiveDtype,
        device: effectiveDevice,
        num_threads: effectiveDevice === "cpu" && schema.execution.num_threads ? threads : undefined,
      };
      const r = await ipc.request<{ job_id: string | null; cache: { existing_run_id: string; in_flight?: boolean } | null }>(
        "descriptor.submit",
        submitRequest,
      );
      const watchCompute = (jobId: string) => {
        void watchJob(jobId).then((done) => {
          if (done.status === "COMPLETED") message.success(t("Run {id} completed", { id: String(done.result?.run_id ?? "") }));
          else message.error(t("Compute {status}: {message}", { status: done.status, message: done.error?.message ?? "" }));
        });
      };
      if (r.cache?.in_flight && r.job_id) {
        // An identical compute is already queued/running: attach to the live
        // job instead of offering a recalculation of the same input.
        trackJob(r.job_id, "descriptor.compute", d.id);
        message.info(t("Identical compute already in progress — attached to it"));
        watchCompute(r.job_id);
        return;
      }
      if (r.cache) {
        Modal.confirm({
          title: t("Existing compatible result found"),
          content: t("A completed run with identical configuration exists ({id}). Recalculate anyway?", { id: r.cache.existing_run_id }),
          okText: t("Recalculate"),
          cancelText: t("Use existing"),
          onOk: async () => {
            const r2 = await ipc.request<{ job_id: string | null }>("descriptor.submit", { ...submitRequest, force: true });
            if (r2.job_id) {
              trackJob(r2.job_id, "descriptor.compute", d.id);
              watchCompute(r2.job_id);
            }
          },
          onCancel: () => message.info(t("Using existing run {id}", { id: r.cache!.existing_run_id })),
        });
        return;
      }
      if (r.job_id) {
        trackJob(r.job_id, "descriptor.compute", d.id);
        message.info(t("Compute submitted — see Jobs"));
        watchCompute(r.job_id);
      }
    } catch (e) {
      const err = e as { code: string; message: string };
      message.error(`${err.code}: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  // Full metadata shown in the sidebar row tooltip (replaces the removed
  // DESCRIPTOR INFO card).
  const infoTooltip = (info: DescriptorInfo) => {
    const s = schemas[info.name];
    const reason = incompatibleReasons.get(info.name);
    const description = s?.description || info.description;
    const rows: [string, string][] = s
      ? ([
          [t("Descriptor version"), s.descriptor_version],
          [t("Schema version"), String(s.schema_version)],
          [t("Level"), s.level],
          [t("Backend"), s.backend],
          [t("Execution engine"), s.execution_engine],
          [t("Category"), s.category],
          [t("Devices"), s.execution.devices.join(", ")],
          [t("Threads"), s.execution.num_threads ? t("yes") : t("no")],
          [t("Cancelable"), s.execution.cooperative_cancel ? t("yes") : t("no")],
          [t("Sparse"), String(s.output.sparse)],
          [t("Model"), s.asset.policy === "none" ? t("none") : `${s.asset.policy} (${s.asset.bundled_resources[0] ?? t("external")})`],
          [t("Input periodicity"), s.input.periodicity.join(", ")],
          [t("Spin"), s.input.spin ? t("yes") : t("no")],
        ] as [string, string][])
      : ([
          [t("Level"), info.level],
          [t("Backend"), info.backend],
          [t("Category"), info.category],
        ] as [string, string][]);
    return (
      <div style={{ maxWidth: 380 }}>
        {reason && (
          <div style={{ color: "#FFD666", fontWeight: 600, fontSize: 12, marginBottom: 6 }}>⚠ {reason}</div>
        )}
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 2 }}>{s?.display_name ?? info.display_name}</div>
        {description && (
          <div style={{ fontSize: 12, color: "rgba(255,255,255,0.75)", marginBottom: 8 }}>{description}</div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", columnGap: 12, rowGap: 2, fontSize: 12 }}>
          {rows.map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <span style={{ color: "rgba(255,255,255,0.65)" }}>{k}</span>
              <span style={{ fontWeight: 500 }}>{v}</span>
            </div>
          ))}
        </div>
      </div>
    );
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
        <Input.Search
          placeholder={t("Filter descriptors")}
          size="small"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          allowClear
          style={{ marginBottom: 8 }}
        />
        {filtered.map((info) => {
          const reason = incompatibleReasons.get(info.name);
          const disabled = Boolean(reason);
          const active = selected === info.name;
          return (
            <Tooltip key={info.name} title={infoTooltip(info)} placement="right" mouseEnterDelay={0.2} styles={{ body: { padding: "10px 12px" } }}>
              <div
                onClick={() => !disabled && setSelected(info.name)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 10px",
                  borderRadius: 6,
                  cursor: disabled ? "not-allowed" : "pointer",
                  opacity: disabled ? 0.45 : 1,
                  background: active ? "#EBF3FC" : "transparent",
                  marginBottom: 2,
                }}
              >
                <div
                  style={{
                    flex: 1,
                    minWidth: 0,
                    fontSize: 13,
                    fontWeight: active ? 600 : 400,
                    color: active ? "#0F6CBD" : "#242424",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {info.display_name}
                </div>
                <div style={{ display: "flex", gap: 3 }}>
                  <MetaBadge value={info.level} />
                  <MetaBadge value={info.backend} />
                  <MetaBadge value={info.category} />
                </div>
              </div>
            </Tooltip>
          );
        })}
      </div>

      {/* center column: configuration form (metadata lives in the sidebar badges/tooltip) */}
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 16, minHeight: 0 }}>
        {/* configuration form */}
        <div
          style={{
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            background: "#FFFFFF",
            border: "1px solid #EAECF0",
            borderRadius: 6,
            overflow: "hidden",
          }}
        >
          {schema ? (
            <>
              <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "20px 20px 24px" }}>
              <SectionHeading
                number={1}
                title={t("Parameters")}
                description={t("Configure the {name} descriptor parameters.", { name: schema.display_name })}
              />
              <Typography.Paragraph type="secondary" style={{ margin: "-6px 0 18px", fontSize: 12 }}>
                {schema.description}
              </Typography.Paragraph>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
                  columnGap: 18,
                  rowGap: 20,
                  paddingBottom: 24,
                  borderBottom: "1px solid #EAECF0",
                }}
              >
                {Object.entries(schema.parameters).map(([name, meta]) => (
                  <SchemaField
                    key={name}
                    name={name}
                    schema={meta}
                    value={values[name]}
                    elementOptions={elementOptions}
                    modelExtensions={schema.asset.file_extensions}
                    allowExternalModel={schema.asset.allow_external}
                    onModelBrowseError={() => message.error(t("Could not open the model file dialog"))}
                    onChange={(v) => setValues((prev) => ({ ...prev, [name]: v }))}
                  />
                ))}
              </div>
              <div style={{ padding: "22px 0 24px", borderBottom: "1px solid #EAECF0" }}>
                <SectionHeading number={2} title={t("Execution")} description={t("Choose where and how the descriptor is calculated.")} />
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                    columnGap: 20,
                    rowGap: 18,
                    alignItems: "start",
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <Typography.Text style={{ fontSize: 13, fontWeight: 500 }}>{t("Device")}</Typography.Text>
                    <div style={{ marginTop: 7 }}>
                      {availableDevices.length > 1 ? (
                        <Tooltip title={t("CUDA needs a compatible NVIDIA GPU and drivers; the run fails with DEVICE_UNAVAILABLE otherwise.")}>
                          <Select
                            value={effectiveDevice}
                            style={{ width: "100%" }}
                            onChange={(v) => setDevice(v)}
                            options={availableDevices.map((dev) => ({
                              value: dev,
                              label: DEVICE_LABELS[dev] ?? dev.toUpperCase(),
                            }))}
                          />
                        </Tooltip>
                      ) : (
                        <Select value="cpu" style={{ width: "100%" }} disabled options={[{ value: "cpu", label: "CPU" }]} />
                      )}
                    </div>
                  </div>
                  {schema.execution.num_threads && (
                    <div style={{ minWidth: 0 }}>
                      <Typography.Text style={{ fontSize: 13, fontWeight: 500 }}>{t("Threads")}</Typography.Text>
                      <div style={{ marginTop: 7 }}>
                        <InputNumber min={1} max={64} precision={0} value={threads} disabled={effectiveDevice !== "cpu"} onChange={(v) => setThreads(v ?? undefined)} style={{ width: "100%" }} placeholder={t("engine default")} />
                      </div>
                    </div>
                  )}
                  <div style={{ minWidth: 0 }}>
                    <Typography.Text style={{ fontSize: 13, fontWeight: 500 }}>{t("Output dtype")}</Typography.Text>
                    <div style={{ marginTop: 9 }}>
                      <Radio.Group value={effectiveDtype} onChange={(event) => setDtype(event.target.value)}>
                        <Space size={18} wrap>
                          {availableDtypes.map((type) => (
                            <Radio key={type} value={type}>
                              {type}
                            </Radio>
                          ))}
                        </Space>
                      </Radio.Group>
                    </div>
                  </div>
                </div>
              </div>
              <div style={{ paddingTop: 22 }}>
                <SectionHeading number={3} title={t("Scope")} description={t("Choose whether to calculate one frame or the entire dataset.")} />
                <Radio.Group value={scope} onChange={(event) => setScope(event.target.value as "dataset" | "frame")}>
                  <Space size={24} wrap>
                    <Radio value="frame">
                      {t("Current frame")} <Typography.Text type="secondary">({t("Frame {index}", { index: frameIndex })})</Typography.Text>
                    </Radio>
                    <Radio value="dataset">
                      {t("Entire dataset")} <Typography.Text type="secondary">({t("{n} structures", { n: d.number_of_frames.toLocaleString() })})</Typography.Text>
                    </Radio>
                  </Space>
                </Radio.Group>
                {scope === "frame" && (
                  <div style={{ marginTop: 16 }}>
                    <Typography.Text style={{ fontSize: 13, fontWeight: 500 }}>{t("Frame index")}</Typography.Text>
                    <div style={{ marginTop: 7, maxWidth: 220 }}>
                      <InputNumber min={0} max={d.number_of_frames - 1} value={frameIndex} onChange={(v) => setFrameIndex(v ?? 0)} style={{ width: "100%" }} />
                    </div>
                  </div>
                )}
              </div>
              </div>
              <div
                style={{
                  flex: "0 0 auto",
                  display: "flex",
                  alignItems: "center",
                  padding: "14px 20px 16px",
                  borderTop: "1px solid #EAECF0",
                  background: "#FFFFFF",
                }}
              >
                <Space size={16} wrap>
                  <Button type="primary" onClick={() => void submit()} loading={submitting}>
                    {t("Calculate")}
                  </Button>
                  <Checkbox checked disabled>
                    {t("Cache reuse enabled")}
                  </Checkbox>
                </Space>
              </div>
            </>
          ) : (
            <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Empty description={t("Select a descriptor")} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
