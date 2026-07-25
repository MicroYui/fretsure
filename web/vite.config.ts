import { alphaTab } from "@coderline/alphatab-vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import type { ProxyOptions } from "vite";

const API_TARGET = "http://127.0.0.1:8000";
const DEV_ORIGIN = "http://127.0.0.1:5173";

function loopbackProxy(): ProxyOptions {
  return {
    target: API_TARGET,
    changeOrigin: true,
    configure(proxy) {
      proxy.on("proxyReq", (proxyRequest, request) => {
        if (request.headers.origin === DEV_ORIGIN) {
          proxyRequest.setHeader("origin", API_TARGET);
        }
      });
    },
  };
}

export default defineConfig({
  plugins: [alphaTab(), react()],
  build: {
    outDir: "../src/fretsure/web_static",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": loopbackProxy(),
      "/healthz": loopbackProxy(),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    css: true,
  },
});
