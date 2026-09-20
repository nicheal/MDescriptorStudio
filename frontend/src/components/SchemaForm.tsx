// Schema-driven parameter form: 8 param types + one nesting level (ADR-7/§3.2
// of engine-api-report). Values collected into a params object on change.
import { useEffect, useState } from "react";
import { Button, Checkbox, Input, InputNumber, Select, Space, Tooltip, Typography } from "antd";
import { FolderOpen16Regular, Info16Regular } from "@fluentui/react-icons";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { useT } from "../i18n";
import type { ParamSchema } from "../types/protocol";

export type ParamValues = Record<string, unknown>;

function humanizeParameterName(name: string): string {
  return name
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .toLowerCase();
}

function parameterLabel(name: string, schema: ParamSchema): string {
  return schema.display_name?.trim() || humanizeParameterName(name).replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function parameterDescription(t: (key: string, vars?: Record<string, string | number>) => string, name: string, schema: ParamSchema): string {
  return schema.description?.trim() || t("{name} setting.", { name: parameterLabel(name, schema) });
}

function formatObjectValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  try {
    return JSON.stringify(value, null, 2) ?? "";
  } catch {
    return "";
  }
}

export function SchemaField({
  name,
  schema,
  value,
  onChange,
  elementOptions,
  depth = 0,
  modelExtensions,
  allowExternalModel = true,
  onModelBrowseError,
}: {
  name: string;
  schema: ParamSchema;
  value: unknown;
  onChange: (v: unknown) => void;
  elementOptions?: string[];
  depth?: number;
  modelExtensions?: string[];
  allowExternalModel?: boolean;
  onModelBrowseError?: () => void;
}) {
  const { t } = useT();
  const description = parameterDescription(t, name, schema);
  const displayName = parameterLabel(name, schema);
  const hasProperties = Object.keys(schema.properties ?? {}).length > 0;
  const [objectText, setObjectText] = useState(() => formatObjectValue(value ?? schema.default));
  const [objectError, setObjectError] = useState(false);

  useEffect(() => {
    if (schema.type === "object" && !hasProperties) {
      setObjectText(formatObjectValue(value ?? schema.default));
      setObjectError(false);
    }
  }, [hasProperties, schema.default, schema.type, value]);

  const commitObjectText = () => {
    const text = objectText.trim();
    if (!text) {
      setObjectError(false);
      onChange(undefined);
      return;
    }
    try {
      const parsed: unknown = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("expected an object");
      setObjectError(false);
      onChange(parsed);
    } catch {
      setObjectError(true);
    }
  };

  const label = (
    <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, minHeight: 22 }}>
      <Typography.Text style={{ fontSize: 13, fontWeight: depth === 0 ? 600 : 500 }}>
        {displayName}
      </Typography.Text>
      {displayName !== name && (
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          ({name})
        </Typography.Text>
      )}
        {schema.required && <span style={{ color: "#C42B1C" }}>*</span>}
        {schema.unit && <Typography.Text type="secondary" style={{ fontSize: 11 }}>({schema.unit})</Typography.Text>}
      <Tooltip title={description}>
        <Info16Regular
          aria-label={`${displayName}: ${description}`}
          style={{ color: "#98A2B3", fontSize: 13, cursor: "help" }}
        />
      </Tooltip>
    </div>
  );
  const wrap = (control: React.ReactNode) => (
    <div
      style={{
        minWidth: 0,
        paddingLeft: depth * 16,
        gridColumn: schema.type === "object" || schema.type === "model" ? "1 / -1" : undefined,
      }}
    >
      {label}
      <div style={{ minWidth: 0, marginTop: 6 }}>{control}</div>
    </div>
  );

  switch (schema.type) {
    case "integer":
      return wrap(
        <InputNumber
          style={{ width: "100%" }}
          value={value === undefined || value === null ? (schema.default as number | undefined) : (value as number)}
          precision={0}
          min={schema.minimum ?? schema.exclusiveMinimum}
          onChange={(v) => onChange(v)}
        />,
      );
    case "number":
      return wrap(
        <InputNumber
          style={{ width: "100%" }}
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
          style={{ width: "100%" }}
          value={(value ?? schema.default) as string}
          options={(schema.enum ?? []).map((e) => ({ value: e, label: e }))}
          onChange={(v) => onChange(v)}
        />,
      );
    case "species":
      return wrap(
        <Select
          mode="multiple"
          style={{ width: "100%" }}
          placeholder={t("elements")}
          value={(value as string[]) ?? elementOptions ?? []}
          options={(elementOptions ?? []).map((e) => ({ value: e, label: e }))}
          onChange={(v) => onChange(v)}
        />,
      );
    case "array":
      return wrap(
        <Select
          mode="tags"
          style={{ width: "100%" }}
          placeholder={t("numbers, Enter to add")}
          value={((value as unknown[]) ?? (schema.default as unknown[]) ?? []).map(String)}
          onChange={(vs) => {
            if (schema.items?.type === "string") {
              onChange(vs);
              return;
            }
            const parsed = vs
              .map((v) => Number(v))
              .filter((n) => !Number.isNaN(n))
              .map((n) => (schema.items?.type === "integer" ? Math.trunc(n) : n));
            onChange(parsed);
          }}
          tokenSeparators={[",", " "]}
        />,
      );
    case "model": {
      const extensions = (modelExtensions ?? [])
        .map((extension) => extension.replace(/^\./, "").trim())
        .filter(Boolean);

      const browseModel = async () => {
        try {
          const selected = await openDialog({
            multiple: false,
            title: t("Select model file"),
            ...(extensions.length > 0
              ? { filters: [{ name: t("Model files"), extensions }] }
              : {}),
          });
          const path = Array.isArray(selected) ? selected[0] : selected;
          if (path) onChange(path);
        } catch (error) {
          console.error("Could not open the model file dialog", error);
          onModelBrowseError?.();
        }
      };

      return wrap(
        <Space.Compact style={{ width: "100%", display: "flex" }}>
          <Input
            style={{ flex: 1, minWidth: 0 }}
            placeholder={t("Leave empty to use the bundled model")}
            value={(value as string) ?? ""}
            readOnly
            allowClear
            onClear={() => onChange(undefined)}
          />
          {allowExternalModel && (
            <Button
              icon={<FolderOpen16Regular />}
              onClick={() => void browseModel()}
              aria-label={t("Browse for a model file")}
            >
              {t("Browse")}
            </Button>
          )}
        </Space.Compact>,
      );
    }
    case "object": {
      const nested: ParamValues = (value as ParamValues) ?? {};
      if (!hasProperties) {
        return wrap(
          <Input.TextArea
            autoSize={{ minRows: 2, maxRows: 8 }}
            value={objectText}
            status={objectError ? "error" : undefined}
            placeholder="{}"
            onChange={(event) => {
              setObjectText(event.target.value);
              setObjectError(false);
            }}
            onBlur={commitObjectText}
          />,
        );
      }
      return wrap(
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
            gap: "14px 16px",
            padding: "12px 12px 2px",
            border: "1px solid #E5E7EB",
            borderRadius: 6,
            background: "#FAFBFC",
          }}
        >
          {Object.entries(schema.properties ?? {}).map(([sub, subSchema]) => (
            <SchemaField
              key={sub}
              name={sub}
              schema={subSchema}
              value={nested[sub]}
              elementOptions={elementOptions}
              depth={depth + 1}
              modelExtensions={modelExtensions}
              allowExternalModel={allowExternalModel}
              onModelBrowseError={onModelBrowseError}
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

export function collectDefaults(schema: Record<string, ParamSchema>, elementOptions: string[] = []): ParamValues {
  const out: ParamValues = {};
  for (const [k, meta] of Object.entries(schema)) {
    if (meta.type === "species") {
      out[k] = [...elementOptions];
    } else if (meta.type !== "model" && meta.default !== undefined) {
      // Seeded for every parameter that shows a default, not only the required
      // ones: the controls display schema.default for an untouched optional
      // field, so omitting it here submitted something other than what the form
      // showed — and made the cache key disagree with the screen.
      out[k] = meta.default;
    }
  }
  return out;
}
