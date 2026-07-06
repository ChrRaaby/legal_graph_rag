import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: Vite serves the UI on :5173 and proxies API calls to the FastAPI backend
// on :8000 (which also serves the built bundle in production). The SSE endpoint
// needs buffering off, which the proxy passes through transparently.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
