import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// A separate build keeps the backend CSP document's entry/CSS names stable.
// Matrix/PDF workers remain real bundled chunks and retain their normal origin.
export default defineConfig({
  plugins: [react()],
  optimizeDeps: { exclude: ['@matrix-org/matrix-sdk-crypto-wasm'] },
  build: {
    emptyOutDir: false,
    cssCodeSplit: false,
    rollupOptions: {
      input: 'src/embed-entry.tsx',
      output: {
        entryFileNames: 'embed-entry.js',
        chunkFileNames: 'embed-assets/[name]-[hash].js',
        assetFileNames: asset => asset.names.some(name => name.endsWith('.css')) ? 'embed-entry.css' : 'embed-assets/[name]-[hash][extname]',
      },
    },
  },
});
