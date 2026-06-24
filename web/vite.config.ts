import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// 后端 FastAPI 固定 8788；dev 与 preview 都代理 /api，前端代码里不写死后端地址
const proxy = { "/api": { target: "http://127.0.0.1:8788", changeOrigin: true } };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5180, proxy },
  preview: { port: 5181, proxy },
});
