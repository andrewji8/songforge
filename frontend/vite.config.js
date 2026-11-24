import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Production-grade Vite config. Dev proxies API/WS to the backend so the
// SPA can call /v1 and /ws without CORS in local dev. In prod the API is
// served from the same origin (or a configured VITE_API_BASE).
export default defineConfig({
  plugins: [react()],
  server: {
    // 强制监听 0.0.0.0（全地址族）。Windows 上 Vite 默认可能只绑定
    // IPv6 ::1，导致浏览器访问 http://localhost:5173 (走 127.0.0.1) 拒绝连接。
    host: true,
    strictPort: true,
    port: 5173,
    proxy: {
      '/v1': { target: 'http://localhost:8000', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8000', ws: true },
      '/healthz': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
