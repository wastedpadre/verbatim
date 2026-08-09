import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    // For local dev: run the backend on 8080 and `npm run dev` here.
    proxy: {
      "/api": { target: "http://localhost:8080", changeOrigin: true, ws: true },
    },
  },
});
