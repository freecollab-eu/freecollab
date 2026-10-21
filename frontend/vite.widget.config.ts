import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    lib: {
      entry: 'src/widget-entry.ts',
      name: 'FreeCollab',
      fileName: 'widget',
      formats: ['iife'],
    },
    outDir: 'dist-widget',
    emptyOutDir: true,
    minify: true,
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
      },
    },
  },
});
