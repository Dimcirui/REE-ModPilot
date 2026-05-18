import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

const backend = process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/agent': { target: backend, changeOrigin: true, ws: false },
      '/app': { target: backend, changeOrigin: true },
      '/viewport_screenshot': { target: backend, changeOrigin: true },
      '/health': { target: backend, changeOrigin: true },
      '/scene_info': { target: backend, changeOrigin: true },
    },
  },
  build: {
    outDir: path.resolve(__dirname, '../app/static_built'),
    emptyOutDir: true,
    sourcemap: true,
  },
});
