import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const DEFAULT_CONTROL_PLANE_URL = "http://localhost:8000";

export default defineConfig(({ mode }) => {
  // loadEnv returns env from .env files; never read process.env directly so
  // the config stays usable in any Vite-compatible runtime (rolldown, etc).
  const env = loadEnv(mode, ".", "");
  const controlPlaneUrl = env.HELIX_CONTROL_PLANE_URL || DEFAULT_CONTROL_PLANE_URL;
  return {
    plugins: [react()],
    server: {
      port: 5173,
      open: true,
      proxy: {
        "/v1": {
          target: controlPlaneUrl,
          changeOrigin: true,
        },
      },
    },
  };
});
