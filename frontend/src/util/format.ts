// Small formatting helpers shared across dataset views.
export function formatLabel(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

/** Keep integer identifiers exact and numeric displays free of grouping separators. */
export function formatNumber(value: number, precision = 5): string {
  if (Number.isInteger(value)) return String(value);
  return Math.abs(value) >= 1000
    ? value.toLocaleString("en-US", { useGrouping: false, maximumFractionDigits: 2 })
    : value.toPrecision(precision);
}
