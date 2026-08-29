// Schema-driven parameter form: 8 param types + one nesting level (ADR-7/§3.2
// of engine-api-report). Values collected into a params object on change.
import { Checkbox, Input, InputNumber, Select, Space, Typography } from "antd";
import type { ParamSchema } from "../types/protocol";

export type ParamValues = Record<string, unknown>;

// MDescriptor is the source of truth when it provides a description. Older
// schemas leave many parameters undocumented, so keep concise GUI fallbacks
// here to ensure every configuration field has useful context.
const PARAMETER_DESCRIPTIONS: Record<string, string> = {
  species: "Chemical species included in the descriptor.",
  N: "Maximum expansion or basis size.",
  r0: "Reference radial distance or scale.",
  trans: "Transformation applied to radial coordinates.",
  type: "Transformation or function type.",
  p: "Power or exponent controlling the function.",
  a: "Scale or shape parameter for the function.",
  wL: "Weights assigned to angular or radial channels.",
  maxdeg: "Maximum degree retained for each channel.",
  D: "Descriptor channel weighting and cutoff settings.",
  csp: "Chemical species contribution weight.",
  chc: "Chemical channel contribution weight.",
  ahc: "Angular channel contribution weight.",
  bhc: "Basis channel contribution weight.",
  rcut: "Outer radial cutoff distance.",
  rin: "Inner radial cutoff distance.",
  pcut: "Outer cutoff polynomial order.",
  pin: "Inner cutoff polynomial order.",
  constants: "Include constant terms in the descriptor.",
  r_cut: "Neighbor cutoff distance.",
  g2_params: "Parameters for two-body radial functions.",
  g3_params: "Parameters for three-body angular functions.",
  g4_params: "Parameters for four-body angular functions.",
  g5_params: "Parameters for alternative four-body angular functions.",
  per_system: "Return one composition vector per system.",
  n_radial: "Number of radial basis functions.",
  l_max: "Maximum angular momentum degree.",
  cutoff_function: "Function used to smoothly truncate neighbors.",
  radial_sigma: "Width of the radial smoothing function.",
  include_radial: "Include radial two-body terms.",
  include_angular: "Include angular many-body terms.",
  normalize_radial: "Normalize radial components.",
  normalize_angular: "Normalize angular components.",
  super_vector: "Combine components into one feature vector.",
  radial_weight: "Relative weight of radial components.",
  angular_weight: "Relative weight of angular components.",
  exclude_self_interaction: "Exclude the central atom from neighbor terms.",
  n_atoms_max: "Maximum atoms supported per structure.",
  permutation: "Handling of atom-order permutations.",
  exponent: "Exponent used in the interaction kernel.",
  model: "Model resource or local checkpoint path.",
  calibrate: "Apply calibration to the model output.",
  parameters: "Descriptor-specific parameter group.",
  Rc: "Radial cutoff distance.",
  cutoff: "Neighbor cutoff distance.",
  accuracy: "Target accuracy for reciprocal-space terms.",
  w: "Relative weight of the reciprocal-space contribution.",
  g_cut: "Reciprocal-space cutoff.",
  twojmax: "Maximum angular expansion index.",
  diagonal: "Diagonal or channel selection mode.",
  rfac0: "Radial basis scaling factor.",
  rmin0: "Minimum radial distance.",
  rcutfac: "Radial cutoff scaling factor.",
  element_profile: "Element-specific profile settings.",
  element_radii: "Element-specific radii.",
  weights: "Element or channel weighting factors.",
  normalize_U: "Normalize expansion coefficients.",
  geometry: "Geometry function used to build the distribution.",
  grid: "Grid range and resolution for the distribution.",
  weighting: "Distance-weighting function and parameters.",
  periodic: "Account for periodic boundary conditions.",
  normalize_gaussians: "Normalize Gaussian contributions.",
  normalization: "Final normalization applied to the descriptor.",
  density_width: "Width of the neighbor density.",
  max_radial: "Maximum radial basis index.",
  max_angular: "Maximum angular basis index.",
  k_cutoff: "Cutoff for radial basis functions.",
  radial_radius: "Radial extent of the basis.",
  min_dist: "Minimum interatomic distance.",
  max_dist: "Maximum interatomic distance.",
  radial_basis_size: "Number of functions in the radial basis.",
  radial_funcs_count: "Number of radial function channels.",
  max_rank: "Maximum tensor rank.",
  radial_basis_type: "Radial basis function family.",
  full_neighbor_list: "Include both directions of each neighbor pair.",
  self_pairs: "Include self-pairs in the neighbor list.",
  nmax: "Maximum radial basis index.",
  lmax: "Maximum angular expansion index.",
  alpha: "Radial weighting exponent.",
  weight_on: "Enable distance-based atom weighting.",
  rbf: "Radial basis function family.",
  n_max: "Maximum radial basis index.",
  average: "Pooling method for local environments.",
  compression: "Compression scheme for the descriptor.",
  alpha_max: "Maximum radial basis order per species.",
  rcut_hard: "Hard outer cutoff distance.",
  rcut_soft: "Soft cutoff distance.",
  nf: "Number of radial feature channels.",
  radial_enhancement: "Strength of radial feature enhancement.",
  basis: "Basis function family.",
  atom_sigma_r: "Radial Gaussian width for each atom type.",
  atom_sigma_r_scaling: "Radial width scaling for each atom type.",
  atom_sigma_t: "Tangential Gaussian width for each atom type.",
  atom_sigma_t_scaling: "Tangential width scaling for each atom type.",
  amplitude_scaling: "Per-species amplitude scaling.",
  central_weight: "Weight of the central atom.",
  central_species: "Species treated as central atoms.",
  max_neighbors: "Maximum neighbors retained per atom.",
  separate_neighbor_types: "Keep neighbor species channels separate.",
  function: "Distribution function used for the descriptor.",
  n: "Order of the descriptor function.",
  sigma: "Gaussian smoothing width.",
};

