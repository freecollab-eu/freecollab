import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

// Same product components and API, with an isolated temporary sample account.
export default defineConfig({
  root: 'demo',
  base: '/product-demo/',
  publicDir: '../public',
  plugins: [react()],
  resolve: { alias: { '/src': fileURLToPath(new URL('./src', import.meta.url)) } },
  optimizeDeps: { exclude: ['@matrix-org/matrix-sdk-crypto-wasm'] },
  build: { outDir: '../dist-demo', emptyOutDir: true },
  server: {
    host: '127.0.0.1', port: 3011, strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, rewrite: path => path.replace(/^\/api/, '') },
      '/socket.io': { target: 'http://127.0.0.1:8000', ws: true },
    },
  },
});
