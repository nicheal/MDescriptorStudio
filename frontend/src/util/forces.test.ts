import { describe, expect, it } from "vitest";
import { forceArrowGeometry, frameMaxForce } from "./forces";
import type { FramePayload } from "../types/protocol";

function makeRow(i: number, fx: number | null, fy: number | null, fz: number | null) {
  const f = fx != null && fy != null && fz != null ? Math.sqrt(fx * fx + fy * fy + fz * fz) : null;
  return { i, el: "Si", x: 0, y: 0, z: 0, fx, fy, fz, f: f == null ? null : Number(f.toFixed(5)) };
}

function makeFrame(rows: ReturnType<typeof makeRow>[]): FramePayload {
  return {
    index: 0,
    natoms: rows.length,
    formula: "Si",
    xyz: "",
    atom_rows: rows,
    energy: null,
    energy_per_atom: null,
    force_max: null,
    virial: null,
    volume: null,
    pbc: "—",
    cell: null,
    ghost_count: 0,
    bond_cutoff: 2.4,
  };
}

describe("forceArrowGeometry", () => {
  it("normalizes arrow length so the frame's strongest force hits the target length", () => {
    // magnitude 3 with frame max 4 → 3/4 of the 3.0 Å target
    const arrow = forceArrowGeometry(0, 0, 3, 4, 1);
    expect(arrow).not.toBeNull();
    expect(arrow!.magnitude).toBeCloseTo(3, 10);
    expect(arrow!.length).toBeCloseTo(2.25, 10);
    expect(arrow!.dir).toEqual({ x: 0, y: 0, z: 1 });
  });

  it("applies the user multiplier and caps extreme lengths", () => {
    expect(forceArrowGeometry(1, 0, 0, 1, 2)!.length).toBeCloseTo(6, 10);
    // multiplier ×10 would be 30 Å but the cap limits the arrow to 12 Å
    expect(forceArrowGeometry(1, 0, 0, 1, 10)!.length).toBe(12);
  });

  it("keeps weak forces visible with a minimum arrow length", () => {
    // quarter of the frame max would compute to 0.75 Å; floored to 1.2 Å
    expect(forceArrowGeometry(1, 0, 0, 4, 1)!.length).toBeCloseTo(1.2, 10);
  });

  it("returns null for missing forces, zero forces, or invalid inputs", () => {
    expect(forceArrowGeometry(null, 1, 1, 4, 1)).toBeNull();
    expect(forceArrowGeometry(0, 0, 0, 4, 1)).toBeNull();
    expect(forceArrowGeometry(1, 1, 1, 0, 1)).toBeNull();
    expect(forceArrowGeometry(1, 1, 1, 4, 0)).toBeNull();
    expect(forceArrowGeometry(Number.NaN, 1, 1, 4, 1)).toBeNull();
  });
});

describe("frameMaxForce", () => {
  it("returns the largest |F| across real atoms", () => {
    const frame = makeFrame([makeRow(0, 0.3, 0.4, 0), makeRow(1, 0, -1, 0), makeRow(2, 0.1, 0, 0)]);
    expect(frameMaxForce(frame)).toBeCloseTo(1, 10);
  });

  it("returns 0 when the frame has no force data", () => {
    expect(frameMaxForce(makeFrame([makeRow(0, null, null, null)]))).toBe(0);
    expect(frameMaxForce(makeFrame([]))).toBe(0);
  });
});
