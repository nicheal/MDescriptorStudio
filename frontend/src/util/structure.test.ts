import { describe, expect, it } from "vitest";
import { cellParameters, massDensity, minimumDistancePair, minimumInteratomicDistance, netForceMagnitude, virialSummary } from "./structure";

function makeXyz(atoms: [string, number, number, number][]): string {
  return `${atoms.length}\ntest frame\n${atoms.map(([el, x, y, z]) => `${el} ${x} ${y} ${z}`).join("\n")}\n`;
}

describe("cellParameters", () => {
  it("computes lengths and angles for a cubic cell", () => {
    const p = cellParameters([3.84, 0, 0, 0, 3.84, 0, 0, 0, 3.84]);
    expect(p?.a).toBeCloseTo(3.84);
    expect(p?.b).toBeCloseTo(3.84);
    expect(p?.c).toBeCloseTo(3.84);
    expect(p?.alpha).toBeCloseTo(90);
    expect(p?.beta).toBeCloseTo(90);
    expect(p?.gamma).toBeCloseTo(90);
  });

  it("computes the hexagonal 120° gamma angle", () => {
    const b = 3.2;
    const p = cellParameters([b, 0, 0, -b / 2, b * Math.sin(Math.PI / 3), 0, 0, 0, 5.2]);
    expect(p?.a).toBeCloseTo(3.2);
    expect(p?.b).toBeCloseTo(3.2);
    expect(p?.gamma).toBeCloseTo(120);
    expect(p?.alpha).toBeCloseTo(90);
    expect(p?.beta).toBeCloseTo(90);
  });

  it("rejects malformed and degenerate cells", () => {
    expect(cellParameters([1, 2, 3])).toBeNull();
    expect(cellParameters([0, 0, 0, 0, 3, 0, 0, 0, 3])).toBeNull();
  });
});

describe("minimumInteratomicDistance", () => {
  it("finds the closest pair including periodic ghost atoms", () => {
    // Images of the same chain along z: pair distances 2.6, 2.4, 5.0 → 2.4.
    const xyz = makeXyz([
      ["Si", 0, 0, 0],
      ["Si", 0, 0, 2.6],
      ["Si", 0, 0, 5.0],
    ]);
    expect(minimumInteratomicDistance(xyz)).toBeCloseTo(2.4);
  });

  it("falls back to the exact scan when nothing is within the hash radius", () => {
    const xyz = makeXyz([
      ["H", 0, 0, 0],
      ["H", 0, 0, 4],
    ]);
    expect(minimumInteratomicDistance(xyz)).toBeCloseTo(4);
  });

  it("ignores coincident duplicates and degenerate input", () => {
    expect(minimumInteratomicDistance(makeXyz([["H", 0, 0, 0], ["H", 0, 0, 0]]))).toBeNull();
    expect(minimumInteratomicDistance(makeXyz([["H", 1, 2, 3]]))).toBeNull();
    expect(minimumInteratomicDistance("0\nempty\n")).toBeNull();
  });
});

describe("minimumDistancePair", () => {
  it("returns the indices of the closest pair including ghost images", () => {
    // Same chain as above: pair distances 2.6, 2.4, 5.0 → atoms 1 and 2.
    const xyz = makeXyz([
      ["Si", 0, 0, 0],
      ["Si", 0, 0, 2.6],
      ["Si", 0, 0, 5.0],
    ]);
    const pair = minimumDistancePair(xyz);
    expect(pair?.i).toBe(1);
    expect(pair?.j).toBe(2);
    expect(pair?.distance).toBeCloseTo(2.4);
  });

  it("reports the pair found by the exact scan fallback", () => {
    // 4 Å apart exceeds the hash radius, so the exact scan produces the pair.
    expect(minimumDistancePair(makeXyz([["H", 0, 0, 0], ["H", 0, 0, 4]]))).toEqual({
      distance: 4,
      i: 0,
      j: 1,
    });
  });

  it("returns null for degenerate input", () => {
    expect(minimumDistancePair(makeXyz([["H", 0, 0, 0], ["H", 0, 0, 0]]))).toBeNull();
    expect(minimumDistancePair(makeXyz([["H", 1, 2, 3]]))).toBeNull();
    expect(minimumDistancePair("0\nempty\n")).toBeNull();
  });
});

describe("netForceMagnitude", () => {
  it("returns |ΣF| over complete force rows", () => {
    expect(netForceMagnitude([
      { fx: 1, fy: -1, fz: 0 },
      { fx: -1, fy: 1, fz: 0 },
    ])).toBeCloseTo(0);
    expect(netForceMagnitude([{ fx: 3, fy: 0, fz: 4 }])).toBeCloseTo(5);
  });

  it("returns null when forces are absent or incomplete", () => {
    expect(netForceMagnitude([{ fx: 1, fy: null, fz: 0 }])).toBeNull();
    expect(netForceMagnitude([])).toBeNull();
  });
});

describe("massDensity", () => {
  it("computes density from standard atomic weights", () => {
    // 2 Si (28.085 amu each) in 100 Å³ → 0.93272 g/cm³.
    expect(massDensity(["Si", "Si"], 100)).toBeCloseTo(0.93272, 4);
  });

  it("refuses unknown elements and unusable volumes", () => {
    expect(massDensity(["Xx"], 100)).toBeNull();
    expect(massDensity(["Si"], 0)).toBeNull();
    expect(massDensity(["Si"], null)).toBeNull();
    expect(massDensity([], 100)).toBeNull();
  });
});

describe("virialSummary", () => {
  it("keeps the row-major tensor and derives Voigt order when symmetric", () => {
    const v = virialSummary([1, 0.2, 0.3, 0.2, 2, 0.4, 0.3, 0.4, 3]);
    expect(v?.rows).toEqual([[1, 0.2, 0.3], [0.2, 2, 0.4], [0.3, 0.4, 3]]);
    expect(v?.symmetric).toBe(true);
    expect(v?.voigt).toEqual([1, 2, 3, 0.4, 0.3, 0.2]);
  });

  it("withholds Voigt order but keeps rows when the tensor is asymmetric", () => {
    const v = virialSummary([1, 0.2, 0.3, 0.25, 2, 0.4, 0.3, 0.4, 3]);
    expect(v?.symmetric).toBe(false);
    expect(v?.voigt).toBeNull();
    expect(v?.rows).toEqual([[1, 0.2, 0.3], [0.25, 2, 0.4], [0.3, 0.4, 3]]);
  });

  it("rejects absent, malformed, and non-finite payloads", () => {
    expect(virialSummary(null)).toBeNull();
    expect(virialSummary([1, 2, 3])).toBeNull();
    expect(virialSummary([1, 0.2, 0.3, 0.2, 2, 0.4, 0.3, 0.4, NaN])).toBeNull();
  });
});
