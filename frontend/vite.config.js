import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendPort = process.env.PORTAL_BACKEND_PORT || "3000";
const apiProxyTarget = `http://127.0.0.1:${backendPort}`;
const apiProxyTimeoutMs = 5 * 60 * 1000;

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: apiProxyTarget,
        timeout: apiProxyTimeoutMs,
        proxyTimeout: apiProxyTimeoutMs,
      },
    },
  },
});