function humanizeParameterName(name: string): string {
  return name
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .toLowerCase();
}

function parameterDescription(name: string, schema: ParamSchema): string {
  return schema.description?.trim() || PARAMETER_DESCRIPTIONS[name] || `${humanizeParameterName(name)} setting.`;
}

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
  const description = parameterDescription(name, schema);
  const label = (
    <div style={{ display: "flex", alignItems: "baseline", flexWrap: "wrap", gap: "2px 8px", lineHeight: 1.4 }}>
      <Space size={6}>
        <Typography.Text style={{ fontSize: 13, fontWeight: depth === 0 ? 600 : 500 }}>{name}</Typography.Text>
        {schema.required && <span style={{ color: "#C42B1C" }}>*</span>}
        {schema.unit && <Typography.Text type="secondary" style={{ fontSize: 11 }}>{schema.unit}</Typography.Text>}
      </Space>
      <Typography.Text style={{ fontSize: 11, fontWeight: 400, color: "#667085", lineHeight: 1.4 }}>
        — {description}
      </Typography.Text>
    </div>
  );
  const wrap = (control: React.ReactNode) => (
    <div style={{ marginBottom: 12, paddingLeft: depth * 16 }}>
      {label}
      <div style={{ marginTop: 4 }}>{control}</div>
    </div>
  );

  switch (schema.type) {
    case "integer":
      return wrap(
        <InputNumber
          style={{ width: "100%", maxWidth: 200 }}
          value={value === undefined || value === null ? (schema.default as number | undefined) : (value as number)}
          precision={0}
          min={schema.minimum ?? schema.exclusiveMinimum}
          onChange={(v) => onChange(v)}
        />,
      );
    case "number":
      return wrap(
        <InputNumber
          style={{ width: "100%", maxWidth: 200 }}
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
          style={{ width: "100%", maxWidth: 240 }}
          value={(value ?? schema.default) as string}
          options={(schema.enum ?? []).map((e) => ({ value: e, label: e }))}
          onChange={(v) => onChange(v)}
        />,
      );
    case "species":
      return wrap(
        <Select
          mode="multiple"
          style={{ width: "100%", maxWidth: 240 }}
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
          style={{ width: "100%", maxWidth: 240 }}
          placeholder="numbers, Enter to add"
          value={((value as unknown[]) ?? (schema.default as unknown[]) ?? []).map(String)}
          onChange={(vs) => onChange(vs.map((v) => Number(v)).filter((n) => !Number.isNaN(n)))}
          tokenSeparators={[",", " "]}
        />,
      );
    case "model":
      return wrap(
        <Input
          style={{ width: "100%", maxWidth: 320 }}
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
        <Input
          style={{ width: "100%", maxWidth: 260 }}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />,
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
