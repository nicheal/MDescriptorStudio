// AntD ConfigProvider tokens — logos.png palette fused with design doc §124.
import type { ThemeConfig } from "antd";

export const theme: ThemeConfig = {
  token: {
    colorPrimary: "#0F6CBD",
    colorInfo: "#0F6CBD",
    colorSuccess: "#107C10",
    colorWarning: "#F0A000",
    colorError: "#C42B1C",
    colorBgLayout: "#F5F6F8",
    colorBgContainer: "#FFFFFF",
    colorText: "#242424",
    colorTextSecondary: "#616161",
    colorBorder: "#E1E4E8",
    colorBorderSecondary: "#EAECF0",
    borderRadius: 6,
    fontSize: 14,
    controlHeight: 32,
    fontFamily: '"Segoe UI","Microsoft YaHei UI",sans-serif',
  },
};

// scientific categorical palette (10-class, colorblind-aware ordering)
export const categoricalPalette = [
  "#0F6CBD",
  "#00B8A9",
  "#2ECC71",
  "#F0A000",
  "#C42B1C",
  "#7A5AF8",
  "#E858C6",
  "#667085",
  "#0A3D91",
  "#8FB339",
];
