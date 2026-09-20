import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      ["/api", "/health", "/ready", "/docs", "/openapi.json"].map((path) => [
        path,
        {
          target: process.env.BANK_API_PROXY || "http://127.0.0.1:8000",
          changeOrigin: true,
          timeout: 0,
          proxyTimeout: 0,
        },
      ]),
    ),
  },
});
