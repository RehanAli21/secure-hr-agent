// Vite runs the page while we develop (npm run dev) and builds it (npm run build).
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The page calls "/api/...", and Vite forwards it to the agent service on port 8001
    // (without the "/api" part). To the browser, everything comes from ONE address, so the
    // login cookie just works and we don't need any CORS settings.
    proxy: {
      "/api": {
        target: "http://localhost:8001",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
