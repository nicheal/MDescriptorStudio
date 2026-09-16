import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const workspaceRoot = fileURLToPath(new URL("../", import.meta.url));

// Tauri and Playwright share this fixed localhost port; it avoids Windows exclusions.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  define: {
    global: "globalThis",
  },
  server: {
    port: 4173,
    strictPort: true,
    // The UI reuses the native Tauri icon from ../src-tauri/icons during dev.
    // Allow Vite to serve that source file through its /@fs asset URL.
    fs: {
      allow: [workspaceRoot],
    },
  },
  build: {
    target: "es2021",
    outDir: "dist",
  },
});
