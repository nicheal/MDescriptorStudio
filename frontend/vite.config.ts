import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const workspaceRoot = fileURLToPath(new URL("../", import.meta.url));

// Tauri expects a fixed port; HMR over the Tauri WebView uses the same origin.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
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
