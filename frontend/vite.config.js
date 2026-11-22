import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The UI calls relative URLs (/api/..., /health). In development Vite forwards
// them to the backend, so the browser sees one origin and no CORS is involved.
const backend = process.env.API_BASE_URL || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": backend,
      "/health": backend,
    },
  },
});
