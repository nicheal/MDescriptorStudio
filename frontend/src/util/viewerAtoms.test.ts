import { describe, expect, it } from "vitest";

import { atomPageFor } from "./viewerAtoms";

describe("atomPageFor", () => {
  const rows = Array.from({ length: 120 }, (_, index) => ({ i: index }));

  it("opens the page that holds the selected row", () => {
    expect(atomPageFor(rows, 0, 50)).toBe(1);
    expect(atomPageFor(rows, 49, 50)).toBe(1);
    expect(atomPageFor(rows, 50, 50)).toBe(2);
    expect(atomPageFor(rows, 119, 50)).toBe(3);
  });

  it("follows the page size the user chose", () => {
    expect(atomPageFor(rows, 50, 25)).toBe(3);
    expect(atomPageFor(rows, 50, 200)).toBe(1);
  });

  it("leaves the page alone for a row that is not in this frame", () => {
    expect(atomPageFor([{ i: 7 }], 8, 50)).toBeNull();
    expect(atomPageFor([], 0, 50)).toBeNull();
  });
});
