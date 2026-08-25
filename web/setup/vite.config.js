import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The Python backend serves the built SPA from src/lsm/setup/web/dist, so build
// straight there. In dev, proxy /api to the running lsm-setup server (:8989).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../../src/lsm/setup/web/dist",
    emptyOutDir: true,
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.LSM_SETUP_API || "http://127.0.0.1:8989",
        changeOrigin: true,
      },
    },
  },
});
