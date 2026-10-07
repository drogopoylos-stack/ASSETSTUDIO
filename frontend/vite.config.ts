import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API + WebSocket to the FastAPI backend on :8777, so the
// frontend uses relative URLs everywhere (works in dev, in the FastAPI-served
// build, and inside Electron).
export default defineConfig({
  plugins: [react()],
  base: "/",
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8777", changeOrigin: true, ws: true },
      "/ws": { target: "ws://127.0.0.1:8777", ws: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
