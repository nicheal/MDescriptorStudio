// Deterministic per-element colors: identical element renders identically in
// every view (design doc §71). Hash of the symbol picks from the palette.
import { categoricalPalette } from "../theme";

const HASHES: Record<string, number> = {
  H: 0,
  C: 1,
  N: 2,
  O: 3,
  S: 4,
  Si: 5,
  Ga: 0,
  As: 1,
  Mo: 6,
  Cr: 8,
  W: 9,
  Al: 5,
  Cu: 7,
};

export function elementColor(symbol: string): string {
  const known = HASHES[symbol];
  if (known !== undefined) return categoricalPalette[known % categoricalPalette.length];
  let h = 0;
  for (let i = 0; i < symbol.length; i++) h = (h * 31 + symbol.charCodeAt(i)) >>> 0;
  return categoricalPalette[h % categoricalPalette.length];
}
