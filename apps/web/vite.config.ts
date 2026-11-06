import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo, /api va al backend (python -m opscentral dev). En producción, Nginx sirve dist/ y /api.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1200 },
});
