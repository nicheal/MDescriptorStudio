// Schema-driven parameter form: 8 param types + one nesting level (ADR-7/§3.2
// of engine-api-report). Values collected into a params object on change.
import { Checkbox, Input, InputNumber, Select, Space, Typography } from "antd";
import type { ParamSchema } from "../types/protocol";

export type ParamValues = Record<string, unknown>;

export function SchemaField({
  name,
  schema,
  value,
  onChange,
  elementOptions,
  depth = 0,
}: {
  name: string;
  schema: ParamSchema;
  value: unknown;
  onChange: (v: unknown) => void;
  elementOptions?: string[];
  depth?: number;
}) {
  const label = (
    <Space size={6}>
      <Typography.Text style={{ fontSize: 13, fontWeight: depth === 0 ? 600 : 500 }}>{name}</Typography.Text>
      {schema.required && <span style={{ color: "#C42B1C" }}>*</span>}
      {schema.unit && <Typography.Text type="secondary" style={{ fontSize: 11 }}>{schema.unit}</Typography.Text>}
    </Space>
  );
  const wrap = (control: React.ReactNode) => (
    <div style={{ marginBottom: 12, paddingLeft: depth * 16 }}>
      {label}
      <div style={{ marginTop: 4 }}>{control}</div>
      {schema.description && depth === 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {schema.description}
        </Typography.Text>
      )}
    </div>
  );

  switch (schema.type) {
    case "integer":
      return wrap(
        <InputNumber
          style={{ width: 200 }}
          value={value === undefined || value === null ? (schema.default as number | undefined) : (value as number)}
          precision={0}
          min={schema.minimum ?? schema.exclusiveMinimum}
          onChange={(v) => onChange(v)}
        />,
      );
    case "number":
      return wrap(
        <InputNumber
          style={{ width: 200 }}
          value={value === undefined || value === null ? (schema.default as number | undefined) : (value as number)}
          step={0.05}
          min={schema.minimum ?? schema.exclusiveMinimum}
          max={schema.maximum ?? schema.exclusiveMaximum}
          onChange={(v) => onChange(v)}
          addonAfter={schema.unit}
        />,
      );
    case "boolean":
      return wrap(
        <Checkbox
          checked={Boolean(value ?? schema.default ?? false)}
          onChange={(e) => onChange(e.target.checked)}
        />,
      );
    case "enum":
      return wrap(
        <Select
          style={{ width: 240 }}
          value={(value ?? schema.default) as string}
          options={(schema.enum ?? []).map((e) => ({ value: e, label: e }))}
          onChange={(v) => onChange(v)}
        />,
      );
    case "species":
      return wrap(
        <Select
          mode="multiple"
          style={{ width: 240 }}
          placeholder="elements"
          value={(value as string[]) ?? []}
          options={(elementOptions ?? []).map((e) => ({ value: e, label: e }))}
          onChange={(v) => onChange(v)}
        />,
      );
    case "array":
      return wrap(
        <Select
          mode="tags"
          style={{ width: 240 }}
          placeholder="numbers, Enter to add"
          value={((value as unknown[]) ?? (schema.default as unknown[]) ?? []).map(String)}
          onChange={(vs) => onChange(vs.map((v) => Number(v)).filter((n) => !Number.isNaN(n)))}
          tokenSeparators={[",", " "]}
        />,
      );
    case "model":
      return wrap(
        <Input
          style={{ width: 320 }}
          placeholder="bundled resource used when empty; or local model path"
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value || undefined)}
        />,
      );
    case "object": {
      const nested: ParamValues = (value as ParamValues) ?? {};
      return wrap(
        <div style={{ borderLeft: "2px solid #EBF3FC", paddingLeft: 12 }}>
          {(Object.entries(schema.properties ?? {}).length ?? 0) === 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>no fields</Typography.Text>
          )}
          {Object.entries(schema.properties ?? {}).map(([sub, subSchema]) => (
            <SchemaField
              key={sub}
              name={sub}
              schema={subSchema}
              value={nested[sub]}
              elementOptions={elementOptions}
              depth={depth + 1}
              onChange={(v) => {
                const next = { ...nested };
                if (v === undefined) delete next[sub];
                else next[sub] = v;
                onChange(Object.keys(next).length ? next : undefined);
              }}
            />
          ))}
        </div>,
      );
    }
    default:
      return wrap(
        <Input style={{ width: 260 }} value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} />,
      );
  }
}

export function collectDefaults(schema: Record<string, ParamSchema>): ParamValues {
  const out: ParamValues = {};
  for (const [k, meta] of Object.entries(schema)) {
    if (meta.required && meta.type !== "species" && meta.type !== "model" && meta.default !== undefined) {
      out[k] = meta.default;
    }
  }
  return out;
}

export function speciesToNumbers(selected: string[], elementOptions: string[]): number[] {
  const z: Record<string, number> = {
    H: 1, He: 2, Li: 3, Be: 4, B: 5, C: 6, N: 7, O: 8, F: 9, Ne: 10, Na: 11, Mg: 12,
    Al: 13, Si: 14, P: 15, S: 16, Cl: 17, Ar: 18, K: 19, Ca: 20, Sc: 21, Ti: 22, V: 23,
    Cr: 24, Mn: 25, Fe: 26, Co: 27, Ni: 28, Cu: 29, Zn: 30, Ga: 31, Ge: 32, As: 33,
    Se: 34, Br: 35, Kr: 36, Rb: 37, Sr: 38, Y: 39, Zr: 40, Nb: 41, Mo: 42, Tc: 43,
    Ru: 44, Rh: 45, Pd: 46, Ag: 47, Cd: 48, In: 49, Sn: 50, Sb: 51, Te: 52, I: 53,
    Xe: 54, Cs: 55, Ba: 56, La: 57, Ce: 58, Pr: 59, Nd: 60, Pm: 61, Sm: 62, Eu: 63,
    Gd: 64, Tb: 65, Dy: 66, Ho: 67, Er: 68, Tm: 69, Yb: 70, Lu: 71, Hf: 72, Ta: 73,
    W: 74, Re: 75, Os: 76, Ir: 77, Pt: 78, Au: 79, Hg: 80, Tl: 81, Pb: 82, Bi: 83,
    Po: 84, At: 85, Rn: 86, Fr: 87, Ra: 88, Ac: 89, Th: 90, Pa: 91, U: 92,
  };
  void elementOptions;
  return selected.map((s) => z[s]).filter((n) => n !== undefined);
}
