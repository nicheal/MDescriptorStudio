import { describe, expect, it, vi } from "vitest";
import { formatHistogramTooltip } from "./Histogram";

vi.mock("echarts-for-react", () => ({ default: () => null }));
vi.mock("../i18n", () => ({ useT: () => ({ t: (key: string) => key }) }));

describe("formatHistogramTooltip", () => {
  it("uses the y value from value-axis bar data as the quantity", () => {
    expect(
      formatHistogramTooltip(
        { edges: [-8.44, -8.05], counts: [1523] },
        { data: [-8.245291, 1523], dataIndex: 0 },
        "每原子能量 (eV)",
        "数量",
      ),
    ).toBe("每原子能量 (eV): -8.44 – -8.05<br/>数量: <b>1523</b>");
  });
});
