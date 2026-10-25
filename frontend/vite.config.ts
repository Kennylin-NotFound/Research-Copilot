import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss(), {
    name: "research-copilot-local-identity",
    configureServer(server) {
      server.middlewares.use("/__rc_runtime", (_request, response) => {
        response.setHeader("Content-Type", "application/json");
        response.end(JSON.stringify({product: "research-copilot", instance_id: process.env.RC_INSTANCE_ID || "unmanaged"}));
      });
    }
  }],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) {
            return undefined;
          }
          if (id.includes("/react/") || id.includes("/react-dom/") || id.includes("/scheduler/")) {
            return "react-vendor";
          }
          if (
            id.includes("/antd/") ||
            id.includes("/@ant-design/") ||
            id.includes("/rc-") ||
            id.includes("/@rc-component/")
          ) {
            return "antd-vendor";
          }
          return "vendor";
        }
      }
    }
  },
  server: {
    host: "127.0.0.1",
    port: 15174,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.RC_API_URL || "http://127.0.0.1:18181",
        changeOrigin: true
      },
      "/ws": {
        target: (process.env.RC_API_URL || "http://127.0.0.1:18181").replace(/^http/, "ws"),
        ws: true
      }
    }
  }
});
