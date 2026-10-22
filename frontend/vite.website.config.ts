import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// A separate entry keeps account/SSO/native initialization out of the public
// website. The customer application continues to use the normal Vite build.
export default defineConfig({
  root: 'website',
  publicDir: '../public',
  plugins: [react()],
  build: { outDir: '../dist-website', emptyOutDir: true },
  server: { host: '127.0.0.1', port: 3010, strictPort: true },
});
